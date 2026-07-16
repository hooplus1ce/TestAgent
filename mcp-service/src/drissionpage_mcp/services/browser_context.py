"""BrowserContext lifecycle and permission helpers."""

from __future__ import annotations

import re

from . import browser_session, role_sessions


def new_context(proxy: str = None) -> dict:
    """Create a 4.2 BrowserContext with an initial blank tab."""
    try:
        context_id, tab = browser_session.create_context(proxy=proxy)
    except Exception as exc:
        return {"ok": False, "reason": "创建上下文失败: %s" % exc}
    return {
        "ok": True,
        "context_id": context_id,
        "tab_ids": [getattr(tab, "tab_id", "")],
        "initial_tab_id": tab.tab_id,
        "hint": "调用 switch_context(%d) 切换到该上下文操作" % context_id,
    }


def switch_context(context_id: int) -> dict:
    tab = browser_session.switch_context(context_id)
    if tab is None:
        return {
            "ok": False,
            "reason": "context 不存在或无可用 tab",
            "context_id": context_id,
        }
    return {
        "ok": True,
        "url": getattr(tab, "url", "") or "",
        "context_id": context_id,
    }


def close_context(context_id: int) -> dict:
    result = browser_session.close_context(context_id)
    if result.get("ok"):
        removed = role_sessions.remove_by_context(context_id)
        if removed:
            result = dict(result)
            result["removed_roles"] = removed
    return result


def list_contexts() -> dict:
    return {"ok": True, "contexts": browser_session.list_contexts()}


def set_permission(perm: str, allow: bool = True) -> dict:
    if not isinstance(allow, bool):
        return {"ok": False, "reason": "allow 必须是布尔值"}
    permission = str(perm or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", permission):
        return {"ok": False, "reason": "不支持的权限: %s" % permission}
    browser = browser_session.get_browser()
    perm_fn = getattr(browser.set.perm, permission, None)
    if not callable(perm_fn):
        return {"ok": False, "reason": "不支持的权限: %s" % permission}
    try:
        perm_fn(allow=allow)
    except Exception as exc:
        return {"ok": False, "reason": "设置权限失败: %s" % exc}
    return {"ok": True, "perm": permission, "allow": allow}
