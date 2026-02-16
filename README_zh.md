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
对于普通用户: 
> 在任意节点标题中添加 `@NoCache` (不分大小写) 即可使其在执行结束后自动释放缓存 (需搭配此插件).

对于开发者:  
> 在您的节点定义中添加 `NO_CACHE = True` 来使其被 ComfyUI 的缓存机制所忽略 (需搭配此插件).

## 致谢   
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) @comfyanonymous
