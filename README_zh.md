# ComfyUI-NoCache  
在 ComfyUI 中忽略任意节点的缓存以节省内存.  
**[[📃English](./README.md)]**

## 安装  

#### 安装节点:  
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/lihaoyun6/ComfyUI-NoCache.git
```

#### 使用方法:  
- 在任意节点标题中添加 `@NC` (不分大小写) 来使其输出值不被 ComfyUI 所缓存.  
- 在任意节点标题中添加 `@GC` (不分大小写) 来释放已执行的 NC 节点占用的内存.  

    > `@GC` 节点必须位于 `@NC` 节点之后. 因为 `@NC` 节点无法自己清理自己的内存占用.

## 致谢   
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) @comfyanonymous
