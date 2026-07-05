---
name: modal-interaction-strategy
description: SCM弹窗交互必须先dom_tree分析DOM结构再用精确选择器操作
metadata:
  type: project
---

与SCM系统弹窗交互时，**必须先通过 `dom_tree(selector=".ant-modal")` 获取弹窗完整DOM结构**，分析后再用精确选择器执行交互操作。

**Why:** SCM系统大量使用自定义封装弹窗（如带 `modal-minimize-btn`/`modal-maximize-btn` 的高级搜索弹窗），React合成事件与DrissionPage原生模拟点击存在兼容性差异。盲目猜测选择器会导致点击无效——即使点击返回ok，React组件状态也未更新。

**How to apply:**
```
observe_start() → 触发动作 → observe_wait() → 确认弹窗存在
  ↓
dom_tree(selector=".ant-modal", max_depth=8) → 获取弹窗完整结构
  ↓
分析DOM → 确定精确交互目标选择器
  ↓
click/input → 执行交互
  ↓
observe_start() → 触发动作 → observe_wait() → 确认状态变化
```

**Related:** 点击结果观察已统一为 `observe_start` → action → `observe_wait`；残留弹窗/通知/消息用 `close_modal` 清理。

See also [[test-case-generator-dp-skill]]
