# 弹窗类型参考

## 检测优先级

每次点击操作后按顺序检测：
1. iframe 内交互弹窗/消息提醒 → 无则
2. top 层系统级确认弹窗 → 无则
3. 正常继续

## 三种弹窗类型

### 1. 交互弹窗 / 业务确认弹窗

| 属性 | 值 |
|------|-----|
| 检测位置 | iframe 内 |
| CSS 选择器 | `.ant-modal-content`（含 `.ant-confirm-body-wrapper`） |
| 特征 | 有标题 + 内容区 + 操作按钮（确定/取消/关闭×） |
| 处理方式 | DFS 探索弹窗内所有按钮和字段后，点击关闭×或取消返回 |

**判断逻辑**：iframe 内的 `.ant-modal-content` **全部**视为业务弹窗。只有 top 层的 `.ant-confirm` 才视为系统级确认弹窗。

### 2. 消息提醒

| 属性 | 值 |
|------|-----|
| 检测位置 | iframe 内 |
| CSS 选择器 | `.ant-notification-notice` / `.ant-message-notice` |
| 特征 | 只有文字提示，无业务操作按钮 |
| 处理方式 | 提取文字内容后关闭或等待自动消失 |

### 3. 系统级确认弹窗

| 属性 | 值 |
|------|-----|
| 检测位置 | top 层主页面 |
| CSS 选择器 | `.ant-modal-content:has(.ant-confirm-body-wrapper)` |
| 特征 | 警告图标 + 文字提示 + 单一操作按钮 |
| 处理方式 | `check_session()` → 过期则 `refresh_session()` → 失败则 `login_ocr()` |

## detect_modal 返回值结构

```python
{
  "type": "none" | "notification" | "message" | "interactive" | "business_confirm" | "system_confirm",
  "title": str,
  "content": str,
  "buttons": [str],
  "hasClose": bool,
  "message": str
}
```

## 各类型处理逻辑

| 类型 | 检测到后做什么 |
|------|--------------|
| **交互弹窗** | 记录标题和内容 → DFS 弹窗内字段/按钮 → 测「取消」和「关闭×」→ 关闭后回到列表页 |
| **业务确认弹窗** | 记录标题/提示/按钮 → 分别测「取消」→「关闭×」→「确定」，各自衍生用例 |
| **消息提醒** | 提取消息文字 → 关闭或等待自动消失 |
| **系统级确认弹窗** | `check_session()` → 过期则 `refresh_session()` → 失败则 `login_ocr()` |

> ⚠️ **幽灵元素误报**：偶尔返回 `type: "system_confirm"` 但 `title/content/buttons` 全部为空。先 `close_modal()` 清除，重新检测，仍返回相同结果则忽略继续。
