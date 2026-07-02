"""会话维持：cookie 缓存（内存）、DrissionPage 内置注入、OCR 免登、过期检测。

移植自 cookie-cache.js / scm-login.js / scm-login-ocr.py。
cookie 注入使用 DrissionPage 内置的 tab.set.cookies() 方法，自动处理域名解析、
格式校验、__Host-/__Secure- 前缀等，无需手写 CDP Network.setCookie。
"""
import importlib.util
import json
import logging
import os

import browser_session
import config

logger = logging.getLogger("drission-ui")

SCM_ADMIN_URL = config.SCM_ADMIN_URL
NEEDED_COOKIES = config.NEEDED_COOKIES

# 内存缓存：{name: value}
_cookie_cache: dict = {}

# OCR 登录脚本路径（内部化：scripts/scm-login-ocr.py）
_HERE = os.path.dirname(os.path.abspath(__file__))
_OCR_PATH = os.path.normpath(os.path.join(_HERE, "scripts", "scm-login-ocr.py"))


def _load_ocr_module():
    """scm-login-ocr.py 文件名含连字符，用 importlib 动态加载。"""
    spec = importlib.util.spec_from_file_location("scm_login_ocr", _OCR_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cache_session():
    """缓存当前 tab 的 SESSION/UCTOKEN/cookie_token 到内存。返回 {ok, cached, missing}。"""
    global _cookie_cache
    tab = browser_session.get_tab()
    # 兼容 cookies() 返回 dict 或 Cookie 对象两种形态
    try:
        cookies_map = dict(tab.cookies(as_dict=True))
    except Exception as e:
        logger.debug("cookies(as_dict=True) 失败，回退到列表模式: %s", e)
        cookies = list(tab.cookies())

        def _gname(c):
            return c.get("name") if isinstance(c, dict) else getattr(c, "name", None)

        def _gval(c):
            return c.get("value") if isinstance(c, dict) else getattr(c, "value", None)

        cookies_map = {_gname(c): _gval(c) for c in cookies}
    cache = {}
    missing = []
    for name in NEEDED_COOKIES:
        v = cookies_map.get(name)
        if v:
            cache[name] = v
        else:
            missing.append(name)
    _cookie_cache.update(cache)
    return {"ok": not missing, "cached": list(cache.keys()), "missing": missing}


def get_cached_cookies():
    return dict(_cookie_cache)


def _inject_cookies(cookies: list):
    """用 DrissionPage 内置方法注入 cookies，自动处理域名解析、格式校验、前缀处理。

    注意：OCR 登录返回的 cookies 不含 domain 字段，需手动补上（DrissionPage 的
    set_tab_cookies 在没有 domain 时从页面 URL 解析，若页面不在目标域则会失败）。
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
    """用缓存 cookie 注入并刷新页面，恢复会话。"""
    if not _cookie_cache:
        return {"ok": False, "reason": "cookie 未缓存，请先 cache_session 或 login_ocr"}
    missing = [n for n in NEEDED_COOKIES if not _cookie_cache.get(n)]
    if missing:
        return {"ok": False, "reason": "缓存缺失: %s" % missing}
    cookies = [{"name": n, "value": _cookie_cache[n]} for n in NEEDED_COOKIES]
    _inject_cookies(cookies)
    tab = browser_session.get_tab()
    tab.refresh()
    tab.wait.doc_loaded(timeout=10)  # 等页面加载完成
    cache_session()  # 服务端可能轮换 cookie，重新缓存
    return {"ok": True}


def login_ocr():
    """OCR 识别验证码 + HTTP 登录 → 注入 cookie → 导航 SCM Admin。"""
    mod = _load_ocr_module()
    auth_cookies = mod.get_login_auth()  # list[{name, value}]
    _inject_cookies(auth_cookies)
    tab = browser_session.get_tab()
    nav = tab.get(SCM_ADMIN_URL)
    nav_ok = getattr(nav, "ok", None)
    if nav_ok is False:
        return {"ok": False, "reason": "导航失败: status=%s" % getattr(nav, "status", "unknown"),
                "cookies": [c["name"] for c in auth_cookies]}
    tab.wait.doc_loaded(timeout=15)  # 等页面加载完成
    cache_session()
    return {"ok": True, "cookies": [c["name"] for c in auth_cookies], "url": tab.url, "title": tab.title,
            "nav_status": getattr(nav, "status", None)}


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
