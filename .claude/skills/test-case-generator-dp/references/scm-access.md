# SCM 系统接入配置

## 目标系统配置

| 配置项 | 值 |
|--------|-----|
| **SCM Admin 入口** | `https://demo19-scm.hoolinks.com/scm-static/scm-admin/scm-admin/#/` |
| **SCM 登录页** | `https://demo19-scm.hoolinks.com/meLogin.do` |
| **企业名称** | 诺贝科技（中山）有限公司 |
| **技术栈** | Ant Design Pro + React SPA（侧边栏菜单 + 顶部标签导航） |
| **表格渲染** | VTable Canvas（`.vtable` 元素） |
| **模块嵌入** | iframe 内嵌旧版 SCM 页面（`scm-spo/#/` 路由） |

## 免登流程

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | `connect(port=9222)` | 连接用户已打开的 Chrome，禁止启动新实例 |
| 2 | `check_session()` | 检测是否过期 |
| 3 | 如 session 失效 | `refresh_session()` 或 `login_ocr()` — 每次重新 OCR 登录获取新 cookie |

`login_ocr()` 内部完成：获取验证码图片 → `ddddocr` 识别（纯数字）→ HTTP 登录 → Cookie 注入 → 导航到 SCM Admin。

## Session 维持

探索过程中按优先级尝试：
1. `check_session()` — 检测是否过期
2. `refresh_session()` — 直接触发 OCR 登录获取新 cookie 并刷新（不再缓存）
3. `login_ocr()` — 等同于 refresh_session，直接免登

恢复后需重新 `enter_module()` 进入目标模块，筛选条件会丢失，需向用户确认。

## 依赖

项目已通过 `pyproject.toml` 声明 `ddddocr`, `httpx`, `openpyxl`，使用 `uv sync` 安装。**禁止** `pip install`。
