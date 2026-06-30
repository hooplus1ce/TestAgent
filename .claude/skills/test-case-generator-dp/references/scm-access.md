# SCM 系统接入配置与免登流程

> 承接原 SKILL.md §1-b。本文件包含：SCM 目标系统配置、MCP 免登流程、Session 维持策略。
> 这是技能在真实 SCM 系统跑通的**入口前提**，不可省略。

## 一、目标系统配置

| 配置项 | 值 |
|--------|-----|
| **SCM Admin 入口** | `https://demo19-scm.hoolinks.com/scm-static/scm-admin/scm-admin/#/` |
| **SCM 登录页** | `https://demo19-scm.hoolinks.com/meLogin.do` |
| **企业名称** | 诺贝科技（中山）有限公司 |
| **技术栈** | Ant Design Pro + React SPA（侧边栏菜单 + 顶部标签导航） |
| **表格渲染** | VTable Canvas（`.vtable` 元素）—— 列表页使用 |
| **模块嵌入** | iframe 内嵌旧版 SCM 页面（`scm-spo/#/` 路由） |

## 二、浏览器连接铁律

**MUST** 使用 `connect(port=9222)` 连接到用户在 port 9222 打开的 Chrome 浏览器。**NEVER** 启动新浏览器、NEVER 使用无头模式。

```
connect(port=9222)
```

连接后 **MUST** 立即 `cache_session()` 缓存当前会话 Cookie，以便后续免登恢复。

```
cache_session()
```

前提：用户已启动 Chrome 并开启远程调试端口（可通过 `http://localhost:9222/json` 验证连通性）。

## 三、免登流程（3 步）

### Step 1 — OCR 识别验证码 + HTTP 登录

调用 MCP 工具 `login_ocr()`，工具内部自动完成：获取验证码图片 → OCR 识别（`ddddocr`，字符集限定纯数字）→ HTTP 登录 → CDP Cookie 注入 → 导航到 SCM Admin 入口。

```
login_ocr()
```

> **内部实现参考**：`scripts/scm-login-ocr.py`（依赖 `ddddocr` + `httpx`，项目 pyproject.toml 已声明）。核心逻辑：
>
> ```python
> import uuid, ddddocr, httpx
>
> ocr = ddddocr.DdddOcr(show_ad=False)
> ocr.set_ranges("0123456789")  # 字符集限定纯数字
>
> client = httpx.Client(base_url="https://demo19-scm.hoolinks.com")
> cookies = {"SESSION": str(uuid.uuid4())}
>
> # 获取验证码图片
> resp = client.get("/validateCode.json", params={"key": "regValidateCode"},
>     headers={"Referer": "https://demo19-scm.hoolinks.com/meLogin.do?"}, cookies=cookies)
> vcode = ocr.classification(resp.read())
>
> # 登录（账号密码固定为演示环境凭证）
> data = {"username": "Hooplus1ce", "userpwd": "Ac123456", "vcode": vcode}
> resp = client.post("/signin.html", data=data, cookies=cookies)
> auth_cookies = [{"name": k, "value": v} for k, v in resp.cookies.items()]
> # 返回 4 个 Cookie: cookie_token, UCTOKEN, SESSION, SYSSOURCE
> ```

### Step 2 — 缓存会话 Cookie

`login_ocr()` 执行完毕后，调用 `cache_session()` 将当前浏览器会话的 Cookie 持久化缓存，供后续 `refresh_session()` 快速恢复。

```
cache_session()
```

> 无需手动操作 CDP `Network.setCookie`。`cache_session()` 自动采集当前浏览器所有 Cookie 并缓存；`refresh_session()` 自动注入缓存到浏览器。

### Step 3 — 验证登录态 + 进入模块

`login_ocr()` 完成后页面应已在 SCM Admin 首页。验证方式：页面标题应为「诺贝科技（中山）有限公司」，侧边栏可见 19 个一级菜单。

进入具体业务模块使用 `enter_module(menu_text)`（通过侧边栏菜单点击导航）：

```
enter_module("生产管理_制造排产")
```

工具内部自动完成：菜单逐级展开 → 点击目标菜单项 → 等待 iframe 加载 → 展开筛选区。

## 四、Session 维持（探索中途过期）

探索过程中检测到登录过期提示时，按优先级依次尝试：

### 1. 检测 session 状态

```
check_session()
```

返回 session 是否有效。若有效则继续；若过期则进入恢复流程。

### 2. 缓存恢复

```
refresh_session()
```

用 `cache_session()` 缓存的 Cookie 重新注入浏览器，恢复登录态。恢复后重新 `enter_module("<模块名>")` 回到当前模块。

### 3. 缓存失效则重新免登

若 `refresh_session()` 失败（缓存过期或不存在），回退到完整免登流程：

```
login_ocr()
cache_session()
enter_module("<模块名>")
```

### 恢复后状态对齐

恢复后页面状态可能与过期前不一致（筛选条件、页签、勾选行等会丢失）。恢复后 MUST：

1. `enter_module("<模块名>")` 重新进入目标模块
2. 重新设置筛选条件
3. 向用户汇报「Session 已恢复，筛选条件已重置，请确认是否继续」

## 五、依赖安装

```bash
cd <project_root> && uv sync  # 安装 ddddocr, httpx, openpyxl
```

**关键约束**：MUST 使用 `uv` 管理依赖，NEVER `pip install`。项目 `.venv` 已由 `uv sync` 创建。

依赖清单：
- `openpyxl >= 3.1` — Excel 生成（核心）
- `ddddocr` — 验证码 OCR（MCP 服务器依赖，`login_ocr()` 内部使用）
- `httpx` — HTTP 登录请求（MCP 服务器依赖，`login_ocr()` 内部使用）
