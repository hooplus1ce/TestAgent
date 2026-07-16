"""BrowserContext tools discovered by FileSystemProvider."""

from fastmcp.tools import tool

from drissionpage_mcp.components._sync import with_read, with_write
from drissionpage_mcp.services import browser_context


@tool(name="new_context")
def new_context(proxy: str = None) -> dict:
    """创建带初始空白标签页的 4.2 BrowserContext。"""
    return with_write(browser_context.new_context, proxy=proxy)


@tool(name="switch_context")
def switch_context(context_id: int) -> dict:
    """切换活动 tab 到指定 context 的首个 tab。"""
    return with_write(browser_context.switch_context, context_id)


@tool(name="close_context")
def close_context(context_id: int) -> dict:
    """关闭 new_context 创建的上下文。"""
    return with_write(browser_context.close_context, context_id)


@tool(name="list_contexts")
def list_contexts() -> dict:
    """列出所有已注册的浏览器上下文。"""
    return with_read(browser_context.list_contexts)


@tool(name="set_permission")
def set_permission(perm: str, allow: bool = True) -> dict:
    """通过 DrissionPage 权限 setter 授予或拒绝浏览器权限。"""
    return with_write(browser_context.set_permission, perm, allow=allow)
