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

import sys
import torch
import logging

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
    print("\n" + "="*65)
    print(f"[ComfyUI-NoCache] Node Cache Analysis Report")
    print("="*65)
    print(f"Index | {'Node Class Type': <30} | Cache Size | Actual RAM")
    print("-" * 65)
    
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
        
        if l_size > 1024 * 1024:
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
        print(f"#{res['id']: <4} | {res['type']: <30} | {format_size(res['logical']): >10} | {format_size(res['physical']): >10}")
        
    print("-" * 65)
    print(f"----- | ------------------------------ | Total Size | {format_size(total_physical_size): >10}")
    print("="*65 + "\n")

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
                        LOG = f"[ComfyUI-NoCache] Cache for node [{node_id}]\"{node_title}\" has been ignored."
                        if LOG != LAST_LOG:
                            print(LOG)
                            LAST_LOG = LOG
        except Exception as e:
            raise e

        if should_cache:
            return original_set_method(self, node_id, value)
        else:
            return None

    return new_set

def patch_executor():
    original_execute = execution.PromptExecutor.execute_async
    
    async def patched_execute_async(self, prompt, prompt_id, extra_data={}, execute_outputs=[]):
        try:
            return await original_execute(self, prompt, prompt_id, extra_data, execute_outputs)
        finally:
            if _CONFIG_CACHE["debug"]:
                try:
                    run_cache_analysis(self, prompt)
                except Exception as e:
                    print(f"[ComfyUI-NoCache] Analysis failed: {e}")
                    
    execution.PromptExecutor.execute_async = patched_execute_async

target_classes = [BasicCache, HierarchicalCache, LRUCache, RAMPressureCache]
for cls in target_classes:
    if hasattr(cls, 'set'):
        original_method = cls.set
        if not getattr(original_method, "_is_patched_by_nocache", False):
            patched_method = create_patched_set(original_method, cls.__name__)
            patched_method._is_patched_by_nocache = True
            cls.set = patched_method
patch_executor()
print("[ComfyUI-NoCache] Patches applied. Usage: Add 'NO_CACHE = True' to node class or add '@NoCache' to node title.")

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}