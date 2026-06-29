# SCM 系统接入配置与免登流程

> 承接原 SKILL.md §0-b。本文件包含：SCM 目标系统配置、Cookie 注入免登 3 步流程、OCR 验证码识别登录代码。
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

`browser.open()` 时 **MUST** 使用 `app: { cdp_url: "http://localhost:9222" }` 连接到用户已在 port 9222 打开的 Chrome 浏览器。**NEVER** 使用默认无头浏览器模式，否则 Cookie 注入和页面交互均无法与用户共享浏览器会话。

```javascript
browser.open({ app: { cdp_url: "http://localhost:9222", target: "诺贝科技" } });
```

前提：用户已启动 Chrome（可通过 `http://localhost:9222/json` 验证连通性）。

## 三、Cookie 注入免登流程（3 步）

### Step 1 — OCR 识别验证码 + HTTP 登录

在 `eval` (py kernel) 中执行，依赖 `ddddocr` + `httpx`（项目 pyproject.toml 已声明）。或直接调用项目中的 `set_LoginAuth.get_login_auth()`。

完整脚本见 **`scripts/scm-login-ocr.py`**，核心逻辑：

```python
import uuid, ddddocr, httpx

ocr = ddddocr.DdddOcr(show_ad=False)
ocr.set_ranges("0123456789")  # 字符集限定纯数字

client = httpx.Client(base_url="https://demo19-scm.hoolinks.com")
cookies = {"SESSION": str(uuid.uuid4())}

# 获取验证码图片
resp = client.get("/validateCode.json", params={"key": "regValidateCode"},
    headers={"Referer": "https://demo19-scm.hoolinks.com/meLogin.do?"}, cookies=cookies)
vcode = ocr.classification(resp.read())

# 登录（账号密码固定为演示环境凭证）
data = {"username": "Hooplus1ce", "userpwd": "Ac123456", "vcode": vcode}
resp = client.post("/signin.html", data=data, cookies=cookies)
auth_cookies = [{"name": k, "value": v} for k, v in resp.cookies.items()]
# 返回 4 个 Cookie: cookie_token, UCTOKEN, SESSION, SYSSOURCE
```

### Step 2 — 将 Cookie 注入 `browser` 标签页

```javascript
// 在 browser run 中执行
const cdp = await page.target().createCDPSession();
for (const c of auth_cookies) {
  await cdp.send('Network.setCookie', {
    name: c.name, value: c.value,
    domain: '.hoolinks.com', path: '/',
    secure: true, sameSite: 'Lax'
  });
}
```

### Step 3 — 导航到 SCM Admin 入口，验证登录态

```javascript
await tab.goto("https://demo19-scm.hoolinks.com/scm-static/scm-admin/scm-admin/#/",
  { waitUntil: "networkidle0" });
// 页面标题应为「诺贝科技（中山）有限公司」，侧边栏可见 19 个一级菜单
```

## 四、Session 维持（探索中途过期）

探索过程中检测到 top 层 `.ant-confirm`（提示「您还未登录或登录信息过期，请重新登录」）时：

1. **优先用缓存恢复**：调用 `scripts/cookie-cache.js` 的 `refreshIfExpired(page)`（Phase 1 已缓存 cookie）
2. **缓存失效则重新登录**：重新执行 Step 1~2 获取新 Cookie → 注入 → 重新导航
3. 底层注入逻辑用 `scripts/scm-login.js` 的 `refreshSession(page, s, u, c)`

## 五、依赖安装

```bash
cd <project_root> && uv sync  # 安装 ddddocr, httpx, openpyxl
```

**关键约束**：MUST 使用 `uv` 管理依赖，NEVER `pip install`。项目 `.venv` 已由 `uv sync` 创建。

依赖清单：
- `openpyxl >= 3.1` — Excel 生成（核心）
- `ddddocr` — 验证码 OCR（仅免登需要）
- `httpx` — HTTP 登录请求（仅免登需要）
