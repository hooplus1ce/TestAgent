"""会话维持：cookie 缓存（内存）、CDP 注入刷新、OCR 免登、过期检测。

移植自 cookie-cache.js / scm-login.js / scm-login-ocr.py。
cookie 缓存从原 page.__scmCookieCache（JS 对象属性）改为模块级内存变量。
"""
import importlib.util
import json
import os
import uuid

import browser_session

SCM_ADMIN_URL = "https://demo19-scm.hoolinks.com/scm-static/scm-admin/scm-admin/#/"
COOKIE_DOMAIN = ".demo19-scm.hoolinks.com"
SCM_ACCESS_DOMAIN = ".hoolinks.com"

NEEDED_COOKIES = ["SESSION", "UCTOKEN", "cookie_token"]

# 内存缓存：{name: value}
_cookie_cache: dict = {}

# OCR 登录脚本路径（复用技能内 scm-login-ocr.py）
_HERE = os.path.dirname(os.path.abspath(__file__))
_OCR_PATH = os.path.normpath(os.path.join(
    _HERE, "..", "..", ".claude", "skills", "test-case-generator-optimized", "scripts", "scm-login-ocr.py"
))


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
    except Exception:
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
    """通过 CDP 把 cookies 注入到 tab。"""
    tab = browser_session.get_tab()
    for c in cookies:
        tab.run_cdp("Network.setCookie",
                    name=c["name"], value=c["value"],
                    domain=SCM_ACCESS_DOMAIN, path="/",
                    secure=True, sameSite="Lax")


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
    import time
    time.sleep(3)
    cache_session()  # 服务端可能轮换 cookie，重新缓存
    return {"ok": True}


def login_ocr():
    """OCR 识别验证码 + HTTP 登录 → 注入 cookie → 导航 SCM Admin。"""
    mod = _load_ocr_module()
    auth_cookies = mod.get_login_auth()  # list[{name, value}]
    _inject_cookies(auth_cookies)
    tab = browser_session.get_tab()
    tab.get(SCM_ADMIN_URL)
    import time
    time.sleep(3)
    cache_session()
    return {"ok": True, "cookies": [c["name"] for c in auth_cookies], "url": tab.url, "title": tab.title}


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
