# MCP 服务优化报告

日期：2026-07-08

## 结论

当前 `drission-ui` MCP 服务已经具备标准 MCP stdio 服务的基本形态：使用官方 Python MCP SDK 的 `FastMCP` 注册工具，通过 `mcp.run()` 启动，日志输出到 `stderr`，不会污染 stdio 协议帧。

本次优化前的主要问题不在协议接入层，而在工具治理层：

- 实际暴露工具数为 60，但测试契约仍按 58 个工具校验。
- `DRISSION_UI_CAPS=core` 不会影响 `tools/list`，所有工具仍会暴露。
- 新增工具 `scan_floats`、`get_element_coords` 未进入 capability 分组。
- 部分 list 入参 schema 过宽，生成 `items: {}`，不利于 Agent 准确构造参数。

本次优化后：

- 默认/all 模式仍暴露 60 个公开工具。
- `DRISSION_UI_CAPS=core` 会实际收窄到 29 个工具。
- 公开工具清单、capability 分组、测试契约保持一致。
- 常用 list 入参 schema 已精确到 `items.type = string`。
- 自动化测试已全部通过：`63 passed`。

## 变更内容

### 1. MCP 工具注册增加 capability 过滤

文件：`mcp-servers/drission-ui/server.py`

在 `FastMCP("drission-ui")` 初始化后增加 cap-aware tool decorator：

- 保留标准 `@mcp.tool()` 写法。
- 注册时读取 `caps.is_tool_enabled(tool_name)`。
- 未启用的工具不会进入 MCP `tools/list`。
- 被过滤工具只输出 debug 日志，避免正常启动时污染 stderr。

这使 `DRISSION_UI_CAPS` 从“仅报告能力”变成“真实控制工具暴露面”。

### 2. 补齐 capability 分组

文件：`mcp-servers/drission-ui/caps.py`

将新增公开工具补入 `core` 分组：

- `scan_floats`
- `get_element_coords`

同时保证 `browser_list_caps` 始终可见，避免用户在收窄能力后无法查询当前能力分组。

### 3. 精化 MCP input schema

文件：`mcp-servers/drission-ui/server.py`

将部分裸 `list` 类型改成 `list[str]`：

- `find_batch.locators`
- `observe_start.signals`
- `explore_action.signals`
- `explore_action.modifiers`
- `browser_press_key.modifiers`

优化后 MCP schema 会生成明确的数组元素类型：

```json
{
  "type": "array",
  "items": {
    "type": "string"
  }
}
```

### 4. 更新和补充测试

文件：`tests/test_server.py`

新增/调整测试覆盖：

- 公开工具清单更新到 60 个工具。
- 公开工具必须全部归入 `CAP_GROUPS`。
- `DRISSION_UI_CAPS=core` 必须实际过滤 `tools/list`。
- 常用 list 参数必须生成明确的 `items.type = string` schema。

## 验证结果

完整测试：

```bash
uv run pytest tests/ -q
```

结果：

```text
63 passed in 1.77s
```

默认/all 工具面：

```text
60 tools
```

`DRISSION_UI_CAPS=core` 工具面：

```text
29 tools
```

core 模式下确认：

- `connect`、`scan_floats`、`get_element_coords`、`browser_list_caps` 可见。
- `scan_table` 不可见。
- `run_js` 不可见。

## 与标准 MCP 服务的符合度

参考官方 MCP 规范：

- Tools: https://modelcontextprotocol.io/specification/2025-06-18/server/tools
- Transports: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports

当前服务符合以下要求：

- 使用 MCP SDK 注册工具。
- 通过 stdio 运行，适配 Claude/Codex 等 MCP 客户端。
- 工具具备 name、description、inputSchema。
- 日志走 stderr，避免污染 stdout 协议帧。
- `tools/list` 暴露的工具面现在可被 capability 配置控制。

仍可继续增强的方向：

- 为高风险工具补充更明确的 tool annotations 或内部风险分级。
- 对 `action`、`kind`、`direction` 等固定取值参数进一步使用枚举类型。
- 增加 MCP 客户端级 smoke test，覆盖真实 stdio 初始化、`tools/list`、`tools/call`。
- 按测试用例生成场景定义推荐 caps，例如 `core,filter,vtable,observe,network`。

## 建议默认使用方式

日常页面分析和测试用例生成建议优先使用：

```bash
DRISSION_UI_CAPS=core,filter,vtable,observe,network
```

需要执行调试、下载、控制台、PDF、权限、上下文隔离等能力时，再显式启用：

```bash
DRISSION_UI_CAPS=all
```

这样可以降低 Agent 工具选择噪声，同时保留高覆盖测试设计所需的页面结构、表格、弹窗、网络和状态流转信息。
