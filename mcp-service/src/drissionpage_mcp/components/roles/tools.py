"""Role-session tools backed by isolated DrissionPage BrowserContexts."""

from collections.abc import Callable

from fastmcp.tools import tool

from drissionpage_mcp.core.lock import _rwlock
from drissionpage_mcp.services import role_sessions


_WRITE_TAGS = {"cap:roles", "profile:enterprise", "level:facade", "risk:write"}
_READ_TAGS = {"cap:roles", "profile:enterprise", "level:facade", "risk:read"}
_WRITE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
_READ_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


def _role_tool(operation: Callable, role_id: str, **kwargs) -> dict:
    try:
        return operation(role_id, **kwargs)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}


def _write(operation: Callable, *args, **kwargs) -> dict:
    _rwlock.acquire_write()
    try:
        return operation(*args, **kwargs)
    finally:
        _rwlock.release_write()


@tool(tags=_WRITE_TAGS, annotations=_WRITE_ANNOTATIONS)
def role_session_open(role_id: str, proxy: str = None) -> dict:
    """创建角色独立 BrowserContext，隔离 Cookie、Storage 与登录态。"""
    return _write(_role_tool, role_sessions.open_role, role_id, proxy=proxy)


@tool(tags=_WRITE_TAGS, annotations=_WRITE_ANNOTATIONS)
def role_session_login(role_id: str) -> dict:
    """在角色独立 Context 内登录，只读取该角色的环境变量凭据。"""
    return _write(_role_tool, role_sessions.login_role, role_id)


@tool(tags=_WRITE_TAGS, annotations=_WRITE_ANNOTATIONS)
def role_session_start(role_id: str) -> dict:
    """创建并登录角色 Context；登录失败时自动清理。"""
    return _write(_role_tool, role_sessions.start_role, role_id)


@tool(tags=_WRITE_TAGS, annotations=_WRITE_ANNOTATIONS)
def role_session_activate(role_id: str) -> dict:
    """切换通用浏览器工具到指定角色会话。"""
    return _write(_role_tool, role_sessions.activate_role, role_id)


@tool(tags=_READ_TAGS, annotations=_READ_ANNOTATIONS)
def role_session_list() -> dict:
    """列出角色 Context 与登录配置状态，不返回任何凭据值。"""
    _rwlock.acquire_read()
    try:
        return {"ok": True, "roles": role_sessions.list_roles()}
    finally:
        _rwlock.release_read()


@tool(
    tags={"cap:roles", "profile:enterprise", "level:facade", "risk:destructive"},
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def role_session_close(role_id: str) -> dict:
    """关闭角色专属 Context 并清除对应浏览器登录态。"""
    return _write(_role_tool, role_sessions.close_role, role_id)
