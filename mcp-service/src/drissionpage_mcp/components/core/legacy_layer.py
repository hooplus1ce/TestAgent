"""Legacy Bootstrap + layer.js dialog flow tool."""

from fastmcp.tools import tool

from drissionpage_mcp.core.lock import _rwlock
from drissionpage_mcp.services import legacy_layer_flow


@tool(
    name="select_row_open_layer",
    tags={
        "cap:core",
        "cap:legacy",
        "framework:layer",
        "framework:bootstrap-table",
        "level:facade",
        "profile:enterprise",
        "risk:write",
    },
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def select_row_open_layer(
    row: int = 0,
    table_index: int = 0,
    select_all: bool = False,
    close_shade: bool = True,
    toolbar_text: str = "编辑",
    layer_timeout: float = 3.0,
    scan_layer: bool = True,
) -> dict:
    """遗留 jQuery 页收敛路径：清理 layer 遮罩 → 勾选 Bootstrap 行 → 点工具栏 → 进入 layer iframe。

    典型「账号管理」：选中一行 → 编辑 → 在 layer 嵌套表单里 set_field_value/select_option。
    toolbar_text 传空字符串可跳过工具栏点击（弹层已由其它步骤打开）。
    """
    _rwlock.acquire_write()
    try:
        return legacy_layer_flow.select_row_open_layer(
            row=row,
            table_index=table_index,
            select_all=select_all,
            close_shade=close_shade,
            toolbar_text=toolbar_text,
            layer_timeout=layer_timeout,
            scan_layer=scan_layer,
        )
    finally:
        _rwlock.release_write()
