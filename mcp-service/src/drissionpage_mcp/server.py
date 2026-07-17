"""drissionpage-mcp 服务器：把 DrissionPage 浏览器自动化封装成结构化 MCP 工具。

供 AI 驱动的 UI 测试技能调用。浏览器原语(连接/扫描/点击/输入/截图)、
VTable 工具(内部 frame.run_js 注入 bundled JS)、会话维持、弹窗检测、网络监听。

启动：uv run --package drissionpage-mcp -m drissionpage_mcp  (stdio 传输)
"""
import functools
import importlib.metadata
import json
import logging
import os
import sys
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from fastmcp.server.middleware.timing import DetailedTimingMiddleware
from fastmcp.server.providers import FileSystemProvider
from fastmcp.server.transforms.search import RegexSearchTransform
from DrissionPage.common import Keys

from . import __version__
from .core import config, tool_metadata
from .core.lock import _rwlock
from .resources import resource_store
from .services import (
    bootstrap_table,
    browser_context,
    browser_session,
    devtools,
    filter_area,
    html_table,
    interaction,
    modal,
    network_record,
    observe,
    page_model,
    page_scan,
    role_sessions,
    session_auth,
    table_facade,
    vtable,
)
from .workflows import (
    flow_evidence,
    flow_ops,
    recipe_execution,
)

# Re-exported for unit-test monkeypatches and recipe helpers that still reach
# through the server module attribute path.
Keys = Keys

# 日志输出到 stderr（stdout 用于 MCP 协议帧，不可污染）
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("drissionpage-mcp")


@lifespan
async def app_lifespan(server: FastMCP):
    """Initialize optional heavyweight services when the MCP process starts."""
    ocr_warmed = False
    if config.WARMUP_OCR:
        try:
            session_auth.warmup_ocr()
            ocr_warmed = True
            logger.info("warmup_ocr: OCR model loaded")
        except Exception as exc:
            logger.warning(
                "warmup_ocr skipped (%s); first refresh_session may load model lazily",
                exc,
            )
    else:
        logger.info("warmup_ocr disabled for this server process")
    yield {"ocr_warmed": ocr_warmed}


# FastMCP 3.x：按业务域挂载独立组件 Provider，保留部署级 capability 边界。
_COMPONENTS_ROOT = Path(__file__).parent / "components"
_COMPONENT_RELOAD = config.COMPONENT_RELOAD


def _mount_component_dir(relative: str, probe_tools: tuple[str, ...]) -> FileSystemProvider | None:
    if not any(tool_metadata.is_component_needed(name) for name in probe_tools):
        return None
    return FileSystemProvider(_COMPONENTS_ROOT / relative, reload=_COMPONENT_RELOAD)


component_providers = [
    provider
    for provider in (
        _mount_component_dir(
            "core",
            (
                "connect",
                "check_session",
                "detect_page_family",
                "scan_layer_content",
                "detect_layer_msg",
                "activate_tool_groups",
            ),
        ),
        _mount_component_dir("roles", ("role_session_list",)),
        _mount_component_dir("filter", ("scan_filter_fields", "select_option")),
        _mount_component_dir(
            "storage",
            ("new_context", "list_contexts", "set_permission"),
        ),
        _mount_component_dir(
            "network",
            ("listen_start", "network_trace_start", "network_record_export"),
        ),
        _mount_component_dir(
            "observe",
            ("observe_start", "observe_wait", "observe_snapshot", "close_modal"),
        ),
        _mount_component_dir("vtable", ("query_table", "scan_table", "table_action")),
        _mount_component_dir(
            "workflow",
            ("flow_start", "run_test_cases", "generate_test_report"),
        ),
        _mount_component_dir(
            "page",
            ("capture_page_model", "scan_page_elements", "dom_tree"),
        ),
        _mount_component_dir(
            "interaction",
            ("click", "enter_module", "explore_action", "set_field_value"),
        ),
        _mount_component_dir(
            "devtools",
            ("run_js", "browser_tabs", "browser_list_caps"),
        ),
    )
    if provider is not None
]
_DISCOVERY_ALWAYS_VISIBLE = [
    # Connection and session lifecycle.
    "connect",
    "browser_tabs",
    "check_session",
    "refresh_session",
    "get_active_frame",
    "detect_page_family",
    # Daily page discovery and module navigation.
    "enter_module",
    "capture_page_model",
    "scan_page_elements",
    "scan_toolbar_actions",
    "scan_form_fields",
    "scan_filter_fields",
    "scan_table",
    "query_table",
    # Standard interaction and UI feedback loop.
    "explore_action",
    "click",
    "set_field_value",
    "set_date",
    "select_option",
    "table_action",
    "observe_snapshot",
    "close_modal",
    "screenshot",
    # Evidence flow controls needed for browser exploration tasks.
    "flow_start",
    "flow_capture_page_state",
    "flow_stop",
    "activate_tool_groups",
]
server_transforms = [tool_metadata.ToolMetadataTransform()]
if config.DISCOVERY_MODE == "search":
    server_transforms.append(
        RegexSearchTransform(
            max_results=config.SEARCH_MAX_RESULTS,
            always_visible=_DISCOVERY_ALWAYS_VISIBLE,
        )
    )
server_middleware = [
    ErrorHandlingMiddleware(
        logger=logger,
        include_traceback=config.INCLUDE_ERROR_TRACEBACK,
        transform_errors=True,
    ),
    ResponseLimitingMiddleware(
        max_size=config.RESPONSE_MAX_BYTES,
        tools=[
            "capture_page_model",
            "dom_tree",
            "get_all_table_data",
            "run_test_cases",
            "scan_page_elements",
            "scan_table",
            "explore_action",
        ],
    ),
]
if config.OBSERVABILITY:
    server_middleware.extend([
        StructuredLoggingMiddleware(
            logger=logger,
            include_payloads=False,
            include_payload_length=True,
            estimate_payload_tokens=True,
        ),
        DetailedTimingMiddleware(logger=logger),
    ])
mcp = FastMCP(
    "drissionpage-mcp",
    version=__version__,
    lifespan=app_lifespan,
    providers=component_providers,
    transforms=server_transforms,
    middleware=server_middleware,
)
# 应用环境变量驱动的可见性过滤（替代旧 _cap_aware_tool 注册时过滤）
tool_metadata.configure_visibility(mcp)






def read_synchronized(fn):
    """允许多个读操作并发，写操作进行时阻塞所有读。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        _rwlock.acquire_read()
        try:
            return fn(*args, **kwargs)
        finally:
            _rwlock.release_read()
    wrapper._du_access = "read"
    return wrapper


def write_synchronized(fn):
    """写操作互斥，且阻塞所有读操作。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        _rwlock.acquire_write()
        try:
            return fn(*args, **kwargs)
        finally:
            _rwlock.release_write()
    wrapper._du_access = "write"
    return wrapper


def _role_operation(operation, role_id: str, **kwargs) -> dict:
    """Run a role service operation for internal workflow dispatch."""
    try:
        return operation(role_id, **kwargs)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}


@write_synchronized
def role_session_open(role_id: str, proxy: str = None) -> dict:
    return _role_operation(role_sessions.open_role, role_id, proxy=proxy)


@write_synchronized
def role_session_login(role_id: str) -> dict:
    return _role_operation(role_sessions.login_role, role_id)


@write_synchronized
def role_session_start(role_id: str) -> dict:
    return _role_operation(role_sessions.start_role, role_id)


@write_synchronized
def role_session_activate(role_id: str) -> dict:
    return _role_operation(role_sessions.activate_role, role_id)


@read_synchronized
def role_session_list() -> dict:
    return {"ok": True, "roles": role_sessions.list_roles()}


@write_synchronized
def role_session_close(role_id: str) -> dict:
    return _role_operation(role_sessions.close_role, role_id)


@mcp.resource(
    "drissionpage-mcp://caps",
    name="drissionpage-mcp caps",
    title="drissionpage-mcp capability groups",
    description="Current enabled capability groups and the tools in each group.",
    mime_type="application/json",
)
def caps_resource() -> str:
    return json.dumps(tool_metadata.list_caps(), ensure_ascii=False, indent=2)


@mcp.resource(
    "drissionpage-mcp://context",
    name="drissionpage-mcp context",
    title="drissionpage-mcp runtime context",
    description="Runtime context that can be read without connecting to the browser.",
    mime_type="application/json",
)
def context_resource() -> str:
    packages = {}
    for package_name in ("fastmcp", "DrissionPage"):
        try:
            packages[package_name] = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            packages[package_name] = None
    return json.dumps({
        "ok": True,
        "server_version": __version__,
        "resource_context": resource_store.get_context(),
        "enabled_caps": sorted(tool_metadata.ENABLED_CAPS),
        "tool_profile": tool_metadata.ENABLED_PROFILE,
        "remote_address": config.REMOTE_ADDRESS,
        "remote_port": config.DEFAULT_PORT,
        "target_hint": config.DEFAULT_TARGET_HINT,
        "roles": role_sessions.list_roles(),
        "packages": packages,
    }, ensure_ascii=False, indent=2)


@mcp.resource(
    "drissionpage-mcp://resources",
    name="drissionpage-mcp evidence index",
    title="Saved evidence resource index",
    description="Index of saved files under HL_SHOT_DIR.",
    mime_type="application/json",
)
def resources_index() -> str:
    return json.dumps(resource_store.list_resources(), ensure_ascii=False, indent=2)


@mcp.resource(
    "drissionpage-mcp://resources/{resource_path}",
    name="drissionpage-mcp evidence file",
    title="Saved text evidence file",
    description="Read a UTF-8 text evidence file under HL_SHOT_DIR.",
    mime_type="text/plain",
)
def evidence_resource(resource_path: str) -> str:
    return resource_store.read_text_resource(resource_path)


# ==================== 连接与会话 ====================
# Public MCP tools live in components/core/session.py (FileSystemProvider).
# Keep callable helpers here for automation_recipe / internal workflow dispatch.


@write_synchronized
def connect(port: int | None = None, target_hint: str | None = None) -> dict:
    """连接 Chrome（内部/配方可调用；MCP 注册见 components.core.session）。"""
    tab = browser_session.connect(port, target_hint)
    return {"ok": True, "url": tab.url, "title": tab.title, "tabs": browser_session.list_tabs()}


def refresh_session() -> dict:
    """会话刷新（内部/配方可调用；MCP 注册见 components.core.session）。"""
    return session_auth.refresh_session()


def set_target_env(host_prefix: str) -> dict:
    """运行时切换目标环境（内部/配方可调用；MCP 注册见 components.core.session）。"""
    config.set_target_prefix(host_prefix)
    return {
        "ok": True,
        "host_prefix": host_prefix,
        "HL_URL": config.SCM_ADMIN_URL,
        "HL_BASE_URL": config.SCM_BASE_URL,
        "HL_LOGIN_PAGE": config.SCM_LOGIN_PAGE,
        "HL_COOKIE_DOMAIN": config.COOKIE_DOMAIN,
        "HL_ACCESS_DOMAIN": config.SCM_ACCESS_DOMAIN,
    }


def login_ocr() -> dict:
    """OCR 识别验证码 + HTTP 登录获取 cookie → 清缓存 → 注入 → 刷新。用于首次登录或完全失效。"""
    return session_auth.login_ocr()


@read_synchronized
def check_session() -> dict:
    """检测登录过期弹窗（内部/配方可调用；MCP 注册见 components.core.session）。"""
    return session_auth.check_session()


# ==================== 导航与 frame ====================
@write_synchronized
def expand_filter_area() -> dict:
    """展开筛选区：将弹窗模式切换为内联模式，并展开所有折叠筛选字段。
    使所有筛选字段、运算符、下拉选项暴露在 DOM 中，供后续 click/input 交互。
    若当前已是内联模式或已展开，则自动跳过。
    """
    return filter_area.expand_filter_area()


@read_synchronized
def get_active_frame() -> dict:
    """获取活动业务 iframe（内部/配方可调用；MCP 注册见 components.core.session）。"""
    fr = browser_session.get_active_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe，请先 enter_module"}
    return {
        "ok": True,
        "url": getattr(fr, "url", "") or "",
        "tab_name": browser_session.get_active_tab_name(),
    }



# ==================== 导航 / 交互（实现见 services.interaction）====================

@write_synchronized
def enter_module(menu_text: str, timeout: float = 8, expand_filter: bool = True) -> dict:
    """点击左侧菜单进入模块。"""
    return interaction.enter_module(menu_text, timeout=timeout, expand_filter=expand_filter)


@write_synchronized
def reset_to_initial(module_text: str, timeout: float = 20) -> dict:
    """重置到初始状态：关闭当前业务 tab → 重进模块。"""
    return interaction.reset_to_initial(module_text, timeout=timeout)


@write_synchronized
def set_field_value(field_name: str, value: str, in_frame: bool = True,
                    clear: bool = True, timeout: float = 5.0,
                    scope: str = "auto", select_index: int = 0) -> dict:
    """按可见标签写入文本字段。"""
    return interaction.set_field_value(
        field_name=field_name, value=value, in_frame=in_frame, clear=clear,
        timeout=timeout, scope=scope, select_index=select_index,
    )


@write_synchronized
def set_date(field_name: str, date: str = None, start_date: str = None,
             end_date: str = None, in_frame: bool = True, timeout: float = 8,
             select_index: int = 0, scope: str = "auto") -> dict:
    """统一设置日期字段。"""
    return interaction.set_date(
        field_name=field_name, date=date, start_date=start_date, end_date=end_date,
        in_frame=in_frame, timeout=timeout, select_index=select_index, scope=scope,
    )


@write_synchronized
def explore_action(action: Literal["click", "input", "set_date",
                                   "table_cell", "select_option", "press_key"] = "click",
                   target: dict = None,
                   locator: str = None, x: float = None, y: float = None,
                   row: int = 0, col: int = None, column_title: str = None, kind: str = "auto",
                   table_index: int = 0, icon_name: str = None, option_text: str = None,
                   field_name: str = None, text: str = None, date: str = None,
                   start_date: str = None, end_date: str = None,
                   key: str = None, modifiers: list[str] = None,
                   by_js: bool = False, in_frame: bool = True, timeout: float = 8,
                   signals: list[str] = None, listen_targets: str = None,
                   wait_spec: dict = None,
                   capture_before: bool = False, capture_after: bool = False,
                   include_snapshot: bool = None, detail: str = "summary",
                   expect: str = "auto", observe_mode: str = "auto",
                   clean_overlays: bool = True) -> dict:
    """动作探索封装。"""
    return interaction.explore_action(
        action=action, target=target, locator=locator, x=x, y=y, row=row, col=col,
        column_title=column_title, kind=kind, table_index=table_index, icon_name=icon_name,
        option_text=option_text, field_name=field_name, text=text, date=date,
        start_date=start_date, end_date=end_date, key=key, modifiers=modifiers,
        by_js=by_js, in_frame=in_frame, timeout=timeout, signals=signals,
        listen_targets=listen_targets, wait_spec=wait_spec, capture_before=capture_before,
        capture_after=capture_after, include_snapshot=include_snapshot, detail=detail,
        expect=expect, observe_mode=observe_mode, clean_overlays=clean_overlays,
    )


@read_synchronized
def find_elements(locator: str, in_frame: bool = True, timeout: float = 5) -> dict:
    return interaction.find_elements(locator=locator, in_frame=in_frame, timeout=timeout)


@read_synchronized
def get_element_coords(xpath: str, index: int = 1, timeout: float = 5) -> dict:
    return interaction.get_element_coords(xpath=xpath, index=index, timeout=timeout)


@read_synchronized
def find_static(locator: str = None, in_frame: bool = True, timeout: float = 5, index: int = 1) -> dict:
    return interaction.find_static(locator=locator, in_frame=in_frame, timeout=timeout, index=index)


@read_synchronized
def find_batch(locators: list[str], in_frame: bool = True, timeout: float = 5,
               any_one: bool = True, first_ele: bool = True) -> dict:
    return interaction.find_batch(
        locators=locators, in_frame=in_frame, timeout=timeout,
        any_one=any_one, first_ele=first_ele,
    )


@read_synchronized
def get_frame(locator, timeout: float = 5) -> dict:
    return interaction.get_frame(locator, timeout=timeout)


@write_synchronized
def click(locator: str, in_frame: bool = True, by_js: bool = False, timeout: float = 5,
          clean_overlays: bool = True) -> dict:
    return interaction.click(
        locator=locator, in_frame=in_frame, by_js=by_js, timeout=timeout,
        clean_overlays=clean_overlays,
    )


@write_synchronized
def click_xy(x: float, y: float, hover_first: bool = True, duration: float = 0.3,
             clean_overlays: bool = True, times: int = 1) -> dict:
    return interaction.click_xy(
        x=x, y=y, hover_first=hover_first, duration=duration,
        clean_overlays=clean_overlays, times=times,
    )


def select_date_range(field_name: str, start_date: str, end_date: str,
                      in_frame: bool = True, timeout: float = 8,
                      select_index: int = 0, scope: str = "auto") -> dict:
    return interaction.select_date_range(
        field_name, start_date, end_date, in_frame=in_frame, timeout=timeout,
        select_index=select_index, scope=scope,
    )


@write_synchronized
def input(locator: str, text: str, in_frame: bool = True, clear: bool = True, timeout: float = 5) -> dict:
    return interaction.input(
        locator=locator, text=text, in_frame=in_frame, clear=clear, timeout=timeout,
    )


@write_synchronized
def insert_text(text: str) -> dict:
    return interaction.insert_text(text)


@write_synchronized
def hover(locator: str = None, x: float = None, y: float = None, in_frame: bool = True,
          duration: float = 0.3, timeout: float = 5) -> dict:
    return interaction.hover(
        locator=locator, x=x, y=y, in_frame=in_frame, duration=duration, timeout=timeout,
    )


@read_synchronized
def screenshot(path: str = None, locator: str = None, in_frame: bool = True,
               timeout: float = 5) -> dict:
    return interaction.screenshot(
        path=path, locator=locator, in_frame=in_frame, timeout=timeout,
    )


def _resolve_and_click(locator: str, in_frame: bool = True, by_js: bool = False,
                       timeout: float = 5) -> dict:
    return interaction._resolve_and_click(
        locator=locator, in_frame=in_frame, by_js=by_js, timeout=timeout,
    )


def _press_key_raw(target, key: str, modifiers: list = None, interval: float = 0.01) -> dict:
    return interaction._press_key_raw(target, key, modifiers=modifiers, interval=interval)


def _official_key(value: str):
    return interaction._official_key(value)


def _short_click_timeout(timeout: float, default: float = 2.0, upper: float = 2.0) -> float:
    return interaction._short_click_timeout(timeout, default=default, upper=upper)


def _xpath_literal(value: str) -> str:
    return interaction._xpath_literal(value)


def _normalize_date_value(value: str) -> dict:
    return interaction._normalize_date_value(value)


def _click_field_raw(field_name: str, in_frame: bool = True, timeout: float = 5.0,
                     scope: str = "auto", select_index: int = 0) -> dict:
    return interaction._click_field_raw(
        field_name=field_name, in_frame=in_frame, timeout=timeout,
        scope=scope, select_index=select_index,
    )


def _date_field_contexts(in_frame: bool, scope: str):
    return interaction._date_field_contexts(in_frame, scope)


def _resolve_date_picker(field_name: str, in_frame: bool = True, scope: str = "auto",
                         select_index: int = 0, timeout: float = 5.0):
    return interaction._resolve_date_picker(
        field_name=field_name, in_frame=in_frame, scope=scope,
        select_index=select_index, timeout=timeout,
    )


def _compact_text(text: str) -> str:
    return interaction._compact_text(text)


def _date_picker_values(picker) -> list[str]:
    return interaction._date_picker_values(picker)


def _open_date_calendar(resolved: dict, timeout: float):
    return interaction._open_date_calendar(resolved, timeout)


def _calendar_snapshot(target, target_date_slash: str = "") -> dict:
    return interaction._calendar_snapshot(target, target_date_slash)


def _select_calendar_date(cal, normalized: dict, deadline: float) -> dict:
    return interaction._select_calendar_date(cal, normalized, deadline)


def _find_calendar_root(target, timeout: float):
    return interaction._find_calendar_root(target, timeout)


def _clickable_text_locators(raw_text: str) -> list[str]:
    return interaction._clickable_text_locators(raw_text)


def _extract_text_locator(locator: str) -> str | None:
    return interaction._extract_text_locator(locator)


@write_synchronized
def scan_filter_fields() -> dict:
    """扫描筛选区（内部/配方；MCP 注册见 components.filter）。"""
    return filter_area.scan_filter_fields()


@read_synchronized

# ==================== page scan (services.page_scan) ====================

@read_synchronized
def dom_tree(selector: str = "", max_depth: int = 6, max_children: int = 50,
             text: bool = False, text_limit: int = 100, show_hidden: bool = False,
             filename: str = None, save_path: str = "", save_format: str = "yml",
             max_chars: int = 8000) -> dict:
    return page_scan.dom_tree(
        selector=selector, max_depth=max_depth, max_children=max_children, text=text,
        text_limit=text_limit, show_hidden=show_hidden, filename=filename,
        save_path=save_path, save_format=save_format, max_chars=max_chars,
    )


def _attr(ele, name: str):
    return page_scan._attr(ele, name)


def _element_text(ele) -> str:
    return page_scan._element_text(ele)


def _element_locator_candidates(ele) -> list[str]:
    return page_scan._element_locator_candidates(ele)


def _scan_controls_in_context(target, frame_label: str, start_seq: int, max_items: int):
    return page_scan._scan_controls_in_context(target, frame_label, start_seq, max_items)


@read_synchronized
def scan_page_elements(include_iframe: bool = True, max_items: int = 200, filename: str = None) -> dict:
    return page_scan.scan_page_elements(
        include_iframe=include_iframe, max_items=max_items, filename=filename,
    )


@write_synchronized
def capture_page_model(include_filters: bool = True, include_tables: bool = True,
                       include_table_data: bool = True, max_table_rows: int = 80,
                       max_elements: int = 120, filename: str = None) -> dict:
    return page_scan.capture_page_model(
        include_filters=include_filters, include_tables=include_tables,
        include_table_data=include_table_data, max_table_rows=max_table_rows,
        max_elements=max_elements, filename=filename,
    )


@read_synchronized
def scan_toolbar_actions(scope: str = "page", in_frame: bool = True, max_items: int = 120) -> dict:
    return page_scan.scan_toolbar_actions(scope=scope, in_frame=in_frame, max_items=max_items)


@read_synchronized
def scan_form_fields(scope: str = "page", include_hidden: bool = False,
                     in_frame: bool = True, max_fields: int = 200) -> dict:
    return page_scan.scan_form_fields(
        scope=scope, include_hidden=include_hidden, in_frame=in_frame, max_fields=max_fields,
    )


@read_synchronized
def scan_floats(only_visible: bool = True, include_table_data: bool = True) -> dict:
    return page_scan.scan_floats(only_visible=only_visible, include_table_data=include_table_data)


@read_synchronized
def scan_modal(max_items: int = 20) -> dict:
    return page_scan.scan_modal(max_items=max_items)


@read_synchronized
def scan_drawer(max_items: int = 20) -> dict:
    return page_scan.scan_drawer(max_items=max_items)


@read_synchronized
def scan_pagination(in_frame: bool = True) -> dict:
    return page_scan.scan_pagination(in_frame=in_frame)


@write_synchronized
def select_option(field_name: str, option_text: str, select_index: int = 0,
                  scope: str = "auto", timeout: float = 5.0) -> dict:
    return page_scan.select_option(
        field_name=field_name, option_text=option_text, select_index=select_index,
        scope=scope, timeout=timeout,
    )


@write_synchronized
def get_all_table_data(kind: str = "auto", table_index: int = 0, max_pages: int = 1,
                       max_rows: int = 1000, max_columns: int = 50,
                       raw: bool = False, filename: str = None) -> dict:
    return page_scan.get_all_table_data(
        kind=kind, table_index=table_index, max_pages=max_pages, max_rows=max_rows,
        max_columns=max_columns, raw=raw, filename=filename,
    )


def _click_table_cell_raw(row: int, col: int = None, column_title: str = None,
                          kind: str = "auto", table_index: int = 0,
                          icon_name: str = None, hover_first: bool = True,
                          duration: float = 0.3, double_click: bool = False) -> dict:
    """Undecorated table click helper for aggregate tools."""
    return table_facade._click_table_cell_raw(
        row=row, col=col, column_title=column_title, kind=kind,
        table_index=table_index, icon_name=icon_name, hover_first=hover_first,
        duration=duration, double_click=double_click,
    )


@write_synchronized
def flow_start(module: str, flow_name: str = "exploration", capture_screenshots: bool = True,
               scenario_type: str = "功能测试", risk_type: str = "正常路径",
               destructive: bool = False, cleanup_strategy: str = "",
               screenshot_policy: str = "on_failure") -> dict:
    """开始记录真实业务流证据；后续 explore_action 自动关联元素、反馈、接口和截图。"""
    return flow_ops.flow_start(
        module, flow_name=flow_name, capture_screenshots=capture_screenshots,
        scenario_type=scenario_type, risk_type=risk_type,
        destructive=destructive, cleanup_strategy=cleanup_strategy,
        screenshot_policy=screenshot_policy,
    )


@read_synchronized
def flow_status() -> dict:
    """返回当前或最近一次业务流证据的状态与步骤数量。"""
    return flow_ops.flow_status()


@write_synchronized
def flow_capture_page_state(label: str = "initial", include_filters: bool = True,
                            include_tables: bool = True, max_table_rows: int = 30) -> dict:
    """采集当前 iframe 的元素、DOM、表单、浮层和表格资产，并写入活动业务流证据。"""
    return flow_ops.flow_capture_page_state(
        label=label, include_filters=include_filters,
        include_tables=include_tables, max_table_rows=max_table_rows,
    )


@write_synchronized
def flow_stop(filename: str = None, cleanup_from_sequence: int = None) -> dict:
    """结束并保存业务流；破坏性流用 cleanup_from_sequence 标记必执行清理段。"""
    return flow_ops.flow_stop(filename, cleanup_from_sequence=cleanup_from_sequence)



# ==================== workflow / recipe (services.workflows.recipe_execution) ====================

def _read_json_resource(filename: str):
    return recipe_execution._read_json_resource(filename)


def _safe_artifact_segment(value: str, fallback: str = "default") -> str:
    return recipe_execution._safe_artifact_segment(value, fallback)


def _artifact_root() -> str:
    return recipe_execution._artifact_root()


def _module_artifact_name(module_info: dict | None) -> str:
    return recipe_execution._module_artifact_name(module_info)


def _resolve_artifact_path(filename: str | None, category: str, module_info: dict | None,
                           default_name: str) -> str:
    return recipe_execution._resolve_artifact_path(filename, category, module_info, default_name)


def _report_bundle_path(filename: str | None, module_info: dict | None, *args, **kwargs):
    return recipe_execution._report_bundle_path(filename, module_info, *args, **kwargs)


def _bundle_report_assets(execution: dict, execution_file: str, bundle_dir: str) -> dict:
    return recipe_execution._bundle_report_assets(execution, execution_file, bundle_dir)


def _next_case_id_start(case_dir: str, exclude_path: str = None) -> dict:
    return recipe_execution._next_case_id_start(case_dir, exclude_path)


@write_synchronized
def generate_test_cases_from_flow(flow_file: str, module_info: dict = None,
                                  filename: str = None) -> dict:
    return recipe_execution.generate_test_cases_from_flow(
        flow_file, module_info=module_info, filename=filename,
    )


@write_synchronized
def combine_test_case_files(case_files: list[str], filename: str = None,
                            module_info: dict = None, exclude_case_ids: list[str] = None,
                            exclude_known_defects: bool = False) -> dict:
    return recipe_execution.combine_test_case_files(
        case_files, filename=filename, module_info=module_info,
        exclude_case_ids=exclude_case_ids, exclude_known_defects=exclude_known_defects,
    )


def _recipe_values() -> dict:
    return recipe_execution._recipe_values()


def _reset_recipe_context() -> None:
    recipe_execution._reset_recipe_context()


def _recipe_allows_destructive() -> bool:
    return recipe_execution._recipe_allows_destructive()


def _recipe_requires_native_actions() -> bool:
    return recipe_execution._recipe_requires_native_actions()


def _recipe_ref_value(path: str):
    return recipe_execution._recipe_ref_value(path)


def _resolve_recipe_refs(value, _depth: int = 0):
    return recipe_execution._resolve_recipe_refs(value, _depth=_depth)


def _recipe_element_click(locator: str, in_frame: bool = True, timeout: float = 5,
                          double_click: bool = False) -> dict:
    return recipe_execution._recipe_element_click(
        locator, in_frame=in_frame, timeout=timeout, double_click=double_click,
    )


def _recipe_double_click(locator: str, in_frame: bool = True, timeout: float = 5) -> dict:
    return recipe_execution._recipe_double_click(locator, in_frame=in_frame, timeout=timeout)


def _run_recipe_action(action: str, args: dict) -> dict:
    return recipe_execution._run_recipe_action(action, args)


def _http_success(status) -> bool:
    return recipe_execution._http_success(status)


def _response_flag(value) -> bool:
    return recipe_execution._response_flag(value)


def _business_response_success(body) -> bool:
    return recipe_execution._business_response_success(body)


def _wait_query_table(frame, timeout: float = 10):
    return recipe_execution._wait_query_table(frame, timeout=timeout)


def _query_filter(timeout: float = 10, listen_targets: str = "gateway") -> dict:
    return recipe_execution._query_filter(timeout=timeout, listen_targets=listen_targets)


def _verify_filter_query(filters: list[dict], timeout: float = 10, **kwargs) -> dict:
    return recipe_execution._verify_filter_query(filters, timeout=timeout, **kwargs)


def _execution_module_text(payload: dict) -> str:
    return recipe_execution._execution_module_text(payload)


def _browser_connection_gate() -> dict:
    return recipe_execution._browser_connection_gate()


def _browser_ready_gate(module_text: str) -> dict:
    return recipe_execution._browser_ready_gate(module_text)


@write_synchronized
def run_test_cases(case_file: str, filename: str = None) -> dict:
    return recipe_execution.run_test_cases(case_file, filename=filename)


@write_synchronized
def generate_test_report(execution_file: str, coverage_file: str = None,
                         baseline_file: str = None, filename: str = None,
                         defects_file: str = None,
                         supplemental_execution_files: list[str] = None) -> dict:
    return recipe_execution.generate_test_report(
        execution_file, coverage_file=coverage_file, baseline_file=baseline_file,
        filename=filename, defects_file=defects_file,
        supplemental_execution_files=supplemental_execution_files,
    )


@read_synchronized
def compare_regression_report(execution_file: str, baseline_file: str) -> dict:
    return recipe_execution.compare_regression_report(execution_file, baseline_file)


def _action_disabled_diff(before: dict, after: dict) -> list:
    return table_facade._action_disabled_diff(before, after)


@write_synchronized
def scan_action_availability_by_selection(row: int = 0, col: int = 0,
                                          kind: str = "auto", table_index: int = 0,
                                          select_row: bool = True,
                                          wait_after_click: float = 0.3) -> dict:
    """扫描选中表格行前后工具栏按钮禁用态变化，用于批量/行选择场景设计。"""
    return table_facade.scan_action_availability_by_selection(
        row=row, col=col, kind=kind, table_index=table_index,
        select_row=select_row, wait_after_click=wait_after_click,
    )


def dom_overview(max_buttons: int = 100) -> dict:
    """页面俯瞰：顶部页签(含选中态) + 可见按钮文本(含 disabled)。"""
    return page_scan.dom_overview(max_buttons=max_buttons)





# ==================== table facade thin wrappers ====================

def _normalize_table_kind(kind: str) -> str:
    return table_facade._normalize_table_kind(kind)

def _tag_table_result(kind: str, result: dict) -> dict:
    return table_facade._tag_table_result(kind, result)

def _auto_table_scan_order() -> list[str]:
    return table_facade._auto_table_scan_order()

def _find_vtable_col(column_title: str, max_col: int = 100):
    return table_facade._find_vtable_col(column_title, max_col)

def _pre_click_cleanup(clean_overlays: bool = True):
    return table_facade.pre_click_cleanup(clean_overlays)

def _attach_cleanup(result: dict, cleanup: dict = None) -> dict:
    return table_facade.attach_cleanup(result, cleanup)

@read_synchronized
def scan_table(kind: str = "auto", max_col: int = 50, table_index: int = 0, filename: str = None) -> dict:
    return table_facade.scan_table(kind=kind, max_col=max_col, table_index=table_index, filename=filename)

@read_synchronized
def get_table_values(column_title: str, kind: str = "auto", raw: bool = False, table_index: int = 0, filename: str = None) -> dict:
    return table_facade.get_table_values(column_title=column_title, kind=kind, raw=raw, table_index=table_index, filename=filename)

@read_synchronized
def find_vtable_row(column_title: str, value: str, raw: bool = False, match: str = "equals", header_rows: int = None, timeout: float = 0) -> dict:
    return table_facade.find_vtable_row(column_title=column_title, value=value, raw=raw, match=match, header_rows=header_rows, timeout=timeout)

@read_synchronized
def count_vtable_rows(column_title: str, value: str, raw: bool = False, match: str = "equals", expected_count: int = None, timeout: float = 0) -> dict:
    return table_facade.count_vtable_rows(column_title=column_title, value=value, raw=raw, match=match, expected_count=expected_count, timeout=timeout)

@read_synchronized
def get_vtable_row_values(key_column: str, key_value: str, column_titles: list[str], raw: bool = False, match: str = "equals", timeout: float = 0) -> dict:
    return table_facade.get_vtable_row_values(key_column=key_column, key_value=key_value, column_titles=column_titles, raw=raw, match=match, timeout=timeout)

@read_synchronized
def get_table_data(kind: str = "auto", table_index: int = 0, filename: str = None) -> dict:
    return table_facade.get_table_data(kind=kind, table_index=table_index, filename=filename)

@read_synchronized
def get_vtable_cell_render_info(row: int, col: int = None, column_title: str = None, detail: str = "summary") -> dict:
    return table_facade.get_vtable_cell_render_info(row=row, col=col, column_title=column_title, detail=detail)

@read_synchronized
def get_vtable_cell_icons(row: int, col: int = None, column_title: str = None, icon_name: str = None, detail: str = "summary") -> dict:
    return table_facade.get_vtable_cell_icons(row=row, col=col, column_title=column_title, icon_name=icon_name, detail=detail)

@write_synchronized
def vtable_action(action: str = "click", row: int = 0, col: int = None, column_title: str = None, target: str = "cell", icon_name: str = None, icon_index: int = None, hover_first: bool = True, duration: float = 0.3, drag_to_x: float = None, drag_to_y: float = None, drag_by_x: float = None, drag_by_y: float = None, clean_overlays: bool = True, source_x: float = None, source_y: float = None) -> dict:
    return table_facade.vtable_action(action=action, row=row, col=col, column_title=column_title, target=target, icon_name=icon_name, icon_index=icon_index, hover_first=hover_first, duration=duration, drag_to_x=drag_to_x, drag_to_y=drag_to_y, drag_by_x=drag_by_x, drag_by_y=drag_by_y, clean_overlays=clean_overlays, source_x=source_x, source_y=source_y)

@write_synchronized
def click_table_cell(row: int, col: int = None, column_title: str = None, kind: str = "auto", table_index: int = 0, icon_name: str = None, hover_first: bool = True, duration: float = 0.3, double_click: bool = False, clean_overlays: bool = True) -> dict:
    return table_facade.click_table_cell(row=row, col=col, column_title=column_title, kind=kind, table_index=table_index, icon_name=icon_name, hover_first=hover_first, duration=duration, double_click=double_click, clean_overlays=clean_overlays)

@write_synchronized
def hover_table_cell(row: int, col: int = None, column_title: str = None, kind: str = "auto", table_index: int = 0, duration: float = 0.3) -> dict:
    return table_facade.hover_table_cell(row=row, col=col, column_title=column_title, kind=kind, table_index=table_index, duration=duration)

@write_synchronized
def resize_table_column(width: int, col: int = None, column_title: str = None, kind: str = "vtable") -> dict:
    return table_facade.resize_table_column(width=width, col=col, column_title=column_title, kind=kind)

@write_synchronized
def reorder_vtable_column(column_title: str = None, col: int = None, target_column_title: str = None, target_col: int = None, position: Literal["after", "before"] = "after") -> dict:
    return table_facade.reorder_vtable_column(column_title=column_title, col=col, target_column_title=target_column_title, target_col=target_col, position=position)

@read_synchronized
def query_table(operation: Literal["values", "data", "find", "count", "row"] = "values", column_title: str = None, value: str = None, key_column: str = None, key_value: str = None, column_titles: list[str] = None, kind: Literal["auto", "html", "vtable", "bootstrap"] = "auto", table_index: int = 0, raw: bool = False, match: Literal["equals", "contains"] = "equals", expected_count: int = None, timeout: float = 0, filename: str = None) -> dict:
    return table_facade.query_table(operation=operation, column_title=column_title, value=value, key_column=key_column, key_value=key_value, column_titles=column_titles, kind=kind, table_index=table_index, raw=raw, match=match, expected_count=expected_count, timeout=timeout, filename=filename)

@read_synchronized
def inspect_table_cell(row: int, col: int = None, column_title: str = None, aspect: Literal["all", "render", "icons"] = "all", icon_name: str = None, detail: str = "summary") -> dict:
    return table_facade.inspect_table_cell(row=row, col=col, column_title=column_title, aspect=aspect, icon_name=icon_name, detail=detail)

@write_synchronized
def table_action(action: Literal["click", "double_click", "hover", "drag", "resize"] = "click", row: int = 0, col: int = None, column_title: str = None, kind: Literal["auto", "html", "vtable", "bootstrap"] = "auto", table_index: int = 0, target: Literal["cell", "header", "header-icon", "cell-icon"] = "cell", icon_name: str = None, icon_index: int = None, width: int = None, hover_first: bool = True, duration: float = 0.3, drag_to_x: float = None, drag_to_y: float = None, drag_by_x: float = None, drag_by_y: float = None, clean_overlays: bool = True, signals: list[str] = None, listen_targets: str = None, timeout: float = 8, include_snapshot: bool = True, detail: str = "summary", drag_from_x: float = None, drag_from_y: float = None) -> dict:
    return table_facade.table_action(action=action, row=row, col=col, column_title=column_title, kind=kind, table_index=table_index, target=target, icon_name=icon_name, icon_index=icon_index, width=width, hover_first=hover_first, duration=duration, drag_to_x=drag_to_x, drag_to_y=drag_to_y, drag_by_x=drag_by_x, drag_by_y=drag_by_y, clean_overlays=clean_overlays, signals=signals, listen_targets=listen_targets, timeout=timeout, include_snapshot=include_snapshot, detail=detail, drag_from_x=drag_from_x, drag_from_y=drag_from_y, native_wait=_recipe_requires_native_actions())


# ==================== devtools (services.devtools) ====================

@read_synchronized
def run_js(script: str, in_frame: bool = True, max_chars: int = 4000) -> dict:
    return devtools.run_js(script, in_frame=in_frame, max_chars=max_chars)


@write_synchronized
def mouse_trail(on: bool = True) -> dict:
    return devtools.mouse_trail(on)


@write_synchronized
def click_to_download(locator: str, save_path: str = None, rename: str = None,
                      suffix: str = None, new_tab: bool = False,
                      by_js: bool = False, in_frame: bool = True,
                      timeout: float = 30) -> dict:
    return devtools.click_to_download(
        locator, save_path=save_path, rename=rename, suffix=suffix, new_tab=new_tab,
        by_js=by_js, in_frame=in_frame, timeout=timeout,
    )


@write_synchronized
def click_to_upload(locator: str, file_paths: str, by_js: bool = False,
                    in_frame: bool = True, timeout: float = 10) -> dict:
    return devtools.click_to_upload(
        locator, file_paths, by_js=by_js, in_frame=in_frame, timeout=timeout,
    )


@write_synchronized
def download_by_browser(url: str, save_path: str = None, rename: str = None,
                        suffix: str = None, timeout: float = 30,
                        file_exists: str = "rename") -> dict:
    return devtools.download_by_browser(
        url, save_path=save_path, rename=rename, suffix=suffix,
        timeout=timeout, file_exists=file_exists,
    )


@read_synchronized
def browser_list_caps() -> dict:
    return devtools.browser_list_caps()


@write_synchronized
def browser_scroll(direction: str = "down", pixel: int = 300, locator: str = None,
                   x: int = None, y: int = None, timeout: float = 5) -> dict:
    return devtools.browser_scroll(
        direction=direction, pixel=pixel, locator=locator, x=x, y=y, timeout=timeout,
    )


@write_synchronized
def browser_tabs(action: str = "list", index: int = None, url: str = None) -> dict:
    return devtools.browser_tabs(action=action, index=index, url=url)


@write_synchronized
def browser_save_pdf(path: str = None, filename: str = None) -> dict:
    return devtools.browser_save_pdf(path=path, filename=filename)


def _console_arg_text(arg):
    return devtools._console_arg_text(arg)


def _console_message_to_dict(message) -> dict:
    return devtools._console_message_to_dict(message)


@write_synchronized
def browser_console_messages(level: str = "", timeout: float = 0.0, start: bool = True,
                             clear: bool = False, stop: bool = False,
                             max_messages: int = 50, filename: str = None) -> dict:
    return devtools.browser_console_messages(
        level=level, timeout=timeout, start=start, clear=clear, stop=stop,
        max_messages=max_messages, filename=filename,
    )


@write_synchronized
def browser_press_key(key: str, modifiers: list[str] = None, interval: float = 0.01) -> dict:
    return devtools.browser_press_key(key=key, modifiers=modifiers, interval=interval)


@read_synchronized
def browser_get_element_state(locator: str, state: str = None) -> dict:
    return devtools.browser_get_element_state(locator=locator, state=state)



# ==================== network / storage thin wrappers ====================

@write_synchronized
def listen_start(targets, method: str = None) -> dict:
    return network_record.listen_start(targets, method=method)

@write_synchronized
def listen_wait(count: int = 1, timeout: float = 10, fit_count: bool = False) -> dict:
    return network_record.listen_wait(count=count, timeout=timeout, fit_count=fit_count)

@write_synchronized
def listen_stop() -> dict:
    return network_record.listen_stop()

@write_synchronized
def network_record_start(targets=None, method: str = None) -> dict:
    return network_record.start(targets=targets, method=method)

@write_synchronized
def network_record_stop(timeout: float = 3.0, max_packets: int = 50, fit_count: bool = False, max_body_chars: int = 12000) -> dict:
    return network_record.stop(timeout=timeout, max_packets=max_packets, fit_count=fit_count, max_body_chars=max_body_chars)

@write_synchronized
def network_trace_start(targets=None, method: str = None) -> dict:
    return network_record.start(targets=targets, method=method)

@write_synchronized
def network_trace_stop(timeout: float = 3.0, max_packets: int = 50, fit_count: bool = False, max_body_chars: int = 12000) -> dict:
    return network_record.stop(timeout=timeout, max_packets=max_packets, fit_count=fit_count, max_body_chars=max_body_chars)

@read_synchronized
def network_record_export(filename: str = None) -> dict:
    return network_record.export(filename=filename)

@write_synchronized
def listen_ws_start(targets: str = None) -> dict:
    return network_record.listen_ws_start(targets)

@write_synchronized
def listen_ws_wait(count: int = 1, timeout: float = 10, fit_count: bool = False) -> dict:
    return network_record.listen_ws_wait(count=count, timeout=timeout, fit_count=fit_count)

@write_synchronized
def new_context(proxy: str = None) -> dict:
    return browser_context.new_context(proxy=proxy)


@write_synchronized
def switch_context(context_id: int) -> dict:
    return browser_context.switch_context(context_id)


@write_synchronized
def close_context(context_id: int) -> dict:
    return browser_context.close_context(context_id)


@read_synchronized
def list_contexts() -> dict:
    return browser_context.list_contexts()


@write_synchronized
def set_permission(perm: str, allow: bool = True) -> dict:
    return browser_context.set_permission(perm, allow=allow)

@write_synchronized
def close_modal() -> dict:
    return modal.close_modal()

@write_synchronized
def observe_start(signals: list[str] = None, listen_targets: str = None) -> dict:
    return observe.observe_start(signals=signals, listen_targets=listen_targets)

@write_synchronized
def observe_wait(timeout: float = 8, poll_interval: float = 0.12, include_snapshot: bool = True) -> dict:
    return observe.observe_wait(timeout=timeout, poll_interval=poll_interval, include_snapshot=include_snapshot)

@read_synchronized
def observe_snapshot(only_visible: bool = True, include_table_data: bool = False, detail: str = "summary") -> dict:
    return observe.observe_snapshot(only_visible=only_visible, include_table_data=include_table_data, detail=detail)


def main():
    logger.info(
        "Starting drissionpage-mcp server version=%s profile=%s enabled_caps=%s",
        __version__,
        tool_metadata.ENABLED_PROFILE,
        sorted(tool_metadata.ENABLED_CAPS),
    )
    # FastMCP 3：stdio 为默认传输；host/port 等若将来需要 HTTP 应传给 run()
    mcp.run()


if __name__ == "__main__":
    main()
