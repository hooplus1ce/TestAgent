# drission-ui MCP 服务器

把 [DrissionPage](https://drissionpage.cn) 浏览器自动化封装成结构化 MCP 工具，供 AI 驱动的 UI 测试技能调用。

## 设计

- **"手"与"脑"分离**：本服务器只做确定性浏览器原语（"手"）；测试编排与判断留在技能层（"脑"）。
- **接管而非启动**：连接用户已在 `9222` 端口打开的 Chrome，不启动新实例、不无头。
- **脆弱 JS 被封装**：VTable 扫描/列值/坐标计算等依赖 React fiber 与 canvas scenegraph 的逻辑，作为工具的**内部实现**（`frame.run_js(bundled JS)`），AI 调用即得结构化 JSON，不再每轮手写 `eval`。
- **坐标自动换算**：DOM 控件使用 DrissionPage `ele.rect.viewport_click_point` 获取顶层视口坐标；VTable canvas 坐标由内置 JS 一次换算到顶层视口 → 输出可直接点击的落点（调用侧不再叠加 iframe 偏移）。
- **Token 效率优先**：借鉴 Playwright MCP，工具支持 `filename` 参数将大输出保存到文件，避免占用 LLM 上下文；支持 `DRISSION_UI_CAPS` 环境变量按需启用工具分组。
- **MCP 元数据完整**：工具注册时自动补充 `readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint` annotations；同时暴露 caps、运行上下文和证据文件索引 resources。

## 注册

项目根 `.mcp.json`：

```json
{
  "mcpServers": {
    "drission-ui": {
      "command": "uv",
      "args": ["run", "python", "mcp-servers/drission-ui/server.py"]
    }
  }
}
```

或命令行：`claude mcp add drission-ui uv -- run python mcp-servers/drission-ui/server.py`

## 依赖

```bash
uv add DrissionPage "mcp[cli]>=1.28.1,<2" ddddocr httpx openpyxl
```

前置：Chrome 以 `--remote-debugging-port=9222` 启动。

## 工具清单（精简 public surface）

| 类别 | 工具 |
|------|------|
| 连接/会话 | `connect` `refresh_session` `check_session` |
| 导航/frame | `enter_module` `get_active_frame` `browser_tabs` |
| 页面理解 | `capture_page_model` `scan_page_elements` `scan_toolbar_actions` `scan_form_fields` `scan_modal` `scan_drawer` `scan_pagination` `dom_tree` `find_elements` `find_batch` |
| 通用交互 | `click` `click_xy` `input` `insert_text` `hover` `browser_scroll` `browser_press_key` `browser_get_element_state` |
| 表格 facade | `scan_table` `get_table_values` `get_table_data` `get_all_table_data` `click_table_cell` `hover_table_cell` `resize_table_column` `scan_action_availability_by_selection` |
| 筛选/下拉 | `scan_filter_fields` `select_date_range` `select_option` |
| 观察/弹窗 | `observe_start` `observe_wait` `explore_action` `close_modal` |
| 网络断言 | `listen_start` `listen_wait` `listen_stop` `listen_ws_start` `listen_ws_wait` `network_record_start` `network_record_stop` `network_record_export` |
| 高级/调试 | `screenshot` `run_js` `mouse_trail` `download_by_browser` `browser_console_messages` `browser_save_pdf` `set_permission` `new_context` `switch_context` `list_contexts` `browser_list_caps` |

重复工具已从 public MCP 列表移除：VTable/HTML 表格直连工具统一迁移到表格 facade；点击后观察统一使用 `observe_start → action → observe_wait`。

## 模块

```
server.py          FastMCP 入口，注册全部工具，mcp.run() (stdio)
browser_session.py 单例 Chromium(9222)、活动 tab/frame 解析、find()、list_tabs()
config.py          配置外置：环境变量读 URL/域名/端口/截图目录，保留默认值
vtable.py          VTable 工具：bundled JS 执行 + 帧内坐标→顶层视口换算
filter_area.py     筛选区操作：展开/模式切换、日期范围选择、字段矩阵扫描
session_auth.py    OCR 登录、cookie 注入刷新、过期检测（登录逻辑内嵌，无独立脚本）
modal.py           弹窗三级检测、鼠标轨迹注入、弹窗关闭
page_model.py      页面模型聚合：动作、表单、弹窗/抽屉、分页、表格数据
network_record.py  网络时间线记录：start/stop/export
js/                页面内 JS 载荷（移植自旧技能脚本，改 IIFE+return）
  vtable-scanner.js  vtable-column-values.js  element-scan.js
  modal-detect.js    mouse-trail-inject.js
```

## 配置

通过环境变量覆盖默认值（见 `config.py`）。接管模式（`connect`）相关：

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `HL_SCM_URL` | `https://demo19-scm.hoolinks.com/...` | SCM Admin URL |
| `HL_COOKIE_DOMAIN` | `.demo19-scm.hoolinks.com` | cookie 域 |
| `HL_ACCESS_DOMAIN` | `.hoolinks.com` | CDP 注入 cookie 的域 |
| `HL_TARGET_HINT` | `诺贝科技` | connect 时选 tab 的标题提示 |
| `HL_REMOTE_PORT` | `9222` | Chrome 远程调试端口 |
| `HL_SHOT_DIR` | `./resources` | MCP 证据资源默认保存目录 |
| `HL_COOKIE_SAMESITE` | `Lax` | CDP 注入 cookie 的 sameSite |

OCR 登录凭据（**只走环境变量/secret**，由 `refresh_session` 内部使用）：

| 变量 | 用途 |
|------|------|
| `HL_SCM_USERNAME` | OCR 登录用户名 |
| `HL_SCM_USERPWD` | OCR 登录密码 |

`connect` 启动/接管与跨平台相关：

| 变量 | 用途 |
|------|------|
| `HL_CHROME_PATH` | 浏览器可执行路径（覆盖自动探测）。Linux 未设时自动找 `google-chrome`/`chromium`；Windows/macOS 交给 DrissionPage 探测 |
| `HL_HEADLESS` | 无头模式（`true`/`1`）——CI/CD 无图形环境场景。启用时跳过 Linux 图形环境探测，并加 `--no-sandbox` |

仅 `make_chromium_options`（未来 launch 工具）场景生效，`connect` 接管模式**不**使用：

| 变量 | 用途 |
|------|------|
| `HL_EDGE_MODE` | 使用 Edge（`true`/`1` 启用） |
| `HL_PROXY` | 代理地址 `http://user:pass@ip:port` |
| `HL_DISABLE_PDF_PREVIEW` | 禁用 PDF 预览（`true`/`1`） |
| `HL_REMOVE_TEST_TYPE` | 移除自动化测试标记（`true`/`1`） |
| `DRISSION_UI_CAPS` | 启用的工具分组，默认全部启用；可设置逗号分隔分组（如 `core,vtable,filter`）进行裁剪，或显式设为 `all` |

## 新功能：能力分组（Caps）

默认暴露全部工具。仅在需要减少上下文占用时，才通过环境变量 `DRISSION_UI_CAPS` 按需裁剪工具分组。

```bash
# 仅启用核心工具 + 表格工具
export DRISSION_UI_CAPS=core,vtable

# 启用所有工具
export DRISSION_UI_CAPS=all
```

可用分组：

| 分组 | 包含工具 |
|------|----------|
| `core` | 连接、导航、通用交互、页面理解（默认启用） |
| `vtable` | 表格 facade 工具（默认启用） |
| `filter` | 筛选区工具（默认启用） |
| `observe` | 观察/弹窗检测（默认启用） |
| `network` | 网络监听（默认启用） |
| `storage` | 存储/浏览器上下文（默认启用） |
| `devtools` | 调试/高级功能（默认启用） |

使用 `browser_list_caps` 工具查看当前启用状态。

## MCP Resources 与 Tool Annotations

除工具外，服务还提供只读 resources，便于客户端在不调用浏览器动作的情况下获取上下文：

| URI | 用途 |
|-----|------|
| `drission-ui://caps` | 当前启用的 capability 分组和完整分组清单 |
| `drission-ui://context` | 资源目录、当前模块、端口、目标标题提示和关键依赖版本 |
| `drission-ui://resources` | `HL_SHOT_DIR` 下已保存证据文件索引 |
| `drission-ui://resources/{resource_path}` | 读取 `HL_SHOT_DIR` 下 UTF-8 文本证据文件 |

`drission-ui://resources` 索引会为每个文件返回可直接读取的 `uri`；手写子目录路径时需先 URL 编码，例如 `生产动态表/dom.yml` 对应 `drission-ui://resources/%E7%94%9F...%2Fdom.yml`。

工具会自动带 MCP `ToolAnnotations`：

- 扫描/查询类工具标为 `readOnlyHint=true`。
- `connect`、`screenshot`、`scan_table(filename=...)` 等会连接会话或写证据文件，但不直接修改业务数据，标为 `destructiveHint=false`。
- `click`、`input`、`select_option`、`click_table_cell` 等真实 UI 操作按保守策略标为 `destructiveHint=true`。

`mcp[cli]` 依赖固定为 `>=1.28.1,<2`：当前项目使用官方 Python SDK v1 的 `FastMCP` 接口；v2 仍处于预发布/迁移阶段，暂不让普通安装自动跨大版本升级。

## 新功能：输出重定向（Filename 参数）

大数据量工具新增可选 `filename` 参数，提供时结果保存到文件而非返回 JSON，避免占用 LLM 上下文：

```python
# 保存 DOM 树到文件，不返回大文本
dom_tree(filename="dom-tree.yml")

# 保存表格扫描结果
scan_table(filename="table-scan.json")

# 保存页面元素扫描
scan_page_elements(filename="elements.json")

# 保存表格数据
get_table_values(column_title="单号", filename="values.json")
get_table_data(filename="table-data.json")
get_all_table_data(filename="all-table-data.json")

# 保存页面模型快照
capture_page_model(filename="page-model.json")
```

文件默认保存到 `HL_SHOT_DIR` 配置的目录（默认项目内 `resources/`，已 gitignore）。

调用 `enter_module("模块名")` 后，简单文件名会自动归档到当前模块目录：

```python
enter_module("采购入库")
capture_page_model(filename="page-model.json")
# -> resources/采购入库/page-model.json
```

如果 `filename` 明确包含子目录，则按调用方指定的相对路径保存：

```python
capture_page_model(filename="WMS/采购入库/page-model.json")
# -> resources/WMS/采购入库/page-model.json
```

### 启动模式与跨平台

`connect` 交给 DrissionPage 的 `Chromium()` 处理：**9222 已有实例则接管，否则按 `configs/dp_configs.ini` 自启动**（`existing_only = False）。
`configs/dp_configs.ini` 是本机配置，已 gitignore；仓库只提交 `configs/dp_configs.example.ini` 模板。

- **Linux 有头（默认）**：MCP 常作为无图形会话（tty）的子进程运行，`connect` 会自动探测并补齐 `DISPLAY`/`XAUTHORITY`（GNOME Wayland 的 `.mutter-Xwaylandauth.*`），使自启动的 Chrome 能弹出窗口。真图形会话（已有 `DISPLAY`）则不动。
- **无头（`HL_HEADLESS=1`）**：跳过图形环境探测，`headless(True)` + `--no-sandbox`，适配 CI/CD 与容器。
- **Windows / macOS**：无 `DISPLAY` 概念，跳过探测，图形程序默认可弹窗。

## 坐标换算（关键）

`scan_page_elements` / `find_elements` 返回的 `cx/cy`、`viewportX/viewportY` 均为**顶层视口坐标**，来源为 DrissionPage 官方元素信息接口 `ele.rect.viewport_click_point`。这个坐标已经包含 iframe 相对顶层视口的偏移，可直接传给 `click_xy` / `hover`。

不要在 iframe 内用 `getBoundingClientRect()` 结果直接点击顶层视口；该结果只表示元素相对 iframe 当前视口的位置，缺少 iframe 自身在顶层页面里的偏移。

`scan_table` / `click_table_cell` / `hover_table_cell` / `resize_table_column` 在 VTable 后端返回的 `viewportX/viewportY` 是**顶层视口坐标**，全部在 JS 端一次算好，Python 不再叠加 iframe 偏移：

```
顶层视口坐标 = window.frameElement.getBoundingClientRect().left + .vtable rect.left + scenegraph globalAABBBounds 中心
```

JS 通过 `window.frameElement.getBoundingClientRect()` 直接拿到 iframe 在顶层视口的偏移（恒为视口坐标，不受页面滚动影响），再叠加 `.vtable` 元素偏移与 cell 的 `globalAABBBounds`，一次得到可点击坐标。原 puppeteer 版在 iframe 上下文里查询顶层 iframe 元素恒为 null，存在坐标偏移隐患；本版由 JS 端经 `frameElement` 一次算定，Python 侧无需叠加。

## 网络监听（DrissionPage 4.2）

DrissionPage 4.2 中 `listen.start()` 只接收 `urls/is_regex/targets`，`method` 与 `resourceType` 是监听器状态，必须先通过链式 API 设置：

```python
tab.listen.set_method.GET(only=True).POST()
tab.listen.set_res_type.all()
tab.listen.start(urls=["gateway"])
tab.listen.wait(count=5, timeout=10, fit_count=False)
```

因此 MCP 工具每次 `listen_start` 都会显式重置为普通 HTTP `GET,POST + ALL resourceType`；`listen_ws_start` 则显式切换为 `ALL method + WebSocket resourceType`，避免继承上一轮监听状态。

## 点击前清理

`click` / `click_xy` / `click_table_cell` 默认会在真正点击前清理上一操作残留的 Ant Design `notification` / `message`：

```python
click_xy(x=858.8, y=107.6)  # 默认 clean_overlays=True
```

这只清理短提示，不会关闭业务 modal / confirm。需要关闭业务弹窗时仍显式调用 `close_modal()`，避免误关下一步正要操作的确认框。

## 冒烟测试

```bash
cd mcp-servers/drission-ui
uv run python -c "import asyncio,sys;sys.path.insert(0,'.');import server;
async def m():
    ts=await server.mcp.list_tools();print(len(ts),'tools')
asyncio.run(m())"
```

## 单元测试

```bash
uv run pytest tests/
```

覆盖 `server.py`（public 工具面、表格 facade、synchronized 串行化）、`browser_session.py`（tab 选择 / frame 解析，mock）、`vtable.py`（JS 参数序列化）、`config.py`。浏览器交互的端到端验证见 `verify_live.py`。

## 端到端验证

见 `verify_live.py`（需 Chrome 已在 9222 端口运行）。默认跑只读链路（连接/frame/
VTable 挂载与扫描）；设置环境变量 `HL_E2E=1` 可额外跑写路径 `enter_module →
enter_module → screenshot` 及 `click_table_cell` 落点截图核对（会点击/导航，非只读）。
最高优先级：表格链路 `scan_table(kind="auto") → click_table_cell → 截图核对落点`。
