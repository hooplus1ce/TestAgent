---
name: drissionpage-gotchas
description: DrissionPage run_js 必须 top-level return；tab.ele() 会递归 iframe——drissionpage-mcp 服务端开发必知
metadata:
  type: reference
---

# DrissionPage 两个非显然陷阱（drissionpage-mcp 服务端开发）

## 1. run_js 必须 top-level `return`，IIFE 会丢弃返回值

`tab.run_js(script)` / `fr.run_js(script)` 只捕获**顶层** `return` 的值。

❌ 错误：用 IIFE 包裹，`return` 在 IIFE 内 → 返回 `None`
```js
(function(){
  ...
  return JSON.stringify({ok:true});  // 这是 IIFE 函数的 return，不是顶层
})();
```
DrissionPage 拿到 `None`，即使副作用（如 `window.__du_signals.push(...)`）已生效。

✅ 正确：顶层代码 + 顶层 `return`
```js
var s = window.__du_signals || [];
return JSON.stringify(s.slice(0,5));
```

诊断信号：run_js 返回 None 但 `window` 上的副作用可见 → 八成是 IIFE 吞了返回值。

## 2. `tab.ele('c:.xxx')` 会递归进 iframe

`tab.ele()` / `tab.eles()` 默认**穿透同源 iframe**搜索，不只查顶层文档。

后果：想在 top 文档判定是否有 `.ant-modal-content`，`tab.ele('c:.ant-modal-content')` 会命中 **iframe 内**的残留 modal，导致 scope 误判 + 跨 frame 串扰。

✅ scope 精确检测：用 `target.run_js` + `document.querySelector`（不递归 iframe）
```python
res = target.run_js(r"""
  var m = document.querySelector('.ant-modal-content');
  if (m) { ... return JSON.stringify({type:'interactive',...}); }
  return JSON.stringify({type:'none'});
""")
```
`tab.run_js` 查 top 文档，`fr.run_js` 查 iframe 文档，互不串扰。

## 历史背景

这两个陷阱导致 `detect_modal` 漏抓保存成功 toast（`.ant-message-notice`，顶层，~3s 寿命）：
- `tab.ele('c:.ant-modal-content')` 递归命中 iframe 内残留隐藏 modal
- 残留 modal 触发 `wrap_hidden` 早退 `return none`，跳过 message 检查
- 修复见 modal.py `_detect_in_target`（改用 run_js+querySelector，不早退）和 observe.py
