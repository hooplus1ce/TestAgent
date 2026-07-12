"""多账号角色会话：一个业务角色对应一个隔离 BrowserContext。

该模块不保存账号密码、Cookie 或浏览器对象。密码只在 ``login_role()`` 调用期间从
进程环境读取并传给认证服务；注册表只保存稳定的 Context ID，浏览器重连后可据此识别
失效会话。
"""
from dataclasses import dataclass
import logging
import os
import re
import threading

from . import browser_session, session_auth


_ROLE_ID_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_role_lock = threading.RLock()
_roles = {}
logger = logging.getLogger("drissionpage-mcp")


@dataclass
class RoleSession:
    role_id: str
    context_id: int
    initial_tab_id: str
    has_proxy: bool
    authenticated: bool = False


def normalize_role_id(role_id: str) -> str:
    """校验逻辑角色标识，并归一化为小写环境变量安全名称。"""
    normalized = str(role_id or "").strip().lower()
    if not _ROLE_ID_RE.fullmatch(normalized):
        raise ValueError(
            "role_id 必须是 1-64 位小写字母开头的英文、数字、下划线或连字符"
        )
    return normalized


def credential_env_names(role_id: str) -> tuple[str, str]:
    """返回角色专属账号与密码环境变量名，不读取或返回其值。"""
    normalized = normalize_role_id(role_id)
    token = normalized.upper().replace("-", "_")
    return (
        "HL_SCM_ROLE_%s_USERNAME" % token,
        "HL_SCM_ROLE_%s_USERPWD" % token,
    )


def _get_session(role_id: str) -> RoleSession | None:
    normalized = normalize_role_id(role_id)
    with _role_lock:
        return _roles.get(normalized)


def _forget_if_stale(role_id: str, context_id: int) -> None:
    with _role_lock:
        session = _roles.get(role_id)
        if session and session.context_id == context_id:
            _roles.pop(role_id, None)


def open_role(role_id: str, proxy: str = None) -> dict:
    """为业务角色创建一个 Cookie/Storage 相互隔离的 BrowserContext。"""
    normalized = normalize_role_id(role_id)
    with _role_lock:
        existing = _roles.get(normalized)
        if existing and browser_session.context_exists(existing.context_id):
            return {
                "ok": False,
                "reason": "角色会话已存在；请先关闭后再创建",
                "role_id": normalized,
                "context_id": existing.context_id,
            }
        if existing:
            _roles.pop(normalized, None)

        try:
            context_id, tab = browser_session.create_context(proxy=proxy)
        except Exception as exc:
            logger.warning("创建角色上下文失败 role=%s error=%s", normalized, type(exc).__name__)
            return {
                "ok": False,
                "reason": "创建角色上下文失败，请检查浏览器连接或代理配置",
                "role_id": normalized,
            }

        session = RoleSession(
            role_id=normalized,
            context_id=context_id,
            initial_tab_id=str(getattr(tab, "tab_id", "") or ""),
            has_proxy=bool(proxy),
        )
        _roles[normalized] = session
        username_env, password_env = credential_env_names(normalized)
        return {
            "ok": True,
            "role_id": normalized,
            "context_id": context_id,
            "tab_id": session.initial_tab_id,
            "credential_env": {"username": username_env, "password": password_env},
            "hint": "调用 role_session_login(%r) 注入该角色凭据" % normalized,
        }


def activate_role(role_id: str) -> dict:
    """将现有通用 MCP 工具切换到指定角色的首个 tab。"""
    normalized = normalize_role_id(role_id)
    session = _get_session(normalized)
    if session is None:
        return {"ok": False, "reason": "角色会话不存在", "role_id": normalized}

    tab = browser_session.switch_context(session.context_id)
    if tab is None:
        _forget_if_stale(normalized, session.context_id)
        return {
            "ok": False,
            "reason": "角色上下文已失效，请重新创建并登录",
            "role_id": normalized,
        }
    return {
        "ok": True,
        "role_id": normalized,
        "context_id": session.context_id,
        "url": getattr(tab, "url", "") or "",
    }


def login_role(role_id: str) -> dict:
    """在角色专属 Context 中读取环境变量并执行 OCR 登录。"""
    normalized = normalize_role_id(role_id)
    session = _get_session(normalized)
    if session is None:
        return {"ok": False, "reason": "角色会话不存在", "role_id": normalized}

    username_env, password_env = credential_env_names(normalized)
    username = os.environ.get(username_env, "")
    password = os.environ.get(password_env, "")
    missing = [name for name, value in ((username_env, username), (password_env, password)) if not value]
    if missing:
        return {
            "ok": False,
            "reason": "缺少角色凭据环境变量: %s" % ", ".join(missing),
            "role_id": normalized,
        }

    tab = browser_session.switch_context(session.context_id)
    if tab is None:
        _forget_if_stale(normalized, session.context_id)
        return {
            "ok": False,
            "reason": "角色上下文已失效，请重新创建并登录",
            "role_id": normalized,
        }

    try:
        result = session_auth.login_ocr_for_tab(
            tab,
            username,
            password,
            credential_envs=(username_env, password_env),
        )
    except Exception as exc:
        logger.warning("角色登录失败 role=%s error=%s", normalized, type(exc).__name__)
        return {
            "ok": False,
            "reason": "角色登录失败，请检查账号、网络或服务日志",
            "role_id": normalized,
        }

    with _role_lock:
        current = _roles.get(normalized)
        if current and current.context_id == session.context_id:
            current.authenticated = bool(result.get("ok"))
    return {
        **result,
        "role_id": normalized,
        "context_id": session.context_id,
        "authenticated": bool(result.get("ok")),
    }


def start_role(role_id: str) -> dict:
    """创建并登录角色 Context；登录失败时关闭刚创建的隔离上下文。"""
    normalized = normalize_role_id(role_id)
    opened = open_role(normalized)
    if not opened.get("ok"):
        return {**opened, "stage": "open"}
    logged_in = login_role(normalized)
    if not logged_in.get("ok"):
        cleanup = close_role(normalized)
        return {
            "ok": False,
            "role_id": normalized,
            "stage": "login",
            "reason": logged_in.get("reason", "角色登录失败"),
            "credential_env": opened.get("credential_env"),
            "cleanup": {
                "ok": bool(cleanup.get("ok")),
                "reason": cleanup.get("reason", ""),
            },
        }
    return {
        **logged_in,
        "ok": True,
        "role_id": normalized,
        "stage": "ready",
        "credential_env": opened.get("credential_env"),
    }


def close_role(role_id: str) -> dict:
    """关闭角色专属 Context，并移除本地角色映射。"""
    normalized = normalize_role_id(role_id)
    session = _get_session(normalized)
    if session is None:
        return {"ok": False, "reason": "角色会话不存在", "role_id": normalized}

    result = browser_session.close_context(session.context_id)
    if result.get("ok") or not browser_session.context_exists(session.context_id):
        _forget_if_stale(normalized, session.context_id)
    return {**result, "role_id": normalized}


def remove_by_context(context_id: int) -> list[str]:
    """在通用 close_context 成功后同步删除关联角色。"""
    with _role_lock:
        removed = [role_id for role_id, session in _roles.items() if session.context_id == context_id]
        for role_id in removed:
            _roles.pop(role_id, None)
        return removed


def list_roles() -> list[dict]:
    """列出角色会话状态；返回变量名和状态，绝不返回凭据值或 Cookie。"""
    contexts = {item["context_id"]: item for item in browser_session.list_contexts()}
    with _role_lock:
        sessions = list(_roles.values())

    result = []
    for session in sessions:
        context = contexts.get(session.context_id)
        username_env, password_env = credential_env_names(session.role_id)
        result.append({
            "role_id": session.role_id,
            "context_id": session.context_id,
            "tab_id": session.initial_tab_id,
            "state": "ready" if context else "stale",
            "is_active": bool(context and context.get("is_active")),
            "authenticated": session.authenticated if context else False,
            "has_proxy": session.has_proxy,
            "credentials_configured": bool(
                os.environ.get(username_env) and os.environ.get(password_env)
            ),
            "credential_env": {"username": username_env, "password": password_env},
        })
    return result
