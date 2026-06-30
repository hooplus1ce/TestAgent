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
    """取当前可见 tabpanel 内的业务 iframe（ChromiumFrame）；无则返回 None。
    
    修复: 两步策略——先用 JS document.querySelector 获取 iframe name，
    再用 DrissionPage get_frame(name) 按名称查找，避免 CSS 选择器
    因 ChromiumFrame 类型检查失败而返回 NoneElement。
    """
    with _lock:
        tab = tab or get_tab()
        try:
            # Step 1: JS 查找 iframe 元素并获取 name/id
            js = (
                "var f=document.querySelector('[role=\"tabpanel\"][aria-hidden=\"false\"] iframe');"
                "if(!f)return JSON.stringify({found:false});"
                "return JSON.stringify({found:true, name:f.name||'', id:f.id||''});"
            )
            res = tab.run_js(js)
            d = json.loads(res) if isinstance(res, str) else (res or {})
            name = d.get("name") or d.get("id") or ""
            
            # Step 2: 优先按 name 查找 frame（DrissionPage 对 name 查找更可靠）
            if name:
                try:
                    fr = tab.get_frame(name, timeout=3)
                    if fr and not isinstance(fr, str) and getattr(fr, '_type', None) == 'ChromiumFrame':
                        return fr
                except Exception:
                    pass
            
            # Step 3: 降级到原始 CSS 选择器
            try:
                fr = tab.get_frame(ACTIVE_FRAME_LOC, timeout=3)
                if fr and not isinstance(fr, str) and getattr(fr, '_type', None) == 'ChromiumFrame':
                    return fr
            except Exception:
                pass
            
            return None
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
    """按 DrissionPage 定位符查找元素：优先在活动 iframe 内，未命中再回退到 top 文档。

    Args:
        locator: DrissionPage 定位符
        in_frame: 优先在活动 iframe 内查找，未命中再回退 top 文档
        timeout: 查找超时秒数
        wait_clickable: True 时找到后等待元素可点击；若超时仍不可点击则返回 None
            （click/input 需可交互元素；截图/hover 等只读场景应传 False 跳过此校验）
    """
    with _lock:
        tab = get_tab()
        ele = None
        # 优先在活动 iframe 内查找，未命中再回退 top 文档
        if in_frame:
            fr = get_active_frame(tab)
            if fr is not None:
                ele = fr.ele(locator, timeout=timeout)
        if not ele:
            ele = tab.ele(locator, timeout=timeout)
        if not ele:
            return None
        if wait_clickable:
            try:
                ele.wait.clickable(wait_stop=False)  # 轮询至可点击或超时（超时不抛错）
                if not ele.states.is_clickable:
                    logger.debug("元素已找到但不可点击: %s", locator)
                    return None
            except Exception:
                # 某些元素无 clickable 概念（如纯展示节点），忽略校验返回元素
                pass
        return ele


# ==================== BrowserContext 注册表 ====================
# 稳定自增 id → BrowserContext，供 new_context / switch_context / list_contexts 复用。
# 注意：单全局 _tab 锁决定同一时刻只能在一个 context 内操作（多账号并行需多进程）。
_contexts = {}
_context_seq = 0


def register_context(ctx):
    """注册一个 BrowserContext，返回稳定自增 id（跨调用可复现，不依赖 Python id()）。"""
    global _context_seq
    _context_seq += 1
    cid = _context_seq
    _contexts[cid] = ctx
    return cid


def list_contexts():
    """列出所有已注册上下文的 id 与 tab 列表。"""
    return [{"context_id": cid, "tab_ids": list(getattr(ctx, "tab_ids", []) or [])}
            for cid, ctx in _contexts.items()]


def switch_context(cid):
    """切换活动 tab 到指定 context 的首个 tab；context 不存在或无 tab 返回 None。"""
    with _lock:
        global _tab
        ctx = _contexts.get(cid)
        if ctx is None or _browser is None:
            return None
        tids = list(getattr(ctx, "tab_ids", []) or [])
        if not tids:
            return None
        try:
            _tab = _browser.get_tab(tids[0])
            return _tab
        except Exception as e:
            logger.warning("switch_context 取 tab 失败: %s", e)
            return None


