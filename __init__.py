import nodes
import comfy_execution.caching
from comfy_execution.caching import BasicCache, HierarchicalCache, LRUCache, RAMPressureCache

def create_patched_set(original_set_method, cache_class_name):
    def new_set(self, node_id, value):
        should_cache = True
        try:
            if hasattr(self, "dynprompt") and self.dynprompt:
                if self.dynprompt.has_node(node_id):
                    node_info = self.dynprompt.get_node(node_id)
                    class_type = node_info.get("class_type")
                    
                    if class_type in nodes.NODE_CLASS_MAPPINGS:
                        class_def = nodes.NODE_CLASS_MAPPINGS[class_type]
                        if getattr(class_def, "NO_CACHE", False):
                            should_cache = False

                    if should_cache:
                        node_title = node_info.get("_meta", {}).get("title", "")
                        if "@nocache" in node_title.lower():
                            should_cache = False
                            
        except Exception as e:
            pass

        if should_cache:
            return original_set_method(self, node_id, value)
        else:
            return None

    return new_set

print("[ComfyUI-NoCache] Applying Monkey Patches...")

target_classes = [BasicCache, HierarchicalCache, LRUCache, RAMPressureCache]
for cls in target_classes:
    if hasattr(cls, 'set'):
        original_method = cls.set
        cls.set = create_patched_set(original_method, cls.__name__)

print("[ComfyUI-NoCache] Patches applied. Usage: Add 'NO_CACHE = True' to node class or add '@NoCache' to node title.")

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}