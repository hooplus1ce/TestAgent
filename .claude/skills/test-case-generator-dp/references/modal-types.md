# 弹窗类型参考

## 检测工具选择

| 场景 | 工具 | 说明 |
|------|------|------|
| **当前浮层快照** | `observe_snapshot(only_visible=True, include_table_data=True, detail="full")` | 读取所有当前可见浮窗及 VTable 列头筛选、工具栏提示、列设置菜单，返回标题、类型、坐标、按钮、字段和表格数据。**优先用于交互前检查** |
| **DOM 动作后观察** | `explore_action(...)` | facade 在动作前安装观察器，动作后从 `signal` 返回首个反馈 |
| **表格动作后观察** | `table_action(...)` | facade 同时完成表格动作和反馈采集，避免漏掉短寿命 toast |
| 弹窗清理 | `close_modal` | 清理残留弹窗/通知/消息，避免干扰后续交互 |

## facade 信号优先级

`explore_action` / `table_action` 的 `signal` 使用 first-signal-wins，DOM 信号覆盖：
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

## signal 返回值结构

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

调用：`explore_action(..., signals=["modal","notification","message","tab","url","network"], listen_targets="gateway", timeout=30)`。
- `signals` 默认覆盖浮层、消息、页签和 URL；监听网络时提供 `listen_targets`。
- `listen_targets="gateway"` 用于 SCM gateway API。
- 保存、提交、审核等后端校验类操作使用更长 facade timeout（建议 30s+）。若超时但页面出现明显反馈，应补充 `observe_snapshot`，不在 Skill 中内联 raw JS 兜底。


## 各类型处理逻辑

| 类型 | 检测到后做什么 |
|------|--------------|
| **交互弹窗** | 记录标题和内容 → DFS 弹窗内字段/按钮 → 测「取消」和「关闭×」→ 关闭后回到列表页 |
| **业务确认弹窗** | 记录标题/提示/按钮 → 分别测「取消」→「关闭×」→「确定」，各自衍生用例 |
| **消息提醒** | 提取消息文字 → 关闭或等待自动消失 |
| **系统级确认弹窗** | `check_session()` → 过期则 `refresh_session()` |

> ⚠️ **幽灵元素误报**：偶尔返回 `type: "system_confirm"` 但 `title/content/buttons` 全部为空。先 `close_modal()` 清除，重新检测，仍返回相同结果则忽略继续。
