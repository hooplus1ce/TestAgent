# SCM 系统接入配置

## 目标系统配置

| 配置项 | 值 |
|--------|-----|
| **SCM Admin 入口** | `https://demo19-scm.hoolinks.com/scm-static/scm-admin/scm-admin/#/` |
| **SCM 登录页** | `https://demo19-scm.hoolinks.com/meLogin.do` |
| **默认待测页面** | 未指定 URL 时使用 SCM Admin 入口；只给模块名时先打开入口再 `enter_module(...)` |
| **企业名称** | 诺贝科技（中山）有限公司 |
| **技术栈** | Ant Design Pro + React SPA（侧边栏菜单 + 顶部标签导航） |
| **表格渲染** | VTable Canvas（`.vtable` 元素） |
| **模块嵌入** | iframe 内嵌旧版 SCM 页面（`scm-spo/#/` 路由） |

## Browser Ready Gate

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | `connect(port=9222, target_hint=TEST_PAGE_URL)` | 连接用户已打开的 Chrome，禁止启动新实例 |
| 2 | `browser_tabs(action="select", index=...)` 或 `browser_tabs(action="new", url=TEST_PAGE_URL)` | 打开或切换到待测页面 |
| 3 | `check_session()` | 检测当前登录是否过期 |
| 4 | 如 session 失效 | `refresh_session()` — 每次重新 OCR 登录获取新 cookie |
| 5 | 重新回到待测页 | `refresh_session()` 可能导航回 SCM Admin；按 URL 重新打开，或按模块名重新 `enter_module(...)` |
| 6 | `check_session()` | 最终确认登录有效；仍失效则停止 |
| 7 | `get_active_frame()` | 获取 `[role=tabpanel][aria-hidden=false] iframe`；失败时先 `enter_module(...)` 再重试 |

`refresh_session()` 内部完成：获取验证码图片 → `ddddocr` 识别（纯数字）→ HTTP 登录 → Cookie 注入 → 导航到 SCM Admin。

## Session 维持

探索过程中按优先级尝试：
1. `check_session()` — 检测是否过期
2. `refresh_session()` — 直接触发 OCR 登录获取新 cookie 并刷新（不再缓存）
3. 重新打开待测页或重新 `enter_module(...)`
4. 再次 `check_session()` 后调用 `get_active_frame()`

恢复后需重新 `enter_module()` 进入目标模块，筛选条件会丢失，需向用户确认。

## 依赖

项目已通过 `pyproject.toml` 声明 `ddddocr`, `httpx`, `openpyxl`，使用 `uv sync` 安装。**禁止** `pip install`。
