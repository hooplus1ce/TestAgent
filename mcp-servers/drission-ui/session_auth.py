"""会话维持：OCR 免登、cookie 注入、过期检测。

login_ocr 完整流程：OCR 识别验证码 + HTTP 登录获取 cookie → tab.get 到目标域 →
clear_cache 清旧 cookie/localStorage → 注入新 cookie → tab.refresh。
每当检测到登录过期，直接触发该流程刷新，不再缓存旧 cookie（避免多 SESSION 冲突）。
"""
import json
import logging
import uuid
from time import perf_counter
from urllib.parse import urlparse

import browser_session
import config

logger = logging.getLogger("drission-ui")

SCM_ADMIN_URL = config.SCM_ADMIN_URL
NEEDED_COOKIES = config.NEEDED_COOKIES


def _is_admin_app_url(url: str) -> bool:
    try:
        current = urlparse(url or "")
        target = urlparse(SCM_ADMIN_URL)
    except Exception:
        return False
    if (current.hostname or "") != (target.hostname or ""):
        return False
    current_path = (current.path or "/").rstrip("/") + "/"
    target_path = (target.path or "/").rstrip("/") + "/"
    return current_path.startswith(target_path)


def _stage(timings: dict, name: str, func):
    started = perf_counter()
    try:
        return func()
    finally:
        timings[name] = round(perf_counter() - started, 3)


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
    ocr.set_ranges("0123456789")

    cookies = {"SESSION": str(uuid.uuid4())}
    with httpx.Client(base_url=config.SCM_BASE_URL, timeout=config.REFRESH_HTTP_TIMEOUT) as client:
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


def _inject_cookies(cookies: list, tab=None):
    """用 DrissionPage 内置方法注入 cookies，自动处理域名解析、格式校验、前缀处理。

    注意：OCR 登录返回的 cookies 不含 domain 字段。set.cookies 不带 domain 时
    默认用当前 host（demo19-scm.hoolinks.com，host-only），这样 cookie 不会发给
    gateway.hoolinks.com 等其他子域。手动补 domain=.hoolinks.com 确保所有子域
    都能携带认证 cookie。
    """
    tab = tab or browser_session.get_tab()
    enriched = []
    for c in cookies:
        c = dict(c)
        if "domain" not in c:
            c["domain"] = config.SCM_ACCESS_DOMAIN
        enriched.append(c)
    tab.set.cookies(enriched)
    return enriched


def refresh_session():
    """会话过期时触发 OCR 登录并刷新页面（login_ocr 已含清缓存 + 注入 + 刷新）。"""
    logger.info("refresh_session → 触发 login_ocr 获取新 cookie")
    return login_ocr()


def _ensure_on_admin_host(tab):
    """进入 SCM Admin 应用页；只有已经在应用页时才跳过导航。"""
    current_url = getattr(tab, "url", "")
    if _is_admin_app_url(current_url):
        return {"skipped": True, "reason": "already_on_admin_page", "url": current_url}

    result = tab.get(
        SCM_ADMIN_URL,
        retry=0,
        interval=0,
        timeout=config.REFRESH_NAV_TIMEOUT,
        raise_err=False,
    )
    ok = bool(getattr(result, "ok", result))
    if not ok:
        logger.warning("refresh_session 导航到 SCM 域未在 %.1fs 内完成，继续尝试清缓存",
                       config.REFRESH_NAV_TIMEOUT)
        try:
            tab.stop_loading()
        except Exception:
            pass
    return {"skipped": False, "ok": ok, "url": getattr(tab, "url", "")}


def _clear_cache(tab):
    """清理旧缓存/cookie 状态，再注入 OCR 登录得到的新 cookie。"""
    try:
        tab.clear_cache()
        return {"ok": True}
    except Exception as e:
        logger.warning("refresh_session clear_cache 失败，继续注入新 cookie: %s", e)
        return {"ok": False, "reason": str(e)}


def _bounded_reload(tab):
    """用 DrissionPage refresh() 刷新页面，再用短超时等待加载完成。"""
    tab.refresh(ignore_cache=False)
    loaded = bool(tab.wait.doc_loaded(timeout=config.REFRESH_LOAD_TIMEOUT, raise_err=False))
    if not loaded:
        logger.warning("refresh_session reload 后页面未在 %.1fs 内完成加载，执行 stop_loading",
                       config.REFRESH_LOAD_TIMEOUT)
        try:
            tab.stop_loading()
        except Exception:
            pass
    return loaded


def login_ocr():
    """OCR 识别验证码 + HTTP 登录 → 进入目标页 → 注入 cookie → 刷新。"""
    started = perf_counter()
    timings = {}
    tab = browser_session.get_tab()
    auth_cookies = _stage(timings, "get_login_auth", get_login_auth)
    navigation = _stage(timings, "ensure_admin_host", lambda: _ensure_on_admin_host(tab))
    cache = _stage(timings, "clear_cache", lambda: _clear_cache(tab))
    injected = _stage(timings, "inject_cookies", lambda: _inject_cookies(auth_cookies, tab))
    reload_loaded = _stage(timings, "reload", lambda: _bounded_reload(tab))
    timings["total"] = round(perf_counter() - started, 3)
    return {"ok": True, "cookies": [c["name"] for c in injected],
            "url": tab.url, "title": tab.title, "navigation": navigation,
            "cache": cache, "reload_loaded": reload_loaded, "timings": timings}


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
