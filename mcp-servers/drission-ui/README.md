# drission-ui MCP 服务器

把 [DrissionPage](https://drissionpage.cn) 浏览器自动化封装成结构化 MCP 工具，供 AI 驱动的 UI 测试技能调用。

## 设计

- **"手"与"脑"分离**：本服务器只做确定性浏览器原语（"手"）；测试编排与判断留在技能层（"脑"）。
- **接管而非启动**：连接用户已在 `9222` 端口打开的 Chrome，不启动新实例、不无头。
- **脆弱 JS 被封装**：VTable 扫描/列值/坐标计算等依赖 React fiber 与 canvas scenegraph 的逻辑，作为工具的**内部实现**（`frame.run_js(bundled JS)`），AI 调用即得结构化 JSON，不再每轮手写 `eval`。
- **坐标自动换算**：JS 产出帧内坐标 → Python 叠加 iframe 在顶层视口的偏移 → 输出可直接点击的顶层视口坐标。

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

## 工具清单（27 个）

| 类别 | 工具 |
|------|------|
| 连接/会话 | `connect` `cache_session` `refresh_session` `login_ocr` `check_session` |
| 导航/frame | `enter_module` `reset_to_initial` `get_active_frame` |
| 通用原语 | `scan_page_elements` `dom_overview` `click` `click_xy` `input` `insert_text` `hover` `screenshot` `run_js` |
| VTable | `mount_vtable` `scan_vtable_columns` `get_column_values` `get_cell_rect` `scroll_to_cell` `click_cell` |
| 弹窗/网络/调试 | `detect_modal` `listen_start` `listen_wait` `mouse_trail` |

## 模块

```
server.py          FastMCP 入口，注册全部工具，mcp.run() (stdio)
browser_session.py 单例 Chromium(9222)、活动 tab/frame 解析、坐标偏移、find()
vtable.py          VTable 工具：bundled JS 执行 + 帧内坐标→顶层视口换算
session_auth.py    cookie 缓存(内存)、CDP 注入刷新、OCR 登录、过期检测
modal.py           弹窗三级检测、鼠标轨迹注入
js/                页面内 JS 载荷（移植自旧技能脚本，改 IIFE+return）
  vtable-scanner.js  vtable-column-values.js  element-scan.js
  modal-detect.js    mouse-trail-inject.js
```

## 坐标换算（关键）

`scan_vtable_columns` / `get_cell_rect` / `click_cell` 返回的 `viewportX/viewportY` 是**顶层视口坐标**，计算方式：

```
顶层视口坐标 = frame 元素在顶层视口的偏移(rect.location) + 帧内坐标(.vtable rect + scenegraph globalAABBBounds 中心)
```

原 puppeteer 版在 iframe 上下文里查询顶层 iframe 元素恒为 null，存在坐标偏移隐患；本版由 Python 显式叠加 frame 偏移，正确性更高。

## 冒烟测试

```bash
cd mcp-servers/drission-ui
uv run python -c "import asyncio,sys;sys.path.insert(0,'.');import server;
async def m():
    ts=await server.mcp.list_tools();print(len(ts),'tools')
asyncio.run(m())"
```

## 端到端验证

见计划文件 `ancient-baking-crab.md` 的验证章节。最高优先级：VTable 链路 `mount_vtable → scan_vtable_columns → click_cell → 截图核对落点`。
