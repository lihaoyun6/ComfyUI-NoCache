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
For users: 
> Add `@NoCache` (case-insensitive) to the title of the node where you want to ignore caching.

For developers:  
> Add `NO_CACHE = True` to the class definition of your node to prevent it from being cached.

## Credits  
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) @comfyanonymous
