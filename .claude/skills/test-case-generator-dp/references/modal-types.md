# 弹窗类型参考（SCM 系统）

系统页面中每点击一个按钮后，MUST 先检测是否有弹窗弹出，判断其类型后再决定下一步处理。

## 检测优先级

每次点击操作后，按以下顺序检测：

1. iframe 内检测交互弹窗 / 消息提醒 → 无则
2. top 层（主页面）检测系统级确认弹窗 → 无则
3. 正常继续

原因：业务操作的弹窗（交互弹窗、消息提醒）都在 iframe 内部。top 层的系统级确认弹窗由 session/权限等全局事件触发，iframe 内无法感知。

## 三种弹窗类型

### 1. 交互弹窗 / 业务确认弹窗

| 属性 | 值 |
|------|-----|
| 检测位置 | iframe 内 |
| CSS 选择器 | `.ant-modal-content`（含 `.ant-confirm-body-wrapper`） |
| 特征 | 有标题 `.ant-modal-title` / `.ant-confirm-title` + 内容区 + 操作按钮（确定/取消/关闭×） |
| 处理方式 | DFS 探索弹窗内所有按钮和字段后，点击关闭×或取消返回 |

**判断逻辑**：iframe 内的 `.ant-modal-content` **全部**视为业务弹窗（无论是否包含 `.ant-confirm-body-wrapper`）。只有 top 层的 `.ant-confirm` 才视为系统级确认弹窗。
### 2. 消息提醒

| 属性 | 值 |
|------|-----|
| 检测位置 | iframe 内 |
| CSS 选择器 | `.ant-notification-notice` / `.ant-message-notice` |
| 特征 | 只有文字提示（如「请勾选一项」「正在导出，请稍等...」），无业务操作按钮 |
| 处理方式 | 提取文字内容后关闭或等待自动消失 |

### 3. 系统级确认弹窗

| 属性 | 值 |
|------|-----|
| 检测位置 | top 层主页面 |
| CSS 选择器 | `.ant-modal-content:has(.ant-confirm-body-wrapper)` |
| 特征 | 警告图标 + 文字提示 + 单一操作按钮（如「登录」），出现在主页面顶层 |
| 处理方式 | 按需点击确认/登录按钮处理，或记录后关闭 |

## 弹窗内可交互元素的识别

检测到交互弹窗后，需识别其中哪些元素可点击。使用组合规则：

```
可点击判定 =
  ① 原生交互标签 (button / a / select / textarea / input[type=checkbox/radio/submit] / [role="button"] / [onclick])
  ② OR 计算样式 cursor === 'pointer'（捕获样式驱动的可点击元素，如 label、绑了 click 的 div/span）
```

识别出候选元素后，MUST 检查 className 确认其语义：

| className 关键词 | 实际功能 | 容易误判为 |
|-----------------|---------|-----------|
| `ant-table-row-expand-icon` | 行展开/收起按钮 | 排序图标 |
| `ant-select` | 下拉选择器 | — |
| `ant-calendar-picker` / `ant-picker` | 日期选择器 | — |
| `ant-checkbox` / `ant-radio` | 复选框/单选框 | — |
| `ant-btn` | 按钮 | — |

## 检测代码模板

MCP `detect_modal()` 单次调用即可完成 iframe 内 + top 层两级检测，无需手动查 frame、evaluate DOM。

```python
# Step 1: 调用 detect_modal() — 自动处理 iframe 内 + top 层两级检测
result = detect_modal()

# 返回值结构：
# {
#   "type": "none" | "notification" | "message" | "interactive" | "business_confirm" | "system_confirm",
#   "title": str,           # 弹窗标题（可能为空）
#   "content": str,         # 弹窗正文
#   "buttons": [str],       # 按钮文字数组
#   "hasClose": bool,       # 是否有关闭×按钮
#   "message": str          # 仅 notification 类型
# }

# Step 2: 按 type 分支处理
if result["type"] == "none":
    # 无弹窗，正常继续下一步
    pass
elif result["type"] in ("interactive", "business_confirm"):
    # → 交互弹窗 / 业务确认弹窗：DFS 探索
    # 记录 title / content / buttons，穷尽弹窗内所有可交互元素
    # 探索完成后 close_modal() 关闭
    pass
elif result["type"] in ("notification", "message"):
    # → 消息提醒：提取文字 → close_modal() 或等待自动消失
    pass
elif result["type"] == "system_confirm":
    # → 系统级确认弹窗：检查 session 是否过期
    # 处理流程：check_session() → refresh_session() → 失败则 login_ocr()
    pass
```

> **⚠️ 幽灵元素误报**：`detect_modal()` 偶尔返回 `type: "system_confirm"` 但 `title`、`content`、`buttons` 全部为空。这是 DOM 残留的幽灵元素，并非真实弹窗。处理方式：
> 1. 先调用 `close_modal()` 尝试清除
> 2. 重新调用 `detect_modal()` 确认
> 3. 若仍返回相同幽灵结果，忽略并继续下一步操作

## 各类型处理逻辑

| 类型 | 检测到后做什么 |
|------|--------------|
| **交互弹窗** | 1. 记录弹窗标题和内容 → 2. DFS 探索弹窗内所有下拉字段、输入框、子按钮 → 3. 测试弹窗「取消」和「关闭×」行为 → 4. 关闭后回到列表页 |
| **业务确认弹窗** | 1. 记录弹窗标题、提示文字、按钮 → 2. 点击「取消」或关闭× → 弹窗关闭，数据无变化 → 3. 点击「确定」→ 执行操作，表格刷新 |
| **消息提醒** | 1. 提取消息文字写入预期结果 → 2. 点击关闭×或等待自动消失 → 3. 继续下一步操作 |
| **系统级确认弹窗** | 1. 记录弹窗提示文字 → 2. `check_session()` 检测 → 过期则 `refresh_session()`（缓存 Cookie 注入）→ 失败则 `login_ocr()`（重新免登） → 3. 刷新页面后继续操作 → 4. 在用例备注标注「需重新登录」 |
