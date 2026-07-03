"""会话维持：OCR 免登、cookie 注入、过期检测。

每当检测到登录过期，直接触发 OCR 登录获取新 cookie 并注入刷新，
不再缓存旧 cookie（避免重复注入导致多 SESSION 冲突）。
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
    """（已废弃）直接返回 ok，不再缓存 cookie。

    旧版缓存逻辑会在 refresh_session 时重复注入 cookie 导致多 SESSION 冲突，
    现改为由 refresh_session → login_ocr 每次重新获取。
    """
    return {"ok": True, "cached": [], "missing": [], "deprecated": True}


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
    """会话过期时直接触发 OCR 登录 → 注入新 cookie → 刷新页面。

    不再依赖缓存 cookie，每次都重新获取，避免多 SESSION 冲突。
    """
    logger.info("refresh_session → 触发 login_ocr 获取新 cookie")
    result = login_ocr()
    if result.get("ok"):
        tab = browser_session.get_tab()
        tab.refresh()
        tab.wait.doc_loaded(timeout=10)
    return result


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
