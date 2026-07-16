"""Page-model scan tools via FileSystemProvider."""

from fastmcp.tools import tool

from drissionpage_mcp.components._sync import with_read, with_write
from drissionpage_mcp.services import page_scan


@tool(name="capture_page_model")
def capture_page_model(
    include_filters: bool = True,
    include_tables: bool = True,
    include_table_data: bool = True,
    max_table_rows: int = 80,
    max_elements: int = 120,
    filename: str = None,
) -> dict:
    """采集当前页面模型：筛选、表单、工具栏、表格与浮层摘要。"""
    return with_write(
        page_scan.capture_page_model,
        include_filters=include_filters,
        include_tables=include_tables,
        include_table_data=include_table_data,
        max_table_rows=max_table_rows,
        max_elements=max_elements,
        filename=filename,
    )


@tool(name="scan_toolbar_actions")
def scan_toolbar_actions(
    scope: str = "page",
    in_frame: bool = True,
    max_items: int = 120,
) -> dict:
    """扫描页面可见动作按钮/链接。"""
    return with_read(
        page_scan.scan_toolbar_actions,
        scope=scope,
        in_frame=in_frame,
        max_items=max_items,
    )


@tool(name="scan_form_fields")
def scan_form_fields(
    scope: str = "page",
    include_hidden: bool = False,
    in_frame: bool = True,
    max_fields: int = 200,
) -> dict:
    """扫描表单字段。"""
    return with_read(
        page_scan.scan_form_fields,
        scope=scope,
        include_hidden=include_hidden,
        in_frame=in_frame,
        max_fields=max_fields,
    )


@tool(name="scan_pagination")
def scan_pagination(in_frame: bool = True) -> dict:
    """扫描页面分页器。"""
    return with_read(page_scan.scan_pagination, in_frame=in_frame)


@tool(name="scan_page_elements")
def scan_page_elements(
    include_iframe: bool = True,
    max_items: int = 200,
    filename: str = None,
) -> dict:
    """扫描页面可见交互控件。"""
    return with_read(
        page_scan.scan_page_elements,
        include_iframe=include_iframe,
        max_items=max_items,
        filename=filename,
    )


@tool(name="dom_tree")
def dom_tree(
    selector: str = "",
    max_depth: int = 6,
    max_children: int = 50,
    text: bool = False,
    text_limit: int = 100,
    show_hidden: bool = False,
    filename: str = None,
    save_path: str = "",
    save_format: str = "yml",
    max_chars: int = 8000,
) -> dict:
    """打印页面或元素的 DOM 树结构。"""
    return with_read(
        page_scan.dom_tree,
        selector=selector,
        max_depth=max_depth,
        max_children=max_children,
        text=text,
        text_limit=text_limit,
        show_hidden=show_hidden,
        filename=filename,
        save_path=save_path,
        save_format=save_format,
        max_chars=max_chars,
    )
