import os
import sys
import json
import torch
import nodes
import execution
import comfy_execution.caching
from comfy_execution.caching import BasicCache, HierarchicalCache, LRUCache, RAMPressureCache

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CURRENT_DIR, "config.json")
LAST_LOG = ""

_CONFIG_CACHE = {
    "realtime": False,
    "debug": False,
    "node_class": [],
    "_loaded_once": False
}

class AnyType(str):
    def __ne__(self, __value: object) -> bool:
        return False
    
any_type = AnyType("*")

def load_config():
    global _CONFIG_CACHE
    
    if _CONFIG_CACHE["_loaded_once"] and not _CONFIG_CACHE["realtime"]:
        return
    if not os.path.exists(CONFIG_PATH):
        _CONFIG_CACHE["_loaded_once"] = True
        return
    
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            _CONFIG_CACHE["realtime"] = data.get("realtime", False)
            _CONFIG_CACHE["debug"] = data.get("debug", False)
            _CONFIG_CACHE["node_class"] = data.get("node_class", [])
            _CONFIG_CACHE["_loaded_once"] = True
    except Exception:
        pass

def format_size(size_bytes):
    if size_bytes == 0:
        return "0.00 B "
    
    size_name = ("B ", "KB", "MB", "GB", "TB")
    i = int(torch.floor(torch.log(torch.tensor(size_bytes)) / torch.log(torch.tensor(1024))))
    p = 1024 ** i
    s = size_bytes / p
    return f"{s:.2f} {size_name[i]}"

def calc_obj_size(obj, seen):
    obj_id = id(obj)
    if obj_id in seen:
        return 0
    seen.add(obj_id)
    
    size = 0
    if isinstance(obj, torch.Tensor):
        if obj.device.type == 'cpu':
            size += obj.element_size() * obj.nelement()

    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            size += calc_obj_size(item, seen)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            size += calc_obj_size(k, seen)
            size += calc_obj_size(v, seen)

    elif hasattr(obj, "__dict__"):
        size += calc_obj_size(vars(obj), seen)
    elif hasattr(obj, "outputs"):
        size += calc_obj_size(obj.outputs, seen)

    else:
        size += sys.getsizeof(obj)
    return size

def run_cache_analysis(executor, prompt):
    print("\n" + "="*70)
    print(f"[ComfyUI-NoCache]      Node Cache Analysis Report          (≥ 1.0 MB)")
    print("=" * 70)
    print(f"Node ID  | {'Node Class Type': <32} | Cache Size | Actual RAM")
    print("-" * 70)
    
    output_cache = executor.caches.outputs
    physical_seen = set()
    total_physical_size = 0
    sorted_node_ids = sorted(prompt.keys(), key=lambda x: int(x) if x.isdigit() else 0)
    
    results = []
    for node_id in sorted_node_ids:
        val = output_cache.get(node_id)
        if val is None:
            continue
        
        logical_seen = set()
        l_size = calc_obj_size(val, logical_seen)
        p_size = calc_obj_size(val, physical_seen)
        
        if l_size >= 1024 * 1024:
            class_type = prompt[node_id].get("class_type", "Unknown")
            results.append({
                "id": node_id,
                "type": class_type,
                "logical": l_size,
                "physical": p_size
            })
            total_physical_size += p_size

    results.sort(key=lambda x: x["logical"], reverse=True)
    for res in results:
        print(f"#{res['id']: <7} | {res['type']: <32} | {format_size(res['logical']): >10} | {format_size(res['physical']): >10}")
        
    print("-" * 70)
    print(f"-------- | {'-'*32} | Total Size | {format_size(total_physical_size): >10}")
    print("="*70 + "\n")

def create_patched_set(original_set_method, cache_class_name):
    def new_set(self, node_id, value):
        load_config()
        global LAST_LOG
        should_cache = True
        
        try:
            if hasattr(self, "dynprompt") and self.dynprompt:
                if self.dynprompt.has_node(node_id):
                    node_info = self.dynprompt.get_node(node_id)
                    node_title = node_info.get("_meta", {}).get("title", "")
                    class_type = node_info.get("class_type")
                    
                    if class_type in _CONFIG_CACHE["node_class"]:
                        should_cache = False
                    
                    if class_type in nodes.NODE_CLASS_MAPPINGS:
                        class_def = nodes.NODE_CLASS_MAPPINGS[class_type]
                        if getattr(class_def, "NO_CACHE", False):
                            should_cache = False

                    if should_cache:
                        if "@nocache" in node_title.lower():
                            should_cache = False
                    
                    if not should_cache:
                        target_cache_obj = self
                        if hasattr(self, "_get_cache_for"):
                            try:
                                target_cache_obj = self._get_cache_for(node_id)
                            except:
                                target_cache_obj = self
                                
                        if target_cache_obj and hasattr(target_cache_obj, "cache"):
                            cache_key = target_cache_obj.cache_key_set.get_data_key(node_id)
                            if cache_key is not None and cache_key in target_cache_obj.cache:
                                del target_cache_obj.cache[cache_key]
                                for attr in ["used_generation", "children", "timestamps"]:
                                    if hasattr(target_cache_obj, attr):
                                        attr_dict = getattr(target_cache_obj, attr)
                                        if cache_key in attr_dict:
                                            del attr_dict[cache_key]
                                            
                        LOG = f"[ComfyUI-NoCache] Cache for node [{node_id}]\"{node_title}\" has been ignored."
                        if LOG != LAST_LOG:
                            print(LOG)
                            LAST_LOG = LOG
        except Exception as e:
            print(f"[NoCache] Error during cache clearing: {e}")

        if should_cache:
            return original_set_method(self, node_id, value)
        else:
            return None

    return new_set

def patch_executor():
    original_execute = execution.PromptExecutor.execute_async

    if getattr(original_execute, "_is_patched_by_nocache", False):
        return
    
    async def patched_execute_async(self, prompt, prompt_id, extra_data={}, execute_outputs=[]):
        try:
            return await original_execute(self, prompt, prompt_id, extra_data, execute_outputs)
        finally:
            if _CONFIG_CACHE.get("debug"):
                try:
                    run_cache_analysis(self, prompt)
                except Exception as e:
                    print(f"[ComfyUI-NoCache] Analysis failed: {e}")
                    
    patched_execute_async._is_patched_by_nocache = True
    execution.PromptExecutor.execute_async = patched_execute_async

print("="*40 + " ComfyUI-NoCache Initialization " + "="*40)

patch_executor()
target_classes = [BasicCache, HierarchicalCache, RAMPressureCache, LRUCache]
for cls in target_classes:
    if hasattr(cls, 'set'):
        original_method = cls.set
        if not getattr(original_method, "_is_patched_by_nocache", False):
            patched_method = create_patched_set(original_method, cls.__name__)
            patched_method._is_patched_by_nocache = True
            cls.set = patched_method
            print(f"[ComfyUI-NoCache] {cls.__name__} patche applied!")

print("-"*112)
print("You can skip caching by adding 'NO_CACHE = True' to the node class or adding '@NoCache' to the node title.")
print("="*112)

class NoCacheDebug:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "any": (any_type,),
                "realtime": ("BOOLEAN", {"default": False}),
                "debug": ("BOOLEAN", {"default": False}),
            }
        }

    FUNCTION = "main"
    RETURN_TYPES = ()
    OUTPUT_NODE = True
    CATEGORY = "NoCache"
    
    def main(self, any, realtime, debug):
        global _CONFIG_CACHE
        _CONFIG_CACHE["realtime"] = realtime
        _CONFIG_CACHE["debug"] = debug
        return ()

NODE_CLASS_MAPPINGS = {
    "NoCacheDebug": NoCacheDebug
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "NoCacheDebug": "NoCache Debug"
}