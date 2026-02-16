import os
import json
import nodes
import comfy_execution.caching
from comfy_execution.caching import BasicCache, HierarchicalCache, LRUCache, RAMPressureCache

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CURRENT_DIR, "config.json")

_CONFIG_CACHE = {
    "realtime": False,
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
            _CONFIG_CACHE["node_class"] = data.get("node_class", [])
            _CONFIG_CACHE["_loaded_once"] = True
    except Exception:
        pass

def create_patched_set(original_set_method, cache_class_name):
    def new_set(self, node_id, value):
        load_config()
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
                        print(f"[ComfyUI-NoCache] Cache for node [{node_id}]\"{node_title}\" has been ignored.")
        except Exception as e:
            pass

        if should_cache:
            return original_set_method(self, node_id, value)
        else:
            return None

    return new_set

target_classes = [BasicCache, HierarchicalCache, LRUCache, RAMPressureCache]

for cls in target_classes:
    if hasattr(cls, 'set'):
        original_method = cls.set
        if not getattr(original_method, "_is_patched_by_nocache", False):
            patched_method = create_patched_set(original_method, cls.__name__)
            patched_method._is_patched_by_nocache = True
            cls.set = patched_method

print("[ComfyUI-NoCache] Patches applied. Usage: Add 'NO_CACHE = True' to node class or add '@NoCache' to node title.")

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}