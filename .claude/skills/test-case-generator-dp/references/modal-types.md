# 弹窗类型参考

## 检测工具选择

| 场景 | 工具 | 说明 |
|------|------|------|
| **综合浮窗检测** | `scan_floats(only_visible=True, include_table_data=True)` | 一次性检测所有可见浮窗：模态框/抽屉/弹出框/提示框/下拉框/消息/通知。返回每浮窗的标题、类型、中心坐标、关闭按钮定位、操作按钮、表单字段、表格数据。**优先使用** |
| **点击后观察（旧方案）** | `observe_start` → action → `observe_wait` | 点击前安装观察器，点击后读取首个信号并清理。适合只需知道「有无弹窗」的场景 |
| **短寿命消息/通知** | `scan_floats` 内置 toast 检测 | 自动调用 `detect_message`/`detect_notification`，捕获「操作成功」这类 ~100ms 延迟渲染的短暂提示 |
| 弹窗清理 | `close_modal` | 清理残留弹窗/通知/消息，避免干扰后续交互 |

## 统一观察器优先级

`observe_wait` first-signal-wins，DOM 信号覆盖：
1. iframe 内交互弹窗/通知/消息 → 无则
2. top 层弹窗/通知/消息 → 无则
3. `none`

## 三种弹窗类型

### 1. 交互弹窗 / 业务确认弹窗

| 属性 | 值 |
|------|-----|
| 检测位置 | 由 MCP 返回的 `scope` 判断 |
| 特征 | 有标题 + 内容区 + 操作按钮（确定/取消/关闭×） |
| 处理方式 | DFS 探索弹窗内所有按钮和字段后，点击关闭×或取消返回 |

**判断逻辑**：业务模块内出现的可交互弹窗优先视为业务弹窗；顶层会话/权限/系统确认类弹窗按系统级确认处理。

### 2. 消息提醒

| 属性 | 值 |
|------|-----|
| 检测位置 | 由 MCP 返回的 `scope` 判断 |
| 特征 | 只有文字提示，无业务操作按钮 |
| 处理方式 | 提取文字内容后关闭或等待自动消失 |

### 3. 系统级确认弹窗

| 属性 | 值 |
|------|-----|
| 检测位置 | 由 MCP 返回的 `scope` 判断 |
| 特征 | 警告图标 + 文字提示 + 单一操作按钮 |
| 处理方式 | `check_session()` → 过期则 `refresh_session()` |

## observe_wait 返回值结构

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

调用：先 `observe_start(signals=["modal","notification","message","tab","url"], listen_targets="gateway")`，执行 action 后 `observe_wait(timeout=8)`
- `signals` 默认 `["modal","notification","message","tab","url"]`，加 `"network"` 需配 `listen_targets`
- `listen_targets` 用 `"gateway"` 抓 SCM 所有 API（保存接口走 `gateway.hoolinks.com/api/gateway`，业务关键词不命中）
- 保存、提交、审核等后端校验类操作可能延迟返回提示；保存类操作统一使用更长 `observe_wait` 超时时间（建议 30s+）。若超时但页面出现明显反馈，应补充截图和 DOM 观察结果，不在 skill 中内联 raw JS 兜底。


## 各类型处理逻辑

| 类型 | 检测到后做什么 |
|------|--------------|
| **交互弹窗** | 记录标题和内容 → DFS 弹窗内字段/按钮 → 测「取消」和「关闭×」→ 关闭后回到列表页 |
| **业务确认弹窗** | 记录标题/提示/按钮 → 分别测「取消」→「关闭×」→「确定」，各自衍生用例 |
| **消息提醒** | 提取消息文字 → 关闭或等待自动消失 |
| **系统级确认弹窗** | `check_session()` → 过期则 `refresh_session()` |

> ⚠️ **幽灵元素误报**：偶尔返回 `type: "system_confirm"` 但 `title/content/buttons` 全部为空。先 `close_modal()` 清除，重新检测，仍返回相同结果则忽略继续。
