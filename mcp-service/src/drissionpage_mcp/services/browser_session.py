"""DrissionPage 浏览器单例：连接/启动 9222 端口、活动 tab / iframe(frame) 解析与自愈重连。

所有 MCP 工具通过本模块取 tab / frame，保证跨工具调用复用同一个浏览器连接。
启动/接管由 DrissionPage 的 Chromium() 依据 dp_configs.ini 自动处理（端口无实例则启动，有则接管）。
"""

import json
import logging
import os
import shutil
import sys
import threading
from functools import lru_cache
from time import monotonic

from DrissionPage import Chromium, ChromiumOptions

from ..core import config, ui_contract

logger = logging.getLogger("drissionpage-mcp")

_JS_DIR = str(config.PACKAGE_ROOT / "assets" / "js")

# 模块级单例
_browser = None
_tab = None
_port = config.DEFAULT_PORT
_target_hint = config.DEFAULT_TARGET_HINT

# 并发锁：FastMCP 支持并发调用，所有浏览器操作串行化避免竞态
_lock = threading.RLock()


# dp_configs.ini 默认读取 mcp-service/configs/，也可由 DRISSIONPAGE_MCP_CONFIG_DIR 覆盖。
_DP_INI = str(config.DP_CONFIG_PATH)

# 最后一次检测到的活跃 tab 名称缓存
_active_tab_name = ""


@lru_cache(maxsize=None)
def load_js(name: str) -> str:
    """读取 js/ 目录下的页面注入脚本。"""
    with open(os.path.join(_JS_DIR, name), encoding="utf-8") as f:
        return f.read()


def ele_with_fallback(target, css_selector, xpath_selector, timeout=1.0):
    """在一个总超时预算内先用 CSS、再用 XPath 定位。

    固定 UI 的 CSS 是主契约，XPath 只承担兼容兜底。旧实现让两次查找各等待完整
    timeout，空结果会耗时 2 倍；这里给 CSS 65% 的保底预算，并把实际剩余时间交
    给 XPath，整个调用最多等待一次 timeout。
    """
    timeout = max(float(timeout or 0), 0.0)
    deadline = monotonic() + timeout
    css_timeout = timeout * 0.65 if xpath_selector else timeout
    try:
        el = target.ele(css_selector, timeout=css_timeout)
        if el and not isinstance(el, str):
            return el
    except Exception:
        pass
    remaining = max(deadline - monotonic(), 0.0)
    try:
        return target.ele(xpath_selector, timeout=remaining)
    except Exception:
        return None


def eles_with_fallback(target, css_selector, xpath_selector):
    """尝试用 CSS 批量获取元素，若为空用 XPath 兜底。"""
    try:
        res = target.eles(css_selector)
        if res:
            return res
    except Exception:
        pass
    try:
        return target.eles(xpath_selector)
    except Exception:
        return []


def _ensure_display_env():
    """确保图形环境变量就绪，供 DrissionPage 启动非 headless Chrome（仅 Linux 有头场景）。

    仅在 Linux + 有头模式下生效：
      - Windows/macOS：无 DISPLAY 概念，图形程序默认可弹窗，直接跳过。
      - headless（HL_HEADLESS）：无头不需要显示，直接跳过。

    MCP server 常作为 Claude Code（tty 会话）的子进程启动，继承的环境无 DISPLAY，
    导致 Chromium() 自启动 Chrome 时报 "Missing X server or $DISPLAY"。此处在无
    DISPLAY 时探测系统 X/Xwayland 显示并补进 os.environ：
      - DISPLAY 默认 :0
      - XAUTHORITY 探测 /run/user/<uid>/.mutter-Xwaylandauth.*（GNOME Wayland 的 Xwayland auth）
    已有 DISPLAY（真图形会话）则不动。
    """
    if config.HEADLESS or not sys.platform.startswith("linux"):
        return
    if os.environ.get("DISPLAY"):
        return
    os.environ.setdefault("DISPLAY", ":0")
    if "XAUTHORITY" not in os.environ:
        getuid = getattr(os, "getuid", None)
        uid = getuid() if callable(getuid) else 0
        xauth_dir = "/run/user/%d" % uid
        try:
            for f in os.listdir(xauth_dir):
                if f.startswith(".mutter-Xwaylandauth."):
                    os.environ["XAUTHORITY"] = os.path.join(xauth_dir, f)
                    break
        except OSError:
            pass
    logger.info(
        "补齐图形环境: DISPLAY=%s XAUTHORITY=%s",
        os.environ.get("DISPLAY"),
        os.environ.get("XAUTHORITY", "(未找到)"),
    )


def _pick_tab(browser, hint):
    """按固定 SCM 域、标题提示、最后激活顺序选择标签页。"""
    try:
        tab = browser.get_tab(url="hoolinks")
        if tab:
            return tab
    except Exception as exc:
        logger.debug("get_tab(url=hoolinks) 失败: %s", exc)
    if hint:
        try:
            tab = browser.get_tab(title=hint)
            if tab:
                return tab
        except Exception as exc:
            logger.debug("get_tab(title=%r) 失败: %s", hint, exc)
    return browser.latest_tab


def connect(
    port: int = config.DEFAULT_PORT, target_hint: str = config.DEFAULT_TARGET_HINT
):
    """接管或启动 Chromium，并复用同端口的健康 4.2 浏览器连接。

    启动参数只组合 DrissionPage 4.2 官方 API：Edge、账密代理、PDF 下载模式和
    remove_test_type 均由项目环境变量显式控制。重连新浏览器时清空旧 Context
    注册表，避免把失效对象暴露给后续工具。
    """
    with _lock:
        global _browser, _tab, _port, _target_hint, _active_tab_name, _context_seq
        logger.info("connect port=%s target_hint=%r", port, target_hint)

        if _browser is not None and port == _port:
            try:
                browser_alive = bool(_browser.states.is_alive)
                tab_alive = _tab is not None and bool(_tab.states.is_alive)
                if browser_alive:
                    if not tab_alive or target_hint != _target_hint:
                        _tab = _pick_tab(_browser, target_hint)
                    _target_hint = target_hint
                    return _tab
            except Exception as exc:
                logger.debug("现有浏览器连接不可复用: %s", exc)

        _port = port
        _target_hint = target_hint
        _ensure_display_env()
        if os.path.isfile(_DP_INI):
            options = ChromiumOptions(read_file=True, ini_path=_DP_INI)
        else:
            options = ChromiumOptions(read_file=False)
            logger.info("DrissionPage ini not found; using runtime options: %s", _DP_INI)
        options.set_address(f"127.0.0.1:{port}")

        if config.CHROME_PATH:
            options.set_browser_path(config.CHROME_PATH)
        elif config.EDGE_MODE:
            options.set_browser_path(edge=True)
        elif sys.platform.startswith("linux"):
            executable = (
                shutil.which("google-chrome")
                or shutil.which("google-chrome-stable")
                or shutil.which("chromium")
                or shutil.which("chromium-browser")
            )
            if executable:
                options.set_browser_path(executable)
        if config.PROXY:
            options.set_proxy(config.PROXY)
        if config.DISABLE_PDF_PREVIEW:
            options.disable_pdf_preview()
        if config.REMOVE_TEST_TYPE:
            options.remove_test_type()
        if config.HEADLESS:
            options.headless(True)
            options.set_argument("--no-sandbox")
            logger.info("headless 模式启动")

        _browser = Chromium(options)
        _tab = _pick_tab(_browser, target_hint)
        _active_tab_name = ""
        _contexts.clear()
        _context_seq = 0
        logger.info("connected tab url=%s", (_tab.url or "")[:120])
        return _tab


def list_tabs():
    """用 4.2 ``get_tabs()`` 一次取得标签页对象及当前状态。"""
    with _lock:
        if _browser is None:
            return []
        try:
            current_id = getattr(_tab, "tab_id", None)
            return [
                {
                    "tab_id": tab.tab_id,
                    "url": (tab.url or "")[:120],
                    "title": (tab.title or "")[:40],
                    "is_current": tab.tab_id == current_id,
                }
                for tab in _browser.get_tabs()
            ]
        except Exception as exc:
            logger.warning("list_tabs 失败: %s", exc)
            return []


def tab_count():
    """返回当前 tab 数量（轻量，仅读 tab_ids 长度，供 detect_tab_change 高频轮询用）。"""
    with _lock:
        if _browser is None:
            return 0
        try:
            return len(_browser.tab_ids)
        except Exception as e:
            logger.debug("tab_count 失败: %s", e)
            return 0


def get_tab():
    """取活动 tab；用 4.2 ``states.is_alive`` 探活并自愈重连。"""
    with _lock:
        global _tab
        if _tab is None:
            connect(_port, _target_hint)
        else:
            try:
                if not _tab.states.is_alive:
                    raise RuntimeError("tab is closed")
            except Exception as exc:
                logger.warning("tab 探活失败，重连: %s", exc)
                connect(_port, _target_hint)
        return _tab


def get_browser():
    """取浏览器实例，连接失效则自愈重连。"""
    with _lock:
        global _browser
        if _browser is None:
            connect(_port, _target_hint)
        return _browser


def get_active_frame(tab=None):
    """用 4.2 ``get_frame()`` 获取当前可见业务 iframe，并同步模块名称。"""
    with _lock:
        tab = tab or get_tab()
        try:
            global _active_tab_name
            frame = tab.get_frame(ui_contract.ACTIVE_FRAME, timeout=1.0)
            if not frame or getattr(frame, "_type", None) != "ChromiumFrame":
                _active_tab_name = ""
                return None

            active_tab = ele_with_fallback(
                tab,
                'css:div[role="tab"][aria-selected="true"]',
                'xpath://div[@role="tab" and @aria-selected="true"]',
                timeout=0.5,
            )
            if active_tab:
                trigger = ele_with_fallback(
                    active_tab,
                    'css:.ant-dropdown-trigger',
                    'xpath:.//*[contains(@class,"ant-dropdown-trigger")]',
                    timeout=0.1,
                ) or active_tab
                _active_tab_name = (trigger.text or "").strip()
            else:
                _active_tab_name = ""
            return frame
        except Exception as exc:
            _active_tab_name = ""
            logger.debug("get_active_frame 失败: %s", exc)
            return None


def get_active_tab_name(tab=None):
    """获取当前活跃的 tab 名称。

    返回最近一次 get_active_frame 检测到的 tab 名称，
    或重新查询（tab 为 None 时使用默认 tab）。
    """
    global _active_tab_name
    if not _active_tab_name:
        get_active_frame(tab or get_tab())
    return _active_tab_name


def get_tab_ro():
    """返回当前 tab（只读，不探活不重连）。仅在读锁保护下调用，确保无并发写。"""
    return _tab

def set_tab(tab):
    """更新 MCP 当前标签页并清空依赖旧页面的活动模块名称缓存。"""
    with _lock:
        global _tab, _active_tab_name
        _tab = tab
        _active_tab_name = ""
        return _tab


def get_active_frame_ro(tab=None, timeout: float = 1.0):
    """只读路径用 4.2 ``get_frame()`` 获取活动 iframe，不触发重连。"""
    tab = tab or _tab
    if tab is None:
        return None
    try:
        frame = tab.get_frame(ui_contract.ACTIVE_FRAME, timeout=max(float(timeout or 0), 0.0))
        return frame if frame and getattr(frame, "_type", None) == "ChromiumFrame" else None
    except Exception as exc:
        logger.debug("get_active_frame_ro 失败: %s", exc)
        return None


def _remaining(deadline: float) -> float:
    return max(deadline - monotonic(), 0.0)


def _lookup_frame(tab, deadline: float):
    """给 frame 解析最多 0.5 秒，且不突破调用方总预算。"""
    return get_active_frame_ro(tab, timeout=min(_remaining(deadline), 0.5))


def find(
    locator: str, in_frame: bool = True, timeout: float = 5, wait_clickable: bool = True
):
    """在单一 timeout 预算内按业务 iframe → 顶层顺序查找并等待可点击。"""
    with _lock:
        timeout = max(float(timeout or 0), 0.0)
        deadline = monotonic() + timeout
        tab = get_tab()
        element = None
        if in_frame:
            frame = _lookup_frame(tab, deadline)
            if frame is not None:
                element = frame.ele(locator, timeout=_remaining(deadline) * 0.9)
        if not element:
            element = tab.ele(locator, timeout=_remaining(deadline))
        if not element:
            return None
        if wait_clickable:
            try:
                element.wait.clickable(
                    timeout=_remaining(deadline), wait_stop=False, raise_err=False
                )
                if not element.states.is_clickable:
                    logger.debug("元素已找到但不可点击: %s", locator)
            except Exception:
                # 纯展示节点没有 clickable 语义，定位成功仍应返回元素。
                pass
        return element


def find_all(locator: str, in_frame: bool = True, timeout: float = 5):
    """在单一 timeout 预算内从业务 iframe 查找集合，空结果再查顶层。"""
    with _lock:
        deadline = monotonic() + max(float(timeout or 0), 0.0)
        tab = get_tab()
        if in_frame:
            frame = _lookup_frame(tab, deadline)
            if frame is not None:
                elements = frame.eles(locator, timeout=_remaining(deadline) * 0.9)
                if elements:
                    return elements
        return tab.eles(locator, timeout=_remaining(deadline))


def find_static(
    locator: str = None, in_frame: bool = True, timeout: float = 5, index: int = 1
):
    """在单一 timeout 预算内获取只读 SessionElement。"""
    with _lock:
        deadline = monotonic() + max(float(timeout or 0), 0.0)
        tab = get_tab()
        if in_frame:
            frame = _lookup_frame(tab, deadline)
            if frame is not None:
                element = (
                    frame.s_ele(locator, index=index, timeout=_remaining(deadline) * 0.9)
                    if locator else frame.s_ele()
                )
                if element:
                    return element
        return (
            tab.s_ele(locator, index=index, timeout=_remaining(deadline))
            if locator else tab.s_ele()
        )


def find_batch(
    locators: list,
    in_frame: bool = True,
    timeout: float = 5,
    any_one: bool = True,
    first_ele: bool = True,
):
    """在单一 timeout 预算内批量匹配业务 iframe，空结果再查顶层。"""
    with _lock:
        deadline = monotonic() + max(float(timeout or 0), 0.0)
        tab = get_tab()
        if in_frame:
            frame = _lookup_frame(tab, deadline)
            if frame is not None:
                result = frame.find(
                    locators,
                    any_one=any_one,
                    first_ele=first_ele,
                    timeout=_remaining(deadline) * 0.9,
                )
                if any_one and result and result[0] is not None:
                    return result
                if not any_one and result and any(result.values()):
                    return result
        return tab.find(
            locators,
            any_one=any_one,
            first_ele=first_ele,
            timeout=_remaining(deadline),
        )


def get_frame_by_locator(locator, timeout: float = 5):
    """按定位符/序号/id/name 获取 iframe/frame 元素（get_frame 封装）。

    locator 可以是定位字符串、int 序号（1 开始，负数倒数）、id 属性内容、name 属性内容。
    返回 ChromiumFrame 对象，可在其内部继续查找元素。
    """
    with _lock:
        tab = get_tab()
        if isinstance(locator, int) or (isinstance(locator, str) and locator.isdigit()):
            return tab.get_frame(int(locator), timeout=timeout)
        return tab.get_frame(locator, timeout=timeout)


# ==================== BrowserContext 注册表 ====================
# 稳定自增 id → BrowserContext，供 new_context / switch_context / list_contexts 复用。
# 单全局 _tab 锁让通用工具按角色顺序切换，避免跨角色页面状态混用。
_contexts = {}
_context_seq = 0


def create_context(proxy=None):
    """创建隔离的 BrowserContext、首个 tab，并注册后返回 ``(context_id, tab)``。

    DrissionPage 新建的 context 没有可操作的页面，因此这里始终紧接着创建一个
    tab。调用方只持有稳定的 context id，后续通过 ``switch_context()`` 取得最新 tab，
    避免浏览器重连后继续使用失效对象。
    """
    with _lock:
        browser = get_browser()
        context = None
        try:
            context = browser.new_context(proxy=proxy) if proxy else browser.new_context()
            tab = context.new_tab()
            return register_context(context), tab
        except Exception:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            raise


def register_context(context):
    """线程安全地注册 BrowserContext，返回稳定自增 id。"""
    with _lock:
        global _context_seq
        _context_seq += 1
        context_id = _context_seq
        _contexts[context_id] = context
        return context_id


def context_exists(context_id: int) -> bool:
    """返回 context 是否仍在当前浏览器注册表中。"""
    with _lock:
        return context_id in _contexts


def list_contexts():
    """列出上下文 tab，并标记当前活动上下文。"""
    with _lock:
        active_id = getattr(_tab, "tab_id", None)
        result = []
        for context_id, context in _contexts.items():
            tab_ids = list(getattr(context, "tab_ids", []) or [])
            result.append({
                "context_id": context_id,
                "tab_ids": tab_ids,
                "is_active": active_id in tab_ids,
            })
        return result


def switch_context(context_id):
    """激活 BrowserContext 管理的首个标签页并同步 MCP 活动 tab。"""
    with _lock:
        context = _contexts.get(context_id)
        if context is None:
            return None
        tab_ids = list(getattr(context, "tab_ids", []) or [])
        if not tab_ids:
            return None
        try:
            tab = context.get_tab(tab_ids[0])
            tab.activate()
            return set_tab(tab)
        except Exception as exc:
            logger.warning("switch_context 取 tab 失败: %s", exc)
            return None


def close_context(context_id) -> dict:
    """关闭并注销上下文；若其正在使用，自动切回主浏览器标签页。"""
    with _lock:
        context = _contexts.pop(context_id, None)
        if context is None:
            return {"ok": False, "reason": "context 不存在", "context_id": context_id}
        tab_ids = set(getattr(context, "tab_ids", []) or [])
        was_active = getattr(_tab, "tab_id", None) in tab_ids
        try:
            context.close()
        except Exception as exc:
            _contexts[context_id] = context
            return {
                "ok": False,
                "reason": "关闭 context 失败: %s" % exc,
                "context_id": context_id,
            }

        replacement = None
        if was_active:
            if _browser is not None:
                try:
                    replacement = _pick_tab(_browser, _target_hint)
                except Exception:
                    replacement = None
                if replacement is None:
                    try:
                        replacement = _browser.new_tab()
                    except Exception:
                        pass
            set_tab(replacement)
        return {
            "ok": True,
            "context_id": context_id,
            "switched_tab_id": getattr(replacement, "tab_id", None),
        }
