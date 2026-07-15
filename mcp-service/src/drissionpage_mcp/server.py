"""drissionpage-mcp 服务器：把 DrissionPage 浏览器自动化封装成结构化 MCP 工具。

供 AI 驱动的 UI 测试技能调用。浏览器原语(连接/扫描/点击/输入/截图)、
VTable 工具(内部 frame.run_js 注入 bundled JS)、会话维持、弹窗检测、网络监听。

启动：uv run --package drissionpage-mcp -m drissionpage_mcp  (stdio 传输)
"""
import asyncio
import functools
import importlib.metadata
import threading
import json
import math
import logging
import os
import re
import shutil
import sys
import time
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.server.lifespan import lifespan
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from fastmcp.server.middleware.timing import DetailedTimingMiddleware
from fastmcp.server.providers import FileSystemProvider
from fastmcp.server.transforms.search import RegexSearchTransform
from DrissionPage.common import Keys

from . import __version__
from .core import caps, config, ui_contract
from .core.lock import _rwlock
from .resources import resource_store
from .core import page_family
from .services import (
    bootstrap_table,
    browser_session,
    filter_area,
    html_table,
    modal,
    network_record,
    observe,
    page_model,
    role_sessions,
    session_auth,
    vtable,
)
from .workflows import (
    flow_evidence,
    test_execution,
    test_reporting,
    testcase_generation,
)

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
core_component_provider = FileSystemProvider(
    Path(__file__).parent / "components" / "core",
    reload=config.COMPONENT_RELOAD,
)
roles_component_provider = FileSystemProvider(
    Path(__file__).parent / "components" / "roles",
    reload=config.COMPONENT_RELOAD,
)
component_providers = []
if caps.is_tool_enabled("detect_page_family"):
    component_providers.append(core_component_provider)
if caps.is_tool_enabled("role_session_list"):
    component_providers.append(roles_component_provider)
_DISCOVERY_ALWAYS_VISIBLE = [
    "connect",
    "browser_tabs",
    "check_session",
    "refresh_session",
    "get_active_frame",
    "detect_page_family",
    "capture_page_model",
    "explore_action",
    "activate_tool_groups",
]
server_transforms = []
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
_fastmcp_tool = mcp.tool


_READ_ONLY_TOOLS = {
    "browser_get_element_state",
    "browser_list_caps",
    "check_session",
    "detect_page_family",
    "scan_layer_content",
    "find_batch",
    "find_elements",
    "get_active_frame",
    "get_element_coords",
    "list_contexts",
    "observe_snapshot",
    "query_table",
    "inspect_table_cell",
    "role_session_list",
    "scan_drawer",
    "scan_floats",
    "scan_form_fields",
    "scan_modal",
    "scan_pagination",
    "scan_toolbar_actions",
    "flow_status",
}

_ADDITIVE_TOOLS = {
    "browser_console_messages",
    "browser_save_pdf",
    "capture_page_model",
    "connect",
    "dom_tree",
    "network_record_export",
    "network_trace_start",
    "network_trace_stop",
    "role_session_activate",
    "role_session_login",
    "role_session_open",
    "role_session_start",
    "listen_wait",
    "listen_ws_wait",
    "scan_page_elements",
    "scan_table",
    "screenshot",
    "generate_test_cases_from_flow",
    "generate_test_report",
    "compare_regression_report",
}

_IDEMPOTENT_WRITE_TOOLS = {
    "check_session",
    "connect",
    "get_active_frame",
    "role_session_activate",
    "browser_list_caps",
}

_TOOL_TIMEOUTS = {
    "listen_wait": config.WAIT_TOOL_TIMEOUT,
    "listen_ws_wait": config.WAIT_TOOL_TIMEOUT,
    "observe_wait": config.WAIT_TOOL_TIMEOUT,
}


def _tool_annotations_for(tool_name: str, fn) -> dict[str, bool]:
    """Infer MCP tool annotations from the local synchronization contract."""
    if tool_name in _READ_ONLY_TOOLS:
        return {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }

    if tool_name in _ADDITIVE_TOOLS:
        return {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": tool_name in _IDEMPOTENT_WRITE_TOOLS,
            "openWorldHint": True,
        }

    access = getattr(fn, "_du_access", "")
    if access == "read":
        return {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }

    return {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": tool_name in _IDEMPOTENT_WRITE_TOOLS,
        "openWorldHint": True,
    }


def _cap_aware_tool(*args, **kwargs):
    """Register enabled tools with FastMCP discovery and risk tags."""
    def register(fn):
        explicit_name = kwargs.get("name")
        if args and isinstance(args[0], str):
            explicit_name = args[0]
        tool_name = explicit_name or getattr(fn, "__name__", "")
        if caps.is_tool_enabled(tool_name):
            tool_kwargs = dict(kwargs)
            if tool_kwargs.get("timeout") is None and tool_name in _TOOL_TIMEOUTS:
                tool_kwargs["timeout"] = _TOOL_TIMEOUTS[tool_name]
            if tool_kwargs.get("annotations") is None:
                tool_kwargs["annotations"] = _tool_annotations_for(tool_name, fn)
            annotations = tool_kwargs["annotations"]
            tags = set(tool_kwargs.get("tags") or ())
            tags.update(caps.get_tool_tags(tool_name))
            if isinstance(annotations, dict):
                if annotations.get("readOnlyHint"):
                    tags.add("risk:read")
                elif annotations.get("destructiveHint"):
                    tags.add("risk:destructive")
                else:
                    tags.add("risk:write")
            tool_kwargs["tags"] = tags
            decorator = _fastmcp_tool(*args, **tool_kwargs)
            return decorator(fn)
        logger.debug("Skipping MCP tool %s; disabled by MCP profile/capabilities", tool_name)
        return fn

    return register


mcp.tool = _cap_aware_tool




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
    return json.dumps(caps.list_caps(), ensure_ascii=False, indent=2)


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
        "enabled_caps": sorted(caps.ENABLED_CAPS),
        "tool_profile": caps.ENABLED_PROFILE,
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

@mcp.tool()
@write_synchronized
def connect(port: int = config.DEFAULT_PORT, target_hint: str = config.DEFAULT_TARGET_HINT) -> dict:
    """连接 Chrome。先检查 port 上是否已有 Chrome 实例，有则接管；无则根据
    dp_configs.ini 配置自动启动新实例。返回当前 url/title 与所有 tab 列表。"""
    tab = browser_session.connect(port, target_hint)
    return {"ok": True, "url": tab.url, "title": tab.title, "tabs": browser_session.list_tabs()}


@mcp.tool()
def refresh_session() -> dict:
    """会话过期时直接触发 OCR 登录 → 注入新 cookie → 刷新页面。不再依赖缓存。"""
    return session_auth.refresh_session()


@mcp.tool()
def set_target_env(host_prefix: str) -> dict:
    """运行时切换目标环境（无需重启 MCP 服务）。

    只需提供 host 前缀，例如 'demo15-scm'，系统自动推导 5 个关联配置
    (HL_URL / HL_BASE_URL / HL_LOGIN_PAGE / HL_COOKIE_DOMAIN / HL_ACCESS_DOMAIN)。
    调用后即刻生效，后续 connect / refresh_session 使用新环境。
    """
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


@mcp.tool()
@read_synchronized
def check_session() -> dict:
    """检测 top 层是否出现『登录过期』系统确认弹窗。返回 {expired, detail}。"""
    return session_auth.check_session()


# ==================== 导航与 frame ====================
@write_synchronized
def expand_filter_area() -> dict:
    """展开筛选区：将弹窗模式切换为内联模式，并展开所有折叠筛选字段。
    使所有筛选字段、运算符、下拉选项暴露在 DOM 中，供后续 click/input 交互。
    若当前已是内联模式或已展开，则自动跳过。
    """
    return filter_area.expand_filter_area()



@mcp.tool()
@write_synchronized
def enter_module(menu_text: str, timeout: float = 8, expand_filter: bool = True) -> dict:
    """点击左侧菜单进入模块（按菜单文字匹配），并等待业务 iframe 导航完成。

    优先用 DrissionPage 原生 click 模拟鼠标点击；
    当元素不可见（无位置/大小）时自动降级为 JS click。
    """
    tab = browser_session.get_tab()
    old_fr = browser_session.get_active_frame(tab)
    old_url = old_fr.url if old_fr else None

    # 1. 点击菜单项
    ele = tab.ele(f'text:{menu_text}', timeout=3)
    if not ele:
        # 降级：Python 查找匹配菜单项并点击
        menu_items = browser_session.eles_with_fallback(
            tab,
            'css:.ant-menu-item, li[class*="ant-menu"]',
            'xpath://*[contains(@class, "ant-menu-item") or (local-name()="li" and contains(@class, "ant-menu"))]'
        )
        for item in menu_items:
            if menu_text in (item.text or ""):
                ele = item
                break
        if not ele:
            return {"ok": False, "reason": "menu not found"}

    try:
        ele.wait.clickable(timeout=3, wait_stop=True, raise_err=False)
        ele.click(by_js=False, wait_stop=True)
    except Exception:
        if _recipe_requires_native_actions():
            return {"ok": False, "reason": "formal execution menu click failed without JS fallback"}
        # 浏览器探索可使用 DrissionPage 的 by_js 回退；正式回放禁止该路径。
        try:
            ele.click(by_js=True)
        except Exception as e:
            return {"ok": False, "reason": f"click menu failed: {str(e)}"}

    # 2. 等待 iframe 就绪（智能等待：iframe 元素在 DOM 中可见即视为就绪；超时不抛错，由下方 get_active_frame 兜底判定）
    wait_seconds = int(timeout)
    try:
        if old_url is None:
            tab.wait.ele_displayed(ui_contract.ACTIVE_FRAME, timeout=wait_seconds)
        else:
            new_fr = browser_session.get_active_frame(tab)
            if new_fr:
                new_fr.wait.url_change(old_url, exclude=True, timeout=wait_seconds)
            else:
                tab.wait.ele_displayed(ui_contract.ACTIVE_FRAME, timeout=wait_seconds)
    except Exception:
        pass

    if browser_session.get_active_frame(tab) is None:
        resource_context = resource_store.set_module(menu_text)
        return {"ok": False, "entered": menu_text, "iframe_ready": False,
                "resource_context": resource_context,
                "reason": "iframe 未在 %.0fs 内出现" % timeout}

    expand_result = {}
    if expand_filter:
        expand_result = filter_area.expand_filter_area(tab)
        logger.info("expand_filter_area: %s", expand_result.get("reason", ""))
    resource_context = resource_store.set_module(menu_text)
    return {"ok": True, "entered": menu_text, "iframe_ready": True,
            "expand_filter": expand_result, "resource_context": resource_context}


@write_synchronized
def reset_to_initial(module_text: str, timeout: float = 20) -> dict:
    """重置到初始状态：关闭当前业务 tab → 重进模块 → 等 iframe+VTable 就绪。用例间隔离用。"""
    tab = browser_session.get_tab()
    active_frame = browser_session.get_active_frame(tab)
    active_name = browser_session.get_active_tab_name()
    if active_frame is not None and str(active_name or "").strip() == str(module_text or "").strip():
        try:
            active_frame.refresh()
            try:
                active_frame.wait.doc_loaded(timeout=timeout)
            except Exception:
                pass
            expand_result = filter_area.expand_filter_area(tab)
            return {
                "ok": True, "entered": module_text, "iframe_ready": True,
                "reset_mode": "iframe_refresh", "expand_filter": expand_result,
                "resource_context": resource_store.set_module(module_text),
            }
        except Exception as exc:
            logger.debug("iframe refresh reset failed, falling back to tab reopen: %s", exc)
    close_btn = browser_session.ele_with_fallback(
        tab,
        'css:.ant-tabs-tab-active.outSide .anticon-close',
        'xpath://*[contains(@class, "ant-tabs-tab-active") and contains(@class, "outSide")]//*[contains(@class, "anticon-close")]',
        timeout=1.0
    )
    if close_btn:
        try:
            close_btn.wait.clickable(timeout=3, wait_stop=True, raise_err=False)
            close_btn.click(wait_stop=True)
        except Exception:
            if not _recipe_requires_native_actions():
                try:
                    close_btn.click(by_js=True)
                except Exception:
                    pass
            else:
                logger.debug("native tab-close click failed during formal execution")
    # 智能等待：业务 iframe 从 DOM 消失即说明 tab 已关闭（最多 10s）；超时不阻断，交给后续 enter_module
    try:
        tab.wait.ele_deleted(ui_contract.ACTIVE_FRAME, timeout=10)
    except Exception:
        pass
    return enter_module(module_text, timeout=timeout)

@mcp.tool()
@write_synchronized
def scan_filter_fields() -> dict:
    """扫描筛选区所有字段，返回完整字段矩阵（字段名/操作符/输入类型/下拉待选项）。
    自动展开每个下拉字段获取待选项。需先 enter_module 并展开筛选区。
    """
    return filter_area.scan_filter_fields()


@mcp.tool()
@read_synchronized
def get_active_frame() -> dict:
    """获取当前可见 tabpanel 内的业务 iframe。返回 {ok, url, tab_name}。"""
    fr = browser_session.get_active_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe，请先 enter_module"}
    return {"ok": True, "url": getattr(fr, "url", "") or "",
            "tab_name": browser_session.get_active_tab_name()}


@mcp.tool()
@read_synchronized
def dom_tree(selector: str = "", max_depth: int = 6, max_children: int = 50,
             text: bool = False, text_limit: int = 100, show_hidden: bool = False,
             filename: str = None, save_path: str = "", save_format: str = "yml",
             max_chars: int = 8000) -> dict:
    """打印页面或元素的 DOM 树结构（结构化 JSON，便于 AI 识别）。

    Args:
        selector: CSS 选择器，为空则从 body 开始
        max_depth: 最大递归深度（默认 6）
        max_children: 每节点最多收录子节点数（默认 50），超出在 _more 标注
        text: 是否提取元素文本
        text_limit: 每节点文本最大字符数（默认 100），同时整树文本总量限制 5000 字符
        show_hidden: 是否包含 script/style/comment 等隐藏节点（默认 False）
        filename: 优先保存到指定文件名（相对于截图目录），提供时不返回大文本
        save_path: 指定文件路径则同时写入磁盘（如 "screenshots/dom-tree.yml"）
        save_format: 输出格式，"json" 或 "yml"（默认 yml，更省 token）
        max_chars: 输出字符串最大字符数（默认 8000），超出截断并标 _truncated
    """
    max_depth = min(max(int(max_depth or 0), 0), 20)
    max_children = min(max(int(max_children or 0), 0), 500)
    text_limit = min(max(int(text_limit or 0), 0), 1000)
    max_chars = min(max(int(max_chars or 0), 0), 1_000_000)
    save_format = str(save_format or "yml").lower()
    if save_format not in {"json", "yml"}:
        return {"ok": False, "reason": "save_format 必须为 json 或 yml"}
    tab = browser_session.get_tab()
    fr = browser_session.get_active_frame_ro(tab, timeout=0.5)
    target = fr if fr is not None else tab
    try:
        if selector:
            root = target.ele(f'c:{selector}', timeout=3)
            if not root:
                return {"ok": False, "reason": f"selector 未匹配: {selector}"}
        else:
            root = target

        # json.dumps 生成合法 JavaScript 字符串，避免引号/反斜杠导致选择器注入。
        find_el = (
            "var el = document.querySelector(%s);" % json.dumps(selector)
            if selector else "var el = document.body;"
        )

        # 跳过标签列表
        skip_tags = "" if show_hidden else (
            "var SKIP = {'script':1,'style':1,'link':1,'meta':1,'noscript':1,"
            "'template':1,'#comment':1};")
        text_budget = "var TEXT_LEFT = 5000;"

        js = r"""
        (function walk(el, depth, maxD, maxC, showT, txtLim) {
            if (!el || depth > maxD) return null;
            var tag = (el.tagName || '#text').toLowerCase();
            """ + ("" if show_hidden else "if (SKIP[tag]) return null;") + r"""
            var node = { tag: tag };
            if (el.id) node.id = el.id;
            if (el.className && typeof el.className === 'string') {
                var cls = el.className.trim().split(/\s+/).filter(Boolean);
                if (cls.length > 0) node.classes = cls.slice(0, 5);
            }
            var role = el.getAttribute('role');
            if (role) node.role = role;
            var name = el.getAttribute('name');
            if (name) node.name = name;
            var typ = el.getAttribute('type');
            if (typ) node.type = typ;
            var href = el.getAttribute('href');
            if (href) node.href = href.substring(0, 120);
            var src = el.getAttribute('src');
            if (src) node.src = src.substring(0, 120);
            var placeholder = el.getAttribute('placeholder');
            if (placeholder) node.placeholder = placeholder;
            if (el.disabled) node.disabled = true;
            var val = el.getAttribute('value');
            if (val && tag === 'input' && typ !== 'hidden') node.value = val.substring(0, 60);

            // 文本提取：非 script/style 的任意元素，取 textContent 前 N 字符
            if (showT && TEXT_LEFT > 0 && tag !== 'script' && tag !== 'style') {
                var take = Math.min(txtLim, TEXT_LEFT);
                var t = (el.textContent || '').trim().substring(0, take);
                if (t) { node.text = t; TEXT_LEFT -= t.length; }
            }

            if (depth < maxD && el.children && el.children.length > 0) {
                var children = [];
                for (var i = 0; i < el.children.length && children.length < maxC; i++) {
                    var child = walk(el.children[i], depth + 1, maxD, maxC, showT, txtLim);
                    if (child) children.push(child);
                }
                if (children.length > 0) node.children = children;
                if (el.children.length > maxC) node._more = el.children.length - maxC;
            }
            return node;
        })(el, 0, MAXD, MAXC, SHOWT, TXTLIM)
        """
        js = (find_el + skip_tags + text_budget + "return JSON.stringify(" +
              js.replace('MAXD', str(max_depth))
                .replace('MAXC', str(max_children))
                .replace('SHOWT', 'true' if text else 'false')
                .replace('TXTLIM', str(text_limit)) + ")")
        res = target.run_js(js)

        tree_dict = json.loads(res) if isinstance(res, str) else res
        if not isinstance(tree_dict, dict):
            return {"ok": False, "reason": "DOM tree scan returned no object"}
        result = {"ok": True, "save_format": save_format}

        # 生成文本内容
        content_str = ""
        if save_format == "yml":
            def _yaml(obj, i=0):
                p = "  " * i
                if isinstance(obj, dict):
                    r = []
                    for k, v in obj.items():
                        if isinstance(v, (dict, list)) and v:
                            r.append(f"{p}{k}:")
                            r.append(_yaml(v, i + 1))
                        elif isinstance(v, list) and not v:
                            r.append(f"{p}{k}: []")
                        else:
                            x = _yaml(v, i + 1).strip()
                            r.append(f"{p}{k}: {x}")
                    return "\n".join(r)
                elif isinstance(obj, list):
                    r = []
                    for x in obj:
                        if isinstance(x, (dict, list)):
                            r.append(f"{p}-")
                            r.append(_yaml(x, i + 1))
                        else:
                            r.append(f"{p}- {_yaml(x, 0).strip()}")
                    return "\n".join(r)
                else:
                    return json.dumps(obj, ensure_ascii=False)
            content_str = _yaml(tree_dict)
        else:
            content_str = json.dumps(tree_dict, ensure_ascii=False, indent=2)

        # filename 参数优先：直接保存到文件，不返回大文本
        if filename:
            full_path = resource_store.resolve_path(filename)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content_str)
            return {
                "ok": True,
                "saved_to": os.path.abspath(full_path),
                "save_format": save_format,
                "content_length": len(content_str),
            }

        # 无 filename 时：正常返回，带截断保护
        result["tree"] = content_str
        if len(content_str) > max_chars:
            result["tree"] = content_str[:max_chars] + (
                f"\n...(_truncated at {max_chars} chars, original {len(content_str)})")
            result["_truncated"] = True
            result["_original_chars"] = len(content_str)

        if save_path:
            resolved_save_path = resource_store.resolve_path(save_path)
            with open(resolved_save_path, "w", encoding="utf-8") as f:
                f.write(content_str)
            result["saved_to"] = os.path.abspath(resolved_save_path)

        return result
    except Exception as e:
        return {"ok": False, "reason": str(e)}

# ==================== 通用 DOM 原语 ====================

_INTERACTIVE_SELECTOR = ui_contract.INTERACTIVE_CONTROLS


def _attr(ele, name: str):
    try:
        return ele.attr(name)
    except Exception:
        return None


def _element_text(ele) -> str:
    for name in ("aria-label", "title", "placeholder", "value"):
        val = _attr(ele, name)
        if val:
            return str(val).strip().replace("\n", " ")[:40]
    try:
        text = ele.text or ""
    except Exception:
        text = ""
    return " ".join(text.split())[:40]


def _element_locator_candidates(ele) -> list[str]:
    """Build framework-neutral locators ordered from stable attributes to text."""
    tag = str(getattr(ele, "tag", "") or "*").lower()
    candidates = []
    for attr_name in ("data-testid", "data-test", "id", "name", "aria-label"):
        value = _attr(ele, attr_name)
        if value:
            candidates.append(
                "xpath://%s[@%s=%s]" % (tag, attr_name, _xpath_literal(value))
            )
    placeholder = _attr(ele, "placeholder")
    if placeholder and tag in {"input", "textarea"}:
        candidates.append(
            "xpath://%s[@placeholder=%s]" % (tag, _xpath_literal(placeholder))
        )
    text = _element_text(ele)
    if text and (
        tag in {"button", "a", "option"}
        or _attr(ele, "role") in {"button", "link", "menuitem", "tab", "option"}
    ):
        candidates.extend(_clickable_text_locators(text))
    return list(dict.fromkeys(candidates))


def _scan_controls_in_context(target, frame_label: str, start_seq: int, max_items: int):
    """Scan visible controls and return top-viewport center coordinates.

    DrissionPage's ``rect.viewport_midpoint`` already accounts for iframe
    offset, so returned ``cx/cy`` can be passed directly to ``click_xy``.
    """
    out = []
    seq = start_seq
    try:
        nodes = target.eles(f"c:{_INTERACTIVE_SELECTOR}", timeout=2)
    except Exception:
        return out, seq

    for ele in nodes:
        if len(out) >= max_items:
            break
        try:
            w, h = ele.rect.size
            if not w or not h:
                continue
            vx, vy = ele.rect.viewport_midpoint
        except Exception:
            continue

        seq += 1
        cls = _attr(ele, "class") or ""
        role = _attr(ele, "role") or ""
        typ = _attr(ele, "type") or role
        disabled = bool(_attr(ele, "disabled") or _attr(ele, "aria-disabled") == "true")
        locator_candidates = _element_locator_candidates(ele)
        item = {
            "ref": f"e{seq}",
            "frame": frame_label,
            "tag": ele.tag,
            "type": typ or "",
            "text": _element_text(ele),
            "cls": str(cls)[:50],
            "disabled": disabled,
            # Backward-compatible names, now top-viewport absolute coordinates.
            "cx": round(float(vx), 1),
            "cy": round(float(vy), 1),
            "viewportX": round(float(vx), 1),
            "viewportY": round(float(vy), 1),
            "coordinate_space": "top-viewport",
            "coord_source": "DrissionPage.Element.rect.viewport_midpoint",
        }
        if locator_candidates:
            item["locator"] = locator_candidates[0]
            item["locatorCandidates"] = locator_candidates
        out.append(item)
    return out, seq


def _normalize_listen_targets(targets):
    """Convert MCP input to DrissionPage listener urls."""
    if targets is None:
        return True
    if isinstance(targets, str):
        values = [t.strip() for t in targets.split(",") if t.strip()]
        return values or True
    return targets


def _set_http_listen_method(listener, method: str = None) -> str:
    """Set DrissionPage 4.2 listener method state and return the effective value."""
    if not method:
        listener.set_method.GET(only=True).POST()
        return "GET,POST"

    methods = [m.strip().upper() for m in str(method).split(",") if m.strip()]
    if not methods:
        listener.set_method.GET(only=True).POST()
        return "GET,POST"
    if len(methods) == 1 and methods[0] == "ALL":
        listener.set_method.all()
        return "ALL"

    try:
        getattr(listener.set_method, methods[0])(only=True)
        for m in methods[1:]:
            getattr(listener.set_method, m)()
    except (ValueError, TypeError, AttributeError) as e:
        logger.warning("不支持的监听方法 %r（%s），回退默认 GET+POST", method, e)
        listener.set_method.GET(only=True).POST()
        return "GET,POST"
    return ",".join(methods)


def _pre_click_cleanup(clean_overlays: bool = True):
    """Remove transient notification/message overlays before a new click."""
    if not clean_overlays:
        return None
    try:
        return modal.clear_transient_overlays()
    except Exception as e:
        logger.debug("点击前清理通知失败: %s", e)
        return {"ok": False, "closed": [], "errors": [str(e)]}


def _attach_cleanup(result: dict, cleanup: dict = None) -> dict:
    if cleanup and cleanup.get("closed"):
        result["pre_cleaned"] = cleanup.get("closed")
    if cleanup and cleanup.get("errors"):
        result["pre_clean_errors"] = cleanup.get("errors")
    return result


def _short_click_timeout(timeout: float, default: float = 2.0, upper: float = 2.0) -> float:
    """Keep native click probes responsive before fallback paths run."""
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(value, upper))


def _extract_text_locator(locator: str) -> str | None:
    for prefix in ("text:", "text=", "tx:", "tx="):
        if isinstance(locator, str) and locator.startswith(prefix):
            return locator[len(prefix):]
    return None


def _xpath_literal(value: str) -> str:
    text = str(value)
    if "'" not in text:
        return "'%s'" % text
    if '"' not in text:
        return '"%s"' % text
    parts = text.split("'")
    return "concat(%s)" % ', "\'", '.join("'%s'" % part for part in parts)


def _clickable_text_locators(raw_text: str) -> list[str]:
    text = str(raw_text).strip()
    if not text:
        return []
    literal = _xpath_literal(text)
    clickable = (
        "self::button or self::a or @role='button' or @role='tab' or @role='menuitem' "
        "or contains(concat(' ', normalize-space(@class), ' '), ' ant-btn ') "
        "or contains(concat(' ', normalize-space(@class), ' '), ' ant-tabs-tab ') "
        "or contains(concat(' ', normalize-space(@class), ' '), ' ant-dropdown-menu-item ') "
        "or contains(concat(' ', normalize-space(@class), ' '), ' ant-pagination-item ')"
    )
    return [
        "x://*[%s][normalize-space(.)=%s]" % (clickable, literal),
        "x://*[%s][contains(normalize-space(.), %s)]" % (clickable, literal),
    ]


def _click_text_by_js(locator: str, in_frame: bool = True) -> dict | None:
    raw_text = _extract_text_locator(locator)
    if not raw_text:
        return None

    target = (browser_session.get_active_frame() if in_frame else None) or browser_session.get_tab()
    needle = json.dumps("".join(str(raw_text).split()), ensure_ascii=False)
    js = f"""
        var needle = {needle};
        var preferredSelector = [
          'button', 'a', '[role="button"]', '[role="tab"]', '[role="menuitem"]',
          'input[type="button"]', 'input[type="submit"]',
          '.ant-btn', '.ant-tabs-tab', '.ant-dropdown-menu-item', '.ant-pagination-item'
        ].join(',');
        var allSelector = preferredSelector + ',span,div';
        function norm(v) {{ return (v || '').trim().replace(/\\s+/g, ''); }}
        function visible(el) {{
          var style = window.getComputedStyle(el);
          var rect = el.getBoundingClientRect();
          return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
        }}
        function disabled(el) {{
          return el.disabled || el.getAttribute('aria-disabled') === 'true' || el.classList.contains('disabled');
        }}
        function clickTarget(el) {{
          return el.closest(preferredSelector) || el;
        }}
        function probe(selector) {{
          var els = Array.from(document.querySelectorAll(selector));
          for (var i = 0; i < els.length; i++) {{
            var el = els[i];
            if (!visible(el) || norm(el.innerText || el.textContent) !== needle) continue;
            var target = clickTarget(el);
            if (!visible(target) || disabled(target)) continue;
            target.click();
            return JSON.stringify({{
              ok: true,
              tag: target.tagName,
              className: target.className || '',
              text: (target.innerText || target.textContent || '').trim().slice(0, 80)
            }});
          }}
          return null;
        }}
        return probe(preferredSelector) || probe(allSelector) || JSON.stringify({{ok:false}});
    """
    res = target.run_js(js)
    if isinstance(res, str):
        try:
            res = json.loads(res)
        except json.JSONDecodeError:
            return {"ok": False, "reason": "JS 文本点击返回非 JSON: %s" % res}
    return res or {"ok": False}


@mcp.tool()
@read_synchronized
def scan_page_elements(include_iframe: bool = True, max_items: int = 200, filename: str = None) -> dict:
    """扫描页面所有可见交互控件(button/a/input/role=*/canvas)，递归穿透同源 iframe，
    按 frame 分组返回，含可直接传给 click/input 的 locatorCandidates、顶层视口坐标和扫描 ref。
    locatorCandidates 优先 data-testid/data-test/id/name/ARIA，再回退可点击文本，适配常见组件框架。
    进入模块后第一件事。
    max_items 限制返回元素数（超出截断并标 _truncated），避免吃尽上下文。
    filename 提供时保存到文件，不返回大 JSON。"""
    tab = browser_session.get_tab()
    elements, seq = _scan_controls_in_context(tab, "", 0, max_items)

    if include_iframe and len(elements) < max_items:
        fr = browser_session.get_active_frame(tab)
        if fr is not None:
            frame_name = getattr(fr, "name", "") or getattr(fr, "id", "") or "active_iframe"
            iframe_elements, seq = _scan_controls_in_context(
                fr, frame_name, seq, max_items - len(elements)
            )
            elements.extend(iframe_elements)

    data = {
        "url": tab.url,
        "title": tab.title,
        "total": len(elements),
        "elements": elements,
        "coordinate_space": "top-viewport",
        "coord_source": "DrissionPage.Element.rect.viewport_midpoint",
    }

    if len(elements) >= max_items:
        data["_truncated"] = True
        data["returned"] = max_items

    # filename 参数优先
    if filename:
        full_path = resource_store.resolve_path(filename)
        with open(full_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {
            "ok": True,
            "saved_to": os.path.abspath(full_path),
            "element_count": len(data.get("elements", []) if isinstance(data, dict) else []),
        }

    return data


@mcp.tool()
@write_synchronized
def capture_page_model(include_filters: bool = True, include_tables: bool = True,
                       include_table_data: bool = True, max_table_rows: int = 80,
                       max_elements: int = 120, filename: str = None) -> dict:
    """聚合采集当前页面模型：URL/frame、工具栏动作、字段、弹窗/抽屉、分页、表格结构和可选表格数据。

    这是测试用例设计的高信息密度入口。`include_filters=True` 会展开筛选区并读取下拉选项；
    `filename` 提供时保存大 JSON 到截图目录而不直接返回。
    """
    return page_model.capture_page_model(
        include_filters=include_filters,
        include_tables=include_tables,
        include_table_data=include_table_data,
        max_table_rows=max_table_rows,
        max_elements=max_elements,
        filename=filename,
    )


@mcp.tool()
@read_synchronized
def scan_toolbar_actions(scope: str = "page", in_frame: bool = True, max_items: int = 120) -> dict:
    """扫描页面可见动作按钮/链接，返回文本、禁用态、下拉提示、区域归属和矩形位置。

    scope: page=页面主动作，toolbar=尽量聚焦工具栏，all=包含弹窗/筛选/分页等区域。
    """
    return page_model.scan_toolbar_actions(scope=scope, in_frame=in_frame, max_items=max_items)


@mcp.tool()
@read_synchronized
def scan_form_fields(scope: str = "page", include_hidden: bool = False,
                     in_frame: bool = True, max_fields: int = 200) -> dict:
    """扫描通用表单字段。scope: page/filter/modal/drawer/layer/all 或自定义 CSS。

    layer 会自动进入 layer.js 嵌套 iframe 表单（账号新增/编辑等）。
    """
    return page_model.scan_form_fields(scope=scope, include_hidden=include_hidden,
                                       in_frame=in_frame, max_fields=max_fields)







@mcp.tool()
@read_synchronized
def scan_floats(only_visible: bool = True, include_table_data: bool = True) -> dict:
    """扫描所有可见浮窗（modal/drawer/popover/tooltip/dropdown/calendar/message/notification/VTable 浮层）。
    单次 JS 注入完成。返回浮窗内所有操作按钮的位置（可点击关闭）、
    关闭按钮的 CSS 定位符（可用于 click 工具）、日历面板摘要、内部表格结构和可选的全量行数据。
    """
    return page_model.scan_floats(only_visible=only_visible,
                                  include_table_data=include_table_data)


@mcp.tool()
@read_synchronized
def scan_modal(max_items: int = 20) -> dict:
    """扫描当前可见 Ant Design 弹窗，返回标题、正文摘要、字段、按钮和表格数量。"""
    return page_model.scan_modal(max_items=max_items)


@mcp.tool()
@read_synchronized
def scan_drawer(max_items: int = 20) -> dict:
    """扫描当前可见 Ant Design 抽屉，返回标题、正文摘要、字段、按钮和表格数量。"""
    return page_model.scan_drawer(max_items=max_items)


@mcp.tool()
@read_synchronized
def scan_pagination(in_frame: bool = True) -> dict:
    """扫描页面分页器，返回当前页、页大小、总数文本、上一页/下一页可用状态。"""
    return page_model.scan_pagination(in_frame=in_frame)


@mcp.tool()
@write_synchronized
def select_option(field_name: str, option_text: str, select_index: int = 0,
                  scope: str = "auto", timeout: float = 5.0) -> dict:
    """按字段名选择下拉项。

    支持 Ant Design Select / Legions Quick Filter，以及遗留 bootstrap-select。
    scope: auto | frame | top | layer（仅 layer 内容区 / bootstrap-select）。
    field_name 为空时选择第一个可见下拉；select_index 用于同名字段多个下拉。
    """
    return page_model.select_option(field_name=field_name, option_text=option_text,
                                    select_index=select_index, scope=scope, timeout=timeout)


@mcp.tool()
@write_synchronized
def get_all_table_data(kind: str = "auto", table_index: int = 0, max_pages: int = 1,
                       max_rows: int = 1000, max_columns: int = 50,
                       raw: bool = False, filename: str = None) -> dict:
    """读取表格数据。HTML 表格可按 max_pages 翻页采集；VTable 通过列值重建当前实例可读数据。

    max_pages>1 会点击分页下一页，属于会改变页面状态的采集动作。VTable 虚拟滚动/懒加载行需结合
    分页或滚动继续采集，返回中会标注 limitation。
    """
    return page_model.get_all_table_data(kind=kind, table_index=table_index,
                                         max_pages=max_pages, max_rows=max_rows,
                                         max_columns=max_columns, raw=raw,
                                         filename=filename)


def _click_table_cell_raw(row: int, col: int = None, column_title: str = None,
                          kind: str = "auto", table_index: int = 0,
                          icon_name: str = None, hover_first: bool = True,
                          duration: float = 0.3, double_click: bool = False) -> dict:
    """Undecorated table click helper for aggregate tools."""
    kind = _normalize_table_kind(kind)

    def _click_vtable():
        target_col = col
        if target_col is None and column_title:
            target_col, reason = _find_vtable_col(column_title)
            if target_col is None:
                return {"ok": False, "kind": "vtable", "reason": reason}
        if target_col is None:
            return {"ok": False, "kind": "vtable", "reason": "VTable 点击需要 col 或 column_title"}
        return _tag_table_result("vtable", vtable.click_cell(target_col, row, icon_name, hover_first, duration, double_click))

    def _click_bootstrap():
        if not column_title:
            return {"ok": False, "kind": "bootstrap", "reason": "Bootstrap Table 点击需要 column_title"}
        return _tag_table_result(
            "bootstrap",
            bootstrap_table.click_bootstrap_table_cell(column_title, row, table_index),
        )

    def _click_html():
        if not column_title:
            return {"ok": False, "kind": "html", "reason": "HTML 表格点击需要 column_title"}
        return _tag_table_result("html", html_table.click_html_table_cell(column_title, row, table_index))

    if kind == "vtable":
        return _click_vtable()
    if kind == "bootstrap":
        return _click_bootstrap()
    if kind == "html":
        return _click_html()

    reasons = {}
    for item in _auto_table_scan_order():
        if item == "vtable":
            candidate = _click_vtable()
        elif item == "bootstrap":
            candidate = _click_bootstrap()
        else:
            candidate = _click_html()
        reasons[item] = candidate.get("reason", "")
        if candidate.get("ok"):
            return candidate
    return {"ok": False, "kind": "auto", "reason": "表格单元格点击失败", "details": reasons}


_KEY_ALIASES = {
    "alt": Keys.ALT,
    "backspace": Keys.BACKSPACE,
    "control": Keys.CONTROL,
    "ctrl": Keys.CTRL,
    "del": Keys.DELETE,
    "delete": Keys.DELETE,
    "down": Keys.DOWN,
    "arrowdown": Keys.DOWN,
    "end": Keys.END,
    "enter": Keys.ENTER,
    "return": Keys.RETURN,
    "esc": Keys.ESCAPE,
    "escape": Keys.ESCAPE,
    "home": Keys.HOME,
    "left": Keys.LEFT,
    "arrowleft": Keys.LEFT,
    "meta": Keys.META,
    "command": Keys.COMMAND,
    "pagedown": Keys.PAGE_DOWN,
    "pageup": Keys.PAGE_UP,
    "right": Keys.RIGHT,
    "arrowright": Keys.RIGHT,
    "shift": Keys.SHIFT,
    "space": Keys.SPACE,
    "tab": Keys.TAB,
    "up": Keys.UP,
    "arrowup": Keys.UP,
}


def _official_key(value: str):
    """把 MCP 友好键名映射为 DrissionPage 4.2 官方 ``Keys`` 常量。"""
    text = str(value or "")
    if len(text) == 1:
        return text
    normalized = re.sub(r"[\s_-]+", "", text).lower()
    if normalized in _KEY_ALIASES:
        return _KEY_ALIASES[normalized]
    if re.fullmatch(r"f(?:[1-9]|1[0-2])", normalized):
        return getattr(Keys, normalized.upper())
    raise ValueError("unsupported key: %s" % value)


def _press_key_raw(target, key: str, modifiers: list = None, interval: float = 0.01) -> dict:
    """在动作链发送按键，并在主键释放失败时仍反向释放修饰键。"""
    modifiers = list(modifiers or [])
    if len(key) == 1 and not modifiers:
        target.actions.type(key, interval=interval)
        return {"ok": True, "key": key, "modifiers": []}

    main_key = _official_key(key)
    modifier_keys = [_official_key(item) for item in modifiers]
    pressed = []
    main_pressed = False
    release_error = None
    try:
        for modifier in modifier_keys:
            target.actions.key_down(modifier)
            pressed.append(modifier)
        target.actions.key_down(main_key)
        main_pressed = True
    finally:
        if main_pressed:
            try:
                target.actions.key_up(main_key)
            except Exception as exc:
                release_error = exc
        for modifier in reversed(pressed):
            try:
                target.actions.key_up(modifier)
            except Exception:
                logger.debug("释放修饰键失败", exc_info=True)
    if release_error is not None:
        raise release_error
    return {"ok": True, "key": key, "modifiers": modifiers}


def _compact_text(text: str) -> str:
    return "".join(str(text or "").split()).lower()


def _xpath_literal(value: str) -> str:
    """Return an XPath string literal for labels that may contain quotes."""
    value = str(value or "")
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(%s)" % ', "\'", '.join(f"'{part}'" for part in parts)


def _normalize_target_dict(target):
    if target is None:
        return None
    if isinstance(target, str):
        return {"type": "locator", "locator": target}
    if isinstance(target, dict):
        return dict(target)
    return {"type": "locator", "locator": str(target)}


def _target_get(target: dict, *names, default=None):
    for name in names:
        if name in target and target[name] is not None:
            return target[name]
    return default


def _as_locator(prefix: str, value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    for known in ("css:", "c:", "xpath:", "x:", "text:", "tx:", "tag:", "t:", "ax:", "#", "."):
        if value.startswith(known):
            return value
    return f"{prefix}:{value}"


def _resolve_visible_action_target(target: dict, in_frame: bool) -> dict:
    text = _target_get(target, "text", "name", "label", "title")
    if not text:
        return {"ok": False, "reason": "action/button target requires text/name/label"}
    scope = str(_target_get(target, "scope", default="auto") or "auto").lower()
    max_items = int(_target_get(target, "max_items", default=160) or 160)

    def _matching_button(overlays: list[dict], area: str):
        needle = _compact_text(text)
        for exact in (True, False):
            for overlay in reversed(overlays or []):
                # dropdown/select 的可选项不是 button；页面模型把它们放在 options，
                # 语义点击统一消费两类元素，避免退化成不稳定坐标点击。
                candidates = list(overlay.get("buttons") or []) + list(overlay.get("options") or [])
                for item in reversed(candidates):
                    hay = _compact_text(item.get("text") or item.get("title") or "")
                    matched = hay == needle if exact else bool(needle and needle in hay)
                    if not matched or item.get("disabled"):
                        continue
                    semantic_xpath = item.get("semanticXPath")
                    structural_xpath = item.get("xpath")
                    selector_hint = item.get("selectorHint")
                    locator = (
                        _as_locator("xpath", semantic_xpath)
                        if semantic_xpath else
                        _as_locator("css", selector_hint)
                        if selector_hint else
                        _as_locator("xpath", structural_xpath)
                        if structural_xpath else ""
                    )
                    overlay_type = overlay.get("type") or ""
                    resolved_area = (
                        "modal" if overlay_type in {"modal", "confirm", "system_confirm", "interactive"}
                        else overlay_type or area
                    )
                    result = {
                        "ok": True,
                        "action": "click",
                        "locator": locator,
                        "in_frame": overlay.get("scope") == "iframe",
                        "meta": {
                            "target_type": "action",
                            "text": item.get("text") or text,
                            "area": resolved_area,
                            "overlay_type": overlay_type,
                            "overlay_title": overlay.get("title") or "",
                            "scope": overlay.get("scope") or "",
                            "matched": item,
                        },
                    }
                    rect = item.get("rect") or {}
                    result["x"] = item.get("cx", item.get("viewportX", rect.get("cx")))
                    result["y"] = item.get("cy", item.get("viewportY", rect.get("cy")))
                    if result["x"] is None and {"x", "width"} <= set(rect):
                        result["x"] = float(rect["x"]) + float(rect["width"]) / 2
                    if result["y"] is None and {"y", "height"} <= set(rect):
                        result["y"] = float(rect["y"]) + float(rect["height"]) / 2
                    if not locator and result["x"] is not None and result["y"] is not None:
                        result["action"] = "click_xy"
                    return result
        return None

    overlay_types = {
        "modal": {"modal", "confirm", "system_confirm", "interactive"},
        "drawer": {"drawer"},
        "dropdown": {"dropdown", "select-dropdown", "vtable-filter-menu", "vtable-menu"},
        "select-dropdown": {"select-dropdown"},
        "calendar": {"calendar"},
        "popover": {"popover"},
        "tooltip": {"tooltip", "vtable-tooltip"},
        "notification": {"notification"},
        "message": {"message"},
        "vtable-filter-menu": {"vtable-filter-menu"},
        "vtable-menu": {"vtable-menu"},
    }
    all_overlay_types = set().union(*overlay_types.values())
    overlay_scopes = set(overlay_types) | {"auto", "all", "overlay"}
    if scope in overlay_scopes:
        snapshot = observe.observe_snapshot(
            only_visible=True, include_table_data=False, detail="summary",
        )
        visible_overlays = snapshot.get("overlays") or []
        accepted_types = (
            all_overlay_types if scope in {"auto", "all", "overlay"}
            else overlay_types[scope]
        )
        typed = [item for item in visible_overlays if item.get("type") in accepted_types]
        resolved = _matching_button(typed, "overlay" if scope in {"auto", "all", "overlay"} else scope)
        if resolved:
            return resolved
        # 显式限定浮层时绝不退回页面同名按钮；这是提交/删除类误点击的安全边界。
        if scope not in {"auto", "all"}:
            return {"ok": False, "reason": "visible %s action not found: %s" % (scope, text)}

    toolbar_scope = "toolbar" if scope == "auto" else scope
    data = page_model.scan_toolbar_actions(
        scope=toolbar_scope, in_frame=in_frame, max_items=max_items,
    )
    if not data.get("ok"):
        return {"ok": False, "reason": data.get("reason", "scan toolbar actions failed")}

    needle = _compact_text(text)
    candidates = []
    for item in data.get("actions", []) or []:
        hay = _compact_text(item.get("text") or item.get("title") or "")
        if hay == needle:
            candidates.insert(0, item)
        elif needle and needle in hay:
            candidates.append(item)
    if not candidates:
        return {"ok": False, "reason": "visible action not found: %s" % text}

    item = candidates[0]
    semantic_xpath = item.get("semanticXPath")
    structural_xpath = item.get("xpath")
    selector_hint = item.get("selectorHint")
    locator = (
        _as_locator("xpath", semantic_xpath) if semantic_xpath else
        _as_locator("css", selector_hint) if selector_hint else
        _as_locator("xpath", structural_xpath) if structural_xpath else ""
    )
    if locator:
        return {
            "ok": True,
            "action": "click",
            "locator": locator,
            "in_frame": in_frame,
            "meta": {
                "target_type": "action",
                "text": item.get("text") or text,
                "area": item.get("area") or toolbar_scope,
                "matched": item,
            },
        }
    cx = item.get("cx") or item.get("viewportX")
    cy = item.get("cy") or item.get("viewportY")
    if cx is None or cy is None:
        rect = item.get("rect") or {}
        if {"x", "y", "width", "height"} <= set(rect):
            cx = float(rect["x"]) + float(rect["width"]) / 2
            cy = float(rect["y"]) + float(rect["height"]) / 2
    if cx is None or cy is None:
        return {"ok": False, "reason": "visible action has no usable coordinates: %s" % text}
    return {
        "ok": True,
        "action": "click_xy",
        "x": float(cx),
        "y": float(cy),
        "locator": "",
        "in_frame": in_frame,
        "meta": {
            "target_type": "action",
            "text": item.get("text") or text,
            "area": item.get("area") or toolbar_scope,
            "matched": item,
        },
    }


def _element_is_visible(element) -> bool:
    states = getattr(element, "states", None)
    return bool(getattr(states, "is_displayed", True))


def _field_container_label(container) -> str:
    try:
        label = container.ele(
            "css:.ant-form-item-label label,.ant-form-item-label,label,[class*='label']",
            timeout=0.2,
        )
        return str(getattr(label, "text", "") or "").strip().rstrip("：:")
    except Exception:
        return ""


def _semantic_field_candidates(target, field_name: str, area: str, timeout: float) -> list:
    label_lit = _xpath_literal(field_name)
    label_predicate = (
        ".//*[self::label or contains(@class, 'label')]"
        "[contains(normalize-space(.), %s)]" % label_lit
    )
    form_item = (
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' ant-form-item ') or "
        "contains(concat(' ', normalize-space(@class), ' '), ' el-form-item ') or "
        "contains(concat(' ', normalize-space(@class), ' '), ' arco-form-item ') or "
        "contains(concat(' ', normalize-space(@class), ' '), ' ivu-form-item ') or "
        "contains(concat(' ', normalize-space(@class), ' '), ' semi-form-field ') or "
        "contains(concat(' ', normalize-space(@class), ' '), ' form-group ') or "
        "contains(@class, 'MuiFormControl-root')][%s]" % label_predicate
    )
    if area == "modal":
        locator = (
            "xpath://div[contains(@class, 'ant-modal')][%s]//div[contains(@class, 'ant-form-item')]"
            "[%s]" % (label_predicate, label_predicate)
        )
    elif area == "drawer":
        locator = (
            "xpath://div[contains(@class, 'ant-drawer')][%s]//div[contains(@class, 'ant-form-item')]"
            "[%s]" % (label_predicate, label_predicate)
        )
    elif area == "filter":
        locator = (
            "xpath://div[contains(@class, 'page-query') or contains(@class, 'quick-filter')]"
            "//*[contains(@class, 'ant-form-item') or contains(@class, 'ant-col')][%s]"
            % label_predicate
        )
    else:
        locator = "xpath:" + form_item
    try:
        return [item for item in target.eles(locator, timeout=min(timeout, 2.0)) if _element_is_visible(item)]
    except Exception:
        return []


def _generic_field_controls(target, field_name: str, area: str, timeout: float) -> list:
    """Resolve native/ARIA fields when no component-specific form item matched."""
    literal = _xpath_literal(field_name)
    control = (
        "self::input[not(@type='hidden')] or self::textarea or "
        "@contenteditable='true' or @role='textbox' or @role='combobox'"
    )
    roots = {
        "modal": "//*[@role='dialog' or contains(@class,'modal') or contains(@class,'dialog')]",
        "drawer": "//*[contains(@class,'drawer')]",
        "filter": "//*[contains(@class,'filter') or contains(@class,'query') or contains(@class,'search')]",
    }
    root = roots.get(area, "")
    prefix = root + "//" if root else "//"
    label_match = "normalize-space(.)=%s" % literal
    expressions = [
        "%s*[(%s) and (@aria-label=%s or @placeholder=%s or @name=%s)]"
        % (prefix, control, literal, literal, literal),
        "%s*[(%s) and @id=//label[%s]/@for]"
        % (prefix, control, label_match),
        "%slabel[%s]//*[%s]" % (prefix, label_match, control),
    ]
    try:
        found = target.eles(
            "xpath:" + " | ".join(expressions), timeout=min(timeout, 1.0)
        ) or []
    except Exception:
        return []
    visible = []
    seen = set()
    for item in found:
        identity = id(item)
        if identity in seen or not _element_is_visible(item):
            continue
        seen.add(identity)
        visible.append(item)
    return visible


def _native_element_input(control, value: str, clear: bool, timeout: float) -> None:
    """Use DrissionPage element input; the fallback only supports lightweight test doubles."""
    waiter = getattr(control, "wait", None)
    if waiter is not None:
        waiter.clickable(timeout=timeout, wait_stop=True, raise_err=False)
    try:
        control.input(value, clear=clear, by_js=False)
    except TypeError:
        if clear:
            control.clear()
        control.input(value)


@mcp.tool()
@write_synchronized
def set_field_value(field_name: str, value: str, in_frame: bool = True,
                    clear: bool = True, timeout: float = 5.0,
                    scope: str = "auto", select_index: int = 0) -> dict:
    """按可见标签写入文本字段；所有候选定位共享一个总超时预算。

    scope 支持 layer / layui：写入可见 layer.js 嵌套 iframe 表单字段。
    auto 时优先 Ant/页面字段，未命中再尝试 layer 内容。
    """
    field_name = str(field_name or "").strip()
    if not field_name:
        return {"ok": False, "reason": "field_name is required"}
    scope = str(scope or "auto").lower()
    supported = {
        "auto", "top", "frame", "iframe", "modal", "drawer", "overlay",
        "filter", "page", "layer", "layui", "layui-layer",
    }
    if scope not in supported:
        return {"ok": False, "reason": "unsupported scope: %s" % scope}
    timeout = max(float(timeout or 0), 0.0)
    deadline = time.monotonic() + timeout
    select_index = max(int(select_index or 0), 0)

    def remaining(cap=None):
        available = max(deadline - time.monotonic(), 0.0)
        return min(available, cap) if cap is not None else available

    if scope in {"layer", "layui", "layui-layer"}:
        from .services import layer_modal as _layer_modal
        return _layer_modal.set_layer_field_value(
            field_name=field_name,
            value=value,
            clear=clear,
            select_index=select_index,
            timeout=timeout,
        )

    tab = browser_session.get_tab()
    contexts = []
    if in_frame and scope != "top":
        frame = browser_session.get_active_frame(tab)
        if frame is not None:
            contexts.append(("iframe", frame))
    if scope in {"auto", "top", "modal", "drawer", "overlay", "filter", "page"} or not contexts:
        contexts.append(("top", tab))

    if scope in {"frame", "iframe"} and not any(name == "iframe" for name, _ in contexts):
        return {"ok": False, "reason": "未找到活动 iframe"}
    if scope == "modal":
        areas = ["modal"]
    elif scope == "drawer":
        areas = ["drawer"]
    elif scope == "overlay":
        areas = ["modal", "drawer"]
    elif scope == "filter":
        areas = ["filter"]
    elif scope == "page":
        areas = ["page"]
    else:
        areas = ["modal", "drawer", "filter", "page"]

    control_locator = (
        "css:input:not([type='hidden']),textarea,.ant-input-number-input,"
        "[contenteditable='true']"
    )
    text_value = "" if value is None else str(value)

    def apply_control(control, scope_name, area, index):
        states = getattr(control, "states", None)
        if not bool(getattr(states, "is_enabled", True)):
            return {"ok": False, "reason": "field is disabled: %s" % field_name}
        try:
            if control.attr("readonly") not in (None, False, "", "false"):
                return {"ok": False, "reason": "field is read-only: %s" % field_name}
        except Exception:
            pass
        try:
            _native_element_input(control, text_value, clear, remaining())
            try:
                actual = control.property("value")
            except Exception:
                actual = None
            if actual is None:
                try:
                    actual = control.attr("value")
                except Exception:
                    actual = None
            if actual is None and getattr(control, "tag", "") not in {"input", "textarea"}:
                actual = getattr(control, "text", None)
            return {
                "ok": True,
                "action": "set_field_value",
                "field_name": field_name,
                "value": text_value,
                "actual_value": actual,
                "matches_requested": None if actual is None else str(actual) == text_value,
                "scope": scope_name,
                "area": "overlay" if area in {"modal", "drawer"} else area,
                "select_index": index,
            }
        except Exception as exc:
            return {"ok": False, "reason": "field input failed: %s" % exc,
                    "field_name": field_name, "scope": scope_name, "area": area}

    for scope_name, context in contexts:
        for area in areas:
            if remaining() <= 0:
                break
            containers = _semantic_field_candidates(
                context, field_name, area, remaining(2.0)
            )
            exact = [
                item for item in containers
                if _compact_text(_field_container_label(item)) == _compact_text(field_name)
            ]
            for container in reversed(exact or containers):
                try:
                    controls = [
                        item for item in container.eles(
                            control_locator, timeout=remaining(0.3)
                        )
                        if _element_is_visible(item)
                    ]
                except Exception:
                    controls = []
                if not controls:
                    continue
                index = min(select_index, len(controls) - 1)
                return apply_control(controls[index], scope_name, area, index)
            controls = _generic_field_controls(
                context, field_name, area, remaining(1.0)
            )
            if controls:
                index = min(select_index, len(controls) - 1)
                result = apply_control(controls[index], scope_name, area, index)
                if result.get("ok"):
                    result["adapter"] = "generic-dom"
                return result

    if "filter" in areas:
        for scope_name, context in contexts:
            column, _ = filter_area._quick_filter_field_column(context, field_name)
            if column is None:
                continue
            try:
                controls = [
                    item for item in column.eles(control_locator, timeout=remaining(0.3))
                    if _element_is_visible(item)
                ]
            except Exception:
                controls = []
            if controls:
                index = min(select_index, len(controls) - 1)
                return apply_control(controls[index], scope_name, "filter", index)

    # auto/modal：Ant 路径未命中时尝试 layer 嵌套表单
    if scope in {"auto", "modal", "overlay"} and remaining() > 0:
        from .services import layer_modal as _layer_modal
        legacy = _layer_modal.set_layer_field_value(
            field_name=field_name,
            value=value,
            clear=clear,
            select_index=select_index,
            timeout=remaining(),
        )
        if legacy.get("ok"):
            legacy["fallback_from"] = "ant-field"
            return legacy
        layer_reason = legacy.get("reason")
    else:
        layer_reason = None

    reason = "field lookup timed out" if remaining() <= 0 else "field not found"
    result = {
        "ok": False,
        "reason": "%s: %s" % (reason, field_name),
        "scope": scope,
        "in_frame": in_frame,
    }
    if layer_reason:
        result["layer_reason"] = layer_reason
    return result


def _click_field_raw(field_name: str, in_frame: bool = True, timeout: float = 5.0,
                     scope: str = "auto", select_index: int = 0) -> dict:
    """按可见标签点击固定 Ant Design 字段，严格遵守 frame/浮层区域。

    与 ``set_field_value`` 共用语义候选逻辑：显式 modal/drawer/filter 不会退回页面同名
    字段；所有候选共享一个总超时预算。日期、Select 等复合控件点击其稳定 opener。
    """
    field_name = str(field_name or "").strip()
    if not field_name:
        return {"ok": False, "reason": "field target requires name/field_name"}
    scope = str(scope or "auto").lower()
    supported = {"auto", "top", "frame", "iframe", "modal", "drawer", "overlay", "filter", "page"}
    if scope not in supported:
        return {"ok": False, "reason": "unsupported scope: %s" % scope}
    timeout = max(float(timeout or 0), 0.0)
    deadline = time.monotonic() + timeout
    select_index = max(int(select_index or 0), 0)

    def remaining(cap=None):
        available = max(deadline - time.monotonic(), 0.0)
        return min(available, cap) if cap is not None else available

    tab = browser_session.get_tab()
    contexts = []
    if in_frame and scope != "top":
        frame = browser_session.get_active_frame(tab)
        if frame is not None:
            contexts.append(("iframe", frame))
    if scope in {"auto", "top", "modal", "drawer", "overlay", "filter", "page"} or not contexts:
        contexts.append(("top", tab))
    if scope in {"frame", "iframe"} and not any(name == "iframe" for name, _ in contexts):
        return {"ok": False, "reason": "未找到活动 iframe"}

    if scope == "modal":
        areas = ["modal"]
    elif scope == "drawer":
        areas = ["drawer"]
    elif scope == "overlay":
        areas = ["modal", "drawer"]
    elif scope == "filter":
        areas = ["filter"]
    elif scope == "page":
        areas = ["page"]
    else:
        areas = ["modal", "drawer", "filter", "page"]

    control_locator = "css:" + ui_contract.FORM_CONTROL
    opener_locator = (
        "css:.ant-calendar-picker-input,.ant-picker-input input,.ant-select-selection,"
        ".ant-select-selector,[role='combobox'],input:not([type='hidden']),textarea,"
        ".ant-input-number-input,.ant-checkbox-wrapper,.ant-radio-group,.ant-switch"
    )

    def click_container(container, scope_name, area):
        try:
            controls = [
                item for item in container.eles(control_locator, timeout=remaining(0.3))
                if _element_is_visible(item)
            ]
        except Exception:
            controls = []
        if not controls:
            return None
        index = min(select_index, len(controls) - 1)
        control = controls[index]
        try:
            opener = control.ele(opener_locator, timeout=remaining(0.2)) or control
            opener.wait.clickable(timeout=remaining(), wait_stop=True, raise_err=False)
            opener.click(by_js=False, wait_stop=True)
            cls = str(control.attr("class") or "")
            control_type = "field"
            if "calendar" in cls or "ant-picker" in cls:
                control_type = "date-picker"
            elif "ant-select" in cls:
                control_type = "select"
            elif "input-number" in cls:
                control_type = "number"
            return {
                "ok": True,
                "action": "field_click",
                "field_name": field_name,
                "scope": scope_name,
                "area": "overlay" if area in {"modal", "drawer"} else area,
                "control_type": control_type,
                "select_index": index,
            }
        except Exception as exc:
            return {"ok": False, "reason": "field click failed: %s" % exc,
                    "field_name": field_name, "scope": scope_name, "area": area}

    for scope_name, context in contexts:
        for area in areas:
            if remaining() <= 0:
                break
            containers = _semantic_field_candidates(context, field_name, area, remaining(2.0))
            exact = [
                item for item in containers
                if _compact_text(_field_container_label(item)) == _compact_text(field_name)
            ]
            for container in reversed(exact or containers):
                result = click_container(container, scope_name, area)
                if result is not None:
                    return result

    if "filter" in areas:
        for scope_name, context in contexts:
            column, _ = filter_area._quick_filter_field_column(context, field_name)
            if column is None:
                continue
            result = click_container(column, scope_name, "filter")
            if result is not None:
                return result

    reason = "field lookup timed out" if remaining() <= 0 else "field not found"
    return {"ok": False, "reason": "%s: %s" % (reason, field_name),
            "scope": scope, "in_frame": in_frame}


def _normalize_date_value(value: str) -> dict:
    raw = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return {
                "ok": True,
                "dash": parsed.strftime("%Y-%m-%d"),
                "slash": parsed.strftime("%Y/%m/%d"),
                "year": parsed.year,
                "month": parsed.month,
                "day": parsed.day,
            }
        except ValueError:
            pass
    return {"ok": False, "reason": "date must be YYYY-MM-DD or YYYY/MM/DD"}


def _field_snapshot(target, field_name: str, select_index: int = 0) -> dict:
    js = r"""
var FIELD_NAME = __FIELD_NAME__;
var SELECT_INDEX = __SELECT_INDEX__;
function clean(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
function frameOffset() {
  var fe = window.frameElement;
  if (!fe) return {left: 0, top: 0};
  var r = fe.getBoundingClientRect();
  return {left: r.left, top: r.top};
}
function rectOf(el) {
  if (!el) return null;
  var off = frameOffset();
  var r = el.getBoundingClientRect();
  var x = Math.round((r.x + off.left) * 10) / 10;
  var y = Math.round((r.y + off.top) * 10) / 10;
  var w = Math.round(r.width * 10) / 10;
  var h = Math.round(r.height * 10) / 10;
  return {x: x, y: y, width: w, height: h,
          cx: Math.round((x + w / 2) * 10) / 10,
          cy: Math.round((y + h / 2) * 10) / 10};
}
function visible(el) {
  if (!el || !el.isConnected) return false;
  var style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  var r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
function controlType(container) {
  if (container.querySelector('.ant-calendar-picker,.ant-picker')) return 'date-picker';
  if (container.querySelector('.ant-select')) return 'select';
  if (container.querySelector('.ant-input-number')) return 'number';
  if (container.querySelector('textarea')) return 'textarea';
  return 'text';
}
var containers = [].slice.call(document.querySelectorAll('.ant-form-item'));
var matches = [];
for (var i = 0; i < containers.length; i++) {
  var c = containers[i];
  if (!visible(c)) continue;
  var labelEl = c.querySelector('.ant-form-item-label label,.ant-form-item-label');
  var label = clean(labelEl ? labelEl.textContent : '');
  var text = clean(c.textContent);
  if ((label && label.indexOf(FIELD_NAME) >= 0) || text.indexOf(FIELD_NAME) >= 0) {
    matches.push({container: c, label: label || FIELD_NAME});
  }
}
if (!matches.length) {
  return JSON.stringify({ok: false, reason: 'field not found: ' + FIELD_NAME});
}
var picked = matches[Math.min(Math.max(SELECT_INDEX, 0), matches.length - 1)];
var container = picked.container;
var control = container.querySelector('.ant-calendar-picker,.ant-picker,.ant-select,.ant-input-number,textarea,input:not([type="hidden"])') || container;
var input = container.querySelector('.ant-calendar-picker input,.ant-picker input,input:not([type="hidden"]),textarea');
var value = input ? (input.value || input.getAttribute('value') || '') : clean(control.textContent);
return JSON.stringify({
  ok: true,
  label: picked.label,
  type: controlType(container),
  value: value,
  readOnly: !!(input && input.readOnly),
  disabled: !!((input && input.disabled) || control.className.indexOf('disabled') >= 0),
  rect: rectOf(control)
});
""".replace("__FIELD_NAME__", json.dumps(str(field_name or ""), ensure_ascii=False)).replace(
        "__SELECT_INDEX__", str(int(select_index or 0))
    )
    return page_model._run_json(target, js, {"ok": False, "reason": "field snapshot failed"})


def _calendar_snapshot(target, target_date_slash: str = "") -> dict:
    js = r"""
var TARGET_DATE = __TARGET_DATE__;
function clean(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
function visible(el) {
  if (!el || !el.isConnected) return false;
  var style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  var r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
function frameOffset() {
  var fe = window.frameElement;
  if (!fe) return {left: 0, top: 0};
  var r = fe.getBoundingClientRect();
  return {left: r.left, top: r.top};
}
function rectOf(el) {
  if (!el) return null;
  var off = frameOffset();
  var r = el.getBoundingClientRect();
  var x = Math.round((r.x + off.left) * 10) / 10;
  var y = Math.round((r.y + off.top) * 10) / 10;
  var w = Math.round(r.width * 10) / 10;
  var h = Math.round(r.height * 10) / 10;
  return {x: x, y: y, width: w, height: h,
          cx: Math.round((x + w / 2) * 10) / 10,
          cy: Math.round((y + h / 2) * 10) / 10};
}
function panelInfo(root) {
  var panel = root.querySelector('.ant-calendar-range-left') || root;
  var ye = panel.querySelector('.ant-calendar-year-select');
  var me = panel.querySelector('.ant-calendar-month-select');
  var yearText = clean(ye ? ye.textContent : '');
  var monthText = clean(me ? me.textContent : '');
  var year = parseInt(yearText.replace(/\D/g, ''), 10) || null;
  var month = parseInt(monthText.replace(/\D/g, ''), 10) || null;
  return {yearText: yearText, monthText: monthText,
          title: yearText + monthText, year: year, month: month};
}
function cellInfo(root, dateTitle) {
  if (!dateTitle) return null;
  var td = root.querySelector('td[title="' + dateTitle + '"]');
  var cell = td ? td.querySelector('.ant-calendar-date') : null;
  if (!td || !cell) return null;
  var cls = td.className || '';
  return {
    title: dateTitle,
    text: clean(cell.textContent),
    disabled: /\bant-calendar-disabled-cell\b/.test(cls),
    selected: /\bant-calendar-selected-date\b|\bant-calendar-selected-start-date\b|\bant-calendar-selected-end-date\b/.test(cls),
    today: /\bant-calendar-today\b/.test(cls),
    inView: !(/\bant-calendar-last-month-cell\b|\bant-calendar-next-month-cell\b|\bant-calendar-next-month-btn-day\b/.test(cls)),
    rect: rectOf(cell)
  };
}
var roots = [].slice.call(document.querySelectorAll('.ant-calendar-picker-container .ant-calendar,.ant-calendar'))
  .filter(function(el) {
    return visible(el) && !el.parentElement.closest('.ant-calendar');
  });
if (!roots.length) {
  return JSON.stringify({ok: false, reason: 'calendar not found'});
}
var root = roots[0];
var cls = root.className || '';
var isRange = /\bant-calendar-range\b/.test(cls) ||
  !!root.querySelector('.ant-calendar-range-left,.ant-calendar-range-right');
var selectedDates = [];
[].slice.call(root.querySelectorAll('td[title]')).forEach(function(td) {
  var t = td.getAttribute('title') || '';
  var tdCls = td.className || '';
  if (t && /\bant-calendar-selected-date\b|\bant-calendar-selected-start-date\b|\bant-calendar-selected-end-date\b/.test(tdCls)) {
    selectedDates.push(t);
  }
});
var info = panelInfo(root);
return JSON.stringify({
  ok: true,
  mode: isRange ? 'range' : 'single',
  title: info.title,
  year: info.year,
  month: info.month,
  selectedDates: selectedDates,
  cellCount: root.querySelectorAll('td[title] .ant-calendar-date').length,
  targetCell: cellInfo(root, TARGET_DATE),
  nav: {
    prevMonth: rectOf(root.querySelector('.ant-calendar-prev-month-btn')),
    nextMonth: rectOf(root.querySelector('.ant-calendar-next-month-btn')),
    prevYear: rectOf(root.querySelector('.ant-calendar-prev-year-btn')),
    nextYear: rectOf(root.querySelector('.ant-calendar-next-year-btn'))
  },
  rect: rectOf(root)
});
""".replace("__TARGET_DATE__", json.dumps(str(target_date_slash or ""), ensure_ascii=False))
    return page_model._run_json(target, js, {"ok": False, "reason": "calendar snapshot failed"})


def _find_calendar_root(target, timeout: float):
    try:
        return target.ele("c:.ant-calendar-picker-container .ant-calendar", timeout=timeout)
    except Exception:
        pass
    try:
        return target.ele("c:.ant-calendar", timeout=timeout)
    except Exception:
        return None


def _calendar_shown_ym(cal) -> tuple[int, int]:
    panel = None
    try:
        panel = cal.ele("c:.ant-calendar-range-left", timeout=0.2)
    except Exception:
        panel = None
    panel = panel or cal
    ye = panel.ele("c:.ant-calendar-year-select", timeout=1)
    me = panel.ele("c:.ant-calendar-month-select", timeout=1)
    year = int("".join(ch for ch in str(ye.text or "") if ch.isdigit()))
    month = int("".join(ch for ch in str(me.text or "") if ch.isdigit()))
    return year, month


def _wait_calendar_ym_change(cal, previous: tuple[int, int], timeout: float) -> tuple[int, int]:
    """Wait with DrissionPage's element waiter, then read the calendar state once."""
    try:
        cal.wait.stop_moving(timeout=max(timeout, 0), raise_err=False)
        current = _calendar_shown_ym(cal)
        return current
    except Exception:
        return previous


def _date_field_contexts(in_frame: bool, scope: str) -> tuple[object, list[tuple[str, object]], list[str]]:
    """Resolve the same scope ordering used by the semantic field facades."""
    tab = browser_session.get_tab()
    contexts = []
    if in_frame and scope != "top":
        frame = browser_session.get_active_frame(tab)
        if frame is not None:
            contexts.append(("iframe", frame))
    if scope in {"auto", "top", "modal", "drawer", "overlay", "filter", "page"} or not contexts:
        contexts.append(("top", tab))

    if scope == "modal":
        areas = ["modal"]
    elif scope == "drawer":
        areas = ["drawer"]
    elif scope == "overlay":
        areas = ["modal", "drawer"]
    elif scope == "filter":
        areas = ["filter"]
    elif scope == "page":
        areas = ["page"]
    else:
        areas = ["modal", "drawer", "filter", "page"]
    return tab, contexts, areas


def _date_picker_inputs(picker) -> list:
    try:
        inputs = picker.eles(
            "css:input.ant-calendar-range-picker-input,input.ant-calendar-picker-input,"
            ".ant-picker-input input,input:not([type='hidden'])",
            timeout=0.3,
        ) or []
    except Exception:
        inputs = []
    return [item for item in inputs if _element_is_visible(item)]


def _date_picker_values(picker) -> list[str]:
    values = []
    for item in _date_picker_inputs(picker):
        value = None
        try:
            value = item.property("value")
        except Exception:
            pass
        if value is None:
            try:
                value = item.attr("value")
            except Exception:
                value = None
        values.append(str(value or ""))
    return values


def _resolve_date_picker(field_name: str, in_frame: bool = True, scope: str = "auto",
                         select_index: int = 0, timeout: float = 5.0) -> dict:
    """Find only the date value control, never a sibling Quick Filter select."""
    field_name = str(field_name or "").strip()
    scope = str(scope or "auto").strip().lower()
    supported = {"auto", "top", "frame", "iframe", "modal", "drawer", "overlay", "filter", "page"}
    if not field_name:
        return {"ok": False, "reason": "field_name is required"}
    if scope not in supported:
        return {"ok": False, "reason": "unsupported scope: %s" % scope}

    timeout = max(float(timeout or 0), 0.0)
    deadline = time.monotonic() + timeout
    select_index = max(int(select_index or 0), 0)

    def remaining(cap=None):
        available = max(deadline - time.monotonic(), 0.0)
        return min(available, cap) if cap is not None else available

    tab, contexts, areas = _date_field_contexts(in_frame, scope)
    if scope in {"frame", "iframe"} and not any(name == "iframe" for name, _ in contexts):
        return {"ok": False, "reason": "未找到活动 iframe"}

    picker_locator = "css:.ant-calendar-picker,.ant-picker"

    def from_container(container, scope_name: str, area: str, component: str):
        try:
            pickers = [
                item for item in container.eles(picker_locator, timeout=remaining(0.4))
                if _element_is_visible(item)
            ]
        except Exception:
            pickers = []
        if not pickers:
            return None
        index = min(select_index, len(pickers) - 1)
        picker = pickers[index]
        inputs = _date_picker_inputs(picker)
        range_inputs = []
        try:
            range_inputs = [
                item for item in picker.eles("css:input.ant-calendar-range-picker-input", timeout=0.2)
                if _element_is_visible(item)
            ]
        except Exception:
            pass
        return {
            "ok": True,
            "tab": tab,
            "target": dict(contexts).get(scope_name),
            "container": container,
            "picker": picker,
            "inputs": inputs,
            "picker_mode": "range" if len(range_inputs) >= 2 else "single",
            "scope": scope_name,
            "area": area,
            "component": component,
            "select_index": index,
        }

    for scope_name, context in contexts:
        for area in areas:
            if remaining() <= 0:
                break
            if area == "filter":
                column, _ = filter_area._quick_filter_field_column(context, field_name)
                if column is not None:
                    resolved = from_container(
                        column, scope_name, area, "legions-pro-quick-filter",
                    )
                    if resolved is not None:
                        return resolved
                continue

            containers = _semantic_field_candidates(context, field_name, area, remaining(2.0))
            exact = [
                item for item in containers
                if _compact_text(_field_container_label(item)) == _compact_text(field_name)
            ]
            for container in reversed(exact or containers):
                resolved = from_container(container, scope_name, area, "ant-design")
                if resolved is not None:
                    return resolved

    reason = "field lookup timed out" if remaining() <= 0 else "date field not found"
    return {"ok": False, "reason": "%s: %s" % (reason, field_name),
            "scope": scope, "in_frame": in_frame}


def _open_date_calendar(resolved: dict, timeout: float) -> tuple[object, object] | tuple[None, None]:
    picker = resolved["picker"]
    inputs = resolved.get("inputs") or _date_picker_inputs(picker)
    opener = inputs[0] if inputs else None
    if opener is None:
        try:
            opener = picker.ele(
                "css:.ant-calendar-picker-input,.ant-picker-input", timeout=min(timeout, 0.5)
            )
        except Exception:
            opener = None
    opener = opener or picker
    try:
        waiter = getattr(opener, "wait", None)
        if waiter is not None:
            waiter.clickable(timeout=timeout, wait_stop=True, raise_err=False)
        try:
            opener.click(by_js=False, timeout=timeout, wait_stop=True)
        except TypeError:
            opener.click()
    except Exception:
        return None, None

    targets = [resolved.get("target"), resolved.get("tab")]
    seen = set()
    deadline = time.monotonic() + max(float(timeout or 0), 0.0)
    for target in targets:
        if target is None or id(target) in seen:
            continue
        seen.add(id(target))
        remaining = max(deadline - time.monotonic(), 0.0)
        if remaining <= 0:
            break
        cal = _find_calendar_root(target, timeout=min(remaining, 3.0))
        if cal is not None and _element_is_visible(cal):
            return target, cal
    return None, None


def _calendar_date_cell(cal, normalized: dict):
    selectors = (
        'c:td[title="%s"]:not(.ant-calendar-last-month-cell)'
        ':not(.ant-calendar-next-month-btn-day)'
        ':not(.ant-calendar-next-month-cell) .ant-calendar-date' % normalized["slash"],
        'c:td[title="%s"] .ant-calendar-date' % normalized["slash"],
    )
    for selector in selectors:
        try:
            cell = cal.ele(selector, timeout=0.2)
        except Exception:
            cell = None
        if cell is not None and _element_is_visible(cell):
            return cell
    return None


def _select_calendar_date(cal, normalized: dict, deadline: float) -> dict:
    navigations = []
    target_index = normalized["year"] * 12 + normalized["month"]
    for _ in range(600):
        if time.monotonic() >= deadline:
            return {"ok": False, "reason": "日期选择超时", "navigations": navigations}
        cell = _calendar_date_cell(cal, normalized)
        if cell is not None:
            try:
                cell.click(by_js=False, timeout=max(deadline - time.monotonic(), 0), wait_stop=True)
            except TypeError:
                cell.click()
            return {"ok": True, "navigations": navigations}

        current = _calendar_shown_ym(cal)
        current_index = current[0] * 12 + current[1]
        try:
            has_right_panel = cal.ele("c:.ant-calendar-range-right", timeout=0.1) is not None
        except Exception:
            has_right_panel = False
        visible_span = 1 if has_right_panel else 0
        delta = target_index - current_index
        if 0 <= delta <= visible_span:
            return {
                "ok": False,
                "reason": "未找到日期单元格: %s" % normalized["slash"],
                "navigations": navigations,
            }
        forward = delta > visible_span
        selector = "c:.ant-calendar-next-month-btn" if forward else "c:.ant-calendar-prev-month-btn"
        try:
            button = cal.ele(selector, timeout=min(max(deadline - time.monotonic(), 0), 1.0))
        except Exception:
            button = None
        if button is None:
            return {"ok": False, "reason": "未找到日历翻月按钮", "navigations": navigations}
        try:
            button.click(by_js=False, timeout=min(max(deadline - time.monotonic(), 0), 1.5), wait_stop=True)
        except TypeError:
            button.click()
        after = _wait_calendar_ym_change(cal, current, min(max(deadline - time.monotonic(), 0), 1.5))
        navigations.append({
            "direction": "next" if forward else "prev",
            "from": "%04d-%02d" % current,
            "to": "%04d-%02d" % after,
        })
        if after == current:
            return {"ok": False, "reason": "日历翻月后月份未变化", "navigations": navigations}
    return {"ok": False, "reason": "日历翻月超过上限", "navigations": navigations}


def _date_part(value: str) -> str:
    matched = re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", str(value or ""))
    return matched.group(0).replace("/", "-") if matched else ""


@mcp.tool()
@write_synchronized
def set_date(field_name: str, date: str = None, start_date: str = None,
             end_date: str = None, in_frame: bool = True, timeout: float = 8,
             select_index: int = 0, scope: str = "auto") -> dict:
    """统一设置日期字段，自动适配 Ant DatePicker/RangePicker 与 Legions Quick Filter。

    单日期传 ``date``；日期范围传 ``start_date`` 和 ``end_date``。工具按真实控件形态
    自动选择单日或范围交互。Quick Filter 的单边界日期字段只能接收一个日期。
    日期格式支持 YYYY-MM-DD 或 YYYY/MM/DD。
    """
    timeout = max(float(timeout or 0), 0.0)
    started = time.monotonic()
    deadline = started + timeout

    def remaining(cap=None):
        value = max(deadline - time.monotonic(), 0.0)
        return min(value, cap) if cap is not None else value
    has_single = date not in (None, "")
    has_range = start_date not in (None, "") or end_date not in (None, "")
    if has_single and has_range:
        return {"ok": False, "reason": "date 不能与 start_date/end_date 同时使用"}
    if not has_single and not (start_date not in (None, "") and end_date not in (None, "")):
        return {"ok": False, "reason": "请传 date，或同时传 start_date 和 end_date"}

    requested_mode = "single" if has_single else "range"
    start_raw = date if has_single else start_date
    end_raw = date if has_single else end_date
    normalized_start = _normalize_date_value(start_raw)
    if not normalized_start.get("ok"):
        return normalized_start
    normalized_end = _normalize_date_value(end_raw)
    if not normalized_end.get("ok"):
        return normalized_end
    if normalized_start["dash"] > normalized_end["dash"]:
        return {"ok": False, "reason": "开始日期不能晚于结束日期"}

    resolved = _resolve_date_picker(
        field_name, in_frame=in_frame, scope=scope, select_index=select_index,
        timeout=remaining(5),
    )
    if not resolved.get("ok"):
        resolved["action"] = "set_date"
        resolved["field_name"] = field_name
        resolved["elapsedMs"] = int((time.monotonic() - started) * 1000)
        return resolved

    picker_mode = resolved["picker_mode"]
    if picker_mode == "single" and normalized_start["dash"] != normalized_end["dash"]:
        return {
            "ok": False,
            "reason": "目标是单边界日期控件，不能写入不同的开始和结束日期；请结合筛选操作符并传 date",
            "action": "set_date",
            "field_name": field_name,
            "component": resolved["component"],
            "picker_mode": picker_mode,
            "elapsedMs": int((time.monotonic() - started) * 1000),
        }

    before_values = _date_picker_values(resolved["picker"])
    calendar_target, cal = _open_date_calendar(resolved, remaining(4))
    if cal is None:
        return {
            "ok": False, "reason": "日历面板未弹出", "action": "set_date",
            "field_name": field_name, "component": resolved["component"],
            "picker_mode": picker_mode,
            "elapsedMs": int((time.monotonic() - started) * 1000),
        }

    opened = _calendar_snapshot(calendar_target, normalized_start["slash"])
    selected_start = _select_calendar_date(cal, normalized_start, deadline)
    if not selected_start.get("ok"):
        return {
            **selected_start, "action": "set_date", "field_name": field_name,
            "component": resolved["component"], "picker_mode": picker_mode,
            "opened": opened, "elapsedMs": int((time.monotonic() - started) * 1000),
        }

    navigations = list(selected_start.get("navigations") or [])
    if picker_mode == "range":
        cal = _find_calendar_root(calendar_target, timeout=remaining(2)) or cal
        selected_end = _select_calendar_date(cal, normalized_end, deadline)
        navigations.extend(selected_end.get("navigations") or [])
        if not selected_end.get("ok"):
            return {
                **selected_end, "action": "set_date", "field_name": field_name,
                "component": resolved["component"], "picker_mode": picker_mode,
                "opened": opened, "navigations": navigations,
                "elapsedMs": int((time.monotonic() - started) * 1000),
            }

    try:
        calendar_target.wait.ele_hidden(
            "c:.ant-calendar-picker-container .ant-calendar",
            timeout=remaining(2), raise_err=False,
        )
    except Exception:
        pass

    refreshed = _resolve_date_picker(
        field_name, in_frame=in_frame, scope=scope, select_index=select_index,
        timeout=remaining(1),
    )
    after_picker = refreshed.get("picker") if refreshed.get("ok") else resolved["picker"]
    after_values = _date_picker_values(after_picker)
    actual_start = _date_part(after_values[0] if after_values else "")
    actual_end = _date_part(after_values[1] if len(after_values) > 1 else "")
    if picker_mode == "range":
        ok = actual_start == normalized_start["dash"] and actual_end == normalized_end["dash"]
    else:
        ok = actual_start == normalized_start["dash"]
    result = {
        "ok": ok,
        "action": "set_date",
        "field_name": field_name,
        "requested_mode": requested_mode,
        "picker_mode": picker_mode,
        "component": resolved["component"],
        "scope": resolved["scope"],
        "area": resolved["area"],
        "field": {
            "before": before_values,
            "after": after_values,
        },
        "calendar": {
            "opened": {
                "title": opened.get("title"),
                "cellCount": opened.get("cellCount"),
                "rect": opened.get("rect"),
            },
            "navigations": navigations,
        },
        "elapsedMs": int((time.monotonic() - started) * 1000),
    }
    if requested_mode == "single":
        result["date"] = normalized_start["dash"]
    else:
        result["startDate"] = normalized_start["dash"]
        result["endDate"] = normalized_end["dash"]
    if not ok:
        result["reason"] = "日期字段值校验失败"
    return result


def _resolve_target_action(target, action_name: str, locator, x, y, field_name,
                           row, col, column_title, kind, table_index, icon_name,
                           option_text, key, modifiers, in_frame: bool):
    """Normalize semantic target input into the legacy action parameters."""
    meta = {}
    target = _normalize_target_dict(target)
    if not target:
        return {
            "action": action_name, "locator": locator, "x": x, "y": y,
            "field_name": field_name, "row": row, "col": col,
            "column_title": column_title, "kind": kind, "table_index": table_index,
            "icon_name": icon_name, "option_text": option_text, "key": key,
            "modifiers": modifiers, "target_meta": meta, "target_error": None,
            "in_frame": in_frame,
        }

    target_type = str(_target_get(target, "type", "kind", default="") or "").lower()
    if not target_type:
        if _target_get(target, "x") is not None and _target_get(target, "y") is not None:
            target_type = "xy"
        elif _target_get(target, "field_name", "name") is not None:
            target_type = "field"
        elif _target_get(target, "row") is not None:
            target_type = "table_cell"
        else:
            target_type = "locator"

    meta["target_type"] = target_type
    if target_type in ("xy", "point", "coord", "coordinate"):
        x = float(_target_get(target, "x", "cx", "viewportX", default=x))
        y = float(_target_get(target, "y", "cy", "viewportY", default=y))
        action_name = "click_xy"
    elif target_type in ("action", "button", "toolbar"):
        resolved = _resolve_visible_action_target(target, in_frame=in_frame)
        if not resolved.get("ok"):
            return {"target_error": resolved, "target_meta": meta, "action": action_name,
                    "locator": locator, "x": x, "y": y, "field_name": field_name,
                    "row": row, "col": col, "column_title": column_title, "kind": kind,
                    "table_index": table_index, "icon_name": icon_name,
                    "option_text": option_text, "key": key, "modifiers": modifiers}
        action_name = resolved["action"]
        locator = resolved.get("locator") or locator
        x = resolved.get("x")
        y = resolved.get("y")
        in_frame = resolved.get("in_frame", in_frame)
        meta.update(resolved.get("meta") or {})
    elif target_type in ("field", "form_field", "date", "date-picker", "select"):
        field_name = str(_target_get(target, "field_name", "name", "label", default=field_name) or "")
        action_name = "field_click" if action_name in ("click", "click_xy") else action_name
        meta["field_name"] = field_name
        meta["scope"] = str(_target_get(target, "scope", default="auto") or "auto")
        meta["select_index"] = int(_target_get(target, "select_index", "selectIndex", default=0) or 0)
        if target_type in ("date", "date-picker") or any(word in field_name for word in ("日期", "时间")):
            meta["control_type"] = "date-picker"
        elif target_type == "select":
            meta["control_type"] = "select"
    elif target_type in ("css", "xpath", "text", "locator"):
        if target_type == "css":
            locator = _as_locator("css", _target_get(target, "value", "css", "selector", default=locator))
        elif target_type == "xpath":
            locator = _as_locator("xpath", _target_get(target, "value", "xpath", default=locator))
        elif target_type == "text":
            locator = _as_locator("text", _target_get(target, "value", "text", "name", default=locator))
        else:
            locator = _target_get(target, "locator", "value", default=locator)
        action_name = "click" if action_name == "click_xy" else action_name
    elif target_type in ("table_cell", "cell"):
        action_name = "table_cell"
        row = int(_target_get(target, "row", default=row) or 0)
        col = _target_get(target, "col", "column", default=col)
        col = int(col) if col is not None else None
        column_title = _target_get(target, "column_title", "columnTitle", "title", default=column_title)
        kind = _target_get(target, "table_kind", "kind", default=kind)
        table_index = int(_target_get(target, "table_index", "tableIndex", default=table_index) or 0)
        icon_name = _target_get(target, "icon_name", "iconName", default=icon_name)
    elif target_type in ("option", "select_option"):
        action_name = "select_option"
        field_name = _target_get(target, "field_name", "name", "label", default=field_name)
        option_text = _target_get(target, "option_text", "option", "value", "text", default=option_text)
    elif target_type in ("key", "keyboard"):
        action_name = "press_key"
        key = _target_get(target, "key", "value", default=key)
        modifiers = _target_get(target, "modifiers", default=modifiers)
    else:
        return {"target_error": {"ok": False, "reason": "unsupported target type: %s" % target_type},
                "target_meta": meta, "action": action_name, "locator": locator, "x": x, "y": y,
                "field_name": field_name, "row": row, "col": col,
                "column_title": column_title, "kind": kind, "table_index": table_index,
                "icon_name": icon_name, "option_text": option_text, "key": key,
                "modifiers": modifiers}

    return {
        "action": action_name, "locator": locator, "x": x, "y": y,
        "field_name": field_name, "row": row, "col": col,
        "column_title": column_title, "kind": kind, "table_index": table_index,
        "icon_name": icon_name, "option_text": option_text, "key": key,
        "modifiers": modifiers, "target_meta": meta, "target_error": None,
        "in_frame": in_frame,
    }


def _signals_for_expect(expect) -> list[str]:
    if not expect:
        return []
    if isinstance(expect, (list, tuple, set)):
        parts = [str(x).lower() for x in expect]
    else:
        parts = [p.strip().lower() for p in str(expect).replace("|", ",").split(",") if p.strip()]
    mapping = {
        "none": [],
        "modal": ["modal"],
        "drawer": ["drawer"],
        "overlay": ["overlay"],
        "calendar": ["calendar"],
        "date": ["calendar"],
        "dropdown": ["dropdown"],
        "select": ["dropdown"],
        "toast": ["message", "notification"],
        "message": ["message"],
        "notification": ["notification"],
        "network": ["network"],
        "navigation": ["url", "tab"],
        "url": ["url"],
        "tab": ["tab"],
    }
    out = []
    for part in parts:
        for sig in mapping.get(part, [part]):
            if sig and sig not in out:
                out.append(sig)
    return out


def _infer_expect(expect, action_name: str, target_meta: dict) -> str:
    raw = str(expect or "auto").strip().lower()
    if raw and raw != "auto":
        return raw
    control_type = (target_meta or {}).get("control_type", "")
    if control_type == "date-picker":
        return "calendar"
    if control_type in ("select", "searchable-select"):
        return "dropdown"
    target_type = (target_meta or {}).get("target_type", "")
    text = _compact_text((target_meta or {}).get("text", ""))
    if target_type == "action":
        if any(word in text for word in ("添加", "新增", "编辑", "详情", "查看")):
            return "modal"
        if any(word in text for word in ("保存", "确定", "提交", "删除", "审核")):
            return "toast"
    if action_name == "select_option":
        return "dropdown"
    return "auto"


def _resolve_observe_policy(signals, listen_targets, expect: str, observe_mode: str,
                            include_snapshot, detail: str, action_name: str,
                            target_meta: dict, timeout: float) -> dict:
    mode = str(observe_mode or "auto").strip().lower()
    if mode not in ("auto", "fast", "evidence", "full", "none", "off"):
        mode = "auto"
    inferred_expect = _infer_expect(expect, action_name, target_meta)
    semantic_requested = (
        str(expect or "auto").strip().lower() != "auto"
        or mode not in ("auto",)
        or bool(target_meta)
    )

    if signals is not None:
        effective_signals = signals
    elif semantic_requested and inferred_expect != "auto":
        effective_signals = _signals_for_expect(inferred_expect)
    elif semantic_requested and mode in ("fast", "none", "off"):
        effective_signals = []
    else:
        effective_signals = (
            ["overlay", "notification", "message", "tab", "url", "network"]
            if listen_targets else ["overlay", "notification", "message", "tab", "url"]
        )

    if mode in ("none", "off") or inferred_expect == "none":
        effective_signals = []

    effective_detail = "full" if mode == "full" and detail == "summary" else detail
    if include_snapshot is None:
        effective_snapshot = mode not in ("fast", "none", "off")
    else:
        effective_snapshot = bool(include_snapshot)

    wait_timeout = timeout
    if mode == "fast":
        wait_timeout = min(timeout, 2.0)

    return {
        "mode": mode,
        "expect": inferred_expect,
        "signals": effective_signals,
        "include_snapshot": effective_snapshot,
        "detail": effective_detail,
        "timeout": wait_timeout,
        "skip_observe": not effective_signals,
    }


@mcp.tool()
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
                   capture_before: bool = False, capture_after: bool = False,
                   include_snapshot: bool = None, detail: str = "summary",
                   expect: str = "auto", observe_mode: str = "auto",
                   clean_overlays: bool = True) -> dict:
    """动作探索封装：observe_start → 执行动作 → observe_wait → 可选页面模型快照。

    enterprise profile 的 action 可选 click/input/set_date/table_cell/select_option/press_key。
    set_date 通过 date 设置单日，通过 start_date/end_date 设置范围。target 可选语义目标：
    {"type":"field","name":"工作日期"}、{"type":"button","text":"添加"}、
    {"type":"css","value":"button.ant-btn"}、{"type":"xpath","value":"//button"}、
    也可使用旧参数 locator/field_name。enterprise profile 禁止显式坐标、JS 点击和跳过观察；
    这些兼容参数只在 full profile 的开发诊断中可用。

    瘦身说明（2026-07）：
    - capture_after 默认 False，避免返回冗余的完整页面模型（actions/fields/modals/tables）。
    - observe_mode=fast/none 可减少或跳过点击后观察；expect=modal/calendar/dropdown/toast/network 等表达观察意图。
    - include_snapshot 默认按 observe_mode 推断；旧调用默认仍返回精简浮层快照。
    - 只需确认浮层有无 → 用 signal.snapshot_after 即可。
    - 需要完整页面动作列表/表格数据 → 显式 capture_after=True。
    """
    flow_started = time.perf_counter()
    action_name = (action or "click").lower()
    requested_target_type = (
        str(target.get("type") or "").strip().lower()
        if isinstance(target, dict) else ""
    )
    if caps.ENABLED_PROFILE == "enterprise":
        violations = []
        if action_name == "click_xy" or requested_target_type == "xy" or x is not None or y is not None:
            violations.append("显式坐标动作")
        if by_js:
            violations.append("JS 点击")
        if str(observe_mode or "auto").strip().lower() == "none" or signals == []:
            violations.append("跳过动作观察")
        if violations:
            return {
                "ok": False,
                "reason": "enterprise profile 禁止%s；请使用语义 target 并保留业务反馈观察" % "、".join(violations),
                "profile": caps.ENABLED_PROFILE,
            }
    resolved = _resolve_target_action(
        target, action_name, locator, x, y, field_name, row, col, column_title,
        kind, table_index, icon_name, option_text, key, modifiers, in_frame,
    )
    target_meta = resolved.get("target_meta") or {}
    target_error = resolved.get("target_error")
    if not target_error:
        action_name = resolved["action"]
        locator = resolved["locator"]
        x = resolved["x"]
        y = resolved["y"]
        field_name = resolved["field_name"]
        row = resolved["row"]
        col = resolved["col"]
        column_title = resolved["column_title"]
        kind = resolved["kind"]
        table_index = resolved["table_index"]
        icon_name = resolved["icon_name"]
        option_text = resolved["option_text"]
        key = resolved["key"]
        modifiers = resolved["modifiers"]
        in_frame = resolved.get("in_frame", in_frame)

    if _recipe_requires_native_actions() and action_name == "click_xy":
        target_error = {
            "ok": False,
            "reason": "run_test_cases 禁止普通坐标点击；请提供可定位的 DOM 控件，VTable 请使用专用动作",
        }

    observe_policy = _resolve_observe_policy(
        signals, listen_targets, expect, observe_mode, include_snapshot, detail,
        action_name, target_meta, timeout,
    )
    if capture_after and include_snapshot is None:
        observe_policy["include_snapshot"] = False
    before = None
    if capture_before:
        before = page_model.capture_page_model(include_filters=False, include_table_data=False,
                                               max_table_rows=20, max_elements=80)

    if observe_policy["skip_observe"]:
        observe_start_result = {"ok": True, "session": "skipped",
                                "reason": "observe disabled by expect/observe_mode"}
    else:
        observe_start_result = observe.observe_start(
            signals=observe_policy["signals"],
            listen_targets=listen_targets,
            native_wait=_recipe_requires_native_actions(),
        )
    action_result = {"ok": False, "reason": "action not executed"}
    cleanup = _pre_click_cleanup(clean_overlays)
    try:
        tab = browser_session.get_tab()
        if target_error:
            action_result = dict(target_error)
        elif action_name == "click":
            if not locator:
                action_result = {"ok": False, "reason": "locator is required for click"}
            else:
                action_result = _resolve_and_click(
                    locator, in_frame=in_frame, by_js=by_js, timeout=timeout,
                )
                if action_result.get("ok"):
                    action_result["action"] = "click"
        elif action_name == "click_xy":
            if x is None or y is None:
                action_result = {"ok": False, "reason": "x/y are required for click_xy"}
            else:
                tab.actions.move_to((x, y), duration=0.3).click()
                action_result = {"ok": True, "action": "click_xy", "x": x, "y": y}
        elif action_name == "input":
            if text is None:
                action_result = {"ok": False, "reason": "text is required for input"}
            elif field_name:
                action_result = set_field_value(
                    field_name, text, in_frame=in_frame, clear=True, timeout=timeout,
                    scope=target_meta.get("scope", "auto"),
                    select_index=int(target_meta.get("select_index", 0) or 0),
                )
                action_result["action"] = "input"
            elif not locator:
                action_result = {"ok": False, "reason": "locator or semantic field target is required for input"}
            else:
                action_result = input(locator, text, in_frame=in_frame, timeout=timeout)
                action_result["action"] = "input"
        elif action_name == "set_date":
            if not field_name or not (date or (start_date and end_date)):
                action_result = {
                    "ok": False,
                    "reason": "set_date requires field_name and either date or start_date/end_date",
                }
            else:
                action_result = set_date(
                    field_name, date=date, start_date=start_date, end_date=end_date,
                    in_frame=in_frame, timeout=timeout,
                    scope=target_meta.get("scope", "auto"),
                    select_index=int(target_meta.get("select_index", 0) or 0),
                )
                action_result["action"] = "set_date"
        elif action_name == "date_range":
            if not field_name or not start_date or not end_date:
                action_result = {"ok": False, "reason": "field_name, start_date and end_date are required for date_range"}
            else:
                action_result = set_date(
                    field_name, start_date=start_date, end_date=end_date,
                    in_frame=in_frame, timeout=timeout,
                    scope=target_meta.get("scope", "auto"),
                    select_index=int(target_meta.get("select_index", 0) or 0),
                )
                action_result["action"] = "date_range"
        elif action_name == "field_click":
            scope = "auto" if in_frame else "top"
            action_result = _click_field_raw(field_name or "", in_frame=in_frame,
                                             timeout=min(timeout, 5), scope=target_meta.get("scope", scope),
                                             select_index=int(target_meta.get("select_index", 0) or 0))
            if action_result.get("control_type") and "control_type" not in target_meta:
                target_meta["control_type"] = action_result["control_type"]
        elif action_name == "table_cell":
            action_result = _click_table_cell_raw(
                row=row, col=col, column_title=column_title, kind=kind,
                table_index=table_index, icon_name=icon_name,
            )
            action_result["action"] = "table_cell"
        elif action_name == "select_option":
            action_result = page_model.select_option(field_name=field_name or "",
                                                     option_text=option_text or "",
                                                     timeout=min(timeout, 5))
            action_result["action"] = "select_option"
        elif action_name == "press_key":
            action_result = _press_key_raw(tab, key or "", modifiers=modifiers)
            action_result["action"] = "press_key"
        else:
            action_result = {"ok": False, "reason": "unsupported action: %s" % action}
    except Exception as e:
        action_result = {"ok": False, "reason": str(e)}
    finally:
        action_result = _attach_cleanup(action_result, cleanup)
        if observe_policy["skip_observe"]:
            signal = {"type": "skipped", "reason": observe_start_result["reason"],
                      "events": []}
        elif not action_result.get("ok"):
            signal = observe.observe_wait(timeout=0, include_snapshot=False,
                                          detail=observe_policy["detail"],
                                          native_wait=_recipe_requires_native_actions())
            signal["skipped_reason"] = "action_failed"
        else:
            signal = observe.observe_wait(
                timeout=observe_policy["timeout"],
                include_snapshot=observe_policy["include_snapshot"],
                detail=observe_policy["detail"],
                native_wait=_recipe_requires_native_actions(),
            )

    after = None
    if capture_after:
        after = page_model.capture_page_model(include_filters=False, include_table_data=False,
                                             max_table_rows=20, max_elements=80)
    result = {
        "ok": bool(action_result.get("ok")),
        "observe_start": observe_start_result,
        "observe_policy": observe_policy,
        "target": target_meta or None,
        "action": action_result,
        "signal": signal,
        "before": before,
        "after": after,
    }
    screenshot_path = None
    if flow_evidence.wants_screenshot():
        try:
            screenshot_path = resource_store.resolve_path(
                default_name="flow_step_%d.png" % time.time_ns(),
                category="screenshots",
            )
            browser_session.get_tab().get_screenshot(path=screenshot_path)
        except Exception as exc:
            logger.debug("flow screenshot failed: %s", exc)
            screenshot_path = None
    flow_step = flow_evidence.record_exploration(
        {
            "action": action, "target": target, "locator": locator, "x": x, "y": y,
            "row": row, "col": col, "column_title": column_title, "kind": kind,
            "table_index": table_index, "icon_name": icon_name, "option_text": option_text,
            "field_name": field_name, "text": text, "date": date,
            "start_date": start_date, "end_date": end_date, "key": key, "modifiers": modifiers,
            "by_js": by_js, "in_frame": in_frame, "timeout": timeout,
            "signals": signals, "listen_targets": listen_targets, "expect": expect,
            "observe_mode": observe_mode, "detail": detail, "clean_overlays": clean_overlays,
        },
        result,
        elapsed_ms=int((time.perf_counter() - flow_started) * 1000),
        screenshot=screenshot_path,
    )
    if isinstance(flow_step, dict) and flow_step.get("ok") is False:
        result = dict(result)
        result["ok"] = False
        result["reason"] = "evidence recording failed: %s" % flow_step.get("reason", "unknown error")
        result["flow_recording"] = flow_step
    elif flow_step:
        result["flow_step"] = flow_step
    return result


@mcp.tool()
@write_synchronized
def flow_start(module: str, flow_name: str = "exploration", capture_screenshots: bool = True,
               scenario_type: str = "功能测试", risk_type: str = "正常路径",
               destructive: bool = False, cleanup_strategy: str = "") -> dict:
    """开始记录真实业务流证据；后续 explore_action 自动关联元素、反馈、接口和截图。"""
    result = flow_evidence.start(
        module, flow_name, capture_screenshots,
        scenario_type=scenario_type,
        risk_type=risk_type,
        destructive=destructive,
        cleanup_strategy=cleanup_strategy,
    )
    if result.get("ok"):
        resource_store.set_module(module)
    return result


@mcp.tool()
@read_synchronized
def flow_status() -> dict:
    """返回当前或最近一次业务流证据的状态与步骤数量。"""
    return flow_evidence.status()


@mcp.tool()
@write_synchronized
def flow_capture_page_state(label: str = "initial", include_filters: bool = True,
                            include_tables: bool = True, max_table_rows: int = 30) -> dict:
    """采集当前 iframe 的元素、DOM、表单、浮层和表格资产，并写入活动业务流证据。"""
    if not flow_evidence.is_active():
        return {"ok": False, "reason": "no active evidence flow; call flow_start first"}
    page_state = page_model.capture_page_model(
        include_filters=include_filters,
        include_tables=include_tables,
        include_table_data=True,
        max_table_rows=max_table_rows,
        max_elements=200,
    )
    reference = flow_evidence.record_page_state(label, page_state)
    if isinstance(reference, dict) and reference.get("ok") is False:
        return reference
    return {"ok": bool(page_state.get("ok")), "reference": reference, "page_state": page_state}


@mcp.tool()
@write_synchronized
def flow_stop(filename: str = None, cleanup_from_sequence: int = None) -> dict:
    """结束并保存业务流；破坏性流用 cleanup_from_sequence 标记必执行清理段。"""
    return flow_evidence.stop(filename, cleanup_from_sequence=cleanup_from_sequence)


def _read_json_resource(filename: str) -> tuple[dict | None, str | None]:
    try:
        value = resource_store.read_json_resource(filename)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, str(exc)
    return value, None


def _safe_artifact_segment(value: str, fallback: str = "default") -> str:
    value = re.sub(r'[^0-9A-Za-z_\-\u4e00-\u9fff]+', "_", str(value or "").strip())
    return value.strip("._-") or fallback


def _artifact_root() -> str:
    """Use the project root normally, while respecting isolated test resource roots."""
    project_root = os.path.abspath(config.PROJECT_ROOT)
    shot_root = os.path.abspath(config.SHOT_DIR)
    try:
        return project_root if os.path.commonpath([project_root, shot_root]) == project_root else shot_root
    except ValueError:
        return shot_root


def _module_artifact_name(module_info: dict | None) -> str:
    info = module_info or {}
    level1 = (info.get("module_level1_pinyin") or info.get("level1_pinyin") or
              info.get("module_level1") or "")
    module = (info.get("module_pinyin") or info.get("module_level2") or
              info.get("module_name") or "default")
    parts = [_safe_artifact_segment(item) for item in (level1, module) if str(item or "").strip()]
    return "_".join(parts) or "default"


def _resolve_artifact_path(filename: str | None, category: str, module_info: dict | None,
                           default_name: str) -> str:
    root = os.path.realpath(os.path.abspath(_artifact_root()))
    requested = str(filename or "").strip()
    if requested and os.path.isabs(requested):
        candidate = os.path.abspath(requested)
    elif requested and os.path.dirname(requested):
        candidate = os.path.abspath(os.path.join(root, requested))
    else:
        candidate = os.path.join(
            root, category, _module_artifact_name(module_info), requested or default_name,
        )
    parent = os.path.realpath(os.path.dirname(candidate) or root)
    try:
        if os.path.commonpath([root, parent]) != root:
            raise ValueError("artifact path escapes project directory")
    except ValueError:
        raise ValueError("artifact path escapes project directory") from None
    os.makedirs(parent, exist_ok=True)
    return os.path.join(parent, os.path.basename(candidate))


def _report_bundle_path(filename: str | None, module_info: dict | None,
                        execution_file: str) -> tuple[str, str]:
    """Return ``(report.md, bundle_dir)`` with every report asset kept locally."""
    requested = str(filename or "").strip()
    report_name = os.path.basename(requested) if requested else "test_report_%d.md" % int(time.time())
    if not report_name.lower().endswith(".md"):
        report_name += ".md"
    if requested and (os.path.isabs(requested) or os.path.dirname(requested)):
        direct = _resolve_artifact_path(requested, "", module_info, report_name)
        bundle_dir = os.path.dirname(direct)
        return direct, bundle_dir
    run_name = _safe_artifact_segment(
        os.path.splitext(os.path.basename(str(execution_file)))[0], "execution",
    )
    report_path = _resolve_artifact_path(
        os.path.join("test_results", "reports", _module_artifact_name(module_info),
                     run_name, report_name),
        "", module_info, report_name,
    )
    return report_path, os.path.dirname(report_path)


def _bundle_report_assets(execution: dict, execution_file: str, bundle_dir: str) -> dict:
    """复制受信目录内的真实位图证据，并在报告目录写入执行快照。"""
    bundled = flow_evidence.sanitize_artifact(execution)
    if not isinstance(bundled, dict):
        raise ValueError("execution must be an object")
    bundle_root = os.path.realpath(os.path.abspath(bundle_dir))
    assets_dir = os.path.realpath(os.path.join(bundle_root, "assets"))
    if os.path.commonpath([bundle_root, assets_dir]) != bundle_root:
        raise ValueError("report assets path escapes bundle directory")
    os.makedirs(assets_dir, exist_ok=True)
    copied, missing, used_names = [], [], set()
    copied_sources = {}
    allowed_roots = {
        os.path.realpath(os.path.abspath(config.SHOT_DIR)),
        os.path.realpath(os.path.abspath(config.PROJECT_ROOT)),
    }
    try:
        execution_dir = os.path.dirname(resource_store._resolve_existing_path(execution_file))
    except (OSError, ValueError):
        execution_dir = ""

    def allowed_source(path: str) -> bool:
        for root in allowed_roots:
            try:
                if os.path.commonpath([root, path]) == root:
                    return True
            except ValueError:
                continue
        return False

    def image_kind(path: str) -> str | None:
        try:
            size = os.path.getsize(path)
            if size <= 0 or size > 20_000_000:
                return None
            with open(path, "rb") as source:
                data = source.read()
        except OSError:
            return None
        if (len(data) >= 45 and data.startswith(b"\x89PNG\r\n\x1a\n")
                and data[12:16] == b"IHDR"
                and int.from_bytes(data[16:20], "big") > 0
                and int.from_bytes(data[20:24], "big") > 0
                and data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")):
            return ".png"
        if len(data) >= 4 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
            return ".jpg"
        if (len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
                and int.from_bytes(data[4:8], "little") == len(data) - 8):
            return ".webp"
        return None

    def resolve_screenshot(raw: str) -> str | None:
        candidates = [str(raw)] if os.path.isabs(str(raw)) else [
            *([os.path.join(execution_dir, str(raw))] if execution_dir else []),
            os.path.join(config.SHOT_DIR, str(raw)),
            os.path.join(config.PROJECT_ROOT, str(raw)),
        ]
        for candidate in candidates:
            source = os.path.realpath(os.path.abspath(candidate))
            if os.path.isfile(source) and allowed_source(source):
                return source
        return None

    def update_ref_path(ref: dict, original: str, relative: str | None) -> None:
        ref["source_screenshot"] = original
        artifacts = ref.get("artifacts") if isinstance(ref.get("artifacts"), dict) else None
        if relative:
            ref["screenshot"] = relative
            ref.pop("screenshot_missing", None)
            if artifacts is not None and "screenshot" in artifacts:
                artifacts["screenshot"] = relative
        else:
            ref.pop("screenshot", None)
            ref["screenshot_missing"] = True
            if artifacts is not None:
                artifacts.pop("screenshot", None)


    def copy_ref(ref: dict) -> None:
        artifacts = ref.get("artifacts") if isinstance(ref, dict) else None
        screenshot = ref.get("screenshot") if isinstance(ref, dict) else None
        if not screenshot and isinstance(artifacts, dict):
            screenshot = artifacts.get("screenshot")
        if not screenshot:
            return
        original = str(screenshot)
        source = resolve_screenshot(original)
        if not source:
            missing.append(original)
            update_ref_path(ref, original, None)
            return
        if source in copied_sources:
            update_ref_path(ref, original, copied_sources[source])
            return
        ext = image_kind(source)
        if not ext:
            missing.append(original)
            update_ref_path(ref, original, None)
            return
        stem = _safe_artifact_segment(os.path.splitext(os.path.basename(source))[0], "evidence")
        base = stem + ext
        candidate, index = base, 2
        while candidate in used_names:
            candidate = "%s_%d%s" % (stem, index, ext)
            index += 1
        used_names.add(candidate)
        destination = os.path.join(assets_dir, candidate)
        relative = "assets/%s" % candidate
        if os.path.realpath(destination) != source:
            temp_path = None
            try:
                descriptor, temp_path = tempfile.mkstemp(prefix=".asset-", dir=assets_dir)
                os.close(descriptor)
                shutil.copy2(source, temp_path)
                os.replace(temp_path, destination)
                temp_path = None
            except OSError:
                missing.append(original)
                update_ref_path(ref, original, None)
                return
            finally:
                if temp_path:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
        copied_sources[source] = relative
        update_ref_path(ref, original, relative)
        copied.append(relative)

    for result in bundled.get("results", []):
        if not isinstance(result, dict):
            continue
        refs = result.get("evidence_refs")
        for ref in refs if isinstance(refs, list) else []:
            if isinstance(ref, dict):
                copy_ref(ref)
        defect = result.get("known_defect")
        if isinstance(defect, dict):
            refs = defect.get("evidence_refs")
            for ref in refs if isinstance(refs, list) else []:
                if isinstance(ref, dict):
                    copy_ref(ref)
    coverage_matrix = bundled.get("coverage_matrix")
    for row in coverage_matrix if isinstance(coverage_matrix, list) else []:
        if not isinstance(row, dict):
            continue
        refs = row.get("asset_evidence_refs") or row.get("evidence_refs") or []
        for ref in refs if isinstance(refs, list) else []:
            if isinstance(ref, dict):
                copy_ref(ref)
    known_defects = bundled.get("known_defects")
    for defect in known_defects if isinstance(known_defects, list) else []:
        if isinstance(defect, dict):
            refs = defect.get("evidence_refs")
            for ref in refs if isinstance(refs, list) else []:
                if isinstance(ref, dict):
                    copy_ref(ref)
    snapshot_path = os.path.join(bundle_dir, "execution.json")
    resource_store.write_json_atomic(snapshot_path, bundled)
    return {"execution": bundled, "execution_copy": snapshot_path,
            "assets_dir": assets_dir, "copied": copied,
            "missing": list(dict.fromkeys(missing))}


def _next_case_id_start(case_dir: str, exclude_path: str = None) -> dict:
    highest = {}
    if not os.path.isdir(case_dir):
        return {"default": 1}
    excluded = os.path.abspath(exclude_path) if exclude_path else None
    scanned_files = 0
    for root, _, names in os.walk(case_dir):
        for name in names:
            if not name.lower().endswith(".json"):
                continue
            if scanned_files >= 10_000:
                return {**{prefix: value + 1 for prefix, value in highest.items()},
                        "default": 1}
            scanned_files += 1
            source_path = os.path.abspath(os.path.join(root, name))
            if excluded and source_path == excluded:
                continue
            try:
                payload = resource_store.read_json_resource(source_path, max_bytes=10_000_000)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            raw_cases = payload.get("test_cases") if isinstance(payload, dict) else None
            for case in raw_cases if isinstance(raw_cases, list) else []:
                if not isinstance(case, dict):
                    continue
                match = re.search(r"([A-Za-z])(\d+)$", str(case.get("case_id", "")))
                if match:
                    prefix = match.group(1).upper()
                    highest[prefix] = max(highest.get(prefix, 0), int(match.group(2)))
    return {**{prefix: value + 1 for prefix, value in highest.items()}, "default": 1}


@mcp.tool()
@write_synchronized
def generate_test_cases_from_flow(flow_file: str, module_info: dict = None,
                                  filename: str = None) -> dict:
    """由已保存的真实证据生成覆盖矩阵和 19 字段用例候选，仅输出已验证场景为正式用例。"""
    loaded = flow_evidence.load(flow_file)
    if not loaded.get("ok"):
        return loaded
    if module_info is not None and not isinstance(module_info, dict):
        return {"ok": False, "reason": "module_info must be an object"}
    info = dict(module_info or {})
    default_name = "cases_%s.json" % loaded["flow"].get("flow_id", "evidence")
    try:
        path = _resolve_artifact_path(filename, "test_cases", info, default_name)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    info.setdefault("case_id_start", _next_case_id_start(os.path.dirname(path), exclude_path=path))
    generated = testcase_generation.generate_verified_cases(loaded["flow"], info)
    persisted = flow_evidence.sanitize_artifact(generated)
    try:
        resource_store.write_json_atomic(path, persisted)
    except (OSError, TypeError, ValueError) as exc:
        return {"ok": False, "reason": "test case persistence failed: %s" % exc}
    persisted["saved_to"] = path
    return persisted


@mcp.tool()
@write_synchronized
def combine_test_case_files(case_files: list[str], filename: str = None,
                            module_info: dict = None, exclude_case_ids: list[str] = None,
                            exclude_known_defects: bool = False) -> dict:
    """合并多个真实 flow 用例文件，并按资产/场景去重汇总覆盖率。"""
    if not isinstance(case_files, list) or not case_files or len(case_files) > 100:
        return {"ok": False, "reason": "case_files must contain 1 to 100 files"}
    if any(not isinstance(item, str) or not item.strip() for item in case_files):
        return {"ok": False, "reason": "case_files entries must be non-empty strings"}
    if module_info is not None and not isinstance(module_info, dict):
        return {"ok": False, "reason": "module_info must be an object"}
    if exclude_case_ids is not None and not isinstance(exclude_case_ids, list):
        return {"ok": False, "reason": "exclude_case_ids must be a list"}
    if exclude_case_ids is not None and (
        len(exclude_case_ids) > 100_000
        or any(not isinstance(item, str) or not item.strip() for item in exclude_case_ids)
    ):
        return {"ok": False, "reason": "exclude_case_ids entries must be non-empty strings"}
    if not isinstance(exclude_known_defects, bool):
        return {"ok": False, "reason": "exclude_known_defects must be a boolean"}
    total_bytes = 0
    payloads = []
    for case_file in case_files:
        try:
            total_bytes += os.path.getsize(resource_store._resolve_existing_path(case_file))
        except (OSError, ValueError):
            pass
        if total_bytes > 200_000_000:
            return {"ok": False, "reason": "combined case files exceed 200000000 bytes"}
        payload, error = _read_json_resource(case_file)
        if error:
            return {"ok": False, "reason": "%s: %s" % (case_file, error)}
        payloads.append(payload)
    if not payloads:
        return {"ok": False, "reason": "case_files is empty"}
    merged = testcase_generation.merge_generated_suites(
        payloads, module_info=module_info,
        exclude_case_ids=exclude_case_ids,
        exclude_known_defects=exclude_known_defects,
    )
    if not isinstance(merged, dict):
        return {"ok": False, "reason": "test suite merger returned an invalid result"}
    if not merged.get("ok"):
        return merged
    info = merged.get("module_info")
    if not isinstance(info, dict):
        return {"ok": False, "reason": "merged module_info must be an object"}
    try:
        path = _resolve_artifact_path(
            filename, "test_cases", info, "test_suite_%d.json" % int(time.time()),
        )
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    persisted = flow_evidence.sanitize_artifact(merged)
    try:
        resource_store.write_json_atomic(path, persisted)
    except (OSError, TypeError, ValueError) as exc:
        return {"ok": False, "reason": "test suite persistence failed: %s" % exc}
    persisted["saved_to"] = path
    return persisted


_recipe_context = threading.local()


def _recipe_values() -> dict:
    values = getattr(_recipe_context, "values", None)
    if values is None:
        values = {}
        _recipe_context.values = values
    return values


def _reset_recipe_context() -> None:
    _recipe_context.values = {}
    _recipe_context.destructive_allowed = False


def _recipe_allows_destructive() -> bool:
    return bool(getattr(_recipe_context, "destructive_allowed", False))


def _recipe_requires_native_actions() -> bool:
    """True only while ``run_test_cases`` is replaying a browser recipe."""
    return bool(getattr(_recipe_context, "native_actions_only", False))


def _recipe_ref_value(path: str):
    parts = str(path or "").strip(".").split(".")
    value = _recipe_values()
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, (list, tuple)) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise KeyError(path)
    return value


def _resolve_recipe_refs(value, _depth: int = 0):
    if _depth > 20:
        raise ValueError("recipe reference nesting exceeds 20 levels")
    if isinstance(value, dict):
        if len(value) > 2_000:
            raise ValueError("recipe argument object is too large")
        if set(value) == {"$ref"}:
            return _resolve_recipe_refs(_recipe_ref_value(value["$ref"]), _depth + 1)
        if any(not isinstance(key, str) or len(key) > 256 for key in value):
            raise ValueError("recipe argument keys must be short strings")
        return {key: _resolve_recipe_refs(item, _depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > 2_000:
            raise ValueError("recipe argument list is too large")
        return [_resolve_recipe_refs(item, _depth + 1) for item in value]
    if isinstance(value, str):
        if len(value) > 12_000:
            raise ValueError("recipe argument text is too large")
        return value
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("recipe argument number must be finite")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError("recipe argument contains an unsupported value type")


def _recipe_element_click(locator: str, in_frame: bool = True, timeout: float = 5,
                          double_click: bool = False) -> dict:
    """Formal-replay click using only DrissionPage element APIs."""
    element = browser_session.find(locator, in_frame=in_frame, timeout=timeout, wait_clickable=False)
    if not element:
        return {"ok": False, "reason": "元素未找到: %s" % locator}
    try:
        element.wait.clickable(timeout=timeout, wait_stop=True, raise_err=False)
        if not element.states.is_clickable:
            return {"ok": False, "reason": "元素不可点击: %s" % locator}
        if double_click:
            element.click.multi(times=2)
        else:
            element.click(by_js=False, wait_stop=True)
        return {"ok": True, "locator": locator,
                "method": "element.click.multi" if double_click else "element.click"}
    except Exception as exc:
        return {"ok": False, "locator": locator,
                "reason": "DrissionPage 原生点击失败: %s" % exc}


def _recipe_double_click(locator: str, in_frame: bool = True, timeout: float = 5) -> dict:
    return _recipe_element_click(locator, in_frame=in_frame, timeout=timeout, double_click=True)


def _run_recipe_action(action: str, args: dict) -> dict:
    actions = {
        "click": _recipe_element_click,
        "double_click": _recipe_double_click,
        "explore_action": explore_action,
        "set_field_value": set_field_value,
        "reset_to_initial": reset_to_initial,
        "enter_module": enter_module,
        "get_active_frame": get_active_frame,
        "check_session": check_session,
        "get_table_values": get_table_values,
        "query_table": query_table,
        "find_vtable_row": find_vtable_row,
        "count_vtable_rows": count_vtable_rows,
        "get_vtable_row_values": get_vtable_row_values,
        "scan_table": scan_table,
        "get_vtable_cell_render_info": get_vtable_cell_render_info,
        "inspect_table_cell": inspect_table_cell,
        "vtable_action": vtable_action,
        "click_table_cell": click_table_cell,
        "table_action": table_action,
        "select_option": select_option,
        "select_date_range": select_date_range,
        "set_date": set_date,
        "query_filter": _query_filter,
        "verify_filter_query": _verify_filter_query,
        "observe_snapshot": observe_snapshot,
        "network_trace_start": network_trace_start,
        "network_trace_stop": network_trace_stop,
        "browser_get_element_state": browser_get_element_state,
        "find_elements": find_elements,
        "input": input,
        "insert_text": insert_text,
        "role_session_open": role_session_open,
        "role_session_login": role_session_login,
        "role_session_start": role_session_start,
        "role_session_activate": role_session_activate,
        "role_session_list": role_session_list,
        "role_session_close": role_session_close,
    }
    runner = actions.get(action)
    if runner is None:
        return {"ok": False, "reason": "unsupported recipe action: %s" % action}
    if not isinstance(args, dict):
        return {"ok": False, "reason": "recipe arguments must be an object"}
    recorded_args = dict(args)
    effective_args = dict(recorded_args)
    save_as = str(effective_args.pop("save_as", "") or "").strip()
    if save_as:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,127}", save_as):
            return {"ok": False, "reason": "save_as must be a short identifier"}
        values = _recipe_values()
        if save_as in values:
            return {"ok": False, "reason": "save_as already exists: %s" % save_as}
        if len(values) >= 100:
            return {"ok": False, "reason": "recipe context exceeds 100 saved values"}
    try:
        effective_args = _resolve_recipe_refs(effective_args)
    except (KeyError, ValueError) as exc:
        reason = ("recipe reference not found: %s" % exc.args[0]
                  if isinstance(exc, KeyError) else str(exc))
        return {"ok": False, "reason": reason}
    if action == "explore_action" and _recipe_requires_native_actions():
        if effective_args.get("by_js"):
            return {"ok": False, "reason": "run_test_cases 禁止 by_js 点击；请使用 DrissionPage 原生元素或动作链"}
        action_name = str(effective_args.get("action") or "").lower()
        target = effective_args.get("target")
        target_type = str(target.get("type") or "").lower() if isinstance(target, dict) else ""
        if action_name == "click_xy" or target_type in {"xy", "point", "coord", "coordinate"}:
            return {"ok": False,
                    "reason": "run_test_cases 禁止普通坐标点击；VTable 请使用 vtable_action 或 click_table_cell"}
    if (_recipe_requires_native_actions()
            and test_execution.is_destructive_command({"action": action, "args": effective_args})
            and not _recipe_allows_destructive()):
        return {"ok": False, "reason": "运行期解析出的破坏性操作要求 destructive=true"}
    for name in (
        "by_js", "in_frame", "clear", "raw", "allow_empty", "clean_overlays",
        "include_snapshot", "include_table_data", "only_visible", "hover_first",
        "select_row",
    ):
        if name in effective_args and effective_args[name] is not None and not isinstance(effective_args[name], bool):
            return {"ok": False, "reason": "%s must be a boolean" % name}
    for name, lower, upper in (("timeout", 0.1, 120.0), ("duration", 0.0, 30.0)):
        if name not in effective_args:
            continue
        if isinstance(effective_args[name], bool):
            return {"ok": False, "reason": "%s must be numeric" % name}
        try:
            numeric = float(effective_args[name])
        except (TypeError, ValueError):
            return {"ok": False, "reason": "%s must be numeric" % name}
        if not math.isfinite(numeric) or numeric < lower or numeric > upper:
            return {"ok": False, "reason": "%s must be between %s and %s" %
                    (name, lower, upper)}
        effective_args[name] = numeric
    started = time.perf_counter()
    try:
        result = runner(**effective_args)
    except TypeError as exc:
        result = {"ok": False, "reason": "invalid recipe arguments for %s: %s" % (action, exc)}
    except Exception as exc:
        result = {"ok": False, "reason": "recipe action %s failed: %s: %s" %
                  (action, type(exc).__name__, exc)}
    if save_as and isinstance(result, dict) and result.get("ok") is True:
        _recipe_values()[save_as] = flow_evidence.sanitize(result)
        result = dict(result)
        result["saved_as"] = save_as
    if action == "explore_action" or not flow_evidence.is_active() or not isinstance(result, dict):
        return result

    screenshot_path = None
    if flow_evidence.wants_screenshot():
        try:
            screenshot_path = resource_store.resolve_path(
                "execution_%d.png" % time.time_ns(),
                category="screenshots",
            )
            browser_session.get_tab().get_screenshot(path=screenshot_path)
        except Exception as exc:
            logger.debug("recipe evidence screenshot failed: %s", exc)
            screenshot_path = None
    reference = flow_evidence.record_exploration(
        {"action": action, **recorded_args},
        {
            "action": {"ok": bool(result.get("ok")), "action": action,
                       "reason": result.get("reason", "")},
            "signal": {"type": "structured_result", "payload": result},
        },
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        screenshot=screenshot_path,
    )
    if isinstance(reference, dict) and reference.get("ok") is False:
        result = dict(result)
        result["ok"] = False
        result["reason"] = "evidence recording failed: %s" % reference.get("reason", "unknown error")
        result["flow_recording"] = reference
    elif reference:
        result = dict(result)
        result["flow_step"] = reference
    return result


def _http_success(status) -> bool:
    try:
        return 200 <= int(status) < 300
    except (TypeError, ValueError):
        return False


def _response_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "ok", "success", "succeeded"}:
            return True
        if normalized in {"false", "0", "no", "n", "failed", "failure", "error"}:
            return False
    return False


def _business_response_success(body) -> bool:
    if not isinstance(body, dict):
        return True
    for key in ("ok", "success"):
        if key in body:
            return _response_flag(body[key])
    if "code" in body:
        return str(body["code"]).strip().lower() in {
            "0", "200", "20000", "00000", "000000", "ok", "success", "succeeded",
        }
    if "status" in body:
        status_value = body["status"]
        if isinstance(status_value, bool):
            return status_value
        if isinstance(status_value, (int, float)):
            return status_value in {0, 1, 200, 20000}
        if isinstance(status_value, str):
            normalized = status_value.strip().lower()
            if normalized in {"true", "ok", "success", "succeeded", "0", "1", "200", "20000"}:
                return True
            return False
        return False

    return True

def _wait_query_table(frame, timeout: float = 10) -> tuple[bool, str]:
    if frame is None:
        return False, "none"
    try:
        limit = min(max(float(timeout or 0), 0.1), 120.0)
    except (TypeError, ValueError):
        return False, "none"
    deadline = time.perf_counter() + limit

    def remaining(cap: float) -> float:
        return max(0.05, min(cap, deadline - time.perf_counter()))

    if vtable.is_loading_complete(frame, remaining(limit)):
        return True, "vtable"
    try:
        if time.perf_counter() >= deadline:
            return False, "none"
        table = frame.ele("c:.ant-table-wrapper", timeout=remaining(0.5))
        if not table:
            return False, "none"
        spinner = table.ele("c:.ant-spin-spinning", timeout=remaining(0.3))
        if spinner and spinner.states.is_displayed:
            if not spinner.wait.hidden(timeout=remaining(limit), raise_err=False):
                return False, "html"
        if time.perf_counter() >= deadline:
            return False, "html"
        stable = table.wait.stop_moving(timeout=remaining(3), raise_err=False)
        return stable is not False and time.perf_counter() <= deadline, "html"
    except Exception:
        return False, "none"


def _query_filter(timeout: float = 10, listen_targets: str = "gateway") -> dict:
    """提交筛选，等待 2xx 业务响应，再被动等待 VTable 或 HTML 表格稳定。"""
    try:
        timeout = min(max(float(timeout or 0), 0.1), 120.0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "timeout 必须为正数"}
    began = time.perf_counter()
    started = observe.observe_start(
        signals=["network"], listen_targets=listen_targets,
        native_wait=_recipe_requires_native_actions(),
    )
    observe_started_at = time.perf_counter()
    if not started.get("ok"):
        return {"ok": False, "reason": "无法开始查询网络监听", "observe_start": started}
    click_result = filter_area.submit_filter_area()
    clicked_at = time.perf_counter()
    observed = observe.observe_wait(
        timeout=timeout if click_result.get("ok") else 0.1,
        include_snapshot=False, detail="summary",
        native_wait=_recipe_requires_native_actions(),
    )
    network_finished_at = time.perf_counter()
    packet = observed.get("packet") or observed.get("payload") or {}
    packet = packet if isinstance(packet, dict) else {}
    response = packet.get("response") if isinstance(packet.get("response"), dict) else {}
    status = packet.get("status", response.get("status", observed.get("status")))
    body = packet.get("body", response.get("body"))
    body = body if isinstance(body, dict) else {}
    http_ok = _http_success(status)
    business_ok = _business_response_success(body)
    loading_complete = False
    table_kind = "none"
    if (click_result.get("ok") and observed.get("type") == "network"
            and http_ok and business_ok):
        frame = browser_session.get_active_frame()
        remaining_timeout = max(0.1, timeout - (network_finished_at - observe_started_at))
        loading_complete, table_kind = _wait_query_table(frame, timeout=remaining_timeout)
    loading_finished_at = time.perf_counter()
    ok = bool(
        click_result.get("ok") and observed.get("type") == "network"
        and http_ok and business_ok and loading_complete
    )
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    network_summary = {
        "type": observed.get("type"),
        "url": packet.get("url") or observed.get("url"),
        "method": packet.get("method") or observed.get("method"),
        "api_target": packet.get("api_target") or observed.get("api_target"),
        "status": status, "elapsedMs": observed.get("elapsedMs"),
        "event_count": observed.get("event_count"),
        "request": packet.get("post_data"),
        "response": {
            "ok": body.get("ok"), "status": body.get("status"),
            "message": body.get("msg") or body.get("message"),
            "total": data.get("total"),
        },
    }
    timings = {
        "observe_start_ms": round((observe_started_at - began) * 1000, 2),
        "locate_and_click_ms": round((clicked_at - observe_started_at) * 1000, 2),
        "network_wait_ms": round((network_finished_at - clicked_at) * 1000, 2),
        "table_wait_ms": round((loading_finished_at - network_finished_at) * 1000, 2),
        "vtable_wait_ms": round((loading_finished_at - network_finished_at) * 1000, 2),
        "total_ms": round((loading_finished_at - began) * 1000, 2),
    }
    result = {
        "ok": ok, "click": click_result, "network": network_summary,
        "query_completed": ok, "loading_complete": loading_complete,
        "table_kind": table_kind, "http_ok": http_ok,
        "business_ok": business_ok, "timings": timings,
    }
    if not ok:
        result["reason"] = click_result.get("reason") or (
            "查询未在 %.1fs 内获得网络响应" % timeout
            if observed.get("type") != "network"
            else "查询接口返回 HTTP %s" % status
            if not http_ok
            else "查询接口业务响应失败"
            if not business_ok
            else "查询接口返回后 VTable 未稳定完成，且未识别到稳定的 HTML 表格"
        )
    return result


def _verify_filter_query(filters: list[dict], timeout: float = 10,
                         listen_targets: str = "gateway",
                         allow_empty: bool = False, raw: bool = False) -> dict:
    """Set filter conditions, submit once, then verify every corresponding table column."""
    if not isinstance(allow_empty, bool) or not isinstance(raw, bool):
        return {"ok": False, "verified": False,
                "reason": "allow_empty 和 raw 必须是布尔值"}
    if isinstance(timeout, bool):
        return {"ok": False, "verified": False, "reason": "timeout 必须为正数"}
    try:
        timeout = min(max(float(timeout or 0), 0.1), 120.0)
    except (TypeError, ValueError):
        return {"ok": False, "verified": False, "reason": "timeout 必须为正数"}
    if isinstance(filters, list) and len(filters) > 100:
        return {"ok": False, "verified": False, "reason": "filters 最多支持 100 项"}
    began = time.perf_counter()
    if not isinstance(filters, list) or not filters:
        return {"ok": False, "verified": False, "reason": "filters 必须是非空列表"}
    for index, condition in enumerate(filters):
        if not isinstance(condition, dict):
            return {"ok": False, "verified": False,
                    "reason": "filters[%d] 必须是对象" % index}
        if "allow_empty" in condition and not isinstance(condition["allow_empty"], bool):
            return {"ok": False, "verified": False,
                    "reason": "filters[%d].allow_empty 必须是布尔值" % index}
        field = str(condition.get("field") or "").strip()
        operator = str(condition.get("operator") or "").strip()
        if not field or not operator:
            return {"ok": False, "verified": False,
                    "reason": "filters[%d] 缺少 field/operator" % index}
        if test_execution.normalize_filter_operator(operator) is None:
            return {"ok": False, "verified": False,
                    "reason": "不支持的筛选操作符: %s" % operator}
    expanded = filter_area.expand_filter_area()
    if not expanded.get("ok"):
        return {"ok": False, "verified": False,
                "reason": "筛选区展开失败: %s" % expanded.get("reason", "")}
    configured = []
    for condition in filters:
        field = str(condition.get("field") or "").strip()
        operator = str(condition.get("operator") or "").strip()
        setup_started = time.perf_counter()
        setup = filter_area.set_filter_condition(
            field, operator, condition.get("value"), timeout=min(timeout, 5.0),
            ensure_expanded=False,
        )
        setup = dict(setup)
        setup["elapsed_ms"] = round((time.perf_counter() - setup_started) * 1000, 2)
        configured.append(setup)
        if not setup.get("ok"):
            return {
                "ok": False, "verified": False,
                "reason": "筛选条件设置失败: %s" % setup.get("reason", field),
                "configured": configured,
            }
    configured_at = time.perf_counter()

    query = _query_filter(timeout=timeout, listen_targets=listen_targets)
    queried_at = time.perf_counter()
    if not query.get("ok"):
        return {"ok": False, "verified": False, "reason": query.get("reason", "查询失败"),
                "configured": configured, "query": query}

    comparisons = []
    for condition in filters:
        comparison_started = time.perf_counter()
        field = str(condition.get("field") or "").strip()
        column_title = str(condition.get("column_title") or field).strip()
        table_values = get_table_values(column_title, kind="auto", raw=raw)
        if not table_values.get("ok"):
            return {
                "ok": False, "verified": False,
                "reason": "读取筛选对应列失败: %s" % column_title,
                "configured": configured, "query": query,
                "comparisons": comparisons,
                "table_values": {key: table_values.get(key) for key in ("ok", "kind", "reason")},
            }
        evaluation = test_execution.evaluate_filter_values(
            table_values.get("values"), condition.get("operator"), condition.get("value"),
            allow_empty=condition.get("allow_empty", allow_empty),
        )
        comparison = {
            "field": field, "column_title": column_title,
            "operator": condition.get("operator"), "expected": condition.get("value"),
            "evaluation": evaluation,
            "elapsed_ms": round((time.perf_counter() - comparison_started) * 1000, 2),
        }
        comparisons.append(comparison)
        if not evaluation.get("ok"):
            return {
                "ok": False, "verified": False, "reason": evaluation.get("reason"),
                "configured": configured, "query": query, "comparisons": comparisons,
            }

    finished_at = time.perf_counter()
    verified = all(item["evaluation"].get("matched") for item in comparisons)
    result = {
        "ok": verified, "verified": verified, "configured": configured,
        "query": query, "comparisons": comparisons,
        "condition_count": len(comparisons),
        "timings": {
            "configure_filters_ms": round((configured_at - began) * 1000, 2),
            "query_and_wait_ms": round((queried_at - configured_at) * 1000, 2),
            "read_and_compare_ms": round((finished_at - queried_at) * 1000, 2),
            "total_ms": round((finished_at - began) * 1000, 2),
        },
    }
    if not verified:
        failed_fields = [item["field"] for item in comparisons if not item["evaluation"].get("matched")]
        result["reason"] = "筛选结果列校验失败: %s" % ", ".join(failed_fields)
    return result


def _execution_module_text(payload: dict) -> str:
    info = payload.get("module_info") if isinstance(payload, dict) else {}
    info = info if isinstance(info, dict) else {}
    return str(info.get("menu_text") or info.get("module_level2") or "").strip()


def _browser_connection_gate() -> dict:
    """Connect without checking or refreshing a default account session.

    Role recipes establish their own BrowserContext and credentials in their setup
    commands. Refreshing the inherited/default tab here could otherwise inject the
    wrong account before the recipe activates its first role.
    """
    try:
        connection = connect(config.DEFAULT_PORT, config.DEFAULT_TARGET_HINT)
        if not connection.get("ok"):
            return {"ok": False, "reason": "browser connection failed", "connection": connection}
        return {
            "ok": True,
            "connection": connection,
            "skipped": True,
            "reason": "role recipe owns session and module preparation",
        }
    except Exception as exc:
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}


def _browser_ready_gate(module_text: str) -> dict:
    """连接浏览器、确认会话与业务 iframe；模块名存在时再精确导航。"""
    try:
        connection = connect(config.DEFAULT_PORT, config.DEFAULT_TARGET_HINT)
        if not connection.get("ok"):
            return {"ok": False, "reason": "browser connection failed",
                    "connection": connection}
        first_session = check_session()
        refresh = None
        if first_session.get("expired"):
            refresh = refresh_session()
        final_session = check_session()
        if final_session.get("expired"):
            return {"ok": False, "reason": "session remains expired after refresh",
                    "connection": connection, "session": final_session,
                    "refresh": refresh}
        frame = get_active_frame()
        entered = {"ok": True, "skipped": True,
                   "reason": "module navigation not requested" if not module_text
                             else "target module already active"}
        if module_text and (
            not frame.get("ok") or str(frame.get("tab_name") or "").strip() != module_text
        ):
            entered = enter_module(module_text, timeout=12)
            frame = get_active_frame()
        if not entered.get("ok") or not frame.get("ok"):
            reason = ("target module iframe is not ready" if module_text
                      else "module_info is absent and no active business iframe is ready")
            return {"ok": False, "reason": reason, "connection": connection,
                    "session": final_session, "entered": entered, "frame": frame,
                    "refresh": refresh}
        return {"ok": True, "connection": connection,
                "initial_session": first_session, "session": final_session,
                "entered": entered, "frame": frame, "refresh": refresh}
    except Exception as exc:
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}


@write_synchronized
def run_test_cases(case_file: str, filename: str = None) -> dict:
    """回放 automation_recipe；支持 role_session_* 步骤进行顺序式审批回归。"""
    payload, error = _read_json_resource(case_file)
    if error:
        return {"ok": False, "reason": error}
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "case file root must be an object"}
    cases = payload.get("test_cases", [])
    if not isinstance(cases, list) or not cases:
        return {"ok": False, "reason": "case file contains no test_cases"}
    if len(cases) > 1_000:
        return {"ok": False, "reason": "case file exceeds 1000 test cases"}
    # Refuse to turn known stale-table patterns into a green result. Keep the
    # affected cases in the execution artifact as skipped, with a repair reason.
    trusted_cases = []
    preflight_results = []
    result_slots = []
    seen_case_ids = set()
    for case in cases:
        reasons = test_execution.weak_recipe_reasons(case)
        case_id = case.get("case_id") if isinstance(case, dict) else None
        case_key = case_id.strip() if isinstance(case_id, str) else ""
        if not isinstance(case_id, str):
            reasons.append("case_id 必须是字符串")
        elif not case_key:
            reasons.append("case_id 不能为空")
        elif case_key == "__HARNESS__":
            reasons.append("case_id __HARNESS__ 为执行框架保留编号")
        elif case_key in seen_case_ids:
            reasons.append("case_id 重复: %s" % case_key)
        else:
            seen_case_ids.add(case_key)
        if reasons:
            rejected = {
                "case_id": case_id,
                "case_title": case.get("case_title", "") if isinstance(case, dict) else "",
                "status": "skipped",
                "reason": "执行配方可信度不足：" + "；".join(dict.fromkeys(reasons)),
                "failure_type": "recipe_quality", "steps": [], "evidence_refs": [],
            }
            preflight_results.append(rejected)
            result_slots.append(rejected)
        else:
            trusted_cases.append(case)
            result_slots.append(None)
    module_info = payload.get("module_info") if isinstance(payload.get("module_info"), dict) else {}
    module_text = _execution_module_text(payload)
    role_session_mode = any(
        test_execution.uses_role_session_actions(case) for case in trusted_cases
    )
    if trusted_cases and flow_evidence.is_active():
        return {"ok": False, "reason": "an evidence flow is already active; stop it before execution"}

    prior_native_actions = _recipe_requires_native_actions()
    _reset_recipe_context()
    _recipe_context.native_actions_only = True if trusted_cases else prior_native_actions
    ready_gate = (
        _browser_connection_gate() if role_session_mode else _browser_ready_gate(module_text)
    ) if trusted_cases else {"ok": True, "skipped": True, "reason": "all cases rejected by preflight"}
    if not ready_gate.get("ok"):
        _recipe_context.native_actions_only = prior_native_actions
        return {"ok": False, "reason": "browser ready gate failed: %s" % ready_gate.get("reason", ""),
                "ready_gate": ready_gate}

    started_flow = {"ok": True, "skipped": True,
                    "reason": "all cases rejected by preflight"}
    if trusted_cases:
        started_flow = flow_evidence.start(
            module_text or str(module_info.get("module_name") or "automated_execution"),
            "automated_execution_%d" % int(time.time()), capture_screenshots=True,
            scenario_type="自动化回归测试", risk_type="执行复验",
            destructive=any(bool(case.get("destructive")) for case in trusted_cases),
            cleanup_strategy="automation_recipe.cleanup + after_case overlay cleanup",
        )
        if not started_flow.get("ok"):
            _recipe_context.native_actions_only = prior_native_actions
            return started_flow

    def reset_case_filters(submit: bool) -> dict:
        began = time.perf_counter()
        observer_active = False

        def drain_observer() -> None:
            nonlocal observer_active
            if not observer_active:
                return
            try:
                observe.observe_wait(
                    timeout=0.1, include_snapshot=False, detail="summary", native_wait=True,
                )
            except Exception:
                pass
            observer_active = False

        try:
            started = ({"ok": True, "skipped": True} if not submit else
                       observe.observe_start(
                           signals=["network"], listen_targets="gateway", native_wait=True,
                       ))
            if not started.get("ok"):
                return {"ok": False, "reason": "无法监听筛选重置请求",
                        "observe_start": started}
            observer_active = bool(submit)
            reset = filter_area.reset_filter_area(submit=submit)
            if not reset.get("ok") or not submit:
                drain_observer()
                return {"ok": bool(reset.get("ok")), "reset": reset,
                        "query_deferred": not submit,
                        "reason": reset.get("reason", "") if not reset.get("ok") else ""}
            observed = observe.observe_wait(
                timeout=10, include_snapshot=False, detail="summary", native_wait=True,
            )
            observer_active = False
            packet = observed.get("packet") or observed.get("payload") or {}
            packet = packet if isinstance(packet, dict) else {}
            response = packet.get("response") if isinstance(packet.get("response"), dict) else {}
            status = packet.get("status", response.get("status", observed.get("status")))
            body = packet.get("body", response.get("body"))
            http_ok = _http_success(status)
            business_ok = _business_response_success(body)
            frame_object = browser_session.get_active_frame()
            remaining = max(0.1, 10 - (time.perf_counter() - began))
            loading_complete, table_kind = _wait_query_table(frame_object, remaining)
            ok = bool(observed.get("type") == "network" and http_ok
                      and business_ok and loading_complete)
            return {
                "ok": ok, "reset": reset, "network": observed,
                "http_ok": http_ok, "business_ok": business_ok,
                "loading_complete": loading_complete, "table_kind": table_kind,
                "reason": "" if ok else "重置后的业务查询未稳定完成",
            }
        except Exception as exc:
            drain_observer()
            return {"ok": False, "reason": "reset_filter_area 失败: %s" % exc}

    def reload_case_frame() -> dict:
        try:
            frame_object = browser_session.get_active_frame()
            if frame_object is None:
                return {"ok": False, "reason": "active iframe is unavailable"}
            frame_object.refresh()
            frame_object.wait.doc_loaded(timeout=10, raise_err=False)
            frame = get_active_frame()
            return {"ok": bool(frame.get("ok")), "frame": frame,
                    "reason": "" if frame.get("ok") else "active iframe reload failed"}
        except Exception as exc:
            return {"ok": False, "reason": "active iframe reload failed: %s" % exc}

    def fallback_case_reset(reset: dict) -> dict:
        if reset.get("ok"):
            return reset
        logger.warning("reset_filter_area 失败，回退到 iframe 刷新: %s", reset.get("reason", ""))
        return (_run_recipe_action("reset_to_initial", {"module_text": module_text})
                if module_text else reload_case_frame())

    def before_case(case):
        _reset_recipe_context()
        _recipe_context.destructive_allowed = bool(
            isinstance(case, dict) and case.get("destructive") is True
        )
        if test_execution.uses_role_session_actions(case):
            return {
                "ok": True,
                "skipped": True,
                "reason": "role recipe performs its own role/session/module preparation",
            }
        if role_session_mode:
            standard_ready = _browser_ready_gate(module_text)
            if not standard_ready.get("ok"):
                return standard_ready
        overlay_cleanup = _pre_click_cleanup(True)
        if overlay_cleanup.get("errors"):
            return {"ok": False, "reason": "; ".join(
                str(item) for item in overlay_cleanup["errors"]
            ), "cleanup": overlay_cleanup}
        recipe = case.get("automation_recipe") if isinstance(case, dict) else None
        if isinstance(recipe, list):
            setup_commands, step_commands = [], recipe
        elif isinstance(recipe, dict):
            setup_commands = recipe.get("setup") or []
            step_commands = recipe.get("steps") or []
        else:
            setup_commands, step_commands = [], []
        defer_query = bool(
            not setup_commands and step_commands and isinstance(step_commands[0], dict)
            and step_commands[0].get("action") in {"query_filter", "verify_filter_query"}
        )
        reset = fallback_case_reset(reset_case_filters(submit=not defer_query))
        if not reset.get("ok"):
            return reset
        frame = get_active_frame()
        if not frame.get("ok"):
            return frame
        return {
            "ok": True, "reset": reset, "frame": frame,
            "query_deferred": defer_query, "flow_step": reset.get("flow_step"),
        }

    def after_case(_case, _result):
        if test_execution.uses_role_session_actions(_case):
            return {
                "ok": True,
                "skipped": True,
                "reason": "role recipe owns cleanup; no default-page reset applied",
            }
        overlay_cleanup = _pre_click_cleanup(True)
        reset = fallback_case_reset(reset_case_filters(submit=True))
        errors = [str(item) for item in overlay_cleanup.get("errors", [])]
        if not reset.get("ok"):
            errors.append(str(reset.get("reason") or "页面状态重置失败"))
        response = {"ok": not errors, "cleanup": overlay_cleanup, "reset": reset,
                    "flow_step": reset.get("flow_step")}
        if errors:
            response["reason"] = "; ".join(errors)
        return response

    execution_flow = None
    harness_failures = []
    try:
        execution = test_execution.execute_cases(
            trusted_cases, _run_recipe_action, before_case=before_case, after_case=after_case,
        )
        if not isinstance(execution, dict) or not isinstance(execution.get("results"), list):
            raise TypeError("execution engine returned an invalid result")
    except Exception as exc:
        now = datetime.now().astimezone().isoformat()
        execution = {"schema_version": "1.0", "started_at": now,
                     "finished_at": now, "results": []}
        harness_failures.append("execution engine failed: %s: %s" % (type(exc).__name__, exc))
    finally:
        _recipe_context.native_actions_only = prior_native_actions
        _reset_recipe_context()
        if trusted_cases:
            execution_flow = flow_evidence.stop() if flow_evidence.is_active() else {
                "ok": False, "reason": "execution evidence flow ended unexpectedly",
            }
        else:
            execution_flow = {"ok": True, "skipped": True,
                              "reason": "all cases rejected by preflight"}
    if not execution_flow.get("ok"):
        harness_failures.append("execution evidence failed: %s" %
                                execution_flow.get("reason", "unknown error"))

    # Reinsert preflight rejections into their original positions. Reports and
    # external result consumers must see the same order as the source suite.
    trusted_results = list(execution.get("results", []))
    trusted_index = 0
    ordered_results = []
    for source_case, slot in zip(cases, result_slots):
        if slot is not None:
            ordered_results.append(slot)
        elif trusted_index < len(trusted_results):
            ordered_results.append(trusted_results[trusted_index])
            trusted_index += 1
        else:
            ordered_results.append({
                "case_id": source_case.get("case_id") if isinstance(source_case, dict) else None,
                "case_title": source_case.get("case_title", "") if isinstance(source_case, dict) else "",
                "status": "failed", "failure_type": "harness",
                "reason": "execution engine did not return a result for this case",
                "steps": [], "evidence_refs": [],
            })
    ordered_results.extend(trusted_results[trusted_index:])
    if harness_failures:
        ordered_results.append({
            "case_id": "__HARNESS__", "case_title": "回放执行框架",
            "status": "failed", "failure_type": "harness",
            "reason": "；".join(harness_failures), "steps": [], "evidence_refs": [],
        })
    execution["results"] = ordered_results
    execution["module_info"] = module_info
    try:
        execution["source_case_file"] = resource_store._resolve_existing_path(case_file)
    except (OSError, ValueError):
        execution["source_case_file"] = str(case_file)
    execution["ready_gate"] = ready_gate
    execution["role_mode"] = role_session_mode
    execution["evidence_flow"] = execution_flow
    if preflight_results:
        execution["recipe_quality_gate"] = {
            "trusted": len(trusted_cases), "rejected": len(preflight_results),
            "policy": "原生动作 + 网络同步 + 全量业务断言 + 可验证清理",
        }
    if payload.get("coverage_summary") is not None:
        execution["coverage_summary"] = payload["coverage_summary"]
    if payload.get("coverage_matrix") is not None:
        execution["coverage_matrix"] = payload["coverage_matrix"]
    try:
        path = _resolve_artifact_path(
            filename, os.path.join("test_results", "executions"), module_info,
            "execution_%d.json" % int(time.time()),
        )
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    sanitized_execution = flow_evidence.sanitize_artifact(execution)
    try:
        resource_store.write_json_atomic(path, sanitized_execution)
    except (OSError, TypeError, ValueError) as exc:
        return {"ok": False, "reason": "execution persistence failed: %s" % exc}
    counts = {state: sum(1 for item in execution["results"] if item.get("status") == state)
              for state in ("passed", "failed", "xfailed", "skipped")}
    return {"ok": True, "saved_to": path, "counts": counts, "execution": sanitized_execution}


@mcp.tool(name="run_test_cases")
async def _run_test_cases_tool(
    case_file: str,
    filename: str = None,
    ctx: Context = CurrentContext(),
) -> dict:
    """回放 automation_recipe，并在批量执行期间向 Agent 报告进度。"""
    await ctx.report_progress(0, 100, "正在校验测试用例并准备浏览器回放")
    await ctx.info("run_test_cases started", logger_name="drissionpage-mcp.workflow")
    result = await asyncio.to_thread(run_test_cases, case_file, filename)
    if result.get("ok"):
        counts = result.get("counts", {})
        message = "回放完成：通过 %s，失败 %s" % (
            counts.get("passed", 0),
            counts.get("failed", 0),
        )
    else:
        message = "回放未完成：%s" % result.get("reason", "未知原因")
    await ctx.report_progress(100, 100, message)
    await ctx.info(message, logger_name="drissionpage-mcp.workflow")
    return result


@mcp.tool()
@write_synchronized
def generate_test_report(execution_file: str, coverage_file: str = None,
                         baseline_file: str = None, filename: str = None,
                         defects_file: str = None,
                         supplemental_execution_files: list[str] = None) -> dict:
    """生成包含执行结果、覆盖率、缺陷、证据、性能和回归差异的 Markdown 报告。"""
    execution, error = _read_json_resource(execution_file)
    if error:
        return {"ok": False, "reason": error}
    if not isinstance(execution, dict):
        return {"ok": False, "reason": "execution file root must be an object"}
    if not isinstance(execution.get("results", []), list):
        return {"ok": False, "reason": "execution.results must be a list"}
    if any(not isinstance(item, dict) for item in execution.get("results", [])):
        return {"ok": False, "reason": "execution.results entries must be objects"}

    def coverage_input_error(value, label: str) -> str | None:
        matrix = value if isinstance(value, list) else value.get("coverage_matrix") if isinstance(value, dict) else None
        summary = value.get("coverage_summary") if isinstance(value, dict) else None
        if matrix is not None and not isinstance(matrix, list):
            return "%s.coverage_matrix must be a list" % label
        if isinstance(matrix, list) and any(not isinstance(item, dict) for item in matrix):
            return "%s.coverage_matrix entries must be objects" % label
        if summary is not None and not isinstance(summary, (dict, list)):
            return "%s.coverage_summary must be an object or list" % label
        return None

    coverage_payload, coverage_error = _read_json_resource(coverage_file) if coverage_file else ({}, None)
    if coverage_error:
        return {"ok": False, "reason": coverage_error}
    if not isinstance(coverage_payload, (dict, list)):
        return {"ok": False, "reason": "coverage file root must be an object or list"}
    coverage_shape_error = coverage_input_error(coverage_payload, "coverage")
    if coverage_shape_error:
        return {"ok": False, "reason": coverage_shape_error}
    baseline, baseline_error = _read_json_resource(baseline_file) if baseline_file else (None, None)
    if baseline_error:
        return {"ok": False, "reason": baseline_error}
    if baseline is not None and not isinstance(baseline, dict):
        return {"ok": False, "reason": "baseline file root must be an object"}
    defects_payload, defects_error = _read_json_resource(defects_file) if defects_file else ({}, None)
    if defects_error:
        return {"ok": False, "reason": defects_error}
    if not isinstance(defects_payload, (dict, list)):
        return {"ok": False, "reason": "defects file root must be an object or list"}

    supplemental_files = [] if supplemental_execution_files is None else supplemental_execution_files
    if not isinstance(supplemental_files, list) or len(supplemental_files) > 100:
        return {"ok": False, "reason": "supplemental_execution_files must contain at most 100 files"}
    current = dict(execution)
    supplemental_results = []
    supplemental_sources = []
    supplemental_bytes = 0
    module_identity_keys = ("system_name", "domain", "module_level1", "module_level2", "module_name", "menu_text")
    module_values = {key: set() for key in module_identity_keys}
    base_module_info = execution.get("module_info") if isinstance(execution.get("module_info"), dict) else {}
    for key in module_identity_keys:
        if base_module_info.get(key):
            module_values[key].add("".join(str(base_module_info[key]).split()).lower())
    for supplemental_file in supplemental_files:
        if not isinstance(supplemental_file, str) or not supplemental_file.strip():
            return {"ok": False, "reason": "supplemental execution file paths must be non-empty strings"}
        try:
            resolved_supplemental = resource_store._resolve_existing_path(supplemental_file)
            supplemental_bytes += os.path.getsize(resolved_supplemental)
        except (OSError, ValueError):
            resolved_supplemental = None
        if supplemental_bytes > 200_000_000:
            return {"ok": False, "reason": "supplemental execution files exceed 200000000 bytes"}
        supplemental, supplemental_error = _read_json_resource(supplemental_file)
        if supplemental_error:
            return {"ok": False, "reason": "%s: %s" % (supplemental_file, supplemental_error)}
        if not isinstance(supplemental, dict) or not isinstance(supplemental.get("results", []), list):
            return {"ok": False, "reason": "%s: execution root/results is invalid" % supplemental_file}
        if any(not isinstance(item, dict) for item in supplemental.get("results", [])):
            return {"ok": False, "reason": "%s: execution results must contain objects" % supplemental_file}
        supplemental_info = supplemental.get("module_info") if isinstance(supplemental.get("module_info"), dict) else {}
        for key in module_identity_keys:
            if supplemental_info.get(key):
                module_values[key].add("".join(str(supplemental_info[key]).split()).lower())
        conflicts = [key for key, values in module_values.items() if len(values) > 1]
        if conflicts:
            return {"ok": False, "reason": "supplemental execution module_info conflicts: %s" % ", ".join(conflicts)}
        supplemental_results.extend(supplemental.get("results", []))
        supplemental_sources.append(resolved_supplemental or os.path.abspath(supplemental_file))

    base_results = list(execution.get("results", []))
    if len(base_results) + len(supplemental_results) > 10_000:
        return {"ok": False, "reason": "report input exceeds 10000 execution results"}
    current["results"] = base_results + supplemental_results
    if supplemental_results:
        current["supplemental_execution_files"] = supplemental_sources

    seen_case_ids = set()
    duplicate_case_ids = []
    for result in current["results"]:
        case_id = result.get("case_id")
        if case_id is None:
            continue
        case_key = case_id.strip() if isinstance(case_id, str) else str(case_id)
        if case_key in seen_case_ids:
            duplicate_case_ids.append(case_key)
        seen_case_ids.add(case_key)
    if duplicate_case_ids:
        return {"ok": False, "reason": "duplicate case ids across executions: %s" %
                ", ".join(str(item) for item in dict.fromkeys(duplicate_case_ids))}

    if isinstance(coverage_payload, list):
        current["coverage_matrix"] = coverage_payload
    elif coverage_payload:
        if coverage_payload.get("coverage_summary") is not None:
            current["coverage_summary"] = coverage_payload["coverage_summary"]
        if coverage_payload.get("coverage_matrix") is not None:
            current["coverage_matrix"] = coverage_payload["coverage_matrix"]
    final_coverage_error = coverage_input_error(current, "execution")
    if final_coverage_error:
        return {"ok": False, "reason": final_coverage_error}
    if defects_payload:
        current["known_defects"] = (
            defects_payload if isinstance(defects_payload, list)
            else defects_payload.get("known_defects", [])
        )
        if not isinstance(current["known_defects"], list):
            return {"ok": False, "reason": "known_defects must be a list"}
        if any(not isinstance(item, dict) for item in current["known_defects"]):
            return {"ok": False, "reason": "known_defects entries must be objects"}

    regression = test_reporting.compare_regression(current, baseline)
    if baseline is not None and regression.get("ok") is not True:
        return {"ok": False, "reason": "regression comparison failed: %s" % regression.get("reason", "unknown error"),
                "regression": regression}
    module_info = execution.get("module_info") if isinstance(execution.get("module_info"), dict) else {}
    try:
        path, bundle_dir = _report_bundle_path(filename, module_info, execution_file)
        bundle = _bundle_report_assets(current, execution_file, bundle_dir)
        markdown = test_reporting.render_markdown(
            bundle["execution"], bundle["execution"], regression,
        )
        resource_store.write_text_atomic(path, markdown)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": "report generation failed: %s" % exc}
    return {"ok": True, "saved_to": path, "regression": regression,
            "has_regressions": bool(regression.get("has_regressions")),
            "coverage_summary": current.get("coverage_summary"),
            "bundle_dir": bundle_dir, "execution_copy": bundle["execution_copy"],
            "assets_dir": bundle["assets_dir"], "copied_screenshots": len(bundle["copied"]),
            "missing_screenshots": bundle["missing"]}


@mcp.tool()
@read_synchronized
def compare_regression_report(execution_file: str, baseline_file: str) -> dict:
    """比较当前执行结果与历史基线，识别状态变化和超过 20% 的性能回退。"""
    execution, error = _read_json_resource(execution_file)
    if error:
        return {"ok": False, "reason": error}
    baseline, error = _read_json_resource(baseline_file)
    if error:
        return {"ok": False, "reason": error}
    return test_reporting.compare_regression(execution, baseline)


def _action_disabled_diff(before: dict, after: dict) -> list:
    def key(item):
        label = (item.get("text") or item.get("title") or item.get("selectorHint") or "").strip()
        return (item.get("area") or "", label) if label else None

    before_map = {key(item): item for item in before.get("actions", []) if key(item)}
    after_map = {key(item): item for item in after.get("actions", []) if key(item)}
    changes = []
    for name, b in before_map.items():
        if name not in after_map:
            continue
        a = after_map[name]
        if bool(b.get("disabled")) != bool(a.get("disabled")):
            changes.append({
                "action": name[1],
                "before_disabled": bool(b.get("disabled")),
                "after_disabled": bool(a.get("disabled")),
                "area": a.get("area") or b.get("area"),
            })
    return changes


@mcp.tool()
@write_synchronized
def scan_action_availability_by_selection(row: int = 0, col: int = 0,
                                          kind: str = "auto", table_index: int = 0,
                                          select_row: bool = True,
                                          wait_after_click: float = 0.3) -> dict:
    """扫描选中表格行前后工具栏按钮禁用态变化，用于批量/行选择场景设计。

    select_row=True 时会尝试点击 VTable 的 col,row 或 HTML 表格行复选框。
    """
    parsed = {}
    for name, value in (("row", row), ("col", col), ("table_index", table_index)):
        try:
            item = int(value)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "%s 必须为非负整数" % name}
        if (isinstance(value, float) and not value.is_integer()) or item < 0:
            return {"ok": False, "reason": "%s 必须为非负整数" % name}
        parsed[name] = item
    row, col, table_index = parsed["row"], parsed["col"], parsed["table_index"]
    try:
        wait_after_click = float(wait_after_click or 0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "wait_after_click 必须为非负数"}
    if not math.isfinite(wait_after_click) or wait_after_click < 0:
        return {"ok": False, "reason": "wait_after_click 必须为非负有限数值"}
    wait_after_click = min(wait_after_click, 30.0)
    before = page_model.scan_toolbar_actions(scope="all", max_items=160)
    if not before.get("ok"):
        return {"ok": False, "reason": "选择前工具栏扫描失败", "before": before,
                "mutated_page": False}
    select_result = {"ok": True, "skipped": True}
    post_selection_wait = {"ok": True, "skipped": True}
    if select_row:
        cleanup = _pre_click_cleanup(True)
        table_kind = _normalize_table_kind(kind)
        if table_kind == "vtable":
            select_result = _tag_table_result("vtable", vtable.click_cell(col, row, hover_first=True))
        elif table_kind == "bootstrap":
            select_result = _tag_table_result(
                "bootstrap",
                bootstrap_table.click_bootstrap_row_selection(row=row, table_index=table_index),
            )
        elif table_kind == "html":
            select_result = _tag_table_result(
                "html", page_model.click_html_row_selection(row=row, table_index=table_index),
            )
        else:
            select_result = {"ok": False, "reason": "row selection failed"}
            for item in _auto_table_scan_order():
                if item == "vtable":
                    candidate = _tag_table_result(
                        "vtable", vtable.click_cell(col, row, hover_first=True),
                    )
                elif item == "bootstrap":
                    candidate = _tag_table_result(
                        "bootstrap",
                        bootstrap_table.click_bootstrap_row_selection(
                            row=row, table_index=table_index,
                        ),
                    )
                else:
                    candidate = _tag_table_result(
                        "html",
                        page_model.click_html_row_selection(row=row, table_index=table_index),
                    )
                if candidate.get("ok"):
                    select_result = candidate
                    break
                select_result = candidate
        select_result = _attach_cleanup(select_result, cleanup)
        if select_result.get("ok"):
            if select_result.get("kind") == "vtable":
                post_selection_wait = vtable.wait_for_render_stable(timeout=max(wait_after_click, 0.1))
            else:
                target = browser_session.get_active_frame() or browser_session.get_tab()
                waited = target.wait.doc_loaded(timeout=max(wait_after_click, 0.1), raise_err=False)
                post_selection_wait = {"ok": waited is not False}
    after = page_model.scan_toolbar_actions(scope="all", max_items=160)
    return {
        "ok": bool(after.get("ok")
                   and (not select_row or (select_result.get("ok") and post_selection_wait.get("ok")))),
        "selection": select_result,
        "post_selection_wait": post_selection_wait,
        "changes": _action_disabled_diff(before, after),
        "before": before,
        "after": after,
        "mutated_page": bool(select_row and select_result.get("ok")),
        "state_note": "选中状态保留在页面中" if select_row and select_result.get("ok") else "页面选择状态未改变",
    }


@read_synchronized
def dom_overview(max_buttons: int = 100) -> dict:
    """页面俯瞰：顶部页签(含选中态) + 可见按钮文本(含 disabled)。不点击任何元素。
    max_buttons 限制返回按钮数（超出截断并标 _truncated），避免吃尽上下文。"""
    tab = browser_session.get_tab()
    script = browser_session.load_js("element-scan.js") + "\nreturn JSON.stringify(domOverview());"
    res = tab.run_js(script)
    data = json.loads(res) if isinstance(res, str) else res
    if isinstance(data, dict):
        btns = data.get("buttons", [])
        if len(btns) > max_buttons:
            data["buttons"] = btns[:max_buttons]
            data["_truncated"] = True
    return data




@mcp.tool()
@read_synchronized
def find_elements(locator: str, in_frame: bool = True, timeout: float = 5) -> dict:
    """查找所有匹配元素（eles 封装）。返回元素数量及文本预览。

    locator 为 DrissionPage 定位符，支持完整语法：
      #id / .cls / tag:div / t:div / text:文 / tx=文
      css:.cls / c:.cls / xpath://div / x://div
      @attr=v / @@k1=v@@k2=v / @|k1=v@|k2=v / @!id=v
      ax:@role=btn@name=xxx
    纯文本自动模糊匹配。简化写法：text→tx, tag→t, css→c, xpath→x
    文档：https://drissionpage.cn/browser_control/get_elements/syntax
    """
    els = browser_session.find_all(locator, in_frame=in_frame, timeout=timeout)
    if not els:
        return {"ok": True, "count": 0, "elements": []}
    previews = []
    for i, e in enumerate(els):
        if i >= 50:
            break
        item = {
            "tag": e.tag,
            "text": (e.text or "")[:100],
            "attrs": {k: e.attr(k) for k in ("id", "class", "href", "src", "title", "aria-label", "placeholder") if e.attr(k)}
        }
        try:
            vx, vy = e.rect.viewport_midpoint
            item.update({
                "cx": round(float(vx), 1),
                "cy": round(float(vy), 1),
                "viewportX": round(float(vx), 1),
                "viewportY": round(float(vy), 1),
                "coordinate_space": "top-viewport",
                "coord_source": "DrissionPage.Element.rect.viewport_midpoint",
            })
        except Exception:
            pass
        previews.append(item)
    return {"ok": True, "count": len(els), "elements": previews, "_truncated": len(els) > 50}


@mcp.tool()
@read_synchronized
def get_element_coords(xpath: str, index: int = 1, timeout: float = 5) -> dict:
    """通过 XPath 定位元素并返回顶层视口绝对中心坐标。

    使用 DrissionPage 原生 rect.viewport_midpoint，已自动叠加 iframe 偏移，
    返回的 cx/cy 可直接用于 click_xy。

    Args:
        xpath: XPath 定位表达式（如 "//button[contains(@class, 'ant-btn-danger')]"）
        index: 第几个匹配元素（默认 1）
        timeout: 查找超时秒数

    Returns:
        {ok, cx, cy, tag, text, xpath}
    """
    return page_model.get_element_coords(xpath=xpath, index=index, timeout=timeout)


@read_synchronized
def find_static(locator: str = None, in_frame: bool = True, timeout: float = 5, index: int = 1) -> dict:
    """查找元素的静态版本（s_ele 封装）。速度极快，适合批量数据采集。

    静态元素（SessionElement）由纯文本构造，只能读取属性/文本，不能交互。
    locator 为 None 时返回页面/iframe 本身的静态副本。
    index 指定第几个匹配（1 开始，负数倒数）。
    """
    ele = browser_session.find_static(locator, in_frame=in_frame, timeout=timeout, index=index)
    if not ele:
        return {"ok": False, "reason": "元素未找到: %s" % (locator or "(self)")}
    return {
        "ok": True,
        "tag": ele.tag,
        "text": (ele.text or "")[:200],
        "html": (ele.html or "")[:500],
        "attrs": {k: ele.attr(k) for k in ("id", "class", "href", "src", "title", "aria-label", "placeholder", "data-*") if ele.attr(k)}
    }

@mcp.tool()
@read_synchronized
def find_batch(locators: list[str], in_frame: bool = True, timeout: float = 5,
               any_one: bool = True, first_ele: bool = True) -> dict:
    """同时匹配多个定位符（find 封装）。一次调用查找多个不同元素。

    any_one=True: 返回第一个有结果的定位符及其元素
    any_one=False: 返回每个定位符的结果 dict
    first_ele=True: 每个定位符取第一个元素，False 取所有
    """
    res = browser_session.find_batch(locators, in_frame=in_frame, timeout=timeout,
                                     any_one=any_one, first_ele=first_ele)
    if any_one:
        loc, ele = res
        if loc is None:
            return {"ok": False, "reason": "所有定位符均未匹配", "matched_locator": None}
        return {
            "ok": True,
            "matched_locator": loc,
            "tag": ele.tag if hasattr(ele, "tag") else "",
            "text": (ele.text or "")[:200] if hasattr(ele, "text") else ""
        }
    else:
        result = {}
        for loc, ele in res.items():
            if ele is None:
                result[loc] = None
            elif isinstance(ele, list):
                result[loc] = [{"tag": e.tag, "text": (e.text or "")[:100]} for e in ele[:20]]
            else:
                result[loc] = {"tag": ele.tag, "text": (ele.text or "")[:200]}
        return {"ok": True, "results": result}


@read_synchronized
def get_frame(locator, timeout: float = 5) -> dict:
    """按定位符/序号/id/name 获取 iframe/frame 元素（get_frame 封装）。

    locator 可以是：
      - 定位字符串（如 '#iframe1', 't:iframe', 'c:iframe'）
      - 序号 int（1 开始，负数倒数）
      - id 属性内容
      - name 属性内容
    返回 ChromiumFrame 对象，可在其内部继续查找元素。
    """
    fr = browser_session.get_frame_by_locator(locator, timeout=timeout)
    if not fr:
        return {"ok": False, "reason": "iframe 未找到: %s" % locator}
    return {"ok": True, "url": getattr(fr, "url", "") or "", "title": getattr(fr, "title", "") or ""}


def _resolve_and_click(locator: str, in_frame: bool = True, by_js: bool = False,
                       timeout: float = 5) -> dict:
    """Resolve a locator, click it using actions chain (move_to + click)."""
    raw_text = _extract_text_locator(locator)
    ele = None
    native_only = _recipe_requires_native_actions()
    if native_only and by_js:
        return {"ok": False, "locator": locator,
                "reason": "run_test_cases 禁止 by_js 点击"}
    if raw_text:
        for candidate in _clickable_text_locators(raw_text):
            ele = browser_session.find(candidate, in_frame=in_frame, timeout=min(timeout, 1.0),
                                       wait_clickable=False)
            if ele:
                break
    if not ele:
        ele = browser_session.find(locator, in_frame=in_frame, timeout=timeout,
                                   wait_clickable=False)
    if not ele and raw_text and in_frame:
        # 1. @@text(): 搜索整个元素内所有文本（非仅直接文本节点）
        if " " in raw_text:
            ele = browser_session.find(f"@@text():{raw_text}", in_frame=in_frame,
                                       timeout=timeout, wait_clickable=False)
        # 2. tx: 简化写法
        if not ele:
            ele = browser_session.find(f"tx:{raw_text}", in_frame=in_frame,
                                       timeout=timeout, wait_clickable=False)
        # 3. tx= 精确匹配
        if not ele:
            ele = browser_session.find(f"tx={raw_text}", in_frame=in_frame,
                                       timeout=timeout, wait_clickable=False)
        # 4. JS 降级只供交互探索使用；正式回放必须可由 DrissionPage 定位。
        if not ele and not native_only:
            res = _click_text_by_js(locator, in_frame=in_frame)
            if res and res.get("ok"):
                return {"ok": True, "locator": locator, "fallback": "js-text"}
            return {
                "ok": False,
                "reason": "元素未找到: %s（等待 %.1fs，DP 降级+JS 均失败）" % (locator, timeout),
            }
    if not ele:
        return {"ok": False, "reason": "元素未找到: %s（等待 %.1fs）" % (locator, timeout)}
    try:
        waiter = getattr(ele, "wait", None)
        if waiter is not None:
            waiter.clickable(timeout=timeout, wait_stop=True, raise_err=False)
        clicked = ele.click(by_js=by_js, timeout=_short_click_timeout(timeout), wait_stop=False)
        if clicked is False:
            raise RuntimeError("DrissionPage click returned False")
    except Exception as e:
        # Formal execution deliberately does not fall back from an element
        # click to coordinates or JavaScript. VTable uses its dedicated facade.
        if native_only:
            return {"ok": False, "locator": locator,
                    "reason": "DrissionPage 原生元素点击失败: %s" % e}

        # Fallback 1: Try coordinate-based click
        if not by_js:
            try:
                mp = ele.rect.viewport_midpoint
                cx, cy = float(mp[0]), float(mp[1])
                if cx > 0 and cy > 0:
                    logger.info(
                        "Actions click failed on %s, trying coordinate-click fallback at (%.1f, %.1f)",
                        locator, cx, cy,
                    )
                    tab = browser_session.get_tab()
                    tab.actions.move_to((cx, cy)).click()
                    return {
                        "ok": True,
                        "locator": locator,
                        "fallback": "coordinate-click",
                        "coords": [cx, cy],
                        "native_error": str(e),
                    }
            except Exception as coord_err:
                logger.debug("Coordinate click fallback failed: %s", coord_err)

        # Fallback 2: Try JS click directly on the found element. Formal
        # execution deliberately stops after native coordinate fallback.
        if not by_js and not native_only:
            try:
                logger.info("Coordinate click failed or skipped, trying direct JS click on %s", locator)
                ele.click(by_js=True)
                return {
                    "ok": True,
                    "locator": locator,
                    "fallback": "direct-js",
                    "native_error": str(e),
                }
            except Exception as js_err:
                logger.debug("Direct JS click fallback failed: %s", js_err)

        # Fallback 3: Try text search JS click outside formal execution only.
        if not by_js and not native_only:
            res = _click_text_by_js(locator, in_frame=in_frame)
            if res and res.get("ok"):
                return {
                    "ok": True,
                    "locator": locator,
                    "fallback": "js-text",
                    "native_error": str(e),
                }
        return {"ok": False, "locator": locator, "reason": "点击失败: %s" % e}
    return {"ok": True, "locator": locator}


@mcp.tool()
@write_synchronized
def click(locator: str, in_frame: bool = True, by_js: bool = False, timeout: float = 5,
          clean_overlays: bool = True) -> dict:
    """点击元素。locator 为 DrissionPage 定位符(#id/.cls/@attr=v/text:文/css:选择器)。
    in_frame 优先在活动 iframe 内查找。by_js=True 用 JS 点击(绕过遮挡)。timeout 为查找超时秒数。
    clean_overlays=True 时先清理上一操作残留的 Ant notification/message，避免干扰本次点击观察。

    定位语法参考：
      #id / .cls / tag:div / t:div / text:文 / tx=文
      css:.cls / c:.cls / xpath://div / x://div
      @attr=v / @@k1=v@@k2=v / @|k1=v@|k2=v / @!id=v
      ax:@role=btn@name=xxx
    简化写法：text→tx, tag→t, css→c, xpath→x
    文档：https://drissionpage.cn/browser_control/get_elements/syntax
    """
    cleanup = _pre_click_cleanup(clean_overlays)
    result = _resolve_and_click(locator, in_frame=in_frame, by_js=by_js, timeout=timeout)
    return _attach_cleanup(result, cleanup)


@mcp.tool()
@write_synchronized
def click_xy(x: float, y: float, hover_first: bool = True, duration: float = 0.3,
             clean_overlays: bool = True, times: int = 1) -> dict:
    """按有限顶层视口坐标点击；``times`` 仅接受 1 到 10。"""
    if (
        isinstance(x, bool) or isinstance(y, bool)
        or not isinstance(x, (int, float)) or not isinstance(y, (int, float))
        or not math.isfinite(float(x)) or not math.isfinite(float(y))
    ):
        return {"ok": False, "reason": "x 和 y 必须是有限数值"}
    if isinstance(times, bool) or not isinstance(times, int) or not 1 <= times <= 10:
        return {"ok": False, "reason": "times 必须是 1 到 10 的整数"}
    if (
        isinstance(duration, bool) or not isinstance(duration, (int, float))
        or not math.isfinite(float(duration)) or duration < 0
    ):
        return {"ok": False, "reason": "duration 必须是非负有限数值"}

    x, y, duration = float(x), float(y), float(duration)
    cleanup = _pre_click_cleanup(clean_overlays)
    try:
        actions = browser_session.get_tab().actions.move_to(
            (x, y), duration=duration if hover_first else 0,
        )
        if times > 1:
            actions.click(times=times)
        else:
            actions.click()
    except Exception as exc:
        return _attach_cleanup(
            {"ok": False, "reason": "坐标点击失败: %s" % exc}, cleanup,
        )
    return _attach_cleanup({"ok": True, "x": x, "y": y, "times": times}, cleanup)

def select_date_range(field_name: str, start_date: str, end_date: str,
                      in_frame: bool = True, timeout: float = 8,
                      select_index: int = 0, scope: str = "auto") -> dict:
    """Backward-compatible recipe shim; the public MCP entry is ``set_date``."""
    return set_date(
        field_name, start_date=start_date, end_date=end_date,
        in_frame=in_frame, timeout=timeout, select_index=select_index, scope=scope,
    )


@mcp.tool()
@write_synchronized
def input(locator: str, text: str, in_frame: bool = True, clear: bool = True, timeout: float = 5) -> dict:
    """定位一次后通过 DrissionPage 元素 input 写入，并返回实际值。"""
    timeout = max(float(timeout or 0), 0.0)
    element = browser_session.find(
        locator, in_frame=in_frame, timeout=timeout, wait_clickable=False
    )
    if not element:
        return {"ok": False, "reason": "元素未找到: %s（等待 %.1fs）" % (locator, timeout)}
    value = "" if text is None else str(text)
    try:
        _native_element_input(element, value, clear, timeout)
        try:
            actual = element.property("value")
        except Exception:
            actual = element.attr("value")
        return {
            "ok": True,
            "locator": locator,
            "method": "element.input",
            "actual_value": actual,
            "matches_requested": None if actual is None else str(actual) == value,
        }
    except Exception as exc:
        return {"ok": False, "locator": locator, "reason": "DrissionPage input failed: %s" % exc}


@mcp.tool()
@write_synchronized
def insert_text(text: str) -> dict:
    """向当前焦点元素插入文本；活动业务 iframe 优先。"""
    tab = browser_session.get_tab()
    target = browser_session.get_active_frame_ro(tab, timeout=0.2) or tab
    try:
        target.actions.input("" if text is None else str(text))
        return {"ok": True, "scope": "iframe" if target is not tab else "top"}
    except Exception as exc:
        return {"ok": False, "reason": "DrissionPage actions.input failed: %s" % exc}


@mcp.tool()
@write_synchronized
def hover(locator: str = None, x: float = None, y: float = None, in_frame: bool = True,
          duration: float = 0.3, timeout: float = 5) -> dict:
    """通过元素或完整坐标执行 DrissionPage 悬停。"""
    tab = browser_session.get_tab()
    duration = max(float(duration or 0), 0.0)
    timeout = max(float(timeout or 0), 0.0)
    try:
        if locator:
            element = browser_session.find(
                locator, in_frame=in_frame, timeout=timeout, wait_clickable=False
            )
            if not element:
                return {"ok": False, "reason": "元素未找到: %s（等待 %.1fs）" % (locator, timeout)}
            tab.actions.move_to(element, duration=duration)
            return {"ok": True, "locator": locator}
        if x is None or y is None:
            return {"ok": False, "reason": "locator 或 x/y 必须提供"}
        tab.actions.move_to((float(x), float(y)), duration=duration)
        return {"ok": True, "x": float(x), "y": float(y)}
    except Exception as exc:
        return {"ok": False, "reason": "DrissionPage hover failed: %s" % exc}


@mcp.tool()
@read_synchronized
def screenshot(path: str = None, locator: str = None, in_frame: bool = True,
               timeout: float = 5) -> dict:
    """截图并将输出限制在资源目录；可截取匹配元素或当前 Tab。"""
    tab = browser_session.get_tab()
    timeout = max(float(timeout or 0), 0.0)
    resolved_path = resource_store.resolve_path(
        path,
        default_name="shot_%d.png" % int(time.time()),
        category="screenshots",
    )
    try:
        if locator:
            element = browser_session.find(
                locator, in_frame=in_frame, timeout=timeout, wait_clickable=False
            )
            if not element:
                return {"ok": False, "reason": "元素未找到: %s（等待 %.1fs）" % (locator, timeout)}
            element.get_screenshot(path=resolved_path)
        else:
            tab.get_screenshot(path=resolved_path)
        if not os.path.isfile(resolved_path):
            return {"ok": False, "reason": "截图未生成文件", "path": resolved_path}
        return {
            "ok": True,
            "path": os.path.abspath(resolved_path),
            "size": os.path.getsize(resolved_path),
        }
    except Exception as exc:
        return {"ok": False, "reason": "DrissionPage screenshot failed: %s" % exc}


@mcp.tool()
@read_synchronized
def run_js(script: str, in_frame: bool = True, max_chars: int = 4000) -> dict:
    """执行显式调试脚本，并按序列化后的真实体积限制返回。"""
    max_chars = min(max(int(max_chars or 0), 0), 1_000_000)
    tab = browser_session.get_tab()
    target = browser_session.get_active_frame_ro(tab, timeout=0.5) if in_frame else None
    target = target or tab
    try:
        result = target.run_js(str(script or ""))
        try:
            serialized = json.dumps(result, ensure_ascii=False)
            output = result
        except (TypeError, ValueError):
            output = str(result)
            serialized = output
        if len(serialized) > max_chars:
            return {
                "ok": True,
                "result": serialized[:max_chars],
                "_truncated": True,
                "_original_chars": len(serialized),
            }
        return {"ok": True, "result": output}
    except Exception as exc:
        return {"ok": False, "reason": "run_js failed: %s" % exc}


def _normalize_table_kind(kind: str) -> str:
    return page_family.normalize_table_kind(kind)


def _tag_table_result(kind: str, result: dict) -> dict:
    if not isinstance(result, dict):
        return {"ok": False, "kind": kind, "reason": "表格后端返回非 dict: %r" % (result,)}
    tagged = dict(result)
    tagged.setdefault("kind", kind)
    return tagged


def _scan_table_bootstrap(table_index: int = 0) -> dict:
    result = bootstrap_table.scan_bootstrap_table()
    tagged = _tag_table_result("bootstrap", result)
    if not tagged.get("ok"):
        return tagged
    tables = tagged.get("tables") or []
    if table_index >= len(tables):
        return {
            "ok": False,
            "kind": "bootstrap",
            "reason": "visible bootstrap-table not found at index %s" % table_index,
            "table_count": len(tables),
        }
    tagged["tables"] = [tables[table_index]]
    tagged["table_index"] = table_index
    tagged["table_count"] = len(tables)
    return tagged


def _auto_table_scan_order() -> list[str]:
    """根据页面族决定 auto 扫描顺序。"""
    preferred = "auto"
    try:
        family = page_family.detect_page_family()
        preferred = (family or {}).get("preferred_table_kind") or "auto"
    except Exception:
        preferred = "auto"
    return page_family.auto_table_scan_order(preferred)


def _find_vtable_col(column_title: str, max_col: int = 100):
    scan = vtable.scan_vtable_columns(max_col)
    if not scan.get("ok"):
        return None, scan.get("reason", "VTable 扫描失败")
    expected = str(column_title or "").strip()
    matches = {
        info.get("col") for info in scan.get("columns", [])
        if str(info.get("title") or info.get("field") or "").strip() == expected
    }
    matches.discard(None)
    if len(matches) == 1:
        return next(iter(matches)), None
    if matches:
        return None, "VTable 列标题匹配不唯一: %s（匹配列 %s）" % (expected, sorted(matches))
    return None, "VTable 列未找到: %s" % expected


def _build_vtable_drag_to(drag_to_x=None, drag_to_y=None, drag_by_x=None, drag_by_y=None):
    has_absolute = drag_to_x is not None or drag_to_y is not None
    has_relative = drag_by_x is not None or drag_by_y is not None
    if has_absolute and has_relative:
        return None, "drag_to_x/drag_to_y 与 drag_by_x/drag_by_y 不能混用"
    if has_absolute:
        drag_to = {}
        if drag_to_x is not None:
            drag_to["x"] = drag_to_x
        if drag_to_y is not None:
            drag_to["y"] = drag_to_y
        return drag_to, None
    if has_relative:
        drag_to = {}
        if drag_by_x is not None:
            drag_to["dx"] = drag_by_x
        if drag_by_y is not None:
            drag_to["dy"] = drag_by_y
        return drag_to, None
    return None, None


def _resolve_vtable_action_col(col: int = None, column_title: str = None):
    target_col = col
    if target_col is None and column_title:
        target_col, reason = _find_vtable_col(column_title)
        if target_col is None:
            return None, reason
    if target_col is None:
        return None, "VTable 动作需要 col 或 column_title"
    return target_col, None


def _scan_table_vtable(max_col: int) -> dict:
    return _tag_table_result("vtable", vtable.scan_vtable_columns(max_col))


def _scan_table_html(table_index: int = 0) -> dict:
    result = html_table.scan_html_table()
    tagged = _tag_table_result("html", result)
    if not tagged.get("ok"):
        return tagged
    tables = tagged.get("tables") or []
    if table_index >= len(tables):
        return {"ok": False, "kind": "html",
                "reason": "visible table not found at index %s" % table_index,
                "table_count": len(tables)}
    tagged["tables"] = [tables[table_index]]
    tagged["table_index"] = table_index
    tagged["table_count"] = len(tables)
    return tagged


# ==================== 统一表格 facade（VTable / HTML Table）====================

@mcp.tool()
@read_synchronized
def scan_table(kind: str = "auto", max_col: int = 50, table_index: int = 0, filename: str = None) -> dict:
    """扫描当前可见表格。

    kind:
      - auto: 按页面族优先（VTable / Bootstrap Table / ant-table）回退
      - vtable / html / bootstrap: 指定后端
    filename 提供时保存到文件，不返回大 JSON。
    """
    try:
        parsed_table_index = int(table_index)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "table_index 必须为非负整数"}
    if (isinstance(table_index, float) and not table_index.is_integer()) or parsed_table_index < 0:
        return {"ok": False, "reason": "table_index 必须为非负整数"}
    table_index = parsed_table_index
    kind = _normalize_table_kind(kind)
    if kind == "vtable":
        result = _scan_table_vtable(max_col)
    elif kind == "bootstrap":
        result = _scan_table_bootstrap(table_index)
    elif kind == "html":
        result = _scan_table_html(table_index)
    else:
        reasons = {}
        result = {
            "ok": False,
            "kind": "auto",
            "reason": "未识别到 VTable / Bootstrap Table / ant-table",
        }
        order = _auto_table_scan_order()
        for item in order:
            if item == "vtable":
                candidate = _scan_table_vtable(max_col)
            elif item == "bootstrap":
                candidate = _scan_table_bootstrap(table_index)
            else:
                candidate = _scan_table_html(table_index)
            reasons[item] = candidate.get("reason", "")
            ok = candidate.get("ok")
            if ok and item in {"html", "bootstrap"} and not (candidate.get("tables") or []):
                ok = False
                reasons[item] = reasons[item] or "tables empty"
            if ok:
                if item != order[0]:
                    candidate["fallback_from"] = order[0]
                    candidate["scan_order"] = order
                result = candidate
                break
        if not result.get("ok"):
            result["details"] = reasons
            result["scan_order"] = order

    # filename 参数优先
    if filename and result.get("ok"):
        full_path = resource_store.resolve_path(filename)
        with open(full_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return {
            "ok": True,
            "saved_to": os.path.abspath(full_path),
            "kind": result.get("kind"),
        }

    return result


@mcp.tool()
@read_synchronized
def get_table_values(column_title: str, kind: str = "auto", raw: bool = False, table_index: int = 0, filename: str = None) -> dict:
    """按列标题读取标量值列表；HTML 同时返回 cells 元数据，raw=true 仅支持 VTable。
    kind=auto 优先 VTable，失败后回退当前可见 HTML Table；filename 可保存大结果。"""
    column_title = str(column_title or "").strip()
    if not column_title:
        return {"ok": False, "reason": "column_title 不能为空"}
    try:
        parsed_table_index = int(table_index)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "table_index 必须为非负整数"}
    if (isinstance(table_index, float) and not table_index.is_integer()) or parsed_table_index < 0:
        return {"ok": False, "reason": "table_index 必须为非负整数"}
    table_index = parsed_table_index
    kind = _normalize_table_kind(kind)
    if kind == "vtable":
        result = _tag_table_result("vtable", vtable.get_column_values(column_title, raw))
    elif kind == "bootstrap":
        if raw:
            return {"ok": False, "kind": "bootstrap",
                    "reason": "Bootstrap Table 仅支持界面文本，raw=true 只适用于 VTable"}
        result = _tag_table_result(
            "bootstrap",
            bootstrap_table.get_bootstrap_table_values(column_title, table_index),
        )
        result.setdefault("raw", False)
    elif kind == "html":
        if raw:
            return {"ok": False, "kind": "html", "reason": "HTML 表格仅支持界面文本，raw=true 只适用于 VTable"}
        result = _tag_table_result("html", html_table.get_html_table_values(column_title, table_index))
        result.setdefault("raw", False)
    else:
        reasons = {}
        result = {"ok": False, "kind": "auto", "reason": "列值读取失败"}
        for item in _auto_table_scan_order():
            if item == "vtable":
                candidate = _tag_table_result("vtable", vtable.get_column_values(column_title, raw))
            elif item == "bootstrap":
                if raw:
                    reasons["bootstrap"] = "raw=true 不支持"
                    continue
                candidate = _tag_table_result(
                    "bootstrap",
                    bootstrap_table.get_bootstrap_table_values(column_title, table_index),
                )
                candidate.setdefault("raw", False)
            else:
                if raw:
                    reasons["html"] = "raw=true 不支持"
                    continue
                candidate = _tag_table_result(
                    "html", html_table.get_html_table_values(column_title, table_index),
                )
                candidate.setdefault("raw", False)
            reasons[item] = candidate.get("reason", "")
            if candidate.get("ok"):
                result = candidate
                break
        if not result.get("ok"):
            if raw:
                return {
                    "ok": False,
                    "kind": "auto",
                    "reason": "未找到可读取原始值的 VTable；其它表格不支持 raw=true",
                    "details": reasons,
                }
            result["details"] = reasons

    # filename 参数优先
    if filename and result.get("ok"):
        full_path = resource_store.resolve_path(filename)
        with open(full_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return {
            "ok": True,
            "saved_to": os.path.abspath(full_path),
            "kind": result.get("kind"),
        }

    return result


@mcp.tool()
@read_synchronized
def find_vtable_row(column_title: str, value: str, raw: bool = False,
                    match: str = "equals", header_rows: int = None,
                    timeout: float = 0) -> dict:
    """按唯一列值解析 VTable 画布行号；默认从实例自动读取表头层数。"""
    match = str(match or "equals").lower()
    if match not in {"equals", "contains"}:
        return {"ok": False, "reason": "unsupported row match: %s" % match}
    expected = str(value or "").strip()
    if match == "contains" and not expected:
        return {"ok": False, "reason": "contains 匹配值不能为空"}
    try:
        timeout_value = float(timeout or 0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "timeout 必须为非负数"}
    if not math.isfinite(timeout_value) or timeout_value < 0:
        return {"ok": False, "reason": "timeout 必须为非负有限数值"}
    explicit_header_rows = None
    if header_rows is not None:
        try:
            explicit_header_rows = int(header_rows)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "header_rows 必须为非负整数"}
        if (isinstance(header_rows, float) and not header_rows.is_integer()) or explicit_header_rows < 0:
            return {"ok": False, "reason": "header_rows 必须为非负整数"}
    if timeout_value > 0:
        settled = vtable.wait_for_render_stable(timeout=timeout_value)
        if not settled.get("ok"):
            return settled
    scanned = get_table_values(column_title=column_title, kind="vtable", raw=raw)
    if not scanned.get("ok"):
        return scanned
    resolved_header_rows = (explicit_header_rows if explicit_header_rows is not None
                            else max(int(scanned.get("header_rows") or 1), 1))
    matches = []
    for index, actual in enumerate(scanned.get("values") or []):
        normalized = str(actual if actual is not None else "").strip()
        if normalized == expected if match == "equals" else expected in normalized:
            matches.append({"data_index": index, "row": index + resolved_header_rows,
                            "actual": actual})
    if len(matches) != 1:
        return {
            "ok": False,
            "kind": "vtable",
            "reason": "列值未唯一定位到 VTable 行: %s=%s（匹配 %d 行）" % (
                column_title, expected, len(matches),
            ),
            "column_title": column_title,
            "value": value,
            "match": match,
            "match_count": len(matches),
            "matches": matches,
            "header_rows": resolved_header_rows,
        }
    found = matches[0]
    return {
        "ok": True,
        "kind": "vtable",
        "column_title": column_title,
        "value": value,
        "row": found["row"],
        "data_index": found["data_index"],
        "match": match,
        "header_rows": resolved_header_rows,
    }


@mcp.tool()
@read_synchronized
def count_vtable_rows(column_title: str, value: str, raw: bool = False,
                      match: str = "equals", expected_count: int = None,
                      timeout: float = 0) -> dict:
    """统计 VTable 指定列的匹配行数，用于新增存在性与删除完成断言。"""
    match = str(match or "equals").lower()
    if match not in {"equals", "contains"}:
        return {"ok": False, "reason": "unsupported row match: %s" % match}
    expected = str(value or "").strip()
    if match == "contains" and not expected:
        return {"ok": False, "reason": "contains 匹配值不能为空"}
    if expected_count is not None:
        try:
            parsed_count = int(expected_count)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "expected_count 必须为非负整数"}
        if (isinstance(expected_count, float) and not expected_count.is_integer()) or parsed_count < 0:
            return {"ok": False, "reason": "expected_count 必须为非负整数"}
        expected_count = parsed_count
    try:
        timeout_value = float(timeout or 0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "timeout 必须为非负数"}
    if not math.isfinite(timeout_value) or timeout_value < 0:
        return {"ok": False, "reason": "timeout 必须为非负有限数值"}
    if timeout_value > 0:
        settled = vtable.wait_for_render_stable(timeout=timeout_value)
        if not settled.get("ok"):
            return settled
    scanned = get_table_values(column_title=column_title, kind="vtable", raw=raw)
    if not scanned.get("ok"):
        return scanned
    matched_indexes = []
    for index, actual in enumerate(scanned.get("values") or []):
        normalized = str(actual if actual is not None else "").strip()
        if normalized == expected if match == "equals" else expected in normalized:
            matched_indexes.append(index)
    return {
        "ok": True, "kind": "vtable", "column_title": column_title,
        "value": value, "match": match, "match_count": len(matched_indexes),
        "data_indexes": matched_indexes, "expected_count": expected_count,
        "matches_expected": (len(matched_indexes) == expected_count
                             if expected_count is not None else None),
    }


@mcp.tool()
@read_synchronized
def get_vtable_row_values(key_column: str, key_value: str, column_titles: list[str],
                          raw: bool = False, match: str = "equals",
                          timeout: float = 0) -> dict:
    """按唯一业务键读取同一 VTable 行的多列值；目标列通过一次脚本批量读取。"""
    titles = list(dict.fromkeys(str(title or "").strip() for title in (column_titles or [])))
    key_column = str(key_column or "").strip()
    if not key_column:
        return {"ok": False, "reason": "key_column 不能为空"}
    if not titles or any(not title for title in titles):
        return {"ok": False, "reason": "column_titles 不能为空"}
    found = find_vtable_row(
        column_title=key_column, value=key_value, raw=raw, match=match,
        timeout=timeout,
    )
    if not found.get("ok"):
        return found
    data_index = found["data_index"]
    scan_titles = list(dict.fromkeys([key_column] + titles))
    scanned = vtable.get_columns_values(scan_titles, raw=raw)
    if not scanned.get("ok"):
        return {"ok": False, "reason": "批量读取目标列失败", "detail": scanned}
    columns = scanned.get("values") or {}
    key_values = columns.get(key_column) or []
    expected = str(key_value or "").strip()
    resolved_match = found.get("match", "equals")
    matching_indexes = []
    for index, actual in enumerate(key_values):
        normalized = str(actual if actual is not None else "").strip()
        if normalized == expected if resolved_match == "equals" else expected in normalized:
            matching_indexes.append(index)
    if matching_indexes != [data_index]:
        return {"ok": False, "kind": "vtable",
                "reason": "VTable 在行定位后发生变化，业务键不再唯一指向原数据行",
                "key_column": key_column, "key_value": key_value,
                "previous_data_index": data_index, "matching_indexes": matching_indexes}
    values = {}
    for title in titles:
        column_values = columns.get(title) or []
        if data_index >= len(column_values):
            return {"ok": False, "reason": "列数据行数不一致: %s" % title,
                    "column": title, "data_index": data_index,
                    "value_count": len(column_values)}
        values[title] = column_values[data_index]
    return {
        "ok": True, "kind": "vtable", "key_column": key_column,
        "key_value": key_value, "row": found["row"], "data_index": data_index,
        "header_rows": found.get("header_rows"), "values": values,
    }


@mcp.tool()
@read_synchronized
def get_table_data(kind: str = "auto", table_index: int = 0,
                   filename: str = None) -> dict:
    """统一读取当前表格完整可读数据，HTML 与 VTable 均受支持。"""
    kind = _normalize_table_kind(kind)
    return page_model.get_all_table_data(
        kind=kind,
        table_index=table_index,
        max_pages=1,
        max_rows=100_000,
        max_columns=1000,
        raw=False,
        filename=filename,
    )


@mcp.tool()
@read_synchronized
def get_vtable_cell_render_info(row: int, col: int = None, column_title: str = None,
                                detail: str = "summary") -> dict:
    """读取 VTable 单元格渲染信息：文本、字体色、标签底色、单元格背景色/边框色。"""
    target_col, reason = _resolve_vtable_action_col(col=col, column_title=column_title)
    if target_col is None:
        return {"ok": False, "kind": "vtable", "reason": reason}
    return _tag_table_result("vtable", vtable.get_cell_render_info(target_col, row, detail=detail))


@mcp.tool()
@read_synchronized
def get_vtable_cell_icons(row: int, col: int = None, column_title: str = None,
                          icon_name: str = None, detail: str = "summary") -> dict:
    """读取任意 VTable 单元格内可能存在的图标，返回图标名称/类型和顶层视口坐标。"""
    target_col, reason = _resolve_vtable_action_col(col=col, column_title=column_title)
    if target_col is None:
        return {"ok": False, "kind": "vtable", "reason": reason}
    return _tag_table_result(
        "vtable",
        vtable.get_cell_icons(target_col, row, icon_name=icon_name, detail=detail),
    )


@mcp.tool()
@write_synchronized
def vtable_action(action: str = "click", row: int = 0, col: int = None,
                  column_title: str = None, target: str = "cell",
                  icon_name: str = None, icon_index: int = None,
                  hover_first: bool = True,
                  duration: float = 0.3, drag_to_x: float = None,
                  drag_to_y: float = None, drag_by_x: float = None,
                  drag_by_y: float = None, clean_overlays: bool = True,
                  source_x: float = None, source_y: float = None) -> dict:
    """VTable 专项指针动作。工具内部负责滚动到可见、重算顶层视口坐标，再执行 click/double_click/hover/drag。

    source_x/source_y: 拖拽源位置偏移（覆盖列中心），用于避免列头图标干扰拖拽。
    """
    action_key = (action or "click").strip().lower().replace("-", "_")
    cleanup = None if action_key in {"hover", "move", "move_to", "mouseover", "mouse_over"} else _pre_click_cleanup(clean_overlays)
    target_col, reason = _resolve_vtable_action_col(col=col, column_title=column_title)
    if target_col is None:
        return _attach_cleanup({"ok": False, "kind": "vtable", "reason": reason}, cleanup)
    drag_to, reason = _build_vtable_drag_to(drag_to_x=drag_to_x, drag_to_y=drag_to_y,
                                           drag_by_x=drag_by_x, drag_by_y=drag_by_y)
    if reason:
        return _attach_cleanup({"ok": False, "kind": "vtable", "reason": reason}, cleanup)
    result = _tag_table_result(
        "vtable",
        vtable.vtable_action(
            action=action,
            col=target_col,
            row=row,
            target=target,
            icon_name=icon_name,
            icon_index=icon_index,
            hover_first=hover_first,
            duration=duration,
            drag_to=drag_to,
            source_x=source_x,
            source_y=source_y,
        ),
    )
    return _attach_cleanup(result, cleanup)


def _hover_table_cell_raw(
    row: int, col: int = None, column_title: str = None, kind: str = "auto",
    table_index: int = 0, duration: float = 0.3,
) -> dict:
    """Undecorated table hover helper（VTable / Bootstrap / ant-table）。"""
    kind = _normalize_table_kind(kind)

    def _hover_vtable():
        target_col = col
        if target_col is None and column_title:
            target_col, reason = _find_vtable_col(column_title)
            if target_col is None:
                return {"ok": False, "kind": "vtable", "reason": reason}
        if target_col is None:
            return {"ok": False, "kind": "vtable", "reason": "VTable 悬停需要 col 或 column_title"}
        return _tag_table_result(
            "vtable",
            vtable.vtable_action(
                action="hover", col=target_col, row=row, target="cell", duration=duration,
            ),
        )

    def _hover_bootstrap():
        if not column_title:
            return {"ok": False, "kind": "bootstrap", "reason": "Bootstrap Table 悬停需要 column_title"}
        return _tag_table_result(
            "bootstrap",
            bootstrap_table.hover_bootstrap_table_cell(
                column_title, row, table_index, duration=duration,
            ),
        )

    def _hover_html():
        if not column_title:
            return {"ok": False, "kind": "html", "reason": "HTML 表格悬停需要 column_title"}
        return _tag_table_result(
            "html",
            html_table.hover_html_table_cell(
                column_title, row, table_index, duration=duration,
            ),
        )

    if kind == "vtable":
        return _hover_vtable()
    if kind == "bootstrap":
        return _hover_bootstrap()
    if kind == "html":
        return _hover_html()

    reasons = {}
    for item in _auto_table_scan_order():
        if item == "vtable":
            candidate = _hover_vtable()
        elif item == "bootstrap":
            candidate = _hover_bootstrap()
        else:
            candidate = _hover_html()
        reasons[item] = candidate.get("reason", "")
        if candidate.get("ok"):
            return candidate
    return {"ok": False, "kind": "auto", "reason": "表格单元格悬停失败", "details": reasons}


@mcp.tool()
@write_synchronized
def click_table_cell(row: int, col: int = None, column_title: str = None, kind: str = "auto",
                     table_index: int = 0, icon_name: str = None, hover_first: bool = True,
                     duration: float = 0.3, double_click: bool = False,
                     clean_overlays: bool = True) -> dict:
    """统一点击表格单元格。

    kind: auto | vtable | html | bootstrap。
    VTable 可用 col 或 column_title；HTML / Bootstrap Table 使用 column_title。
    clean_overlays=True 时先清理上一操作残留的 Ant notification/message。
    """
    cleanup = _pre_click_cleanup(clean_overlays)
    result = _click_table_cell_raw(
        row=row, col=col, column_title=column_title, kind=kind,
        table_index=table_index, icon_name=icon_name, hover_first=hover_first,
        duration=duration, double_click=double_click,
    )
    return _attach_cleanup(result, cleanup)


@mcp.tool()
@write_synchronized
def hover_table_cell(row: int, col: int = None, column_title: str = None, kind: str = "auto",
                     table_index: int = 0, duration: float = 0.3) -> dict:
    """统一悬停表格单元格。kind: auto | vtable | html | bootstrap。"""
    return _hover_table_cell_raw(
        row=row, col=col, column_title=column_title, kind=kind,
        table_index=table_index, duration=duration,
    )


@mcp.tool()
@write_synchronized
def resize_table_column(width: int, col: int = None, column_title: str = None, kind: str = "vtable") -> dict:
    """统一调整表格列宽。目前仅 VTable 支持列宽拖拽，HTML/Bootstrap Table 返回不支持。"""
    kind = _normalize_table_kind(kind)
    if kind in {"html", "bootstrap"}:
        return {"ok": False, "kind": kind, "reason": "%s 表格暂不支持列宽调整" % kind}
    target_col = col
    if target_col is None and column_title:
        target_col, reason = _find_vtable_col(column_title)
        if target_col is None:
            return {"ok": False, "kind": "vtable", "reason": reason}
    if target_col is None:
        return {"ok": False, "kind": "vtable", "reason": "调整列宽需要 col 或 column_title"}
    return _tag_table_result("vtable", vtable.resize_column(target_col, width))


@mcp.tool()
@write_synchronized
def reorder_vtable_column(
    column_title: str = None, col: int = None,
    target_column_title: str = None, target_col: int = None,
    position: Literal["after", "before"] = "after",
) -> dict:
    """拖拽 VTable 列头重排列（仅 VTable 支持）。

    VTable 列重排需要三步式鼠标动作：click 选中列头 → hold → move_to → release。
    本工具封装此流程，只需指定要拖动的列和目标锚点列。

    Args:
        column_title: 要拖动的列标题（与 col 二选一）
        col: 要拖动的列索引（与 column_title 二选一）
        target_column_title: 目标锚点列标题（与 target_col 二选一）
        target_col: 目标锚点列索引（与 target_column_title 二选一）
        position: "after"（默认，拖到目标列右侧）或 "before"（拖到左侧）

    Returns:
        {ok, source_col, target_col, dropX, dropY, position}
    """
    source_col, reason = _resolve_vtable_action_col(col=col, column_title=column_title)
    if source_col is None:
        return {"ok": False, "kind": "vtable", "reason": reason}
    target_col_resolved, reason = _resolve_vtable_action_col(
        col=target_col, column_title=target_column_title
    )
    if target_col_resolved is None:
        return {"ok": False, "kind": "vtable", "reason": reason}
    return _tag_table_result(
        "vtable",
        vtable.reorder_column(source_col, target_col_resolved, position),
    )


@mcp.tool()
@read_synchronized
def query_table(operation: Literal["values", "data", "find", "count", "row"] = "values",
                column_title: str = None,
                value: str = None, key_column: str = None, key_value: str = None,
                column_titles: list[str] = None,
                kind: Literal["auto", "html", "vtable", "bootstrap"] = "auto",
                table_index: int = 0, raw: bool = False,
                match: Literal["equals", "contains"] = "equals",
                expected_count: int = None, timeout: float = 0,
                filename: str = None) -> dict:
    """统一表格读取入口。

    kind: auto | html | vtable | bootstrap。
    operation 只能是：
    - values：读取 column_title 的全部可见值（HTML/VTable/Bootstrap）。
    - data：读取当前表格完整可读数据（HTML/VTable/Bootstrap）。
    - find：按 column_title/value 唯一定位 VTable 行。
    - count：统计 column_title/value 匹配的 VTable 行数。
    - row：按 key_column/key_value 读取 column_titles 指定的同一 VTable 行。
    """
    operation_key = str(operation or "values").strip().lower()
    if operation_key == "values":
        result = get_table_values(
            column_title=column_title, kind=kind, raw=raw,
            table_index=table_index, filename=filename,
        )
    elif operation_key == "data":
        result = get_table_data(kind=kind, table_index=table_index, filename=filename)
    elif operation_key == "find":
        result = find_vtable_row(
            column_title=column_title, value=value, raw=raw,
            match=match, timeout=timeout,
        )
    elif operation_key == "count":
        result = count_vtable_rows(
            column_title=column_title, value=value, raw=raw, match=match,
            expected_count=expected_count, timeout=timeout,
        )
    elif operation_key == "row":
        result = get_vtable_row_values(
            key_column=key_column or column_title,
            key_value=key_value if key_value is not None else value,
            column_titles=column_titles, raw=raw, match=match, timeout=timeout,
        )
    else:
        return {
            "ok": False,
            "reason": "operation 必须是 values/data/find/count/row",
            "operation": operation_key,
        }
    return {**result, "operation": operation_key}


@mcp.tool()
@read_synchronized
def inspect_table_cell(row: int, col: int = None, column_title: str = None,
                       aspect: Literal["all", "render", "icons"] = "all",
                       icon_name: str = None,
                       detail: str = "summary") -> dict:
    """统一读取 VTable 单元格的渲染样式和图标。

    aspect 可选 all/render/icons；all 同时返回 render 和 icons。
    """
    aspect_key = str(aspect or "all").strip().lower()
    if aspect_key not in {"all", "render", "icons"}:
        return {"ok": False, "reason": "aspect 必须是 all/render/icons", "aspect": aspect_key}
    result = {"ok": True, "kind": "vtable", "aspect": aspect_key}
    if aspect_key in {"all", "render"}:
        result["render"] = get_vtable_cell_render_info(
            row=row, col=col, column_title=column_title, detail=detail,
        )
        result["ok"] = result["ok"] and bool(result["render"].get("ok"))
    if aspect_key in {"all", "icons"}:
        result["icons"] = get_vtable_cell_icons(
            row=row, col=col, column_title=column_title,
            icon_name=icon_name, detail=detail,
        )
        result["ok"] = result["ok"] and bool(result["icons"].get("ok"))
    return result


@mcp.tool()
@write_synchronized
def table_action(action: Literal["click", "double_click", "hover", "drag", "resize"] = "click",
                 row: int = 0, col: int = None,
                 column_title: str = None,
                 kind: Literal["auto", "html", "vtable", "bootstrap"] = "auto",
                 table_index: int = 0,
                 target: Literal["cell", "header", "header-icon", "cell-icon"] = "cell",
                 icon_name: str = None, icon_index: int = None,
                 width: int = None, hover_first: bool = True,
                 duration: float = 0.3, drag_to_x: float = None,
                 drag_to_y: float = None, drag_by_x: float = None,
                 drag_by_y: float = None, clean_overlays: bool = True,
                 signals: list[str] = None, listen_targets: str = None,
                 timeout: float = 8, include_snapshot: bool = True,
                 detail: str = "summary",
                 drag_from_x: float = None, drag_from_y: float = None) -> dict:
    """统一表格动作入口。

    kind: auto | html | vtable | bootstrap。
    action 可选 click/double_click/hover/drag/resize。普通单元格支持 HTML、
    Bootstrap Table 与 VTable；header/header-icon/cell-icon、drag 和 resize 仅支持 VTable。
    """
    action_key = str(action or "click").strip().lower().replace("-", "_")
    target_key = str(target or "cell").strip().lower().replace("_", "-")
    kind_key = _normalize_table_kind(kind)
    cleanup = (None if action_key == "hover" else _pre_click_cleanup(clean_overlays))
    effective_signals = signals
    if effective_signals is None and listen_targets:
        effective_signals = ["overlay", "notification", "message", "tab", "url", "network"]
    observed = observe.observe_start(
        signals=effective_signals,
        listen_targets=listen_targets,
        native_wait=_recipe_requires_native_actions(),
    )
    if action_key == "resize":
        if width is None:
            result = {"ok": False, "reason": "resize 必须提供 width", "action": action_key}
        else:
            result = resize_table_column(
                width=width, col=col, column_title=column_title, kind=kind_key,
            )
    elif action_key == "hover" and target_key == "cell" and icon_index is None:
        result = hover_table_cell(
            row=row, col=col, column_title=column_title, kind=kind_key,
            table_index=table_index, duration=duration,
        )
    elif action_key in {"click", "double_click"} and target_key == "cell" and icon_index is None:
        result = click_table_cell(
            row=row, col=col, column_title=column_title, kind=kind_key,
            table_index=table_index, icon_name=icon_name,
            hover_first=hover_first, duration=duration,
            double_click=action_key == "double_click",
            clean_overlays=False,
        )
    elif action_key in {"click", "double_click", "hover", "drag"}:
        if kind_key in {"html", "bootstrap"}:
            result = {
                "ok": False, "kind": kind_key, "action": action_key,
                "reason": "该 target/action 组合仅支持 VTable",
            }
        else:
            result = vtable_action(
                action=action_key, row=row, col=col, column_title=column_title,
                target=target_key, icon_name=icon_name, icon_index=icon_index,
                hover_first=hover_first, duration=duration,
                drag_to_x=drag_to_x, drag_to_y=drag_to_y,
                drag_by_x=drag_by_x, drag_by_y=drag_by_y,
                clean_overlays=False,
                source_x=drag_from_x, source_y=drag_from_y,
            )
    else:
        result = {
            "ok": False,
            "reason": "action 必须是 click/double_click/hover/drag/resize",
            "action": action_key,
        }
    result = _attach_cleanup(result, cleanup)
    signal = observe.observe_wait(
        timeout=timeout if result.get("ok") else 0,
        include_snapshot=include_snapshot,
        detail=detail,
        native_wait=_recipe_requires_native_actions(),
    )
    return {
        "ok": bool(result.get("ok")),
        "action": action_key,
        "target": target_key,
        "result": result,
        "observe_start": observed,
        "signal": signal,
    }


# ==================== VTable（canvas 表格）====================

@read_synchronized
def mount_vtable() -> dict:
    """挂载 VTable 实例到 iframe 的 window._vtable（遍历 React fiber）。所有 VTable 工具的前置。"""
    return vtable.mount_vtable()


@read_synchronized
def scan_vtable_columns(max_col: int = 50) -> dict:
    """扫描 VTable 列定义：标题/body 行为(bodyBehavior/bodyType/bodyEditable)/表头图标(含顶层视口坐标 viewportX/Y)。
    图标坐标可直接用于 click_cell/click_xy。"""
    return vtable.scan_vtable_columns(max_col)


@read_synchronized
def get_column_values(title: str, raw: bool = False) -> dict:
    """按中文列标题取该列所有单元格值。raw=False 视觉文本(与界面一致)；raw=True 原始字段值(如数字码)。筛选断言用。"""
    return vtable.get_column_values(title, raw)


@read_synchronized
def get_cell_rect(col: int, row: int, scroll: bool = True) -> dict:
    """取单元格中心顶层视口坐标(先 scrollToCell 确保可见)。返回 {viewportX, viewportY}。

    Args:
        col: 列索引
        row: 行索引
        scroll: True（默认）先滚动到该单元格再取坐标；
                False 不滚动，取当前位置坐标（可能为负值或超出视口，用于判断是否需要 scroll）。
    """
    return vtable.get_cell_rect(col, row, scroll=scroll)


@write_synchronized
def scroll_to_cell(col: int, row: int) -> dict:
    """滚动 VTable 使目标单元格进入视口。"""
    return vtable.scroll_to_cell(col, row)


@write_synchronized
def click_cell(col: int, row: int, icon_name: str = None, hover_first: bool = True,
               duration: float = 0.3, double_click: bool = False,
               clean_overlays: bool = True) -> dict:
    """点击 VTable 单元格或其图标。icon_name(如 'sort')给定时点该图标(先 hover 再 click)；否则点单元格中心。

    Args:
        col: 列索引
        row: 行索引
        icon_name: 图标名称，如 'sort'、'filter-icon'
        hover_first: 是否先 hover（排序/筛选图标需要）
        duration: hover 动画时长
        double_click: 是否双击（用于 bodyBehavior='链接/按钮' 的单元格）
    """
    cleanup = _pre_click_cleanup(clean_overlays)
    return _attach_cleanup(vtable.click_cell(col, row, icon_name, hover_first, duration, double_click), cleanup)

@write_synchronized
def resize_column(col: int, width: int) -> dict:
    """拖拽调整 VTable 列宽（模拟鼠标拖拽列头右边框）。

    Args:
        col: 列索引（从 0 开始，含复选框列）
        width: 目标宽度（像素）

    Returns:
        {ok, col, old_width, new_width, delta}
    """
    return vtable.resize_column(col, width)


# ==================== 弹窗检测 / 网络 / 轨迹 ====================

@read_synchronized
def detect_modal(timeout: float = 0) -> dict:
    """点击后检测弹窗(三级优先级)：iframe 业务弹窗/消息 → top 层弹窗/通知/消息 → none。每次点击后必调。

    timeout>0 时轮询直到弹窗出现或超时，找到就立即返回（智能等待），不用盲等。
    顶层覆盖 confirm(→system_confirm)/interactive/notification/message，含 .ant-message-notice toast。
    注意：极短寿命 toast 或需并发抓多信号时，改用 observe_post_click（MutationObserver 事件驱动）。
    """
    return modal.detect_modal(timeout=timeout)

@mcp.tool()
@write_synchronized
def close_modal() -> dict:
    """关闭当前残留的弹窗/通知/消息，避免积累在 DOM 中干扰后续交互。
    每次 detect_modal() 返回非 none 后调用此函数清理。
    通知类 → 点×关闭；业务确认弹窗 → 点取消或×。
    返回 {ok, closed:[...], errors:[...]}，可判断清理是否成功。
    """
    return modal.close_modal()


@write_synchronized
def observe_post_click(timeout: float = 10, signals: list = None,
                       listen_targets: str = None, poll_interval: float = 0.12,
                       include_snapshot: bool = True) -> dict:
    """点击后统一观察器：并发监听 DOM 弹窗/通知/消息 + URL 跳转 + Tab 变化 + 网络响应，
    任一信号命中立即返回（first-signal-wins）。DOM 走 MutationObserver 事件驱动，非固定 sleep 轮询。
    点击后默认调用本工具，替代多次串行 detect_modal/dom_overview/get_active_frame。

    Args:
        timeout: 最长观察秒数（默认 10）。信号命中立即提前返回。
        signals: 监听信号类型列表，默认 ['overlay','notification','message','tab','url']。
                 可选：'overlay'/'modal'/'drawer'/'dropdown'/'vtable-filter-menu'/'vtable-tooltip'/'vtable-menu'/'calendar'/
                 'notification'/'message'/'tab'/'url'/'network'。
        listen_targets: 网络监听 URL 特征（逗号分隔）；仅 signals 含 'network' 时生效。
        poll_interval: Python 侧读缓冲间隔秒数（默认 0.12）；DOM 实际由 MutationObserver 即时触发。
        include_snapshot: 返回时附带当前浮层快照 snapshot_after，默认 True。

    Returns:
        命中：{type, scope?, payload?, elapsedMs, snapshot_after, ...信号专属字段}
        未命中：{type:'none', elapsedMs, watched:[...], snapshot_after}
        type ∈ interactive/confirm/notification/message/tab_change/url_change/network/none

    典型用法：保存成功 toast（顶层 .ant-message-notice，~3s 消失）会被 message 信号即时捕获，
    解决 detect_modal 历史漏抓顶层短寿命 toast 的问题。
    """
    return observe.observe_post_click(timeout=timeout, signals=signals,
                                      listen_targets=listen_targets, poll_interval=poll_interval,
                                      include_snapshot=include_snapshot)


@mcp.tool()
@write_synchronized
def observe_start(signals: list[str] = None, listen_targets: str = None) -> dict:
    """两段式观察器·启动：**点击前**调用，安装 MutationObserver + 网络监听，立即返回。
    observer 在点击前就已监听，消除「点击→观察」调用间隙（agent 思考时间可能 > toast 寿命），
    可靠捕获短寿命 toast（如保存成功 ~3s）。必须配对调用 observe_wait() 读取信号并清理。

    Args:
        signals: 监听信号类型列表，默认 ['overlay','notification','message','tab','url']。
                 可选：'overlay'/'modal'/'drawer'/'dropdown'/'vtable-filter-menu'/'vtable-tooltip'/'vtable-menu'/'calendar'/
                 'notification'/'message'/'tab'/'url'/'network'。
        listen_targets: 网络监听 URL 特征（逗号分隔）；仅 signals 含 'network' 时生效。

    Returns:
        {ok, session:'active', watched:[...], base_url, base_tab_count}

    典型用法（抓保存成功 toast + 保存接口）：
        observe_start(signals=["message","network"], listen_targets="gateway")
        click(...)                       # 触发保存
        observe_wait(timeout=8)          # 读首个信号 + 清理
    """
    return observe.observe_start(signals=signals, listen_targets=listen_targets)


@mcp.tool()
@write_synchronized
def observe_wait(timeout: float = 8, poll_interval: float = 0.12,
                 include_snapshot: bool = True) -> dict:
    """两段式观察器·等待：轮询 observe_start 安装的 observer，任一信号命中立即返回（first-signal-wins），
    随后清理 observer + listener。须在 observe_start 之后、点击之后调用。

    Args:
        timeout: 最长等待秒数（默认 8）。
        poll_interval: Python 侧读缓冲间隔秒数（默认 0.12）；DOM 由 MutationObserver 即时触发。
        include_snapshot: 返回时附带当前浮层快照 snapshot_after，默认 True。

    Returns:
        命中：{type, scope?, payload?, elapsedMs, snapshot_after, ...信号专属字段}
        未命中：{type:'none', elapsedMs, watched:[...], snapshot_after}
    """
    return observe.observe_wait(timeout=timeout, poll_interval=poll_interval,
                                include_snapshot=include_snapshot)


@mcp.tool()
@read_synchronized
def observe_snapshot(only_visible: bool = True, include_table_data: bool = False,
                     detail: str = "summary") -> dict:
    """统一观察器快照：读取当前可见浮层/弹窗/抽屉/dropdown/calendar/toast。

    这是手工检查当前浮层状态的推荐入口；legacy scan_floats/scan_modal/scan_drawer 保留内部兼容，
    但不再作为默认公开工具暴露。

    Args:
        only_visible: 是否只返回可见浮层。
        include_table_data: 是否包含浮层内表格数据。
        detail: 详情级别，"summary"（默认）只返回浮层标题/文本摘要/按钮/日历摘要，
                "full" 返回完整日历单元格、字段详情等。
    """
    return observe.observe_snapshot(only_visible=only_visible,
                                    include_table_data=include_table_data,
                                    detail=detail)


@read_synchronized
def detect_notification(timeout: float = 2) -> dict:
    """原子工具：检测 .ant-notification-notice（iframe 优先，回退 top）。
    事件驱动 ele() 等待，非固定 sleep。单点排查通知类 toast 用。"""
    return observe.detect_notification(timeout=timeout)


@read_synchronized
def detect_message(timeout: float = 2) -> dict:
    """原子工具：检测 .ant-message-notice（含 success/info/warning/error/loading，iframe+top）。
    事件驱动 ele() 等待。专门捕获「保存订单成功」这类短寿命 toast（detect_modal 历史盲区）。"""
    return observe.detect_message(timeout=timeout)


@read_synchronized
def detect_url_change(old_url: str, timeout: float = 5) -> dict:
    """原子工具：等待活动 iframe URL 变化。用 DrissionPage wait.url_change 事件驱动。
    点击后判断是否跳转（如新增保存后 saleOrderCreate → saleOrderDetail）。"""
    return observe.detect_url_change(old_url=old_url, timeout=timeout)


@read_synchronized
def detect_tab_change(old_count: int, timeout: float = 5) -> dict:
    """原子工具：等待浏览器 tab 数量变化（新 tab 打开/关闭）。点击后判断是否新开 tab。"""
    return observe.detect_tab_change(old_count=old_count, timeout=timeout)


@mcp.tool()
@write_synchronized
def listen_start(targets, method: str = None) -> dict:
    """启动网络监听。targets 为 URL 特征：单个字符串、逗号分隔的多个特征、或列表。
    method 可选 'POST'/'GET'/'GET,POST'/'ALL' 等，采用 4.2 set_method 链式 API；
    不传则默认监听 GET+POST。每次启动都会重置 resource type，避免继承 WS-only 状态。

    4.2 起 listen.start() 删除 method/res_type 参数，改用 listen.set_method / set_res_type 链式 API：
      tab.listen.set_method.GET(only=True)   # 只监听 GET
      tab.listen.set_method.GET(only=True).POST()  # 监听 GET+POST
      tab.listen.set_method.all()            # 监听全部
    """
    tab = browser_session.get_tab()
    urls = _normalize_listen_targets(targets)
    try:
        tab.listen.stop()
    except Exception:
        pass
    try:
        effective_method, resource_type = network_record.start_http_listener(tab.listen, urls, method)
    except Exception as exc:
        return {"ok": False, "reason": "监听启动失败: %s" % exc}
    return {"ok": True, "targets": urls, "method": effective_method, "resource_type": resource_type}


@mcp.tool()
@write_synchronized
def listen_wait(count: int = 1, timeout: float = 10, fit_count: bool = False) -> dict:
    """等待监听的数据包。返回 {url, method, api_target, post_data, status, body}。
    api_target 为请求头中的接口路由标识（同一 gateway URL 下区分不同接口）。
    post_data 为 POST 请求体（JSON 字符串），含查询参数如 conditions/isDelivery 等。
    count>1 返回 packets 列表。fit_count=False 时超时前抓到多少返回多少，适合探索式断言。"""
    tab = browser_session.get_tab()
    try:
        packets = network_record.wait_for_business_packets(
            tab.listen, count=count, timeout=timeout, fit_count=fit_count,
        )
    except Exception as exc:
        return {"ok": False, "reason": "监听等待失败: %s" % exc}
    if not packets:
        return {"ok": False, "reason": "timeout", "hint": "确认 listen_start 的 targets 是否正确，或增大 timeout"}
    if count > 1 or len(packets) > 1:
        return {
            "ok": True,
            "packets": [network_record.packet_to_dict(item) for item in packets],
        }
    return {"ok": True, **network_record.packet_to_dict(packets[0])}


@mcp.tool()
@write_synchronized
def listen_stop() -> dict:
    """停止网络监听（与 listen_start 配对，避免监听器泄漏）。"""
    tab = browser_session.get_tab()
    try:
        tab.listen.stop()
    except Exception as e:
        return {"ok": False, "reason": "停止监听失败: %s" % e}
    return {"ok": True}


@mcp.tool()
@write_synchronized
def network_record_start(targets=None, method: str = None) -> dict:
    """启动网络时间线记录。targets 为 URL 特征；method 默认 GET,POST，支持 POST/GET/ALL 等。

    与 listen_start 不同，本工具用于围绕一段业务操作收集多包时间线：
    network_record_start -> 执行业务动作 -> network_record_stop。
    """
    return network_record.start(targets=targets, method=method)


@mcp.tool()
@write_synchronized
def network_record_stop(timeout: float = 3.0, max_packets: int = 50,
                        fit_count: bool = False, max_body_chars: int = 12000) -> dict:
    """停止网络时间线记录并返回捕获到的数据包列表。fit_count=False 时超时前抓到多少返回多少。"""
    return network_record.stop(timeout=timeout, max_packets=max_packets,
                               fit_count=fit_count, max_body_chars=max_body_chars)


@mcp.tool()
@write_synchronized
def network_trace_start(targets=None, method: str = None) -> dict:
    """开始多步骤业务网络证据采集。

    单个动作优先使用 explore_action(listen_targets=...)；只有需要覆盖多个连续动作时
    才使用 network_trace_start -> actions -> network_trace_stop。
    """
    return network_record_start(targets=targets, method=method)


@mcp.tool()
@write_synchronized
def network_trace_stop(timeout: float = 3.0, max_packets: int = 50,
                       fit_count: bool = False,
                       max_body_chars: int = 12000) -> dict:
    """结束多步骤网络证据采集并返回脱敏后的数据包时间线。"""
    return network_record_stop(
        timeout=timeout, max_packets=max_packets,
        fit_count=fit_count, max_body_chars=max_body_chars,
    )


@mcp.tool()
@read_synchronized
def network_record_export(filename: str = None) -> dict:
    """导出最近一次 network_record_stop 的数据包到 JSON 文件。"""
    return network_record.export(filename=filename)


@mcp.tool()
@write_synchronized
def mouse_trail(on: bool = True) -> dict:
    """开启/关闭鼠标轨迹可视化(红色圆点跟踪 mousemove/click)。调试 canvas 点击落点用。"""
    return modal.mouse_trail(on)


# ==================== 4.2 新增工具 ====================

@mcp.tool()
@write_synchronized
def click_to_download(locator: str, save_path: str = None, rename: str = None,
                      suffix: str = None, new_tab: bool = False,
                      by_js: bool = False, in_frame: bool = True,
                      timeout: float = 30) -> dict:
    """点击元素触发浏览器下载（无需预知 URL），等待完成并返回文件路径。

    内部调用 DrissionPage 的 click.to_download()，自动拦截浏览器下载任务。
    适合下载模板、导出文件等场景——只需提供触发下载的按钮/链接定位符。
    """
    if not isinstance(locator, str) or not locator.strip():
        return {"ok": False, "reason": "locator 必须是非空字符串"}
    if (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout)) or timeout < 0 or timeout > 3600
    ):
        return {"ok": False, "reason": "timeout 必须是 0 到 3600 的有限数值"}

    element = browser_session.find(
        locator, in_frame=in_frame, timeout=max(min(timeout, 10), 1.0)
    )
    if not element:
        return {"ok": False, "reason": "元素未找到: %s（超时 %.1fs）" % (locator, timeout)}
    try:
        kwargs = {"timeout": float(timeout)}
        if save_path:
            kwargs["save_path"] = os.fspath(save_path)
        if rename:
            kwargs["rename"] = str(rename)
        if suffix:
            kwargs["suffix"] = str(suffix)
        if new_tab:
            kwargs["new_tab"] = True
        if by_js:
            kwargs["by_js"] = True
        mission = element.click.to_download(**kwargs)
        completed_path = mission.wait(show=False, timeout=float(timeout))
        raw_path = completed_path or getattr(mission, "final_path", "") or ""
        path_value = os.path.abspath(os.fspath(raw_path)) if raw_path else ""
        ok = bool(completed_path) and os.path.isfile(path_value)
        result = {
            "ok": ok,
            "path": path_value,
            "file_size": getattr(mission, "total_bytes", None),
            "state": str(getattr(mission, "state", "") or ""),
            "name": str(getattr(mission, "name", "") or ""),
        }
        if not ok:
            result["reason"] = "下载未完成或目标文件不存在"
        return result
    except Exception as exc:
        return {"ok": False, "reason": "单击下载失败: %s" % exc}


@mcp.tool()
@write_synchronized
def click_to_upload(locator: str, file_paths: str, by_js: bool = False,
                    in_frame: bool = True, timeout: float = 10) -> dict:
    """点击元素触发文件上传，自动拦截文件选择框并填入路径。

    内部调用 DrissionPage 的 click.to_upload()，模拟真实用户操作：
    点击按钮 → 拦截系统文件对话框 → 自动填入文件路径。
    适合 Ant Design Upload 等复杂上传组件的自动化。
    多文件路径用 \\n 分隔。
    """
    if not isinstance(locator, str) or not locator.strip():
        return {"ok": False, "reason": "locator 必须是非空字符串"}
    if not file_paths or not isinstance(file_paths, str):
        return {"ok": False, "reason": "file_paths 必须是非空字符串（多文件用 \\n 分隔）"}

    element = browser_session.find(
        locator, in_frame=in_frame, timeout=max(min(timeout, 8), 1.0)
    )
    if not element:
        return {"ok": False, "reason": "元素未找到: %s（超时 %.1fs）" % (locator, timeout)}
    try:
        element.click.to_upload(file_paths, by_js=by_js)
        file_list = [p.strip() for p in file_paths.split("\n") if p.strip()]
        return {
            "ok": True,
            "locator": locator,
            "file_count": len(file_list),
            "file_paths": file_list,
        }
    except Exception as exc:
        return {"ok": False, "reason": "文件上传失败: %s" % exc}


@mcp.tool()
@write_synchronized
def download_by_browser(url: str, save_path: str = None, rename: str = None,
                        suffix: str = None, timeout: float = 30,
                        file_exists: str = "rename") -> dict:
    """触发浏览器下载，等待完成并返回可序列化的绝对文件路径。"""
    if not isinstance(url, str) or not url.strip():
        return {"ok": False, "reason": "url 必须是非空字符串"}
    if len(url) > 100_000:
        return {"ok": False, "reason": "url 超过 100000 字符"}
    if (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout)) or timeout < 0 or timeout > 3600
    ):
        return {"ok": False, "reason": "timeout 必须是 0 到 3600 的有限数值"}
    if file_exists not in {"rename", "overwrite", "skip", "r", "o", "s"}:
        return {"ok": False, "reason": "file_exists 必须是 rename/overwrite/skip 或 r/o/s"}

    tab = browser_session.get_tab()
    download = getattr(tab, "download", None)
    by_browser = getattr(download, "by_browser", None) if download is not None else None
    if not callable(by_browser):
        return {
            "ok": False,
            "reason": "当前 DrissionPage 版本未提供 tab.download.by_browser；可改用 click_to_download 工具",
        }
    kwargs = {"url": url, "timeout": float(timeout), "file_exists": file_exists}
    if save_path:
        kwargs["save_path"] = os.fspath(save_path)
    if rename:
        kwargs["rename"] = str(rename)
    if suffix:
        kwargs["suffix"] = str(suffix)
    try:
        mission = by_browser(**kwargs)
        # show=False：wait() 默认 print 进度到 stdout，会污染 MCP 协议帧。
        completed_path = mission.wait(show=False)
        raw_path = completed_path or getattr(mission, "final_path", "") or ""
        path_value = os.path.abspath(os.fspath(raw_path)) if raw_path else ""
        ok = bool(completed_path) and os.path.isfile(path_value)
        result = {
            "ok": ok,
            "path": path_value,
            "file_size": getattr(mission, "total_bytes", None),
            "url": url,
            "state": str(getattr(mission, "state", "") or ""),
            "name": str(getattr(mission, "name", "") or ""),
        }
        if not ok:
            result["reason"] = "下载未完成或目标文件不存在"
        return result
    except Exception as exc:
        return {"ok": False, "reason": "下载失败: %s" % exc}


@mcp.tool()
@write_synchronized
def listen_ws_start(targets: str = None) -> dict:
    """启动 4.2 WebSocket 专项监听，并重置此前 listener 状态。"""
    tab = browser_session.get_tab()
    urls = _normalize_listen_targets(targets)
    try:
        tab.listen.stop()
    except Exception:
        pass
    try:
        method, resource_type, hint = network_record.start_ws_listener(tab.listen, urls)
    except Exception as exc:
        return {"ok": False, "reason": "WebSocket 监听启动失败: %s" % exc}
    result = {"ok": True, "targets": urls, "method": method, "resource_type": resource_type}
    if hint:
        result["hint"] = hint
    return result


@mcp.tool()
@write_synchronized
def listen_ws_wait(count: int = 1, timeout: float = 10, fit_count: bool = False) -> dict:
    """等待 WebSocket 数据包，并限制每个 payload 的输出体积。"""
    tab = browser_session.get_tab()
    try:
        try:
            packet = tab.listen.wait(
                count=count, timeout=timeout, fit_count=fit_count, raise_err=False
            )
        except TypeError:
            packet = tab.listen.wait(count=count, timeout=timeout, fit_count=fit_count)
    except Exception as exc:
        return {"ok": False, "reason": "WebSocket 监听等待失败: %s" % exc}
    if not packet:
        return {"ok": False, "reason": "timeout", "hint": "确认 listen_ws_start 的 targets 是否正确，或增大 timeout"}
    if isinstance(packet, list):
        return {
            "ok": True,
            "packets": [network_record.ws_packet_to_dict(item) for item in packet],
        }
    return {"ok": True, **network_record.ws_packet_to_dict(packet)}


@mcp.tool()
@write_synchronized
def new_context(proxy: str = None) -> dict:
    """创建带初始空白标签页的 4.2 BrowserContext。

    Context 刚创建时不可见且没有 tab；MCP 又没有 context 专属的 new_tab 调用，因此
    这里立即通过官方 ``context.new_tab()`` 建立可切换入口，避免返回不可用 context。
    """
    try:
        context_id, tab = browser_session.create_context(proxy=proxy)
    except Exception as exc:
        return {"ok": False, "reason": "创建上下文失败: %s" % exc}
    return {
        "ok": True,
        "context_id": context_id,
        "tab_ids": [getattr(tab, "tab_id", "")],
        "initial_tab_id": tab.tab_id,
        "hint": "调用 switch_context(%d) 切换到该上下文操作" % context_id,
    }


@mcp.tool()
@write_synchronized
def switch_context(context_id: int) -> dict:
    """切换活动 tab 到指定 context 的首个 tab（配合 new_context）。返回新 tab url。"""
    tab = browser_session.switch_context(context_id)
    if tab is None:
        return {"ok": False, "reason": "context 不存在或无可用 tab", "context_id": context_id}
    return {"ok": True, "url": getattr(tab, "url", "") or "", "context_id": context_id}


@mcp.tool()
@write_synchronized
def close_context(context_id: int) -> dict:
    """关闭 new_context 创建的上下文，并在必要时切回主浏览器标签页。"""
    result = browser_session.close_context(context_id)
    if result.get("ok"):
        removed_roles = role_sessions.remove_by_context(context_id)
        if removed_roles:
            result["removed_roles"] = removed_roles
    return result


@mcp.tool()
@read_synchronized
def list_contexts() -> dict:
    """列出所有已注册的浏览器上下文（配合 new_context）。"""
    return {"ok": True, "contexts": browser_session.list_contexts()}


@mcp.tool()
@write_synchronized
def set_permission(perm: str, allow: bool = True) -> dict:
    """通过 DrissionPage 权限 setter 明确授予或拒绝浏览器权限。"""
    if not isinstance(allow, bool):
        return {"ok": False, "reason": "allow 必须是布尔值"}
    permission = str(perm or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", permission):
        return {"ok": False, "reason": "不支持的权限: %s" % permission}

    browser = browser_session.get_browser()
    perm_fn = getattr(browser.set.perm, permission, None)
    if not callable(perm_fn):
        return {"ok": False, "reason": "不支持的权限: %s" % permission}
    try:
        perm_fn(allow=allow)
    except Exception as exc:
        return {"ok": False, "reason": "设置权限失败: %s" % exc}
    return {"ok": True, "perm": permission, "allow": allow}


# ==================== HTML 表格（ant-table）====================

@read_synchronized
def scan_html_table() -> dict:
    """扫描页面所有 ant-design HTML 表格，返回列定义与元数据。

    与 VTable 互补：VTable 处理 canvas 渲染表格，本工具处理原生 DOM 表格。
    返回 tables 数组，每项含 columns（标题/对齐/排序/筛选）、rowCount、hasPagination 等。
    """
    return html_table.scan_html_table()


@read_synchronized
def get_html_table_values(column_title: str, table_index: int = 0) -> dict:
    """按列标题获取 HTML 表格中该列所有单元格值。

    每个单元格返回：{row, text, hasLink, hasButton, hasPopover, hasInput}。
    table_index 指定第几个表格（从 0 开始），默认 0。
    """
    return html_table.get_html_table_values(column_title, table_index)


@write_synchronized
def click_html_table_cell(column_title: str, row: int, table_index: int = 0) -> dict:
    """点击 HTML 表格中指定单元格。优先点击单元格内的链接或按钮。

    column_title 为列标题文字（精确匹配），row 为行索引（0-based）。
    返回点击元素信息与中心坐标。
    """
    return html_table.click_html_table_cell(column_title, row, table_index)


@write_synchronized
def hover_html_table_cell(column_title: str, row: int, table_index: int = 0) -> dict:
    """悬停 HTML 表格指定单元格。正确叠加 iframe 偏移后移动鼠标。

    column_title 列标题精确匹配，row 行索引 0-based。
    返回悬停的视口坐标 {viewportX, viewportY}。
    """
    return html_table.hover_html_table_cell(column_title, row, table_index)




@read_synchronized
def get_html_table_data(table_index: int = 0) -> dict:
    """从 DOM 读取 HTML 表格的完整数据（表头 + 所有行）。

    列名直接从 <thead> <th> 读取，数据从 <tbody> <tr> 读取，
    列名和数据按 DOM 顺序一一对应，不存在人工对齐错误。
    table_index 指定第几个表格（从 0 开始），默认 0。
    """
    return html_table.get_html_table_data(table_index)


# ==================== 能力分组工具 ====================

@mcp.tool()
@read_synchronized
def browser_list_caps() -> dict:
    """列出当前启用的能力分组和可用的工具分组。

    使用能力分组减少 LLM 上下文 token 消耗。

    使用方式：
        export DRISSIONPAGE_MCP_CAPS=core,vtable,filter  # 启用指定分组
        export DRISSIONPAGE_MCP_CAPS=all                 # 启用所有分组
    """
    return {
        "ok": True,
        "profile": caps.ENABLED_PROFILE,
        "enabled_caps": sorted(caps.ENABLED_CAPS),
        "available_caps": {
            cap: tools for cap, tools in caps.CAP_GROUPS.items()
        },
        "env_hint": "The default full profile exposes every grouped tool. Use DRISSIONPAGE_MCP_PROFILE=enterprise only for explicit context reduction; DRISSIONPAGE_MCP_CAPS further narrows groups.",
    }


# ==================== 新增：滚动操作工具 ====================

@mcp.tool()
@write_synchronized
def browser_scroll(direction: str = "down", pixel: int = 300, locator: str = None,
                   x: int = None, y: int = None, timeout: float = 5) -> dict:
    """滚动活动 iframe；``see`` 按 iframe → 顶层顺序定位并保留真实作用域。"""
    directions = {"top", "bottom", "half", "up", "down", "left", "right", "see", "location"}
    if direction not in directions:
        return {"ok": False, "reason": "Invalid direction: %s" % direction}
    if direction in {"up", "down", "left", "right"} and (
        isinstance(pixel, bool) or not isinstance(pixel, int) or pixel < 0
    ):
        return {"ok": False, "reason": "pixel 必须是非负整数"}
    if direction == "see" and not str(locator or "").strip():
        return {"ok": False, "reason": "see 方向必须提供 locator"}
    if direction == "location" and (
        isinstance(x, bool) or isinstance(y, bool)
        or not isinstance(x, (int, float)) or not isinstance(y, (int, float))
        or not math.isfinite(float(x)) or not math.isfinite(float(y))
    ):
        return {"ok": False, "reason": "location 方向必须提供有限 x/y"}
    if (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout)) or timeout < 0
    ):
        return {"ok": False, "reason": "timeout 必须是非负有限数值"}

    timeout = min(float(timeout), 120.0)
    tab = browser_session.get_tab()
    target = browser_session.get_active_frame(tab) or tab
    try:
        if direction == "top":
            target.scroll.to_top()
        elif direction == "bottom":
            target.scroll.to_bottom()
        elif direction == "half":
            target.scroll.to_half()
        elif direction == "up":
            target.scroll.up(pixel)
        elif direction == "down":
            target.scroll.down(pixel)
        elif direction == "left":
            target.scroll.left(pixel)
        elif direction == "right":
            target.scroll.right(pixel)
        elif direction == "see":
            deadline = time.monotonic() + timeout
            try:
                element = target.ele(locator, timeout=max(timeout * 0.8, 0.0))
            except Exception:
                element = None
            if not element and target is not tab:
                target = tab
                element = tab.ele(locator, timeout=max(deadline - time.monotonic(), 0.0))
            if not element:
                return {"ok": False, "reason": "Element not found: %s" % locator}
            target.scroll.to_see(element)
        else:
            target.scroll.to_location(float(x), float(y))
        return {
            "ok": True,
            "direction": direction,
            "scope": "iframe" if target is not tab else "top",
            "pixel": pixel if direction in {"up", "down", "left", "right"} else None,
        }
    except Exception as exc:
        logger.error("Scroll error: %s", exc)
        return {"ok": False, "reason": str(exc)}


# ==================== 新增：标签页管理工具 ====================

@mcp.tool()
@write_synchronized
def browser_tabs(action: str = "list", index: int = None, url: str = None) -> dict:
    """用 DrissionPage 管理零基索引标签页，并保持关闭后的业务目标。"""
    if action not in {"list", "new", "close", "select"}:
        return {"ok": False, "reason": "Invalid action: %s" % action}
    if action in {"close", "select"} and (
        isinstance(index, bool) or not isinstance(index, int)
    ):
        return {"ok": False, "reason": "index 必须是整数"}
    if action == "new" and url is not None and not isinstance(url, str):
        return {"ok": False, "reason": "url 必须是字符串"}

    browser = browser_session.get_browser()
    try:
        if action == "list":
            return {
                "ok": True,
                "tabs": [dict(item, index=i) for i, item in enumerate(browser_session.list_tabs())],
            }

        if action == "new":
            new_tab = browser.new_tab(url=url)
            browser_session.set_tab(new_tab)
            return {"ok": True, "url": new_tab.url, "tab_id": new_tab.tab_id}

        tab_ids = list(browser.tab_ids)
        if not 0 <= index < len(tab_ids):
            return {"ok": False, "reason": "Invalid index: %s, total: %d" % (index, len(tab_ids))}
        tab_id = tab_ids[index]
        if action == "select":
            selected = browser.get_tab(tab_id)
            browser.activate_tab(selected)
            browser_session.set_tab(selected)
            return {
                "ok": True,
                "tab_id": selected.tab_id,
                "url": selected.url,
                "title": selected.title,
            }

        current_id = getattr(browser_session.get_tab(), "tab_id", None)
        browser.close_tabs(tab_id)
        replacement = None
        if tab_id == current_id:
            try:
                replacement = browser_session._pick_tab(browser, browser_session._target_hint)
            except Exception:
                replacement = None
            if replacement is None:
                replacement = browser.new_tab()
            browser_session.set_tab(replacement)
        return {
            "ok": True,
            "closed_tab_id": tab_id,
            "active_tab_id": getattr(replacement, "tab_id", current_id),
        }
    except Exception as exc:
        logger.error("Browser tabs error: %s", exc)
        return {"ok": False, "reason": str(exc)}


# ==================== 新增：PDF 导出工具 ====================

@mcp.tool()
@write_synchronized
def browser_save_pdf(path: str = None, filename: str = None) -> dict:
    """将当前页面保存为 PDF，并验证 DrissionPage 确实生成了文件。"""
    try:
        tab = browser_session.get_tab()
        raw_name = os.path.basename(str(filename or "page_%d.pdf" % int(time.time())))
        pdf_filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name).strip(" .")
        if not pdf_filename:
            return {"ok": False, "reason": "filename 必须是有效文件名"}
        if not pdf_filename.lower().endswith(".pdf"):
            pdf_filename += ".pdf"

        if path:
            save_dir = os.path.abspath(str(path))
            os.makedirs(save_dir, exist_ok=True)
        else:
            save_path = resource_store.resolve_path(pdf_filename, category="pdf")
            save_dir = os.path.dirname(save_path)
            pdf_filename = os.path.basename(save_path)

        returned = tab.save(path=save_dir, name=pdf_filename, as_pdf=True)
        expected = os.path.abspath(os.path.join(save_dir, pdf_filename))
        candidates = []
        if isinstance(returned, (str, os.PathLike)):
            returned_path = os.fspath(returned)
            if not os.path.isabs(returned_path):
                returned_path = os.path.join(save_dir, returned_path)
            candidates.append(os.path.abspath(returned_path))
        candidates.append(expected)
        candidate = next((item for item in candidates if os.path.isfile(item)), None)
        if candidate is None and isinstance(returned, (bytes, bytearray)):
            with open(expected, "wb") as output:
                output.write(returned)
            candidate = expected
        if candidate is None:
            return {"ok": False, "reason": "PDF 未生成文件", "path": expected}
        return {
            "ok": True,
            "path": candidate,
            "dir": os.path.dirname(candidate),
            "filename": os.path.basename(candidate),
            "size": os.path.getsize(candidate),
        }
    except Exception as exc:
        logger.error("Save PDF error: %s", exc)
        return {"ok": False, "reason": str(exc)}


def _console_arg_text(arg):
    if not isinstance(arg, dict):
        return str(arg)
    if "value" in arg:
        return str(arg.get("value"))
    if arg.get("description"):
        return str(arg.get("description"))
    if arg.get("unserializableValue"):
        return str(arg.get("unserializableValue"))
    return json.dumps(arg, ensure_ascii=False)


def _console_message_to_dict(message) -> dict:
    data = getattr(message, "data", None) or {}
    args = data.get("args") or []
    text = data.get("text") or ""
    if not text and args:
        text = " ".join(_console_arg_text(a) for a in args)
    stack = data.get("stackTrace") or {}
    call_frames = stack.get("callFrames") or []
    first_frame = call_frames[0] if call_frames else {}
    return {
        "level": data.get("level") or data.get("type") or "",
        "type": data.get("type") or data.get("source") or "",
        "text": str(text)[:2000],
        "url": data.get("url") or first_frame.get("url", ""),
        "line": data.get("lineNumber", first_frame.get("lineNumber")),
        "column": first_frame.get("columnNumber"),
        "timestamp": data.get("timestamp"),
        "arg_count": len(args),
    }


@mcp.tool()
@write_synchronized
def browser_console_messages(level: str = "", timeout: float = 0.0, start: bool = True,
                             clear: bool = False, stop: bool = False,
                             max_messages: int = 50, filename: str = None) -> dict:
    """读取并按级别筛选 DrissionPage 控制台队列；过滤先于数量上限。"""
    tab = browser_session.get_tab()
    console = None
    try:
        console = tab.console
        if (start or timeout > 0) and not getattr(console, "listening", False):
            console.start()
        if clear:
            console.clear()

        limit = max(int(max_messages or 0), 0)
        wanted = {
            item.strip().lower() for item in str(level or "").split(",") if item.strip()
        }
        items = []

        def append_if_wanted(message):
            item = _console_message_to_dict(message)
            if wanted and not (
                (item.get("level") or "").lower() in wanted
                or (item.get("type") or "").lower() in wanted
            ):
                return
            if len(items) < limit:
                items.append(item)

        timeout = max(float(timeout or 0), 0.0)
        deadline = time.monotonic() + timeout
        while limit and len(items) < limit and time.monotonic() < deadline:
            remaining = min(0.5, max(deadline - time.monotonic(), 0.0))
            message = console.wait(timeout=remaining)
            if message:
                append_if_wanted(message)

        if limit and len(items) < limit:
            for message in console.messages:
                append_if_wanted(message)
                if len(items) >= limit:
                    break

        result = {"ok": True, "count": len(items), "messages": items}
        if filename:
            full_path = resource_store.resolve_path(filename)
            with open(full_path, "w", encoding="utf-8") as file:
                json.dump(result, file, ensure_ascii=False, indent=2)
            return {"ok": True, "saved_to": os.path.abspath(full_path), "count": len(items)}
        return result
    except Exception as exc:
        logger.error("Console messages error: %s", exc)
        return {"ok": False, "reason": str(exc)}
    finally:
        if stop and console is not None:
            try:
                console.stop()
            except Exception:
                logger.debug("停止控制台监听失败", exc_info=True)


# ==================== 新增：按键操作工具 ====================

@mcp.tool()
@write_synchronized
def browser_press_key(key: str, modifiers: list[str] = None, interval: float = 0.01) -> dict:
    """在活动业务 iframe 发送官方 Keys 动作，并校验组合键参数。"""
    if not isinstance(key, str) or not key:
        return {"ok": False, "reason": "key 必须是非空字符串"}
    if modifiers is None:
        modifiers = []
    if not isinstance(modifiers, list) or any(not isinstance(item, str) for item in modifiers):
        return {"ok": False, "reason": "modifiers 必须是字符串列表"}
    allowed_modifiers = {"alt", "control", "ctrl", "meta", "command", "shift"}
    normalized_modifiers = [re.sub(r"[\s_-]+", "", item).lower() for item in modifiers]
    if any(item not in allowed_modifiers for item in normalized_modifiers):
        return {"ok": False, "reason": "modifiers 仅支持 Ctrl/Alt/Shift/Meta/Command"}
    if (
        isinstance(interval, bool) or not isinstance(interval, (int, float))
        or not math.isfinite(float(interval)) or interval < 0 or interval > 10
    ):
        return {"ok": False, "reason": "interval 必须是 0 到 10 的有限数值"}

    tab = browser_session.get_tab()
    target = browser_session.get_active_frame(tab) or tab
    try:
        result = _press_key_raw(target, key, modifiers=modifiers, interval=float(interval))
        result["scope"] = "iframe" if target is not tab else "top"
        return result
    except Exception as exc:
        logger.error("Press key error: %s", exc)
        return {"ok": False, "reason": str(exc)}


# ==================== 新增：元素状态查询工具 ====================

@mcp.tool()
@read_synchronized
def browser_get_element_state(locator: str, state: str = None) -> dict:
    """读取 DrissionPage 元素状态；派生 hidden/disabled 并按需求值。"""
    ele = browser_session.find(locator, wait_clickable=False)
    if not ele:
        return {"ok": False, "reason": "Element not found: %s" % locator}

    try:
        element_states = ele.states
        getters = {
            "displayed": lambda: bool(element_states.is_displayed),
            "hidden": lambda: not bool(element_states.is_displayed),
            "enabled": lambda: bool(element_states.is_enabled),
            "disabled": lambda: not bool(element_states.is_enabled),
            "selected": lambda: bool(element_states.is_selected),
            "checked": lambda: bool(element_states.is_checked),
            "clickable": lambda: bool(element_states.is_clickable),
            "covered": lambda: bool(element_states.is_covered),
            "alive": lambda: bool(element_states.is_alive),
            "in_viewport": lambda: bool(element_states.is_in_viewport),
            "whole_in_viewport": lambda: bool(element_states.is_whole_in_viewport),
            "has_rect": lambda: bool(element_states.has_rect),
        }
        if state:
            getter = getters.get(state)
            if getter is None:
                return {
                    "ok": False,
                    "reason": "Invalid state: %s" % state,
                    "available_states": list(getters),
                }
            return {"ok": True, "locator": locator, "state": state, "value": getter()}
        return {
            "ok": True,
            "locator": locator,
            "states": {name: getter() for name, getter in getters.items()},
        }
    except Exception as exc:
        logger.error("Get element state error: %s", exc)
        return {"ok": False, "reason": str(exc)}


def main():
    logger.info(
        "Starting drissionpage-mcp server version=%s profile=%s enabled_caps=%s",
        __version__,
        caps.ENABLED_PROFILE,
        sorted(caps.ENABLED_CAPS),
    )
    # FastMCP 3：stdio 为默认传输；host/port 等若将来需要 HTTP 应传给 run()
    mcp.run()


if __name__ == "__main__":
    main()
