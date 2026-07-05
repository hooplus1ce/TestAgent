---
name: scm-save-button-two-forms
description: SCM保存按钮有两种形态——下拉触发器型(ant-dropdown-trigger,弹菜单需二次点选项)与普通型(直接保存)，点击前必须先判断className
metadata:
  type: project
---

SCM 系统（demo19-scm.hoolinks.com）的"保存"类按钮有**两种形态**，点击前必须先 `run_js` 读 `button.className` 判断，不能假定所有保存按钮行为一致：

**形态一·下拉触发器型**：`className` 含 `ant-dropdown-trigger`（如销售订单新增页"保 存"为 `ant-btn ant-dropdown-trigger ant-btn-primary`）。点击只弹下拉菜单，常见项"保存"/"保存并新增"。需**二次点击菜单项**才真正保存：
```python
click("x://button[span[text()='保 存']]")  # 仅展开菜单
click("x://li[contains(@class,'ant-dropdown-menu-item')][normalize-space(text())='保存']")  # 才触发保存
```

**形态二·普通按钮型**：`className` 不含 `ant-dropdown-trigger`（如 `ant-btn ant-btn-primary`）。点击直接触发保存，无需二次操作。

**判断流程**：
1. `run_js` 读取目标按钮 `className`（注意按钮内文本常带空格，如"保 存"，用 `x://button[span[text()='保 存']]` 精确定位）
2. 含 `ant-dropdown-trigger` → 两步：click 按钮展开 → click `li.ant-dropdown-menu-item` 选项
3. 不含 → 一步：click 按钮直接保存

**Why**: 销售订单用例中"保 存"是下拉触发器，直接 click 后误以为已保存，实际只弹了菜单。后续操作时按钮已变查看态（订单被另一次异步操作保存），导致 click "保 存" 定位失败、保存 toast 错失监听时机。但此行为**不是所有保存按钮通用**——普通型保存按钮直接点击即可，二次点菜单项反而会报错或无反应。两种情况必须分别处理。

**How to apply**: 任何保存/提交/确认类按钮，点击前先 `run_js` 读 className 判断形态；若为下拉触发器型，用 observe 两段式（observe_start → 点按钮 → 点菜单项 → observe_wait）包裹，确保抓到保存 toast/接口。VTable 明细字段填充后触发的保存，还需配合 `changeSourceCellValue`（见 references/vtable-interaction.md「VTable records 与业务侧 React state 同步」）。
