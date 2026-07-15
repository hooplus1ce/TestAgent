"""Layer message toast detection tool loaded by FastMCP's FileSystemProvider."""

from fastmcp.tools import tool

from drissionpage_mcp.core.lock import _rwlock
from drissionpage_mcp.services import observe


@tool(
    name="detect_layer_msg",
)
def detect_layer_msg(timeout: float = 2) -> dict:
    """原子工具：检测 .layui-layer-msg / .layui-layer-dialog.layui-layer-msg。
    事件驱动 ele() 等待。捕获遗留 jQuery 页面 3 秒自动关闭的「保存成功」类短寿命消息提示。"""
    _rwlock.acquire_read()
    try:
        return observe.detect_layer_msg(timeout=timeout)
    finally:
        _rwlock.release_read()
