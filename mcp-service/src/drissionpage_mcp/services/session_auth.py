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

from ..core import config, ui_contract
from ..core.lock import _rwlock
from . import browser_session

logger = logging.getLogger("drissionpage-mcp")

NEEDED_COOKIES = config.NEEDED_COOKIES
_DEFAULT_CREDENTIAL_ENVS = ("HL_USERNAME", "HL_USERPWD")

# OCR 实例缓存，由 warmup_ocr() 在服务启动时预加载，避免首次调用延迟
_ocr_instance = None


def _is_admin_app_url(url: str) -> bool:
    try:
        current = urlparse(url or "")
        target = urlparse(config.SCM_ADMIN_URL)
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


def _require_creds(username: str, password: str, credential_envs: tuple[str, str]):
    """凭据缺失时抛出明确错误，提示设置环境变量。"""
    missing = [
        name for name, value in zip(credential_envs, (username, password)) if not value
    ]
    if missing:
        raise RuntimeError(
            "缺少登录凭据环境变量: %s（请通过环境变量/secret 提供，勿写入代码）"
            % ", ".join(missing)
        )


def warmup_ocr():
    """预加载 OCR 模型（ddddocr），让首次 get_login_auth 跳过模型加载延迟。

    在 MCP 服务启动时调用，将模型加载耗时从工具调用中剥离。
    纯 HTTP 步骤不受影响，但模型加载 ~0.5s 不会再占用工具可见执行时间。
    """
    global _ocr_instance
    if _ocr_instance is not None:
        return
    import ddddocr
    _ocr_instance = ddddocr.DdddOcr(show_ad=False)
    _ocr_instance.set_ranges("0123456789")


def get_login_auth(
    username: str = None,
    password: str = None,
    credential_envs: tuple[str, str] = _DEFAULT_CREDENTIAL_ENVS,
):
    """OCR 识别验证码并登录，返回认证 Cookie 列表（list[{name, value}]）。

    ddddocr/httpx 延迟 import，避免模块加载时拉入重依赖（ddddocr 模型较大）。
    字符集限定纯数字，准确率高。
    """
    username = config.SCM_USERNAME if username is None else username
    password = config.SCM_USERPWD if password is None else password
    if not config.SCM_BASE_URL:
        raise RuntimeError("缺少 SCM 站点根 URL，请配置 HL_BASE_URL 环境变量")
    if not config.SCM_LOGIN_PAGE:
        raise RuntimeError("缺少 SCM 登录页 URL，请配置 HL_LOGIN_PAGE 环境变量")
    _require_creds(username, password, credential_envs)
    import httpx

    global _ocr_instance
    if _ocr_instance is None:
        import ddddocr
        _ocr_instance = ddddocr.DdddOcr(show_ad=False)
        _ocr_instance.set_ranges("0123456789")
    ocr = _ocr_instance

    cookies = {"SESSION": str(uuid.uuid4())}
    with httpx.Client(base_url=config.SCM_BASE_URL, timeout=config.REFRESH_HTTP_TIMEOUT) as client:
        resp = client.get(
            "/validateCode.json",
            params={"key": "regValidateCode"},
            headers={"Referer": config.SCM_LOGIN_PAGE},
            cookies=cookies,
        )
        vcode = ocr.classification(resp.read())

        data = {"username": username, "userpwd": password, "vcode": vcode}
        resp = client.post("/signin.html", data=data, cookies=cookies)
        return [{"name": k, "value": v} for k, v in resp.cookies.items()]


def _inject_cookies(cookies: list, tab=None):
    """用 DrissionPage 内置方法注入 cookies，自动处理域名解析、格式校验、前缀处理。

    注意：OCR 登录返回的 cookies 不含 domain 字段。set.cookies 不带 domain 时
    默认用当前 host（host-only），这样 cookie 不会发给其他子域。
    手动补 domain 确保所有子域都能携带认证 cookie。
    """
    tab = tab or browser_session.get_tab()
    enriched = []
    for c in cookies:
        c = dict(c)
        if "domain" not in c and config.SCM_ACCESS_DOMAIN:
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
    if not config.SCM_ADMIN_URL:
        raise RuntimeError("缺少目标系统入口 URL，请配置 HL_URL 环境变量")
    current_url = getattr(tab, "url", "")
    if _is_admin_app_url(current_url):
        return {"skipped": True, "reason": "already_on_admin_page", "url": current_url}
    result = tab.get(
        config.SCM_ADMIN_URL,
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


def _login_ocr_on_tab(
    tab,
    username: str = None,
    password: str = None,
    credential_envs: tuple[str, str] = _DEFAULT_CREDENTIAL_ENVS,
):
    """在给定 tab 上完成 OCR 登录；供默认会话和角色隔离会话共用。

    分段加锁策略：
      1. 无锁段：OCR 识别 + HTTP 登录（纯网络 IO，不涉及浏览器）
      2. 有锁段：浏览器导航 → 清缓存 → 注入 cookie → 刷新
    """
    started = perf_counter()
    timings = {}

    # ====== 无锁段：OCR + HTTP 获取 cookie（不碰浏览器）=======
    auth_cookies = _stage(
        timings,
        "get_login_auth",
        lambda: get_login_auth(username, password, credential_envs),
    )

    # ====== 有锁段：操作浏览器 ======
    _rwlock.acquire_write()
    try:
        navigation = _stage(timings, "ensure_admin_host", lambda: _ensure_on_admin_host(tab))
        cache = _stage(timings, "clear_cache", lambda: _clear_cache(tab))
        injected = _stage(timings, "inject_cookies", lambda: _inject_cookies(auth_cookies, tab))
        reload_loaded = _stage(timings, "reload", lambda: _bounded_reload(tab))
    finally:
        _rwlock.release_write()

    timings["total"] = round(perf_counter() - started, 3)
    return {"ok": True, "cookies": [c["name"] for c in injected],
            "url": tab.url, "title": tab.title, "navigation": navigation,
            "cache": cache, "reload_loaded": reload_loaded, "timings": timings}


def login_ocr():
    """OCR 识别验证码 + HTTP 登录 → 清缓存 → 注入 cookie → 刷新默认活动 tab。"""
    return _login_ocr_on_tab(browser_session.get_tab())


def login_ocr_for_tab(
    tab,
    username: str,
    password: str,
    credential_envs: tuple[str, str],
):
    """在指定 BrowserContext 的 tab 中登录，不改变默认凭据配置。"""
    if tab is None:
        raise RuntimeError("角色上下文没有可用 tab")
    return _login_ocr_on_tab(tab, username, password, credential_envs)


def check_session():
    """检测 top 层是否出现登录过期确认弹窗。返回 {expired: bool, detail}。"""
    tab = browser_session.get_tab()
    confirm_wrapper = browser_session.ele_with_fallback(
        tab,
        ui_contract.CONFIRM_WRAPPER_CSS,
        ui_contract.CONFIRM_WRAPPER_XPATH,
        timeout=0.5
    )
    if confirm_wrapper and confirm_wrapper.states.is_displayed:
        m = confirm_wrapper.parent(2)
        if m:
            t = browser_session.ele_with_fallback(
                m,
                ui_contract.CONFIRM_BODY_CSS,
                ui_contract.CONFIRM_BODY_XPATH,
                timeout=0.1
            ) or m
            return {"expired": True, "detail": (t.text or "").strip()[:120]}
    return {"expired": False}
