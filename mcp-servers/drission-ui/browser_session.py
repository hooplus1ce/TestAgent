"""DrissionPage 浏览器单例：接管 9222 端口、活动 tab / iframe(frame) 解析与自愈重连。

所有 MCP 工具通过本模块取 tab / frame，保证跨工具调用复用同一个浏览器连接。
"""
import json
import logging
import os
import threading

from DrissionPage import Chromium

import config

logger = logging.getLogger("drission-ui")

_JS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "js")

# 模块级单例
_browser = None
_tab = None
_port = config.DEFAULT_PORT
_target_hint = config.DEFAULT_TARGET_HINT

# 并发锁：FastMCP 支持并发调用，所有浏览器操作串行化避免竞态
_lock = threading.RLock()

# 活动业务 iframe 选择器（SCM：可见 tabpanel 内的 iframe）
ACTIVE_FRAME_LOC = config.ACTIVE_FRAME_LOC


def load_js(name: str) -> str:
    """读取 js/ 目录下的页面注入脚本。"""
    with open(os.path.join(_JS_DIR, name), encoding="utf-8") as f:
        return f.read()


def _pick_tab(browser, hint):
    """优先选 url 含 hoolinks 的 tab，其次按标题，最后用 latest_tab。"""
    try:
        t = browser.get_tab(url="hoolinks")
        if t:
            return t
    except Exception as e:
        logger.debug("get_tab(url=hoolinks) 失败: %s", e)
    try:
        for tid in browser.tab_ids:
            t = browser.get_tab(tid)
            if t and t.url and "hoolinks" in t.url:
                return t
    except Exception as e:
        logger.debug("遍历 tab_ids 失败: %s", e)
    try:
        t = browser.get_tab(title=hint)
        if t:
            return t
    except Exception as e:
        logger.debug("get_tab(title=%r) 失败: %s", hint, e)
    return browser.latest_tab


def connect(port: int = config.DEFAULT_PORT, target_hint: str = config.DEFAULT_TARGET_HINT):
    """接管 port 上已运行的 Chrome（不启新实例），选中目标 tab。"""
    with _lock:
        global _browser, _tab, _port, _target_hint
        _port = port
        _target_hint = target_hint
        logger.info("connect port=%s target_hint=%r", port, target_hint)
        _browser = Chromium(port)
        _tab = _pick_tab(_browser, target_hint)
        logger.info("connected tab url=%s", (_tab.url or "")[:120])
        return _tab


def list_tabs():
    """列出所有 tab 的 url/title（供 server.py connect 工具使用，避免读 _browser 私有变量）。"""
    with _lock:
        if _browser is None:
            return []
        tabs = []
        try:
            for tid in _browser.tab_ids:
                t = _browser.get_tab(tid)
                tabs.append({"url": (t.url or "")[:120], "title": (t.title or "")[:40]})
        except Exception as e:
            logger.warning("list_tabs 失败: %s", e)
        return tabs


def get_tab():
    """取活动 tab，连接失效则自愈重连。"""
    with _lock:
        global _tab
        if _tab is None:
            connect(_port, _target_hint)
        else:
            try:
                _ = _tab.url  # 探活
            except Exception as e:
                logger.warning("tab 探活失败，重连: %s", e)
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
    """取当前可见 tabpanel 内的业务 iframe（ChromiumFrame）；无则返回 None。"""
    with _lock:
        tab = tab or get_tab()
        try:
            fr = tab.get_frame(ACTIVE_FRAME_LOC, timeout=3)
            return fr or None
        except Exception as e:
            logger.debug("get_active_frame 失败: %s", e)
            return None


def frame_offset(tab=None):
    """活动 iframe 左上角在【顶层视口】的偏移 (x, y)。

    actions.move_to((x,y)) 接收视口坐标，故用 getBoundingClientRect（恒为视口坐标）
    而非 rect.location（页面坐标，页面滚动时会错位）。
    """
    with _lock:
        tab = tab or get_tab()
        js = (
            "var f=document.querySelector('[role=\"tabpanel\"][aria-hidden=\"false\"] iframe');"
            "if(!f)return JSON.stringify({x:0,y:0});"
            "var r=f.getBoundingClientRect();"
            "return JSON.stringify({x:r.left,y:r.top});"
        )
        res = tab.run_js(js)
        d = json.loads(res) if isinstance(res, str) else (res or {"x": 0, "y": 0})
        return float(d.get("x", 0)), float(d.get("y", 0))


def find(locator: str, in_frame: bool = True, timeout: float = 5, wait_clickable: bool = True):
    """按 DrissionPage 定位符查找元素：优先在活动 iframe 内，否则在 top 文档。

    Args:
        locator: DrissionPage 定位符
        in_frame: 优先在活动 iframe 内查找
        timeout: 查找超时秒数
        wait_clickable: 找到后是否等待 ele.wait.clickable() (默认 True，确保元素可点击)
    """
    with _lock:
        tab = get_tab()
        if in_frame:
            fr = get_active_frame(tab)
            if fr is not None:
                ele = fr.ele(locator, timeout=timeout)
                if ele and wait_clickable:
                    ele.wait.clickable(wait_stop=False)
                if ele:
                    return ele
        ele = tab.ele(locator, timeout=timeout)
        if ele and wait_clickable:
            ele.wait.clickable(wait_stop=False)
        return ele
