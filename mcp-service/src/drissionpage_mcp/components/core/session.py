"""Session / connection tools loaded by FastMCP FileSystemProvider.

Thin MCP adapters; business logic stays in services + core.config.
"""

from fastmcp.tools import tool

from drissionpage_mcp.core import config
from drissionpage_mcp.core.lock import _rwlock
from drissionpage_mcp.services import browser_session, session_auth


@tool(name="connect")
def connect(
    port: int = config.DEFAULT_PORT,
    target_hint: str = config.DEFAULT_TARGET_HINT,
) -> dict:
    """连接 Chrome。先检查 port 上是否已有 Chrome 实例，有则接管；无则根据
    dp_configs.ini 配置自动启动新实例。返回当前 url/title 与所有 tab 列表。"""
    _rwlock.acquire_write()
    try:
        tab = browser_session.connect(port, target_hint)
        return {
            "ok": True,
            "url": tab.url,
            "title": tab.title,
            "tabs": browser_session.list_tabs(),
        }
    finally:
        _rwlock.release_write()


@tool(name="refresh_session")
def refresh_session() -> dict:
    """会话过期时直接触发 OCR 登录 → 注入新 cookie → 刷新页面。不再依赖缓存。"""
    return session_auth.refresh_session()


@tool(name="set_target_env")
def set_target_env(host_prefix: str) -> dict:
    """运行时切换目标环境（无需重启 MCP 服务）。

    只需提供 host 前缀，例如 'demo15-scm'，系统自动推导 5 个关联配置
    (HL_URL / HL_BASE_URL / HL_LOGIN_PAGE / HL_COOKIE_DOMAIN / HL_ACCESS_DOMAIN)。
    调用后即刻生效，后续 connect / refresh_session 使用新环境。
    """
    config.set_target_prefix(host_prefix)
    return {
        "ok": True,
        "host_prefix": host_prefix,
        "HL_URL": config.SCM_ADMIN_URL,
        "HL_BASE_URL": config.SCM_BASE_URL,
        "HL_LOGIN_PAGE": config.SCM_LOGIN_PAGE,
        "HL_COOKIE_DOMAIN": config.COOKIE_DOMAIN,
        "HL_ACCESS_DOMAIN": config.SCM_ACCESS_DOMAIN,
    }


@tool(name="check_session")
def check_session() -> dict:
    """检测 top 层是否出现『登录过期』系统确认弹窗。返回 {expired, detail}。"""
    _rwlock.acquire_read()
    try:
        return session_auth.check_session()
    finally:
        _rwlock.release_read()


@tool(name="get_active_frame")
def get_active_frame() -> dict:
    """获取当前可见 tabpanel 内的业务 iframe。返回 {ok, url, tab_name}。"""
    _rwlock.acquire_read()
    try:
        fr = browser_session.get_active_frame()
        if fr is None:
            return {"ok": False, "reason": "未找到活动 iframe，请先 enter_module"}
        return {
            "ok": True,
            "url": getattr(fr, "url", "") or "",
            "tab_name": browser_session.get_active_tab_name(),
        }
    finally:
        _rwlock.release_read()
