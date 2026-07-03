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
from pathlib import Path

from DrissionPage import Chromium, ChromiumOptions

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

# dp_configs.ini 路径（configs/ 目录）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DP_INI = str(_PROJECT_ROOT / "configs" / "dp_configs.ini")


def load_js(name: str) -> str:
    """读取 js/ 目录下的页面注入脚本。"""
    with open(os.path.join(_JS_DIR, name), encoding="utf-8") as f:
        return f.read()


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
        xauth_dir = "/run/user/%d" % os.getuid()
        try:
            for f in os.listdir(xauth_dir):
                if f.startswith(".mutter-Xwaylandauth."):
                    os.environ["XAUTHORITY"] = os.path.join(xauth_dir, f)
                    break
        except OSError:
            pass
    logger.info("补齐图形环境: DISPLAY=%s XAUTHORITY=%s",
                os.environ.get("DISPLAY"), os.environ.get("XAUTHORITY", "(未找到)"))


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
    """连接 Chrome：接管指定端口已运行实例，或按 dp_configs.ini 自启动。

    交给 DrissionPage 的 Chromium() 处理——端口有实例则接管，无则启动
    （existing_only=False），CDP 就绪等待由 DrissionPage 内部完成。
    跨平台：Linux 有头自动补 DISPLAY/XAUTHORITY 并探测浏览器路径；
    HL_HEADLESS 走无头 + --no-sandbox；Windows/macOS 交给 DrissionPage 探测。
    """
    with _lock:
        global _browser, _tab, _port, _target_hint
        _port = port
        _target_hint = target_hint
        logger.info("connect port=%s target_hint=%r", port, target_hint)

        _ensure_display_env()
        co = ChromiumOptions(read_file=True, ini_path=_DP_INI)
        co.set_address(f'127.0.0.1:{port}')
        # 浏览器路径（跨平台）：优先 HL_CHROME_PATH；否则 Linux 显式指向 google-chrome
        # （ini 默认 'chrome' 在多数发行版无此命令），Windows/macOS 交给 DrissionPage 自动探测。
        if config.CHROME_PATH:
            co.set_browser_path(config.CHROME_PATH)
        elif sys.platform.startswith("linux"):
            exe = shutil.which("google-chrome") or shutil.which("google-chrome-stable") \
                or shutil.which("chromium") or shutil.which("chromium-browser")
            if exe:
                co.set_browser_path(exe)
        if config.HEADLESS:
            # CI/CD 无图形环境：无头 + 容器常需 no-sandbox
            co.headless(True)
            co.set_argument('--no-sandbox')
            logger.info("headless 模式启动")
        _browser = Chromium(co)

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


def get_tab_ro():
    """返回当前 tab（只读，不探活不重连）。仅在读锁保护下调用，确保无并发写。"""
    return _tab


def get_active_frame_ro(tab=None):
    """取活动 iframe（只读版本，不加 _lock，不重连）。
    
    仅在读锁保护下调用：保证 _tab 不会被写操作替换。
    """
    tab = tab or _tab
    if tab is None:
        return None
    try:
        js = (
            "var f=document.querySelector('[role=\"tabpanel\"][aria-hidden=\"false\"] iframe');"
            "if(!f)return JSON.stringify({found:false});"
            "return JSON.stringify({found:true, name:f.name||'', id:f.id||''});"
        )
        res = tab.run_js(js)
        d = json.loads(res) if isinstance(res, str) else (res or {})
        name = d.get("name") or d.get("id") or ""
        if name:
            try:
                fr = tab.get_frame(name, timeout=3)
                if fr and not isinstance(fr, str) and getattr(fr, '_type', None) == 'ChromiumFrame':
                    return fr
            except Exception:
                pass
        try:
            fr = tab.get_frame(ACTIVE_FRAME_LOC, timeout=3)
            if fr and not isinstance(fr, str) and getattr(fr, '_type', None) == 'ChromiumFrame':
                return fr
        except Exception:
            pass
        return None
    except Exception as e:
        logger.debug("get_active_frame_ro 失败: %s", e)
        return None


def find(locator: str, in_frame: bool = True, timeout: float = 5, wait_clickable: bool = True):
    """按 DrissionPage 定位符查找元素：优先在活动 iframe 内，未命中再回退到 top 文档。

    支持完整 DP 定位语法：
      #id          — id 匹配（精确），#:ne 模糊，#^on 开头，#$ne 结尾
      .cls         — class 匹配（精确），.:_cls 模糊，.^p_ 开头，.$_cls 结尾
      text=文      — 文本精确匹配，text:文 模糊匹配（纯字符串默认模糊匹配文本）
      tag:div      — 标签类型匹配（简化 t:div）
      css:.cls     — CSS 选择器（简化 c:.cls）
      xpath://div  — XPath 定位（简化 x://div）
      @id=val      — 单属性精确匹配（@id:val 模糊，@id^val 开头，@id$val 结尾）
      @@k1=v@@k2=v — 多属性与匹配（所有条件同时满足）
      @|k1=v@|k2=v — 多属性或匹配（任一条件满足）
      @!id=val     — 否定匹配（@!class 匹配无 class 属性的元素）
      ax:@role=btn@name=xxx — 无障碍模式匹配
    简化写法：text→tx, tag→t, css→c, xpath→x, @text()→@tx(), @tag()→@t()
    链式：tab('#id')('.cls') 等价于 tab.ele('#id').ele('.cls')
    shadow-root：ele.sr 等价于 ele.shadow_root
    文档：https://drissionpage.cn/browser_control/get_elements/syntax

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

def find_all(locator: str, in_frame: bool = True, timeout: float = 5):
    """按 DrissionPage 定位符查找所有匹配元素（eles 封装）。

    支持完整 DP 定位语法：tag:div / t:div / #id / .cls / @attr=v / @@k1=v@@k2=v / @|k1=v@|k2=v
    text:文 / tx=文 / css:.cls / c:.cls / xpath://div / x://div / ax:@role=btn@name=xxx
    纯文本自动匹配。简化写法见 https://drissionpage.cn/browser_control/get_elements/simplify
    """
    with _lock:
        tab = get_tab()
        if in_frame:
            fr = get_active_frame(tab)
            if fr is not None:
                els = fr.eles(locator, timeout=timeout)
                if els:
                    return els
        return tab.eles(locator, timeout=timeout)


def find_static(locator: str = None, in_frame: bool = True, timeout: float = 5, index: int = 1):
    """按 DrissionPage 定位符查找元素的静态版本（s_ele 封装）。

    静态元素（SessionElement）由纯文本构造，速度极快，适合批量数据采集。
    返回的静态元素不能交互（点击/输入），只能读取属性/文本。
    locator 为 None 时返回调用者本身的静态副本。
    """
    with _lock:
        tab = get_tab()
        if in_frame:
            fr = get_active_frame(tab)
            if fr is not None:
                ele = fr.s_ele(locator, index=index, timeout=timeout) if locator else fr.s_ele()
                if ele:
                    return ele
        return tab.s_ele(locator, index=index, timeout=timeout) if locator else tab.s_ele()


def find_batch(locators: list, in_frame: bool = True, timeout: float = 5,
               any_one: bool = True, first_ele: bool = True):
    """同时匹配多个定位符（find 封装）。返回 dict{定位符: 元素} 或 tuple(定位符, 元素)。

    any_one=True: 返回第一个有结果的 (定位符, 元素)
    any_one=False: 返回 {定位符: 元素}（first_ele=True 每个定位符第一个，False 所有）
    """
    with _lock:
        tab = get_tab()
        if in_frame:
            fr = get_active_frame(tab)
            if fr is not None:
                res = fr.find(locators, any_one=any_one, first_ele=first_ele, timeout=timeout)
                if any_one:
                    if res[0] is not None:
                        return res
                else:
                    if any(v for v in res.values()):
                        return res
        return tab.find(locators, any_one=any_one, first_ele=first_ele, timeout=timeout)


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


