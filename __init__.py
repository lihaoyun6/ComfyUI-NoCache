import os
import sys
import gc
import json
import time
import torch
import threading
import contextvars

import nodes
import execution
import comfy.model_management as mm
from comfy_execution.caching import BasicCache, HierarchicalCache, LRUCache, RAMPressureCache

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CURRENT_DIR, "config.json")
_LAST_LOG = ""
_CONFIG_CACHE = {
    "enabled": True,
    "debug": False,
    "node_class": []
}

LOCAL_NOCACHE_CONFIG = contextvars.ContextVar("local_nocache_config", default=None)

class AnyType(str):
    def __ne__(self, __value: object) -> bool:
        return False
    
any_type = AnyType("*")

def load_config():
    global _CONFIG_CACHE
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        changed = False
        if "realtime" in data:
            del data["realtime"]
            changed = True
        if "enabled" not in data:
            data["enabled"] = True
            changed = True
        if changed:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        _CONFIG_CACHE["enabled"] = data.get("enabled", True)
        _CONFIG_CACHE["debug"] = data.get("debug", False)
        _CONFIG_CACHE["node_class"] = data.get("node_class", [])
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
            results.append({"id": node_id, "type": class_type, "logical": l_size, "physical": p_size})
            total_physical_size += p_size

    results.sort(key=lambda x: x["logical"], reverse=True)
    for res in results:
        print(f"#{res['id']: <7} | {res['type']: <32} | {format_size(res['logical']): >10} | {format_size(res['physical']): >10}")
    print("-" * 70)
    print(f"-------- | {'-'*32} | Total Size | {format_size(total_physical_size): >10}")
    print("="*70 + "\n")

def check_is_nocache_raw(node_info, config):
    if not isinstance(node_info, dict):
        return False
    try:
        title = node_info.get("_meta", {}).get("title", "")
        class_type = node_info.get("class_type", "")
        if class_type in config.get("node_class", []):
            return True
        if "@nocache" in title.lower() or "@nc" in title.lower():
            return True
    except Exception:
        pass
    return False

def check_is_nocache(node_id, dynprompt, config):
    try:
        if not dynprompt.has_node(node_id):
            return False
        return check_is_nocache_raw(dynprompt.get_node(node_id), config)
    except:
        return False
    
def purge_stale_nocache_entries(executor, prompt, config):
    if not hasattr(executor, "caches") or not hasattr(executor.caches, "outputs"):
        return
    
    output_cache = executor.caches.outputs
    purged_count = 0
    
    for node_id, node_info in prompt.items():
        if check_is_nocache_raw(node_info, config):
            target_cache_obj = output_cache

            if hasattr(output_cache, "_get_cache_for"):
                try:
                    target_cache_obj = output_cache._get_cache_for(node_id)
                except:
                    continue
                
            if target_cache_obj and hasattr(target_cache_obj, "cache"):
                cache_key = target_cache_obj.cache_key_set.get_data_key(node_id)
                if cache_key is not None and cache_key in target_cache_obj.cache:
                    del target_cache_obj.cache[cache_key]
                    for attr in ["used_generation", "children", "timestamps"]:
                        if hasattr(target_cache_obj, attr) and cache_key in getattr(target_cache_obj, attr):
                            del getattr(target_cache_obj, attr)[cache_key]
                    purged_count += 1
                    if config.get("debug", False):
                        title = node_info.get("_meta", {}).get("title", str(node_id))
                        print(f"[ComfyUI-NoCache] Removed existing cache for #{node_id} \"{title}\" before run.")
                            
    if purged_count > 0:
        gc.collect()
        mm.soft_empty_cache()

def create_patched_set(original_set_method, cache_class_name):
    def new_set(self, node_id, value):
        global _LAST_LOG
        should_cache = True
        config = LOCAL_NOCACHE_CONFIG.get() or _CONFIG_CACHE
        
        if config.get("enabled", True):
            try:
                if hasattr(self, "dynprompt") and self.dynprompt:
                    if check_is_nocache(node_id, self.dynprompt, config):
                        should_cache = False
                        if config.get("debug", False):
                            node_info = self.dynprompt.get_node(node_id)
                            node_title = node_info.get("_meta", {}).get("title", "")
                            LOG = f"[ComfyUI-NoCache] Cache for node #{node_id} [{node_title}] has been ignored."
                            if LOG != _LAST_LOG:
                                print(LOG)
                                _LAST_LOG = LOG
            except Exception as e:
                print(f"[NoCache] Error during cache clearing: {e}")

        if should_cache:
            return original_set_method(self, node_id, value)
        else:
            return None
    return new_set

def patch_executor():
    original_execute_async = execution.PromptExecutor.execute_async
    original_execute = execution.execute
    
    if getattr(original_execute_async, "_is_patched_by_nocache", False):
        return
    
    async def patched_execute_async(self, prompt, prompt_id, extra_data={}, execute_outputs=[]):
        local_config = dict(_CONFIG_CACHE)
        config_nodes = []
        
        for node_id, node_info in prompt.items():
            if isinstance(node_info, dict) and node_info.get("class_type") == "NoCacheConfig":
                config_nodes.append((node_id, node_info))
                
        if len(config_nodes) > 1:
            raise ValueError("[ComfyUI-NoCache] Error: Multiple 'NoCache Config' nodes detected! Please use only ONE per workflow.")
            
        if len(config_nodes) == 1:
            node_id, node_info = config_nodes[0]
            inputs = node_info.get("inputs", {})
            en_val, dbg_val = inputs.get("enabled"), inputs.get("debug")
            if en_val is not None and not isinstance(en_val, list):
                local_config["enabled"] = bool(en_val)
            if dbg_val is not None and not isinstance(dbg_val, list):
                local_config["debug"] = bool(dbg_val)
            print(f"[ComfyUI-NoCache] Configuration Applied: {local_config}")
            
        token = LOCAL_NOCACHE_CONFIG.set(local_config)
        
        if local_config.get("enabled", True):
            try:
                purge_stale_nocache_entries(self, prompt, local_config)
            except Exception as e:
                print(f"[ComfyUI-NoCache] Stale purge failed (harmless): {e}")
        
        try:
            return await original_execute_async(self, prompt, prompt_id, extra_data, execute_outputs)
        finally:
            if local_config.get("debug", False):
                try:
                    run_cache_analysis(self, prompt)
                except Exception as e:
                    print(f"[ComfyUI-NoCache] Analysis failed: {e}")
            LOCAL_NOCACHE_CONFIG.reset(token)
            
    patched_execute_async._is_patched_by_nocache = True
    execution.PromptExecutor.execute_async = patched_execute_async
    
    def _gc_task(node_id, node_title):
        time.sleep(1)
        for i in range(3):
            time.sleep(0.5)
            gc.collect()
            mm.soft_empty_cache()
            print(f"[ComfyUI-NoCache] GC Triggered by #{node_id} \"{node_title}\" (Attempt {i+1}/3)")

    async def patched_execute(server, dynprompt, caches, current_item, extra_data, executed, prompt_id, execution_list, pending_subgraph_results, pending_async_nodes, ui_outputs):
        result = await original_execute(server, dynprompt, caches, current_item, extra_data, executed, prompt_id, execution_list, pending_subgraph_results, pending_async_nodes, ui_outputs)
        config = LOCAL_NOCACHE_CONFIG.get() or _CONFIG_CACHE
        if config.get("enabled", True):
            title = dynprompt.get_node(current_item).get("_meta", {}).get("title", "")
            debug = config.get("debug", False)
            if "@gc" in title.lower():
                threading.Thread(target=_gc_task, args=(current_item, title), daemon=True).start()
        return result
    
    patched_execute._is_patched_by_nocache = True
    execution.execute = patched_execute
    print(f"[ComfyUI-NoCache] Execute patche applied!")
    
print("="*40 + " ComfyUI-NoCache Initialization " + "="*40)
load_config()
target_classes = [BasicCache, HierarchicalCache, RAMPressureCache, LRUCache]
for cls in target_classes:
    if hasattr(cls, 'set'):
        original_method = cls.set
        if not getattr(original_method, "_is_patched_by_nocache", False):
            patched_method = create_patched_set(original_method, cls.__name__)
            patched_method._is_patched_by_nocache = True
            cls.set = patched_method
            print(f"[ComfyUI-NoCache] {cls.__name__} patche applied!")
patch_executor()
print("-"*112)
print("Adding \"@NC\" to the title of any node will skip the cache, and adding \"@GC\" to free up your RAM.")
print("="*112)

class NoCacheConfig:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "enabled": ("BOOLEAN", {"default": True}),
                "debug": ("BOOLEAN", {"default": False}),
            }
        }

    FUNCTION = "main"
    RETURN_TYPES = ()
    CATEGORY = "NoCache"
    
    def main(self, any, enabled, debug):
        return ()

NODE_CLASS_MAPPINGS = {
    "NoCacheConfig": NoCacheConfig,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "NoCacheConfig": "NoCache Config",
}