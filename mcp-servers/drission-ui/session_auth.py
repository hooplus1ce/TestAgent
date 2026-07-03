"""会话维持：OCR 免登、cookie 注入、过期检测。

login_ocr 完整流程：OCR 识别验证码 + HTTP 登录获取 cookie → tab.get 到目标域 →
clear_cache 清旧 cookie/localStorage → 注入新 cookie → tab.refresh。
每当检测到登录过期，直接触发该流程刷新，不再缓存旧 cookie（避免多 SESSION 冲突）。
"""
import json
import logging
import uuid

import browser_session
import config

logger = logging.getLogger("drission-ui")

SCM_ADMIN_URL = config.SCM_ADMIN_URL
NEEDED_COOKIES = config.NEEDED_COOKIES


def _require_creds():
    """凭据缺失时抛出明确错误，提示设置环境变量。"""
    missing = [
        k for k, v in (("HL_SCM_USERNAME", config.SCM_USERNAME),
                       ("HL_SCM_USERPWD", config.SCM_USERPWD)) if not v
    ]
    if missing:
        raise RuntimeError(
            "缺少登录凭据环境变量: %s（请通过环境变量/secret 提供，勿写入代码）"
            % ", ".join(missing)
        )


def get_login_auth():
    """OCR 识别验证码并登录，返回认证 Cookie 列表（list[{name, value}]）。

    ddddocr/httpx 延迟 import，避免模块加载时拉入重依赖（ddddocr 模型较大）。
    字符集限定纯数字，准确率高。
    """
    _require_creds()
    import ddddocr
    import httpx

    ocr = ddddocr.DdddOcr(show_ad=False)
    ocr.set_ranges("0123456789")  # 纯数字字符集

    client = httpx.Client(base_url=config.SCM_BASE_URL)
    cookies = {"SESSION": str(uuid.uuid4())}
    resp = client.get(
        "/validateCode.json",
        params={"key": "regValidateCode"},
        headers={"Referer": config.SCM_LOGIN_PAGE},
        cookies=cookies,
    )
    vcode = ocr.classification(resp.read())

    data = {"username": config.SCM_USERNAME, "userpwd": config.SCM_USERPWD, "vcode": vcode}
    resp = client.post("/signin.html", data=data, cookies=cookies)
    return [{"name": k, "value": v} for k, v in resp.cookies.items()]


def _inject_cookies(cookies: list):
    """用 DrissionPage 内置方法注入 cookies，自动处理域名解析、格式校验、前缀处理。

    注意：OCR 登录返回的 cookies 不含 domain 字段。set.cookies 不带 domain 时
    默认用当前 host（demo19-scm.hoolinks.com，host-only），这样 cookie 不会发给
    gateway.hoolinks.com 等其他子域。手动补 domain=.hoolinks.com 确保所有子域
    都能携带认证 cookie。
    """
    tab = browser_session.get_tab()
    enriched = []
    for c in cookies:
        c = dict(c)
        if "domain" not in c:
            c["domain"] = config.SCM_ACCESS_DOMAIN
        enriched.append(c)
    tab.set.cookies(enriched)


def refresh_session():
    """会话过期时触发 OCR 登录并刷新页面（login_ocr 已含清缓存 + 注入 + 刷新）。"""
    logger.info("refresh_session → 触发 login_ocr 获取新 cookie")
    return login_ocr()


def login_ocr():
    """OCR 识别验证码 + HTTP 登录 → 清缓存 → 注入 cookie → 刷新。

    顺序关键：先 tab.get 到目标域 → clear_cache 清旧 cookie/localStorage →
    注入新 cookie → tab.refresh。若先注入再 tab.get 导航，服务端会在导航
    响应里 Set-Cookie 覆盖掉刚注入的有效 SESSION，浏览器残留失效 SESSION，
    导致 gateway.hoolinks.com 收到失效 SESSION 返回 403「会话超时」。
    clear_cache 一步清掉 cookies + localStorage + sessionStorage + cache，
    无需 CDP 介入。
    """
    tab = browser_session.get_tab()
    # 1. 先到目标域：clear_cache 才能定位到该域的缓存
    tab.get(SCM_ADMIN_URL)
    # 2. 清旧 cookie + localStorage + sessionStorage + cache，杜绝 SESSION 冲突
    tab.clear_cache()
    # 3. OCR 登录获取新 cookie（list[{name, value}]）
    auth_cookies = get_login_auth()
    # 4. 注入（补 domain=.hoolinks.com，确保发给 gateway 等所有子域）
    _inject_cookies(auth_cookies)
    # 5. 刷新使 cookie 生效（refresh 而非 get，避免服务端 Set-Cookie 覆盖）
    tab.refresh()
    tab.wait.doc_loaded(timeout=15)
    return {"ok": True, "cookies": [c["name"] for c in auth_cookies],
            "url": tab.url, "title": tab.title}


def check_session():
    """检测 top 层是否出现登录过期确认弹窗。返回 {expired: bool, detail}。"""
    tab = browser_session.get_tab()
    res = tab.run_js(
        "var m=document.querySelector('.ant-modal-content');"
        "if(m&&m.offsetParent!==null&&m.querySelector('.ant-confirm-body-wrapper')){"
        "var t=m.querySelector('.ant-confirm-body')||m;"
        "return JSON.stringify({expired:true,detail:t.textContent.trim().slice(0,120)});"
        "}return JSON.stringify({expired:false});"
    )
    if isinstance(res, str):
        return json.loads(res)
    return {"expired": False}
