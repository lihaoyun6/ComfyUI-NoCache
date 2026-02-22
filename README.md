# ComfyUI-NoCache  
Ignore caching of any nodes in ComfyUI to save your RAM.  
**[[📃中文版](./README_zh.md)]**

## Installation  

#### Install the node:  
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/lihaoyun6/ComfyUI-NoCache.git
```

#### How to use:  
- Add `@NC` (case-insensitive) to the title of the node where you want to ignore caching.  
- Add `@GC` (case-insensitive) to the title of the node where you want to free up your RAM.  

    > A `@GC` node must be located after a `@NC` node. The `@NC` node cannot clear its own RAM usage.

## Credits  
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) @comfyanonymous
