"""Filter-area tools discovered by FileSystemProvider."""

from fastmcp.tools import tool

from drissionpage_mcp.components._sync import with_write
from drissionpage_mcp.services import filter_area, page_model


@tool(name="scan_filter_fields")
def scan_filter_fields() -> dict:
    """扫描筛选区所有字段，返回完整字段矩阵（字段名/操作符/输入类型/下拉待选项）。
    自动展开每个下拉字段获取待选项。需先 enter_module 并展开筛选区。
    """
    return with_write(filter_area.scan_filter_fields)


@tool(name="select_option")
def select_option(
    field_name: str,
    option_text: str,
    select_index: int = 0,
    scope: str = "auto",
    timeout: float = 5.0,
) -> dict:
    """按字段名选择下拉项。

    支持 Ant Design Select / Legions Quick Filter，以及遗留 bootstrap-select。
    scope: auto | frame | top | layer（仅 layer 内容区 / bootstrap-select）。
    field_name 为空时选择第一个可见下拉；select_index 用于同名字段多个下拉。
    """
    return with_write(
        page_model.select_option,
        field_name=field_name,
        option_text=option_text,
        select_index=select_index,
        scope=scope,
        timeout=timeout,
    )
