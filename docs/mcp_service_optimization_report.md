# MCP 服务优化报告

日期：2026-07-08

## 结论

当前 `drission-ui` MCP 服务已经具备标准 MCP stdio 服务的基本形态：使用官方 Python MCP SDK 的 `FastMCP` 注册工具，通过 `mcp.run()` 启动，日志输出到 `stderr`，不会污染 stdio 协议帧。

2026-07-09 复核最新公开资料后，本项目继续按 MCP Python SDK v1 生产接口优化：

- MCP 当前规范已进入 `2025-11-25` 版本，服务端工具支持 `annotations` 作为客户端行为提示。
- 官方 Python SDK 文档显示 v2 仍处于预发布/迁移阶段，生产项目应继续使用 v1 并避免无意跨大版本升级。
- DrissionPage 官网稳定主文档仍以 4.1 为主，4.2 测试版已发布；本项目实际安装 `DrissionPage 4.2.0b20`，继续按 4.2 Listener/BrowserContext 等 API 约束实现。

参考资料：

- MCP Tools 规范：https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- MCP Resources 规范：https://modelcontextprotocol.io/specification/2025-11-25/server/resources
- MCP Transports 规范：https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- Python SDK 文档：https://py.sdk.modelcontextprotocol.io/
- DrissionPage 官网：https://www.drissionpage.cn/
- DrissionPage 4.2 测试版说明：https://www.drissionpage.cn/features/4.2/

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
- 自动化测试已全部通过：`74 passed`。

## 变更内容

### 0. 2026-07-09 增量优化：MCP 元数据与 resources

文件：`mcp-servers/drission-ui/server.py`、`mcp-servers/drission-ui/resource_store.py`、`pyproject.toml`

- `mcp[cli]` 依赖改为 `>=1.28.1,<2`，避免 v2 正式发布后自动升级破坏 FastMCP v1 接口。
- 自动为公开工具补充 MCP `ToolAnnotations`：
  - 只读扫描/查询工具标为 `readOnlyHint=true`。
  - 文件证据写入、连接类工具标为 `destructiveHint=false`。
  - 点击、输入、选择、表格单元格点击等真实 UI 操作按保守策略标为 `destructiveHint=true`。
- 新增只读 MCP resources：
  - `drission-ui://caps`
  - `drission-ui://context`
  - `drission-ui://resources`
  - `drission-ui://resources/{resource_path}`
- `resource_store` 新增证据目录索引和安全文本读取函数，所有读取都限制在 `HL_SHOT_DIR` 下。
- `browser_session._ensure_display_env()` 不再硬依赖 `os.getuid()`，Windows 环境中模拟 Linux 场景的测试可稳定运行。
- 测试新增覆盖：tool annotations、resources/templates、caps resource JSON、证据文件索引和路径穿越防护。

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
74 passed in 2.36s
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
