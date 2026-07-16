"""Table facade tools (VTable / Bootstrap / HTML) via FileSystemProvider."""

from typing import Literal

from fastmcp.tools import tool

from drissionpage_mcp.components._sync import with_read, with_write
from drissionpage_mcp.services import table_facade


@tool(name="scan_table")
def scan_table(
    kind: str = "auto",
    max_col: int = 50,
    table_index: int = 0,
    filename: str = None,
) -> dict:
    """扫描当前可见表格。kind: auto | vtable | html | bootstrap。filename 提供时保存到文件。"""
    return with_read(
        table_facade.scan_table,
        kind=kind,
        max_col=max_col,
        table_index=table_index,
        filename=filename,
    )


@tool(name="get_table_values")
def get_table_values(
    column_title: str,
    kind: str = "auto",
    raw: bool = False,
    table_index: int = 0,
    filename: str = None,
) -> dict:
    """按列标题读取标量值列表；HTML 同时返回 cells 元数据，raw=true 仅支持 VTable。"""
    return with_read(
        table_facade.get_table_values,
        column_title=column_title,
        kind=kind,
        raw=raw,
        table_index=table_index,
        filename=filename,
    )


@tool(name="find_vtable_row")
def find_vtable_row(
    column_title: str,
    value: str,
    raw: bool = False,
    match: str = "equals",
    header_rows: int = None,
    timeout: float = 0,
) -> dict:
    """按列值唯一定位 VTable 行。"""
    return with_read(
        table_facade.find_vtable_row,
        column_title=column_title,
        value=value,
        raw=raw,
        match=match,
        header_rows=header_rows,
        timeout=timeout,
    )


@tool(name="count_vtable_rows")
def count_vtable_rows(
    column_title: str,
    value: str,
    raw: bool = False,
    match: str = "equals",
    expected_count: int = None,
    timeout: float = 0,
) -> dict:
    """统计 VTable 中 column_title/value 匹配的行数。"""
    return with_read(
        table_facade.count_vtable_rows,
        column_title=column_title,
        value=value,
        raw=raw,
        match=match,
        expected_count=expected_count,
        timeout=timeout,
    )


@tool(name="get_vtable_row_values")
def get_vtable_row_values(
    key_column: str,
    key_value: str,
    column_titles: list[str],
    raw: bool = False,
    match: str = "equals",
    timeout: float = 0,
) -> dict:
    """按 key 列定位 VTable 行并读取指定列值。"""
    return with_read(
        table_facade.get_vtable_row_values,
        key_column=key_column,
        key_value=key_value,
        column_titles=column_titles,
        raw=raw,
        match=match,
        timeout=timeout,
    )


@tool(name="get_table_data")
def get_table_data(
    kind: str = "auto",
    table_index: int = 0,
    filename: str = None,
) -> dict:
    """统一读取当前表格完整可读数据。"""
    return with_read(
        table_facade.get_table_data,
        kind=kind,
        table_index=table_index,
        filename=filename,
    )


@tool(name="get_all_table_data")
def get_all_table_data(
    kind: str = "auto",
    table_index: int = 0,
    max_pages: int = 1,
    max_rows: int = 1000,
    max_columns: int = 50,
    raw: bool = False,
    filename: str = None,
) -> dict:
    """读取表格数据；HTML 可按 max_pages 翻页采集。"""
    from drissionpage_mcp.services import page_model

    return with_write(
        page_model.get_all_table_data,
        kind=kind,
        table_index=table_index,
        max_pages=max_pages,
        max_rows=max_rows,
        max_columns=max_columns,
        raw=raw,
        filename=filename,
    )


@tool(name="get_vtable_cell_render_info")
def get_vtable_cell_render_info(
    row: int,
    col: int = None,
    column_title: str = None,
    detail: str = "summary",
) -> dict:
    """读取 VTable 单元格渲染信息：文本、字体色、标签底色、背景/边框色。"""
    return with_read(
        table_facade.get_vtable_cell_render_info,
        row=row,
        col=col,
        column_title=column_title,
        detail=detail,
    )


@tool(name="get_vtable_cell_icons")
def get_vtable_cell_icons(
    row: int,
    col: int = None,
    column_title: str = None,
    icon_name: str = None,
    detail: str = "summary",
) -> dict:
    """读取 VTable 单元格内图标名称/类型和顶层视口坐标。"""
    return with_read(
        table_facade.get_vtable_cell_icons,
        row=row,
        col=col,
        column_title=column_title,
        icon_name=icon_name,
        detail=detail,
    )


@tool(name="vtable_action")
def vtable_action(
    action: str = "click",
    row: int = 0,
    col: int = None,
    column_title: str = None,
    target: str = "cell",
    icon_name: str = None,
    icon_index: int = None,
    hover_first: bool = True,
    duration: float = 0.3,
    drag_to_x: float = None,
    drag_to_y: float = None,
    drag_by_x: float = None,
    drag_by_y: float = None,
    clean_overlays: bool = True,
    source_x: float = None,
    source_y: float = None,
) -> dict:
    """VTable 专项指针动作：click/double_click/hover/drag。"""
    return with_write(
        table_facade.vtable_action,
        action=action,
        row=row,
        col=col,
        column_title=column_title,
        target=target,
        icon_name=icon_name,
        icon_index=icon_index,
        hover_first=hover_first,
        duration=duration,
        drag_to_x=drag_to_x,
        drag_to_y=drag_to_y,
        drag_by_x=drag_by_x,
        drag_by_y=drag_by_y,
        clean_overlays=clean_overlays,
        source_x=source_x,
        source_y=source_y,
    )


@tool(name="click_table_cell")
def click_table_cell(
    row: int,
    col: int = None,
    column_title: str = None,
    kind: str = "auto",
    table_index: int = 0,
    icon_name: str = None,
    hover_first: bool = True,
    duration: float = 0.3,
    double_click: bool = False,
    clean_overlays: bool = True,
) -> dict:
    """统一点击表格单元格。kind: auto | vtable | html | bootstrap。"""
    return with_write(
        table_facade.click_table_cell,
        row=row,
        col=col,
        column_title=column_title,
        kind=kind,
        table_index=table_index,
        icon_name=icon_name,
        hover_first=hover_first,
        duration=duration,
        double_click=double_click,
        clean_overlays=clean_overlays,
    )


@tool(name="hover_table_cell")
def hover_table_cell(
    row: int,
    col: int = None,
    column_title: str = None,
    kind: str = "auto",
    table_index: int = 0,
    duration: float = 0.3,
) -> dict:
    """统一悬停表格单元格。"""
    return with_write(
        table_facade.hover_table_cell,
        row=row,
        col=col,
        column_title=column_title,
        kind=kind,
        table_index=table_index,
        duration=duration,
    )


@tool(name="resize_table_column")
def resize_table_column(
    width: int,
    col: int = None,
    column_title: str = None,
    kind: str = "vtable",
) -> dict:
    """统一调整表格列宽（目前仅 VTable）。"""
    return with_write(
        table_facade.resize_table_column,
        width=width,
        col=col,
        column_title=column_title,
        kind=kind,
    )


@tool(name="reorder_vtable_column")
def reorder_vtable_column(
    column_title: str = None,
    col: int = None,
    target_column_title: str = None,
    target_col: int = None,
    position: Literal["after", "before"] = "after",
) -> dict:
    """拖拽 VTable 列头重排列。"""
    return with_write(
        table_facade.reorder_vtable_column,
        column_title=column_title,
        col=col,
        target_column_title=target_column_title,
        target_col=target_col,
        position=position,
    )


@tool(name="query_table")
def query_table(
    operation: Literal["values", "data", "find", "count", "row"] = "values",
    column_title: str = None,
    value: str = None,
    key_column: str = None,
    key_value: str = None,
    column_titles: list[str] = None,
    kind: Literal["auto", "html", "vtable", "bootstrap"] = "auto",
    table_index: int = 0,
    raw: bool = False,
    match: Literal["equals", "contains"] = "equals",
    expected_count: int = None,
    timeout: float = 0,
    filename: str = None,
) -> dict:
    """统一表格读取入口。operation: values/data/find/count/row。"""
    return with_read(
        table_facade.query_table,
        operation=operation,
        column_title=column_title,
        value=value,
        key_column=key_column,
        key_value=key_value,
        column_titles=column_titles,
        kind=kind,
        table_index=table_index,
        raw=raw,
        match=match,
        expected_count=expected_count,
        timeout=timeout,
        filename=filename,
    )


@tool(name="inspect_table_cell")
def inspect_table_cell(
    row: int,
    col: int = None,
    column_title: str = None,
    aspect: Literal["all", "render", "icons"] = "all",
    icon_name: str = None,
    detail: str = "summary",
) -> dict:
    """统一读取 VTable 单元格渲染样式和图标。"""
    return with_read(
        table_facade.inspect_table_cell,
        row=row,
        col=col,
        column_title=column_title,
        aspect=aspect,
        icon_name=icon_name,
        detail=detail,
    )


@tool(name="table_action")
def table_action(
    action: Literal["click", "double_click", "hover", "drag", "resize"] = "click",
    row: int = 0,
    col: int = None,
    column_title: str = None,
    kind: Literal["auto", "html", "vtable", "bootstrap"] = "auto",
    table_index: int = 0,
    target: Literal["cell", "header", "header-icon", "cell-icon"] = "cell",
    icon_name: str = None,
    icon_index: int = None,
    width: int = None,
    hover_first: bool = True,
    duration: float = 0.3,
    drag_to_x: float = None,
    drag_to_y: float = None,
    drag_by_x: float = None,
    drag_by_y: float = None,
    clean_overlays: bool = True,
    signals: list[str] = None,
    listen_targets: str = None,
    timeout: float = 8,
    include_snapshot: bool = True,
    detail: str = "summary",
    drag_from_x: float = None,
    drag_from_y: float = None,
) -> dict:
    """统一表格动作入口。action: click/double_click/hover/drag/resize。"""
    return with_write(
        table_facade.table_action,
        action=action,
        row=row,
        col=col,
        column_title=column_title,
        kind=kind,
        table_index=table_index,
        target=target,
        icon_name=icon_name,
        icon_index=icon_index,
        width=width,
        hover_first=hover_first,
        duration=duration,
        drag_to_x=drag_to_x,
        drag_to_y=drag_to_y,
        drag_by_x=drag_by_x,
        drag_by_y=drag_by_y,
        clean_overlays=clean_overlays,
        signals=signals,
        listen_targets=listen_targets,
        timeout=timeout,
        include_snapshot=include_snapshot,
        detail=detail,
        drag_from_x=drag_from_x,
        drag_from_y=drag_from_y,
        native_wait=False,
    )


@tool(name="scan_action_availability_by_selection")
def scan_action_availability_by_selection(
    row: int = 0,
    col: int = 0,
    kind: str = "auto",
    table_index: int = 0,
    select_row: bool = True,
    wait_after_click: float = 0.3,
) -> dict:
    """选中表格行后扫描工具栏动作可用性变化。"""
    return with_write(
        table_facade.scan_action_availability_by_selection,
        row=row,
        col=col,
        kind=kind,
        table_index=table_index,
        select_row=select_row,
        wait_after_click=wait_after_click,
    )
