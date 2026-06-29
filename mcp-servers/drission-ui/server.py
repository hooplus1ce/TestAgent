"""drission-ui MCP 服务器：把 DrissionPage 浏览器自动化封装成结构化 MCP 工具。

供 AI 驱动的 UI 测试技能调用。浏览器原语(连接/扫描/点击/输入/截图)、
VTable 工具(内部 frame.run_js 注入 bundled JS)、会话维持、弹窗检测、网络监听。

启动：uv run python mcp-servers/drission-ui/server.py  (stdio 传输)
"""
import json
import time

from mcp.server.fastmcp import FastMCP

import browser_session
import vtable
import session_auth
import modal

mcp = FastMCP("drission-ui")


# ==================== 连接与会话 ====================

@mcp.tool()
def connect(port: int = 9222, target_hint: str = "诺贝科技") -> dict:
    """接管 port 上已运行的 Chrome（不启动新实例），选中目标 tab。前置：Chrome 须以
    --remote-debugging-port=<port> 启动。返回当前 url/title 与所有 tab 列表。"""
    tab = browser_session.connect(port, target_hint)
    tabs = []
    try:
        for tid in browser_session._browser.tab_ids:
            t = browser_session._browser.get_tab(tid)
            tabs.append({"url": (t.url or "")[:120], "title": (t.title or "")[:40]})
    except Exception as e:
        tabs = [{"error": str(e)}]
    return {"ok": True, "url": tab.url, "title": tab.title, "tabs": tabs}


@mcp.tool()
def cache_session() -> dict:
    """缓存当前 tab 的 SESSION/UCTOKEN/cookie_token 到服务器内存，供会话过期时刷新复用。"""
    return session_auth.cache_session()


@mcp.tool()
def refresh_session() -> dict:
    """用缓存 cookie 经 CDP 注入并刷新页面，恢复过期会话。先 cache_session 再调用。"""
    return session_auth.refresh_session()


@mcp.tool()
def login_ocr() -> dict:
    """OCR 识别验证码 + HTTP 登录获取 cookie → 注入 → 导航 SCM Admin。用于首次登录或完全失效。"""
    return session_auth.login_ocr()


@mcp.tool()
def check_session() -> dict:
    """检测 top 层是否出现『登录过期』系统确认弹窗。返回 {expired, detail}。"""
    return session_auth.check_session()


# ==================== 导航与 frame ====================

@mcp.tool()
def enter_module(menu_text: str) -> dict:
    """点击左侧菜单进入模块（按菜单文字匹配），并轮询等待业务 iframe 加载完成。"""
    tab = browser_session.get_tab()
    js = (
        "var items=[].slice.call(document.querySelectorAll('.ant-menu-item, li[class*=\"ant-menu\"]'));"
        "var m=items.find(function(el){return el.textContent.trim().indexOf(%s)>=0;});"
        "if(m){m.click();return JSON.stringify({ok:true,text:m.textContent.trim()});}"
        "return JSON.stringify({ok:false,reason:'menu not found'});"
    ) % json.dumps(menu_text, ensure_ascii=False)
    res = tab.run_js(js)
    res = json.loads(res) if isinstance(res, str) else res
    if not res.get("ok"):
        return res
    # 轮询等待 iframe
    for _ in range(40):
        time.sleep(0.5)
        if browser_session.get_active_frame(tab) is not None:
            return {"ok": True, "entered": menu_text, "iframe_ready": True}
    return {"ok": True, "entered": menu_text, "iframe_ready": False, "reason": "iframe 未在 20s 内出现"}


@mcp.tool()
def reset_to_initial(module_text: str) -> dict:
    """重置到初始状态：关闭当前业务 tab → 重进模块 → 等 iframe+VTable 就绪。用例间隔离用。"""
    tab = browser_session.get_tab()
    tab.run_js(
        "var b=document.querySelector('.ant-tabs-tab-active.outSide .anticon-close');"
        "if(b){b.click();return JSON.stringify({closed:true});}"
        "return JSON.stringify({closed:false});"
    )
    time.sleep(2)
    return enter_module(module_text)


@mcp.tool()
def get_active_frame() -> dict:
    """获取当前可见 tabpanel 内的业务 iframe。返回 {ok, url}。"""
    fr = browser_session.get_active_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe，请先 enter_module"}
    return {"ok": True, "url": getattr(fr, "url", "") or ""}


# ==================== 通用 DOM 原语 ====================

@mcp.tool()
def scan_page_elements(include_iframe: bool = True) -> dict:
    """扫描页面所有可见交互控件(button/a/input/role=*/canvas)，递归穿透同源 iframe，
    按 frame 分组返回，含中心坐标。进入模块后第一件事。"""
    tab = browser_session.get_tab()
    script = browser_session.load_js("element-scan.js") + "\nreturn JSON.stringify(scanInteractiveControls());"
    res = tab.run_js(script)
    return json.loads(res) if isinstance(res, str) else res


@mcp.tool()
def dom_overview() -> dict:
    """页面俯瞰：顶部页签(含选中态) + 可见按钮文本(含 disabled)。不点击任何元素。"""
    tab = browser_session.get_tab()
    script = browser_session.load_js("element-scan.js") + "\nreturn JSON.stringify(domOverview());"
    res = tab.run_js(script)
    return json.loads(res) if isinstance(res, str) else res


@mcp.tool()
def click(locator: str, in_frame: bool = True, by_js: bool = False) -> dict:
    """点击元素。locator 为 DrissionPage 定位符(#id/.cls/@attr=v/text:文/css:选择器)。
    in_frame 优先在活动 iframe 内查找。by_js=True 用 JS 点击(绕过遮挡)。"""
    ele = browser_session.find(locator, in_frame=in_frame)
    if not ele:
        return {"ok": False, "reason": "元素未找到: %s" % locator}
    ele.click(by_js=by_js)
    return {"ok": True, "locator": locator}


@mcp.tool()
def click_xy(x: float, y: float, hover_first: bool = True, duration: float = 0.3) -> dict:
    """按顶层视口坐标点击(用于 canvas)。hover_first 先移动到目标(hover)再点击——VTable 排序图标需要。"""
    tab = browser_session.get_tab()
    tab.actions.move_to((x, y), duration=duration if hover_first else 0).click()
    return {"ok": True, "x": x, "y": y}


@mcp.tool()
def input(locator: str, text: str, in_frame: bool = True, clear: bool = True) -> dict:
    """向输入框填入文本。clear=True 先清空。"""
    ele = browser_session.find(locator, in_frame=in_frame)
    if not ele:
        return {"ok": False, "reason": "元素未找到: %s" % locator}
    if clear:
        try:
            ele.clear()
        except Exception:
            pass
    ele.input(text)
    return {"ok": True, "locator": locator}


@mcp.tool()
def insert_text(text: str) -> dict:
    """向当前焦点元素插入文本(动作链)。"""
    tab = browser_session.get_tab()
    tab.actions.input(text)
    return {"ok": True}


@mcp.tool()
def hover(locator: str = None, x: float = None, y: float = None, in_frame: bool = True, duration: float = 0.3) -> dict:
    """鼠标悬停。给 locator 悬停元素；或给 x,y 悬停坐标。"""
    tab = browser_session.get_tab()
    if locator:
        ele = browser_session.find(locator, in_frame=in_frame)
        if not ele:
            return {"ok": False, "reason": "元素未找到: %s" % locator}
        tab.actions.move_to(ele)
    else:
        tab.actions.move_to((x, y), duration=duration)
    return {"ok": True}


@mcp.tool()
def screenshot(path: str = None, locator: str = None, in_frame: bool = True) -> dict:
    """截图。locator 给定则截元素，否则截全页。path 为空则存当前目录 shot_<时间戳>.png。"""
    tab = browser_session.get_tab()
    path = path or "shot_%d.png" % int(time.time())
    if locator:
        ele = browser_session.find(locator, in_frame=in_frame)
        if not ele:
            return {"ok": False, "reason": "元素未找到: %s" % locator}
        ele.get_screenshot(path=path)
    else:
        tab.get_screenshot(path=path)
    return {"ok": True, "path": path}


@mcp.tool()
def run_js(script: str, in_frame: bool = True) -> dict:
    """逃生舱：执行任意 JS。in_frame=True 在活动 iframe 内执行。script 内可用 return 返回值。
    返回值需为 JSON 可序列化(建议 return JSON.stringify(...))。"""
    target = browser_session.get_active_frame() if in_frame else None
    if target is None:
        target = browser_session.get_tab()
    res = target.run_js(script)
    try:
        json.dumps(res)
    except (TypeError, ValueError):
        res = str(res)
    return {"ok": True, "result": res}


# ==================== VTable（canvas 表格）====================

@mcp.tool()
def mount_vtable() -> dict:
    """挂载 VTable 实例到 iframe 的 window._vtable（遍历 React fiber）。所有 VTable 工具的前置。"""
    return vtable.mount_vtable()


@mcp.tool()
def scan_vtable_columns(max_col: int = 50) -> dict:
    """扫描 VTable 列定义：标题/body 行为(bodyBehavior/bodyType/bodyEditable)/表头图标(含顶层视口坐标 viewportX/Y)。
    图标坐标可直接用于 click_cell/click_xy。"""
    return {"columns": vtable.scan_vtable_columns(max_col)}


@mcp.tool()
def get_column_values(title: str, raw: bool = False) -> dict:
    """按中文列标题取该列所有单元格值。raw=False 视觉文本(与界面一致)；raw=True 原始字段值(如数字码)。筛选断言用。"""
    return {"title": title, "raw": raw, "values": vtable.get_column_values(title, raw)}


@mcp.tool()
def get_cell_rect(col: int, row: int) -> dict:
    """取单元格中心顶层视口坐标(先 scrollToCell 确保可见)。返回 {viewportX, viewportY}。"""
    return vtable.get_cell_rect(col, row)


@mcp.tool()
def scroll_to_cell(col: int, row: int) -> dict:
    """滚动 VTable 使目标单元格进入视口。"""
    return vtable.scroll_to_cell(col, row)


@mcp.tool()
def click_cell(col: int, row: int, icon_name: str = None, hover_first: bool = True, duration: float = 0.3) -> dict:
    """点击 VTable 单元格或其图标。icon_name(如 'sort')给定时点该图标(先 hover 再 click)；否则点单元格中心。"""
    return vtable.click_cell(col, row, icon_name, hover_first, duration)


# ==================== 弹窗检测 / 网络 / 轨迹 ====================

@mcp.tool()
def detect_modal() -> dict:
    """点击后检测弹窗(三级优先级)：iframe 业务弹窗/消息 → top 层系统确认 → none。每次点击后必调。"""
    return modal.detect_modal()


@mcp.tool()
def listen_start(targets: str, method: str = None) -> dict:
    """启动网络监听。targets 为 URL 特征(支持多个用逗号或列表)。method 可选 'POST'/'GET'。"""
    tab = browser_session.get_tab()
    kwargs = {}
    if method:
        kwargs["method"] = method
    tab.listen.start(targets, **kwargs)
    return {"ok": True, "targets": targets}


@mcp.tool()
def listen_wait(count: int = 1, timeout: float = 10) -> dict:
    """等待监听的数据包。返回 {url, method, status, body}(body 自动解析 JSON)。count>1 返回 packets 列表。"""
    tab = browser_session.get_tab()
    pkt = tab.listen.wait(count=count, timeout=timeout)
    if not pkt:
        return {"ok": False, "reason": "timeout"}

    def conv(p):
        return {
            "url": getattr(p, "url", ""),
            "method": getattr(p, "method", ""),
            "status": getattr(p.response, "status", None) if p.response else None,
            "body": getattr(p.response, "body", None) if p.response else None,
        }

    if isinstance(pkt, list):
        return {"ok": True, "packets": [conv(p) for p in pkt]}
    return {"ok": True, **conv(pkt)}


@mcp.tool()
def mouse_trail(on: bool = True) -> dict:
    """开启/关闭鼠标轨迹可视化(红色圆点跟踪 mousemove/click)。调试 canvas 点击落点用。"""
    return modal.mouse_trail(on)


if __name__ == "__main__":
    mcp.run()
