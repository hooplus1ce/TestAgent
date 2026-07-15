"""layer.js discovery tools for legacy enterprise pages."""

from fastmcp.tools import tool

from drissionpage_mcp.core.lock import _rwlock
from drissionpage_mcp.services import layer_modal


@tool(
    name="scan_layer_content",
    tags={
        "cap:core",
        "cap:legacy",
        "domain:page-model",
        "framework:layer",
        "level:facade",
        "profile:enterprise",
        "risk:read",
    },
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def scan_layer_content(layer_index: int = -1, timeout: float = 3.0) -> dict:
    """扫描可见 layer.js 弹层内容，并自动进入其嵌套 iframe 表单。"""
    _rwlock.acquire_read()
    try:
        return layer_modal.scan_layer_content(
            layer_index=layer_index,
            timeout=timeout,
        )
    finally:
        _rwlock.release_read()
