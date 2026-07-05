# 弹窗类型参考

## 检测工具选择

| 场景 | 工具 | 说明 |
|------|------|------|
| **点击后默认观察** | `observe_post_click` | 统一观察器：并发抓 弹窗/通知/消息/Tab/URL/网络，first-signal-wins，MutationObserver 事件驱动。能抓短寿命 toast（~3s） |
| 单点复核弹窗 | `detect_modal` | 三级优先级轮询，返回单个弹窗/通知/消息 |
| 单信号专项 | `detect_notification` / `detect_message` / `detect_url_change` / `detect_tab_change` | 原子工具，事件驱动 wait |

## detect_modal 内部优先级（单点复核时）

按顺序检测，首个命中即返回：
1. iframe 内交互弹窗/通知/消息 → 无则
2. top 层弹窗/通知/消息 → 无则
3. `none`

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

## observe_post_click 返回值结构

```python
# 命中（任一信号触发即返回）
{
  "type": "interactive" | "confirm" | "system_confirm" | "notification" | "message"
          | "tab_change" | "url_change" | "network",
  "scope": "top" | "iframe",        # DOM 信号附带
  "payload": {...},                  # DOM 信号的完整分类结果（title/content/buttons/kind/message）
  "elapsedMs": int,                  # 从观察到命中耗时
  ...信号专属字段                    # network: url/method/status; tab_change: tab_count; url_change: url/old_url
}
# 未命中
{"type": "none", "elapsedMs": int, "watched": [...]}
```

调用：`observe_post_click(timeout=8, signals=["modal","notification","message","tab","url"], listen_targets="gateway")`
- `signals` 默认 `["modal","notification","message","tab","url"]`，加 `"network"` 需配 `listen_targets`
- `listen_targets` 用 `"gateway"` 抓 SCM 所有 API（保存接口走 `gateway.hoolinks.com/api/gateway`，业务关键词不命中）
- ⚠️ **保存校验 notification 异步耗时 ~18s**：销售订单保存后前端调后端校验明细，~18s 后才弹 notification（如"序号为【1】的货物未填写销售数量..."）。`observe_wait`/`observe_post_click` timeout 需 ≥ 25s，否则漏抓（实测 timeout=15 漏抓、timeout=35 成功，elapsedMs=18732）。保存类操作统一用 timeout=30+；失败 notification 是持久型（.ant-notification-notice），不会自动消失，超时后用 `run_js` 读 `.ant-notification-notice` 文本可兜底复核。

## detect_modal 返回值结构

```python
{
  "type": "none" | "notification" | "message" | "interactive" | "confirm" | "system_confirm",
  "scope": "iframe" | "top",
  "title": str,          # interactive/confirm/system_confirm
  "content": str,        # interactive/confirm/system_confirm
  "buttons": [str],      # interactive/confirm/system_confirm
  "hasClose": bool,      # interactive/confirm/system_confirm
  "message": str         # notification/message
}
```

> **type 语义**：iframe 内 `.ant-modal-content` → `interactive`（含 `.ant-confirm-body` 则 `confirm`）；top 层 `.ant-modal-content` 含 `.ant-confirm-body` → `system_confirm`（向后兼容旧契约），其余按 `interactive`/`notification`/`message`。
> **已修复盲区**：顶层 `.ant-message-notice`（保存成功 toast）/ `.ant-notification-notice` 此前被丢弃，现已覆盖；隐藏/残留 modal 不再早退遮挡其他信号。

## 各类型处理逻辑

| 类型 | 检测到后做什么 |
|------|--------------|
| **交互弹窗** | 记录标题和内容 → DFS 弹窗内字段/按钮 → 测「取消」和「关闭×」→ 关闭后回到列表页 |
| **业务确认弹窗** | 记录标题/提示/按钮 → 分别测「取消」→「关闭×」→「确定」，各自衍生用例 |
| **消息提醒** | 提取消息文字 → 关闭或等待自动消失 |
| **系统级确认弹窗** | `check_session()` → 过期则 `refresh_session()` → 失败则 `login_ocr()` |

> ⚠️ **幽灵元素误报**：偶尔返回 `type: "system_confirm"` 但 `title/content/buttons` 全部为空。先 `close_modal()` 清除，重新检测，仍返回相同结果则忽略继续。
