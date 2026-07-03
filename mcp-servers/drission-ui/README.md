# drission-ui MCP 服务器

把 [DrissionPage](https://drissionpage.cn) 浏览器自动化封装成结构化 MCP 工具，供 AI 驱动的 UI 测试技能调用。

## 设计

- **"手"与"脑"分离**：本服务器只做确定性浏览器原语（"手"）；测试编排与判断留在技能层（"脑"）。
- **接管而非启动**：连接用户已在 `9222` 端口打开的 Chrome，不启动新实例、不无头。
- **脆弱 JS 被封装**：VTable 扫描/列值/坐标计算等依赖 React fiber 与 canvas scenegraph 的逻辑，作为工具的**内部实现**（`frame.run_js(bundled JS)`），AI 调用即得结构化 JSON，不再每轮手写 `eval`。
- **坐标自动换算**：注入 JS 经 `window.frameElement` 一次算到顶层视口坐标 → 输出可直接点击的落点（Python 侧不再叠加偏移）。

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
uv add DrissionPage mcp ddddocr httpx openpyxl
```

前置：Chrome 以 `--remote-debugging-port=9222` 启动。

## 工具清单（49 个）

| 类别 | 工具 |
|------|------|
| 连接/会话 | `connect` `refresh_session` `login_ocr` `check_session` |
| 导航/frame | `enter_module` `reset_to_initial` `get_active_frame` |
| 通用原语 | `scan_page_elements` `dom_overview` `click` `click_xy` `input` `insert_text` `hover` `screenshot` `run_js` `dom_tree` `find_elements` `find_static` `find_batch` `get_frame` |
| VTable | `mount_vtable` `scan_vtable_columns` `get_column_values` `get_cell_rect` `scroll_to_cell` `click_cell` `resize_column` |
| HTML 表格 | `scan_html_table` `get_html_table_data` `get_html_table_values` `click_html_table_cell` `hover_html_table_cell` |
| 筛选区 | `expand_filter_area` `scan_filter_fields` `select_date_range` |
| 弹窗/网络/调试 | `detect_modal` `close_modal` `listen_start` `listen_wait` `listen_stop` `listen_ws_start` `listen_ws_wait` `mouse_trail` `download_by_browser` `set_permission` |
| 上下文 | `new_context` `switch_context` `list_contexts` |

## 模块

```
server.py          FastMCP 入口，注册全部工具，mcp.run() (stdio)
browser_session.py 单例 Chromium(9222)、活动 tab/frame 解析、find()、list_tabs()
config.py          配置外置：环境变量读 URL/域名/端口/截图目录，保留默认值
vtable.py          VTable 工具：bundled JS 执行 + 帧内坐标→顶层视口换算
filter_area.py     筛选区操作：展开/模式切换、日期范围选择、字段矩阵扫描
session_auth.py    OCR 登录、cookie 注入刷新、过期检测（登录逻辑内嵌，无独立脚本）
modal.py           弹窗三级检测、鼠标轨迹注入、弹窗关闭
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
| `HL_SHOT_DIR` | `~/.drission-ui-shots` | screenshot 默认保存目录 |
| `HL_COOKIE_SAMESITE` | `Lax` | CDP 注入 cookie 的 sameSite |

OCR 登录凭据（**只走环境变量/secret，无明文默认值**，缺失时 `login_ocr` 报错）：

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

### 启动模式与跨平台

`connect` 交给 DrissionPage 的 `Chromium()` 处理：**9222 已有实例则接管，否则按 `dp_configs.ini` 自启动**（`existing_only = False`）。

- **Linux 有头（默认）**：MCP 常作为无图形会话（tty）的子进程运行，`connect` 会自动探测并补齐 `DISPLAY`/`XAUTHORITY`（GNOME Wayland 的 `.mutter-Xwaylandauth.*`），使自启动的 Chrome 能弹出窗口。真图形会话（已有 `DISPLAY`）则不动。
- **无头（`HL_HEADLESS=1`）**：跳过图形环境探测，`headless(True)` + `--no-sandbox`，适配 CI/CD 与容器。
- **Windows / macOS**：无 `DISPLAY` 概念，跳过探测，图形程序默认可弹窗。

## 坐标换算（关键）

`scan_vtable_columns` / `get_cell_rect` / `click_cell` / `resize_column` 返回的 `viewportX/viewportY` 是**顶层视口坐标**，全部在 JS 端一次算好，Python 不再叠加 iframe 偏移：

```
顶层视口坐标 = window.frameElement.getBoundingClientRect().left + .vtable rect.left + scenegraph globalAABBBounds 中心
```

JS 通过 `window.frameElement.getBoundingClientRect()` 直接拿到 iframe 在顶层视口的偏移（恒为视口坐标，不受页面滚动影响），再叠加 `.vtable` 元素偏移与 cell 的 `globalAABBBounds`，一次得到可点击坐标。原 puppeteer 版在 iframe 上下文里查询顶层 iframe 元素恒为 null，存在坐标偏移隐患；本版由 JS 端经 `frameElement` 一次算定，Python 侧无需叠加。

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

覆盖 `server.py`（工具注册数量、synchronized 串行化）、`browser_session.py`（tab 选择 / frame 解析，mock）、`vtable.py`（JS 参数序列化）、`config.py`。浏览器交互的端到端验证见 `verify_live.py`。

## 端到端验证

见 `verify_live.py`（需 Chrome 已在 9222 端口运行）。默认跑只读链路（连接/frame/
VTable 挂载与扫描）；设置环境变量 `HL_E2E=1` 可额外跑写路径 `enter_module →
reset_to_initial → screenshot` 及 `click_cell` 落点截图核对（会点击/导航，非只读）。
最高优先级：VTable 链路 `mount_vtable → scan_vtable_columns → click_cell → 截图核对落点`。
