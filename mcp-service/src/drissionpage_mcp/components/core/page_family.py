"""Page-family discovery tools loaded by FastMCP's FileSystemProvider."""

from fastmcp.tools import tool

from drissionpage_mcp.core import page_family
from drissionpage_mcp.core.lock import _rwlock


@tool(
    name="detect_page_family",
    tags={"cap:core", "cap:legacy", "domain:page-model", "risk:read"},
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def detect_page_family(include_top: bool = True, include_frame: bool = True) -> dict:
    """探测当前页面族，并返回首选表格类型和可用 UI adapter。"""
    _rwlock.acquire_read()
    try:
        return page_family.detect_page_family(
            include_top=include_top,
            include_frame=include_frame,
        )
    finally:
        _rwlock.release_read()
