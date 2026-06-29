"""DrissionPage 浏览器单例：接管 9222 端口、活动 tab / iframe(frame) 解析与自愈重连。

所有 MCP 工具通过本模块取 tab / frame，保证跨工具调用复用同一个浏览器连接。
"""
import os
import json

from DrissionPage import Chromium

_JS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "js")

# 模块级单例
_browser = None
_tab = None
_port = 9222
_target_hint = "诺贝科技"

# 活动业务 iframe 选择器（SCM：可见 tabpanel 内的 iframe）
ACTIVE_FRAME_LOC = 'css:[role="tabpanel"][aria-hidden="false"] iframe'


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
    except Exception:
        pass
    try:
        for tid in browser.tab_ids:
            t = browser.get_tab(tid)
            if t and t.url and "hoolinks" in t.url:
                return t
    except Exception:
        pass
    try:
        t = browser.get_tab(title=hint)
        if t:
            return t
    except Exception:
        pass
    return browser.latest_tab


def connect(port: int = 9222, target_hint: str = "诺贝科技"):
    """接管 port 上已运行的 Chrome（不启新实例），选中目标 tab。"""
    global _browser, _tab, _port, _target_hint
    _port = port
    _target_hint = target_hint
    _browser = Chromium(port)
    _tab = _pick_tab(_browser, target_hint)
    return _tab


def get_tab():
    """取活动 tab，连接失效则自愈重连。"""
    global _tab
    if _tab is None:
        connect(_port, _target_hint)
    else:
        try:
            _ = _tab.url  # 探活
        except Exception:
            connect(_port, _target_hint)
    return _tab


def get_active_frame(tab=None):
    """取当前可见 tabpanel 内的业务 iframe（ChromiumFrame）；无则返回 None。"""
    tab = tab or get_tab()
    try:
        fr = tab.get_frame(ACTIVE_FRAME_LOC, timeout=3)
        return fr or None
    except Exception:
        return None


def frame_offset(tab=None):
    """活动 iframe 左上角在【顶层视口】的偏移 (x, y)。

    actions.move_to((x,y)) 接收视口坐标，故用 getBoundingClientRect（恒为视口坐标）
    而非 rect.location（页面坐标，页面滚动时会错位）。
    """
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


def find(locator: str, in_frame: bool = True, timeout: float = 5):
    """按 DrissionPage 定位符查找元素：优先在活动 iframe 内，否则在 top 文档。"""
    tab = get_tab()
    if in_frame:
        fr = get_active_frame(tab)
        if fr is not None:
            ele = fr.ele(locator, timeout=timeout)
            if ele:
                return ele
    return tab.ele(locator, timeout=timeout)
