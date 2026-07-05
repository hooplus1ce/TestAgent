# drission-ui MCP + test-case-generator-dp 使用手册

> 基于 DrissionPage 的 AI 驱动 UI 自动化测试工作流。
> 把浏览器自动化封装成结构化 MCP 工具（"手"），把测试编排与判断保留在技能里（"脑"）。

---

## 目录

- [一、系统概览](#一系统概览)
- [二、环境准备与安装](#二环境准备与安装)
- [三、MCP 服务器注册与启动](#三mcp-服务器注册与启动)
- [四、MCP 工具完整参考](#四mcp-工具完整参考)
  - [4.1 连接与会话](#41-连接与会话)
  - [4.2 导航与 frame](#42-导航与-frame)
  - [4.3 通用 DOM 原语](#43-通用-dom-原语)
  - [4.4 VTable（canvas 表格）](#44-vtablecanvas-表格)
  - [4.5 弹窗 / 网络 / 调试](#45-弹窗--网络--调试)
- [五、技能使用方法（4 阶段工作流）](#五技能使用方法4-阶段工作流)
- [六、典型场景示例](#六典型场景示例)
- [七、定位符语法（DrissionPage）](#七定位符语法drissionpage)
- [八、故障排查](#八故障排查)
- [九、与旧技能的关系](#九与旧技能的关系)

---

## 一、系统概览

### 架构

```
AI(模型) ──调用 MCP 工具──► drission-ui MCP 服务器(常驻 Python 进程, stdio)
                                  │  持有单例 Chromium(9222) + 活动 tab/frame
                                  ├─ 通用原语: connect/scan/click/input/screenshot
                                  ├─ VTable: 内部 frame.run_js 注入 bundled JS → 返回 JSON
                                  ├─ 会话: cookie 缓存/OCR 登录/刷新
                                  ├─ 弹窗检测 / 网络监听 / 鼠标轨迹
                                  └─ DrissionPage ──CDP──► Chrome :9222
技能(test-case-generator-dp) = 4 阶段编排 + 质量门 + Excel 导出（调用上面的工具）
```

### 设计原则

| 原则 | 说明 |
|------|------|
| **"手"与"脑"分离** | MCP 服务器只做确定性浏览器原语；测试判断留在技能层，不硬编码 |
| **接管而非启动** | 连接用户已在 9222 端口打开的 Chrome，不启动新实例、不无头 |
| **脆弱 JS 被封装** | VTable 扫描/列值/坐标等依赖 React fiber 与 canvas scenegraph 的逻辑，作为工具内部实现，AI 调用即得结构化 JSON |
| **坐标自动换算** | 注入 JS 经 `window.frameElement` 一次算到顶层视口坐标 → 输出可直接点击的落点（Python 侧不再叠加偏移） |

### 文件布局

```
mcp-servers/drission-ui/
  server.py            # FastMCP 入口，注册 27 个工具，mcp.run() (stdio)
  browser_session.py   # 单例 Chromium(9222)、活动 frame 解析、坐标偏移
  vtable.py            # VTable 工具：bundled JS 执行 + 帧内→顶层坐标换算
  session_auth.py      # cookie 缓存、CDP 注入刷新、OCR 登录、过期检测
  modal.py             # 弹窗三级检测、鼠标轨迹注入
  js/                  # 页面内 JS 载荷（移植自旧脚本）
    vtable-scanner.js  vtable-column-values.js  element-scan.js
    modal-detect.js    mouse-trail-inject.js
  README.md            # 服务器说明
  verify_live.py       # 端到端验证脚本

.claude/skills/test-case-generator-dp/
  SKILL.md             # 4 阶段工作流骨架
  references/           # 细节文档（field-spec / filter-validation / modal-types / vtable-interaction / scm-access / quality-rubric）
  scripts/             # excel-export-template.py（Excel 导出）、load-exploration-state.py（断点续传）
  assets/              # exploration-state.schema.json

.mcp.json              # 项目级 MCP 注册
```

---

## 二、环境准备与安装

### 1. 系统要求

- Python ≥ 3.14（项目 `.venv` 已由 uv 管理）
- uv（包管理器）
- Chrome 浏览器

### 2. 安装依赖

```bash
cd D:\Developer\Hoolinks\TRAE_Work_Space\TestAgent
uv add DrissionPage mcp ddddocr httpx openpyxl
```

依赖用途：

| 依赖 | 用途 |
|------|------|
| `DrissionPage` | 浏览器自动化（4.1.1.4 已验证） |
| `mcp` | MCP 服务器 SDK（FastMCP，1.28.1 已验证） |
| `ddddocr` | 验证码 OCR（免登用） |
| `httpx` | HTTP 登录请求（免登用） |
| `openpyxl` | Excel 测试用例导出 |

### 3. 启动 Chrome（接管模式）

**铁律**：MCP 服务器只接管、不启动。Chrome 必须先用 `--remote-debugging-port=9222` 启动。

**方式一：快捷方式修改**
在 Chrome 快捷方式的"目标"末尾追加：
```
"D:\chrome.exe" --remote-debugging-port=9222
```

**方式二：bat 文件**
创建 `start-chrome-9222.bat`：
```bat
"D:\chrome.exe" --remote-debugging-port=9222
```

**验证端口可用**：
```bash
# Windows Git Bash
export NO_PROXY=127.0.0.1
curl -s http://127.0.0.1:9222/json/version
# 应返回 Chrome 版本 JSON
```

### 4. 登录目标系统

启动 Chrome 后，手动登录 SCM 演示系统，确保浏览器停在已登录状态（标题应显示「诺贝科技（中山）有限公司」）。若无法手动登录，可由 AI 调用 `refresh_session()` 自动 OCR 免登。

---

## 三、MCP 服务器注册与启动

### 1. 注册（项目级）

项目根目录 `.mcp.json` 内容：
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

或命令行注册（等效）：
```bash
claude mcp add drission-ui uv -- run python mcp-servers/drission-ui/server.py
```

### 2. 验证注册

```bash
claude mcp list
# 应输出 drission-ui
```

### 3. 重启会话加载工具

`.mcp.json` 变更后，**必须重启 Claude Code 会话**才会加载 MCP 工具。首次启动会提示批准 `drission-ui` 服务器，批准后即可在对话中调用其工具。

### 4. 冒烟测试（不依赖会话）

直接验证服务器能起、工具注册完整：
```bash
cd mcp-servers/drission-ui
uv run python -c "
import asyncio, sys; sys.path.insert(0, '.')
import server
async def m():
    ts = await server.mcp.list_tools()
    print(len(ts), 'tools')
asyncio.run(m())
"
# 期望输出: 27 tools
```

### 5. 端到端验证（连真实浏览器，只读）

```bash
cd mcp-servers/drission-ui
PYTHONIOENCODING=utf-8 uv run python verify_live.py
```

会依次验证：连接 → 活动 frame → frame 偏移 → 控件扫描 → 会话检测 → 弹窗检测 → VTable 挂载与扫描（最高风险链路）。

---

## 四、MCP 工具完整参考

所有工具返回 JSON-able dict。`locator` 为 DrissionPage 定位符（见[第七节](#七定位符语法drissionpage)）。

### 4.1 连接与会话

#### `connect(port=9222, target_hint="诺贝科技")`
接管 port 上已运行的 Chrome，选中目标 tab。
- **前置**：Chrome 须以 `--remote-debugging-port=<port>` 启动并已登录
- **返回**：`{ok, url, title, tabs:[{url, title}]}`
- **示例**：`connect()` → 选中 SCM 后台

#### `refresh_session()`
会话过期时触发 OCR 登录获取新 cookie → 注入 → 刷新页面，恢复过期会话。每次重新获取，不依赖缓存。
- **返回**：`{ok}` 或 `{ok: false, reason}`

#### `check_session()`
检测 top 层是否出现"登录过期"系统确认弹窗。
- **返回**：`{expired: bool, detail}`

### 4.2 导航与 frame

#### `enter_module(menu_text)`
点击左侧菜单进入模块，并轮询等待业务 iframe 加载完成。
- **参数**：`menu_text` 菜单文字（支持部分匹配，如 `"制造排产"`）
- **返回**：`{ok, entered, iframe_ready}`

#### `get_active_frame()`
获取当前可见 tabpanel 内的业务 iframe。
- **返回**：`{ok, url}` 或 `{ok: false, reason}`

### 4.3 通用 DOM 原语

#### `scan_page_elements(include_iframe=True)`
扫描页面所有可见交互控件（button/a/input/role=*/canvas），递归穿透同源 iframe，按 frame 分组返回，含中心坐标。**进入模块后第一件事**。
- **返回**：`{url, title, total, elements:[{frame, tag, type, text, cls, disabled, cx, cy}]}`
- **示例输出**：`{total: 65, groups: ["ReactIframe01010001", "(main)"]}`

#### `click(locator, in_frame=True, by_js=False)`
点击元素。
- **参数**：
  - `locator` DrissionPage 定位符
  - `in_frame` 优先在活动 iframe 内查找
  - `by_js=True` 用 JS 点击（绕过遮挡）
- **返回**：`{ok, locator}` 或 `{ok: false, reason}`
- **示例**：`click("text:新 增")`、`click("#submitBtn")`、`click(".ant-btn-primary")`

#### `click_xy(x, y, hover_first=True, duration=0.3)`
按顶层视口坐标点击（用于 canvas）。
- **参数**：`hover_first` 先移动到目标（hover）再点击——**VTable 排序图标需要 hover 才出现**
- **返回**：`{ok, x, y}`
- **示例**：`click_xy(201.5, 247.5, hover_first=true)`

#### `input(locator, text, in_frame=True, clear=True)`
向输入框填入文本。`clear=True` 先清空。
- **示例**：`input("text:开始日期", "2026-06-01")`

#### `insert_text(text)`
向当前焦点元素插入文本（动作链）。

#### `hover(locator=None, x=None, y=None, in_frame=True, duration=0.3)`
鼠标悬停。给 `locator` 悬停元素，或给 `x/y` 悬停坐标。

#### `screenshot(path=None, locator=None, in_frame=True)`
截图。`locator` 给定则截元素，否则截全页。`path` 为空则存当前目录 `shot_<时间戳>.png`。
- **返回**：`{ok, path}`

#### `run_js(script, in_frame=True)`
**逃生舱**：执行任意 JS。`in_frame=True` 在活动 iframe 内执行。`script` 内可用 `return` 返回值（建议 `return JSON.stringify(...)`）。
- **示例**：`run_js("return document.querySelector('.vtable-filter-menu')?.outerHTML")`

### 4.4 表格 facade（VTable / HTML Table）

> VTable 是 canvas 渲染，无真实 DOM 节点；HTML Table 是 Ant Design 原生 DOM 表格。public MCP 已统一收敛为表格 facade，默认 `kind="auto"` 自动选择后端。

#### `scan_table(kind="auto", max_col=50, table_index=0)`
扫描表格列定义和元数据。VTable 后端返回列、bodyBehavior、表头图标坐标；HTML 后端返回 tables、columns、rowCount 等。
- **返回**：`{ok, kind, columns?... tables?...}`
- **示例**：`scan_table(kind="auto")`

#### `get_table_values(column_title, kind="auto", raw=False, table_index=0)`
按列标题读取列值。**筛选断言用**。
- **参数**：`raw=False` 视觉文本（与界面一致）；`raw=True` 在 VTable 后端返回原始字段值。
- **返回**：`{ok, kind, title/column, values:[...]}`

#### `get_table_data(kind="auto", table_index=0)`
读取表格完整数据。HTML Table 支持完整表头和行数据；VTable 暂建议用 `get_table_values` 按列验证。

#### `click_table_cell(row, col=None, column_title=None, kind="auto", icon_name=None, double_click=False)`
统一点击表格单元格或 VTable 表头图标。
- VTable 可传 `col` 或 `column_title`；工具内部自动滚动、换算顶层视口坐标。
- HTML Table 使用 `column_title` 定位列，优先点击单元格内链接/按钮。
- **示例**：`click_table_cell(row=0, col=0, icon_name="sort", kind="vtable")`

#### `hover_table_cell(row, col=None, column_title=None, kind="auto")`
统一悬停表格单元格。

#### `resize_table_column(width, col=None, column_title=None, kind="vtable")`
调整 VTable 列宽；HTML Table 暂不支持时返回结构化失败。

### 4.5 弹窗 / 网络 / 调试

#### `observe_start(signals=None, listen_targets=None)` / `observe_wait(timeout=8)`
两段式观察器。点击前调用 `observe_start` 安装 DOM/URL/Tab/网络监听，点击后调用 `observe_wait` 读取首个信号并清理。
- **默认 signals**：`["modal", "notification", "message", "tab", "url"]`
- **需要抓接口**：加入 `"network"` 并传 `listen_targets="gateway"`
- **返回**：命中 `{type, scope?, payload?, elapsedMs...}`；未命中 `{type:"none"}`

#### `listen_start(targets, method=None)`
启动网络监听。
- **参数**：`targets` URL 特征；`method` 可选 `"POST"`/`"GET"`
- **示例**：`listen_start("/api/list", method="POST")`

#### `listen_wait(count=1, timeout=10)`
等待监听的数据包。**body 自动解析 JSON**。
- **返回**：`{ok, url, method, status, body}` 或 `{ok: false, reason}`（timeout）
- **示例用法**：先 `listen_start` → 触发查询 → `listen_wait` 拿 `response.body` 作为可断言预期

#### `mouse_trail(on=True)`
开启/关闭鼠标轨迹可视化（红色圆点跟踪 mousemove/click）。**调试 canvas 点击落点用**。
- **返回**：`{ok, on}`

---

## 五、技能使用方法（4 阶段工作流）

技能 `test-case-generator-dp` 已注册，直接对 AI 说即可触发。

### 触发方式

```
生成 <模块名> 的测试用例
```

例如：`生成 生产管理_制造排产 的测试用例`

### 4 阶段流程

#### Phase 1 — 需求采集
1. 确认领域（MOM/ERP/WMS，显式询问）
2. `connect()` 接管 9222 浏览器
3. `enter_module("<模块名>")` → `get_active_frame()` 确认 iframe 就绪
4. `scan_page_elements()` / `dom_tree()` 做页面结构分析
5. `scan_table(kind="auto")` 穷尽表格列定义
6. 穷尽筛选字段（用 `get_table_values` 验证列值）
7. DFS 穷尽按钮 + 弹窗探索（`observe_start` → `click` → `observe_wait`）
8. 探测表格单元格交互（`click_table_cell` + `get_table_values`）

**推进条件**：领域和模块确认 + 主链路描述清楚 + ≥2 条业务规则 + 测试类型确认。

#### Phase 2 — 用例生成
- 每个用例 18 个字段（见技能 `references/field-spec.md`）
- 预期结果必须可验证——**优先用 `listen_wait` 的 `response.body` 作可断言预期**
- 优先级：阻塞 P0 / 核心 P1 / 常见 P2 / 边角 P3
- DFS 衍生用例导出前去重

#### Phase 3 — 分区域迭代探索（对话驱动）
不一次性生成全部用例，按用户指示逐步覆盖各区域：
```
用户 → 指令（如「测一下批量排产按钮」）
  AI  → 执行（observe_start / click 或 click_table_cell / observe_wait / scan / listen_wait）→ 汇报 + 询问下一步
用户 → 继续或调整方向
```

**区域分解**：页签切换 / 筛选区 / 工具栏按钮 / VTable 表头 / VTable 行选择 / VTable 链接列 / 接口验证 / 页面级。

**每次 `click`/`click_table_cell`/`click_xy` 前后 MUST 执行 `observe_start()` → action → `observe_wait()`**。

**断点续传**：每完成一个区域调用 `load-exploration-state.py` 的 `save_state()`，重启技能时先 `load_state()` 续传。

#### Phase 4 — Excel 导出
1. 按 `scripts/excel-export-template.py` 模板组装数据
2. 按视觉布局排序：筛选区(F) → 页签/按钮(I) → VTable 交互(I) → 页面级(P)
3. 在 `eval` kernel 中执行
4. 告知用户文件路径

---

## 六、典型场景示例

### 场景 1：连接并扫描某模块的控件

```
connect()                                    # 接管 9222
enter_module("制造排产")                     # 进入模块
scan_page_elements()                         # 列出所有控件、页签和按钮
```

### 场景 2：扫描 VTable 列定义并点击排序图标

```
scan_table(kind="auto")                    # 拿到列+图标/表格信息
observe_start(signals=["modal","message","notification","url","tab"])
click_table_cell(row=0, col=0, icon_name="sort", kind="vtable")
observe_wait(timeout=8)                      # 检测弹窗/toast/跳转
```

### 场景 3：筛选后断言列值

```
input("text:制令单号", "MO202606270041")     # 填筛选值
click("text:查询")                           # 点查询
get_table_values("制令单号", kind="auto")   # 取该列所有值
# AI 据此判断筛选是否生效（所有值应都含该单号）
```

### 场景 4：接口断言（可验证的预期结果）

```
listen_start("/api/manufactureOrder/list", method="POST")  # 启动监听
click("text:查询")                                          # 触发请求
listen_wait(timeout=10)                                    # 拿 response.body
# 返回 {ok, url, status, body} → body 可作为预期结果的硬证据
```

### 场景 5：会话过期自愈

```
check_session()              # {expired: true, detail: "您还未登录..."}
refresh_session()            # OCR 重新登录并注入新 cookie
```

### 场景 6：用例间状态隔离

```
enter_module("制造排产")     # 重进模块 → 等 iframe 就绪
# 确保下一个用例从干净初始态开始
```

### 场景 7：调试 canvas 点击落点

```
mouse_trail(on=true)         # 开启红点轨迹
click_table_cell(row=0, col=0, icon_name="filter", kind="vtable")   # 点击
screenshot()                 # 截图核对红点是否落在图标上
mouse_trail(on=false)        # 关闭
```

---

## 七、定位符语法（DrissionPage）

`click` / `input` / `hover` / `screenshot` 的 `locator` 参数支持：

| 写法 | 含义 | 示例 |
|------|------|------|
| `#id` | 按 id | `#submitBtn` |
| `.class` | 按类名 | `.ant-btn-primary` |
| `css:选择器` | CSS 选择器 | `css:div.ant-modal button` |
| `text:文本` | 按文本（精确/部分） | `text:新 增` |
| `@属性=值` | 按属性 | `@name=vcode` |
| `tag:标签` | 按标签名 | `tag:input` |
| `xpath:表达式` | XPath | `xpath://div[@role='dialog']` |

多个定位符可用 `>>` 组合（父子关系）：
```
css:.ant-modal >> tag:button       # 弹窗内的按钮
```

---

## 八、故障排查

| 现象 | 原因与处理 |
|------|------|
| 工具调用报"未找到活动业务 iframe" | 浏览器已关闭模块 tab 或退到登录页。重进模块：`enter_module("模块名")` 或 `enter_module` |
| `connect` 失败 | 检查 Chrome 是否以 9222 启动：`curl http://127.0.0.1:9222/json/version`；设 `NO_PROXY=127.0.0.1` |
| `scan_table` 失败 | `get_active_frame()` 确认 iframe；当前页可能非 VTable 页面。按 `references/vtable-interaction.md` 降级：截图 + 仅生成展示类用例 |
| `get_table_values` 返回 null | 列标题不匹配——当前模块可能无此列。先 `scan_table` 看真实列标题 |
| `observe_start/observe_wait` 返回 none 但预期有弹窗 | VTable 筛选弹窗是 `.vtable-filter-menu`（非 `.ant-dropdown`）。用 `run_js("return document.querySelector('.vtable-filter-menu')?.outerHTML")` 补充探测 |
| 会话过期 | `check_session` → `refresh_session` → 失败则 `refresh_session` |
| MCP 工具在会话中不可见 | `.mcp.json` 变更后需**重启 Claude Code 会话**才加载 |
| openpyxl 未安装 | `uv add openpyxl` |
| Windows 控制台中文乱码 | 跑脚本时设 `PYTHONIOENCODING=utf-8` |
| MCP 进程连接跨调用失效 | 单例会自愈重连（`get_tab()` 探活失败自动 `connect`） |

### 验证 MCP 服务器健康

```bash
# 1. 服务器能起
cd mcp-servers/drission-ui
uv run python -c "import asyncio,sys;sys.path.insert(0,'.');import server;
async def m():
    print(len(await server.mcp.list_tools()),'tools')
asyncio.run(m())"

# 2. 浏览器端口活
export NO_PROXY=127.0.0.1
curl -s http://127.0.0.1:9222/json/version | head -c 200

# 3. 端到端只读链路
PYTHONIOENCODING=utf-8 uv run python verify_live.py
```

---

## 九、与旧技能的关系

| 项 | 旧技能 `test-case-generator-optimized` | 新技能 `test-case-generator-dp` |
|----|------|------|
| 浏览器层 | `browser` 工具 + 每轮手写 JS eval | `drission-ui` MCP 结构化工具 |
| VTable JS | 每轮拼装 eval | 封装在工具内部，AI 只消费 JSON |
| 坐标换算 | 手动 / iframe 内查顶层 iframe（隐患） | JS 端经 `window.frameElement` 一次算定视口坐标 |
| canvas hover | puppeteer 不稳，已转向 DrissionPage | `actions.move_to(hover).click()` 原生支持 |
| 接口断言 | 无 | `listen_start/wait` 拿 body |
| 并存策略 | 保留可回退 | 新增并行 |

**两者并存**：可随时对比验证、回退。确认新技能稳定后，可考虑弃用旧技能。

---

## 附录：工具速查表（精简 public surface）

| 类别 | 工具 |
|------|------|
| 连接/会话 | `connect` `refresh_session` `check_session` |
| 导航/frame | `enter_module` `get_active_frame` |
| 页面理解 | `scan_page_elements` `dom_tree` `find_elements` `find_batch` |
| 通用交互 | `click` `click_xy` `input` `insert_text` `hover` |
| 表格 facade | `scan_table` `get_table_values` `get_table_data` `click_table_cell` `hover_table_cell` `resize_table_column` |
| 筛选区 | `scan_filter_fields` `select_date_range` |
| 观察/弹窗 | `observe_start` `observe_wait` `close_modal` |
| 网络断言 | `listen_start` `listen_wait` `listen_stop` |
| 高级/调试 | `screenshot` `run_js` `mouse_trail` `download_by_browser` `listen_ws_start` `listen_ws_wait` `set_permission` |
| 上下文 | `new_context` `switch_context` `list_contexts` |

---

*本手册基于 DrissionPage 4.2 + mcp 1.28.1，已对 9222 真实浏览器验证核心链路（连接/活动frame/坐标换算/VTable 挂载与扫描全绿）。*
