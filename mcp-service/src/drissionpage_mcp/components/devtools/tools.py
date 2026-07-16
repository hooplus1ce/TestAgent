"""Devtools tools via FileSystemProvider."""

from fastmcp.tools import tool

from drissionpage_mcp.components._sync import with_read, with_write
from drissionpage_mcp.services import devtools


@tool(name="run_js")
def run_js(script: str, in_frame: bool = True, max_chars: int = 4000) -> dict:
    """执行显式调试脚本，并按序列化后的真实体积限制返回。"""
    return with_read(devtools.run_js, script, in_frame=in_frame, max_chars=max_chars)


@tool(name="mouse_trail")
def mouse_trail(on: bool = True) -> dict:
    """开启/关闭鼠标轨迹可视化。"""
    return with_write(devtools.mouse_trail, on)


@tool(name="click_to_download")
def click_to_download(
    locator: str,
    save_path: str = None,
    rename: str = None,
    suffix: str = None,
    new_tab: bool = False,
    by_js: bool = False,
    in_frame: bool = True,
    timeout: float = 30,
) -> dict:
    """点击元素触发浏览器下载并等待完成。"""
    return with_write(
        devtools.click_to_download,
        locator,
        save_path=save_path,
        rename=rename,
        suffix=suffix,
        new_tab=new_tab,
        by_js=by_js,
        in_frame=in_frame,
        timeout=timeout,
    )


@tool(name="click_to_upload")
def click_to_upload(
    locator: str,
    file_paths: str,
    by_js: bool = False,
    in_frame: bool = True,
    timeout: float = 10,
) -> dict:
    """点击上传控件并选择本地文件。"""
    return with_write(
        devtools.click_to_upload,
        locator,
        file_paths,
        by_js=by_js,
        in_frame=in_frame,
        timeout=timeout,
    )


@tool(name="download_by_browser")
def download_by_browser(
    url: str,
    save_path: str = None,
    rename: str = None,
    suffix: str = None,
    timeout: float = 30,
    file_exists: str = "rename",
) -> dict:
    """通过浏览器下载管理器拉取 URL 资源。"""
    return with_write(
        devtools.download_by_browser,
        url,
        save_path=save_path,
        rename=rename,
        suffix=suffix,
        timeout=timeout,
        file_exists=file_exists,
    )


@tool(name="browser_console_messages")
def browser_console_messages(
    level: str = "",
    timeout: float = 0.0,
    start: bool = True,
    clear: bool = False,
    stop: bool = False,
    max_messages: int = 50,
    filename: str = None,
) -> dict:
    """读取并按级别筛选控制台消息。"""
    return with_write(
        devtools.browser_console_messages,
        level=level,
        timeout=timeout,
        start=start,
        clear=clear,
        stop=stop,
        max_messages=max_messages,
        filename=filename,
    )


@tool(name="browser_save_pdf")
def browser_save_pdf(path: str = None, filename: str = None) -> dict:
    """将当前页面保存为 PDF。"""
    return with_write(devtools.browser_save_pdf, path=path, filename=filename)


@tool(name="browser_tabs")
def browser_tabs(action: str = "list", index: int = None, url: str = None) -> dict:
    """列出/切换/新建/关闭浏览器标签页。"""
    return with_write(devtools.browser_tabs, action=action, index=index, url=url)


@tool(name="browser_scroll")
def browser_scroll(
    direction: str = "down",
    pixel: int = 300,
    locator: str = None,
    x: int = None,
    y: int = None,
    timeout: float = 5,
) -> dict:
    """滚动活动 iframe 或定位元素。"""
    return with_write(
        devtools.browser_scroll,
        direction=direction,
        pixel=pixel,
        locator=locator,
        x=x,
        y=y,
        timeout=timeout,
    )


@tool(name="browser_press_key")
def browser_press_key(
    key: str,
    modifiers: list[str] = None,
    interval: float = 0.01,
) -> dict:
    """在活动业务 iframe 发送按键。"""
    return with_write(
        devtools.browser_press_key,
        key=key,
        modifiers=modifiers,
        interval=interval,
    )


@tool(name="browser_get_element_state")
def browser_get_element_state(locator: str, state: str = None) -> dict:
    """读取元素显示/启用等状态。"""
    return with_read(devtools.browser_get_element_state, locator=locator, state=state)


@tool(name="browser_list_caps")
def browser_list_caps() -> dict:
    """列出当前 MCP 能力分组与已暴露工具。"""
    return with_read(devtools.browser_list_caps)
