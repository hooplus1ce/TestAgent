# drissionpage-mcp

这是与原 `mcp-servers/drission-ui` 平行的新 MCP 服务目录。原目录保持不改；本目录内的浏览器连接、元素定位、点击、输入、截图、监听、弹窗观察、HTML 表格和 VTable facade 都通过 DrissionPage 实现。VTable 私有实例、React fiber 探测、canvas 坐标换算等仍封装在工具内部的 `frame.run_js()` 中，不暴露给调用侧。

## 文档基线

- 官方稳定文档：DrissionPage 4.1.1.4，见 https://www.drissionpage.cn/ 。
- 浏览器控制：使用 `Chromium` / `ChromiumOptions` 连接或接管浏览器。
- 元素与 iframe：使用 `ele()` / `eles()` / `get_frame()` 和 DrissionPage 定位语法。
- 交互：使用元素 `click()` / `input()`、`tab.actions` 动作链、`wait` 智能等待。
- JS：`run_js()` 结果必须顶层 `return`，本目录保留该约束并写入工具实现。
- 监听：兼容 4.1.1.4 的 `listen.start(targets, method, res_type)`，同时探测 4.2 beta 的 `listen.set_method` / `listen.set_res_type`。
- 4.2 beta 能力：`download.by_browser`、WebSocket 专项监听、BrowserContext、权限设置均做能力探测；缺失时返回结构化 `ok=false`。

详细摘录见 [docs/drissionpage-official-notes.md](docs/drissionpage-official-notes.md)。

## .claude 规则落地

本目录同时参考了项目 `.claude` 下的技能和记忆：

### 工具选择规则

三个核心交互工具各有定位，按以下规则选取：

| 工具 | 用途 | 使用时机 |
|------|------|----------|
| `scan_page_elements` | **全页侦查**，获取坐标/ref/框架归属 | 进入模块后第一次摸底，了解页面布局和所有可交互元素 |
| `explore_action` | **动作+自动验证**，内置 `observe_start → action → observe_wait` | 每次点击/输入等操作，自动捕获前后状态变化、浮层、网络信号 |
| `find_elements` | **精准轻量查找**，确认元素存在或计数 | 已知要找什么，只需确认某个元素在不在 / 有几个 |

**反模式**：每次操作后用 `scan_page_elements` 做验证——冗余且慢。点击后应直接用 `explore_action`（自带 observe_wait 快照）。

**正确流程**：
1. 进入模块 → `scan_page_elements` 一次，摸底
2. 每次操作 → `explore_action`（click + observe_wait 一步到位）
3. 需要快速断言某个元素 → `find_elements`

### 输出瘦身规则

`explore_action` 和 `observe_snapshot` 的输出已优化，减少 token 消耗：

| 改动 | 说明 |
|------|------|
| `explore_action.capture_after` 默认 `False` | 不再返回冗余的完整页面模型（actions/fields/modals/tables） |
| `explore_action.include_snapshot` 默认 `True` | 通过 `signal.snapshot_after` 返回精简浮层快照 |
| `explore_action.detail` 默认 `"summary"` | 日历只返回年/月/选中日期和单元格数，不返回每个格子的坐标/xpath |
| `observe_snapshot.detail` 默认 `"summary"` | 同上，日历摘要模式 |
| `scan_floats` 新增 `detail` 参数 | JS 层按 `"summary"` / `"full"` 控制日历单元格返回粒度 |
| VTable 列头筛选菜单纳入 observer | `.vtable-filter-menu` 会作为 `vtable-filter-menu` 浮层返回，按 `display` 判断是否可见，并提取 tabs、勾选值、条件筛选控件和按钮 |

**用法**：
- 只需确认浮层有无 → `explore_action`（`signal.snapshot_after` 够用）
- 需要完整页面动作列表/表格数据 → 显式 `capture_after=True`
- 需要全量日历单元格 → `observe_snapshot(detail="full")` 或 `explore_action(detail="full")`
- 设置单日期字段 → `set_date(field_name="工作日期", date="2026-06-01")`，一次完成打开日历、翻月、选日和字段值校验，只返回日期浮窗与字段值的紧凑信息

### 其他规则

- VTable 必须通过表格 facade 操作：`scan_table`、`get_vtable_cell_render_info`、`get_vtable_cell_icons`、`vtable_action`、`click_table_cell`、`get_table_values`，调用侧不手写 VTable raw JS。
- VTable 状态标签/字体/单元格背景等视觉断言使用 `get_vtable_cell_render_info`；数据行单元格内图标先用 `get_vtable_cell_icons` 探测，再用 `vtable_action(target="cell-icon", icon_name=... 或 icon_index=...)` 点击。
- VTable 下拉优先识别 `.virtual-option` / virtual 类选项，再降级到普通 option 或 Ant Design。
- VTable 列头筛选菜单由 observer / `observe_snapshot` 捕获，不再需要用 `run_js` 单独读取 `.vtable-filter-menu`。
- 弹窗/浮层交互统一走观察器：当前状态用 `observe_snapshot`，交互前后用 `observe_start -> action -> observe_wait`，优先使用封装好的 `explore_action`。
- 筛选区优先内联模式，字段扫描必须返回字段、操作符和值控件模式。
- 保存类按钮区分普通按钮和 `ant-dropdown-trigger` 下拉按钮。
- `run_js` 一律顶层 `return`；需要 scope 精确判断时用 `target.run_js(document.querySelector(...))`，避免 `tab.ele()` 递归 iframe 导致误判。

## 启动

从项目根目录运行：

```bash
uv run --project drissionpage-mcp python drissionpage-mcp/server.py
```

或进入目录运行：

```bash
cd drissionpage-mcp
uv run python server.py
```

MCP 配置示例：

```json
{
  "mcpServers": {
    "drissionpage-mcp": {
      "command": "uv",
      "args": ["run", "--project", "drissionpage-mcp", "python", "drissionpage-mcp/server.py"],
      "env": {
        "DRISSIONPAGE_MCP_CAPS": "all"
      }
    }
  }
}
```

## 能力分组

默认启用全部工具。需要裁剪上下文时设置：

```bash
DRISSIONPAGE_MCP_CAPS=core,vtable,filter
```

兼容旧变量 `DRISSION_UI_CAPS`，但新服务优先读取 `DRISSIONPAGE_MCP_CAPS`。

## 目录说明

- `server.py`：FastMCP 入口和工具注册。
- `browser_session.py`：DrissionPage 浏览器单例、活动 tab/frame、元素查找。
- `vtable.py`：VTable facade，内部通过 `frame.run_js()` 获取实例、列定义、值和坐标。
- `filter_area.py`：筛选区展开、字段矩阵和下拉/日期操作。
- `modal.py` / `observe.py`：浮窗、消息、通知、点击后观察。
- `network_record.py`：网络监听与时间线导出，兼容 4.1/4.2 listener API。
- `js/`：只在工具内部注入的页面侧脚本。
