"""DOM interaction tools via FileSystemProvider."""

from typing import Literal

from fastmcp.tools import tool

from drissionpage_mcp.components._sync import with_read, with_write
from drissionpage_mcp.services import interaction


@tool(name="click")
def click(
    locator: str,
    in_frame: bool = True,
    by_js: bool = False,
    timeout: float = 5,
    clean_overlays: bool = True,
) -> dict:
    """定位并点击元素。"""
    return with_write(
        interaction.click,
        locator=locator,
        in_frame=in_frame,
        by_js=by_js,
        timeout=timeout,
        clean_overlays=clean_overlays,
    )


@tool(name="click_xy")
def click_xy(
    x: float,
    y: float,
    hover_first: bool = True,
    duration: float = 0.3,
    clean_overlays: bool = True,
    times: int = 1,
) -> dict:
    """按顶层视口坐标点击。"""
    return with_write(
        interaction.click_xy,
        x=x,
        y=y,
        hover_first=hover_first,
        duration=duration,
        clean_overlays=clean_overlays,
        times=times,
    )


@tool(name="input")
def input(
    locator: str,
    text: str,
    in_frame: bool = True,
    clear: bool = True,
    timeout: float = 5,
) -> dict:
    """定位一次后通过 DrissionPage 元素 input 写入，并返回实际值。"""
    return with_write(
        interaction.input,
        locator=locator,
        text=text,
        in_frame=in_frame,
        clear=clear,
        timeout=timeout,
    )


@tool(name="insert_text")
def insert_text(text: str) -> dict:
    """向当前焦点元素插入文本；活动业务 iframe 优先。"""
    return with_write(interaction.insert_text, text)


@tool(name="hover")
def hover(
    locator: str = None,
    x: float = None,
    y: float = None,
    in_frame: bool = True,
    duration: float = 0.3,
    timeout: float = 5,
) -> dict:
    """悬停到定位元素或坐标。"""
    return with_write(
        interaction.hover,
        locator=locator,
        x=x,
        y=y,
        in_frame=in_frame,
        duration=duration,
        timeout=timeout,
    )


@tool(name="screenshot")
def screenshot(
    path: str = None,
    locator: str = None,
    in_frame: bool = True,
    timeout: float = 5,
) -> dict:
    """截取页面或元素截图。"""
    return with_read(
        interaction.screenshot,
        path=path,
        locator=locator,
        in_frame=in_frame,
        timeout=timeout,
    )


@tool(name="find_elements")
def find_elements(
    locator: str,
    in_frame: bool = True,
    timeout: float = 5,
) -> dict:
    """查找所有匹配元素，返回数量及文本预览。"""
    return with_read(
        interaction.find_elements,
        locator=locator,
        in_frame=in_frame,
        timeout=timeout,
    )


@tool(name="find_batch")
def find_batch(
    locators: list[str],
    in_frame: bool = True,
    timeout: float = 5,
    any_one: bool = True,
    first_ele: bool = True,
) -> dict:
    """批量查找定位符。"""
    return with_read(
        interaction.find_batch,
        locators=locators,
        in_frame=in_frame,
        timeout=timeout,
        any_one=any_one,
        first_ele=first_ele,
    )


@tool(name="get_element_coords")
def get_element_coords(
    xpath: str,
    index: int = 1,
    timeout: float = 5,
) -> dict:
    """通过 XPath 定位元素并返回顶层视口绝对中心坐标。"""
    return with_read(
        interaction.get_element_coords,
        xpath=xpath,
        index=index,
        timeout=timeout,
    )


@tool(name="set_field_value")
def set_field_value(
    field_name: str,
    value: str,
    in_frame: bool = True,
    clear: bool = True,
    timeout: float = 5.0,
    scope: str = "auto",
    select_index: int = 0,
) -> dict:
    """按可见标签写入文本字段。"""
    return with_write(
        interaction.set_field_value,
        field_name=field_name,
        value=value,
        in_frame=in_frame,
        clear=clear,
        timeout=timeout,
        scope=scope,
        select_index=select_index,
    )


@tool(name="set_date")
def set_date(
    field_name: str,
    date: str = None,
    start_date: str = None,
    end_date: str = None,
    in_frame: bool = True,
    timeout: float = 8,
    select_index: int = 0,
    scope: str = "auto",
) -> dict:
    """设置日期/日期范围字段。"""
    return with_write(
        interaction.set_date,
        field_name=field_name,
        date=date,
        start_date=start_date,
        end_date=end_date,
        in_frame=in_frame,
        timeout=timeout,
        select_index=select_index,
        scope=scope,
    )


@tool(name="enter_module")
def enter_module(
    menu_text: str,
    timeout: float = 8,
    expand_filter: bool = True,
) -> dict:
    """点击左侧菜单进入模块，并等待业务 iframe 导航完成。"""
    return with_write(
        interaction.enter_module,
        menu_text=menu_text,
        timeout=timeout,
        expand_filter=expand_filter,
    )


@tool(name="explore_action")
def explore_action(
    action: Literal[
        "click", "input", "set_date", "table_cell", "select_option", "press_key"
    ] = "click",
    target: dict = None,
    locator: str = None,
    x: float = None,
    y: float = None,
    row: int = 0,
    col: int = None,
    column_title: str = None,
    kind: str = "auto",
    table_index: int = 0,
    icon_name: str = None,
    option_text: str = None,
    field_name: str = None,
    text: str = None,
    date: str = None,
    start_date: str = None,
    end_date: str = None,
    key: str = None,
    modifiers: list[str] = None,
    by_js: bool = False,
    in_frame: bool = True,
    timeout: float = 8,
    signals: list[str] = None,
    listen_targets: str = None,
    wait_spec: dict = None,
    capture_before: bool = False,
    capture_after: bool = False,
    include_snapshot: bool = None,
    detail: str = "summary",
    expect: str = "auto",
    observe_mode: str = "auto",
    clean_overlays: bool = True,
) -> dict:
    """执行动作并可使用 wait_spec 精确等待目标状态。

    wait_spec.kind 可为 locator、url、response 或 table；默认返回紧凑匹配摘要。
    response 的请求/响应正文仅在 body_artifact=true 时写入证据文件，不内联到响应。
    """
    return with_write(
        interaction.explore_action,
        action=action,
        target=target,
        locator=locator,
        x=x,
        y=y,
        row=row,
        col=col,
        column_title=column_title,
        kind=kind,
        table_index=table_index,
        icon_name=icon_name,
        option_text=option_text,
        field_name=field_name,
        text=text,
        date=date,
        start_date=start_date,
        end_date=end_date,
        key=key,
        modifiers=modifiers,
        by_js=by_js,
        in_frame=in_frame,
        timeout=timeout,
        signals=signals,
        listen_targets=listen_targets,
        wait_spec=wait_spec,
        capture_before=capture_before,
        capture_after=capture_after,
        include_snapshot=include_snapshot,
        detail=detail,
        expect=expect,
        observe_mode=observe_mode,
        clean_overlays=clean_overlays,
    )
