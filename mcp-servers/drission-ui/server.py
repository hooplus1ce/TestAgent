"""drission-ui MCP 服务器：把 DrissionPage 浏览器自动化封装成结构化 MCP 工具。

供 AI 驱动的 UI 测试技能调用。浏览器原语(连接/扫描/点击/输入/截图)、
VTable 工具(内部 frame.run_js 注入 bundled JS)、会话维持、弹窗检测、网络监听。

启动：uv run python mcp-servers/drission-ui/server.py  (stdio 传输)
"""
import functools
import json
import logging
import os
import sys
import time

# 确保同目录模块可导入（与 verify_live.py 一致）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

import browser_session
import filter_area
import config
import vtable
import session_auth
import modal

# 日志输出到 stderr（stdout 用于 MCP 协议帧，不可污染）
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("drission-ui")

mcp = FastMCP("drission-ui")


def synchronized(fn):
    """所有工具入口串行化，避免并发调用导致浏览器状态竞态。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with browser_session._lock:
            return fn(*args, **kwargs)
    return wrapper


# ==================== 连接与会话 ====================

@mcp.tool()
@synchronized
def connect(port: int = config.DEFAULT_PORT, target_hint: str = config.DEFAULT_TARGET_HINT) -> dict:
    """连接 Chrome。先检查 port 上是否已有 Chrome 实例，有则接管；无则根据
    dp_configs.ini 配置自动启动新实例。返回当前 url/title 与所有 tab 列表。"""
    tab = browser_session.connect(port, target_hint)
    return {"ok": True, "url": tab.url, "title": tab.title, "tabs": browser_session.list_tabs()}


@mcp.tool()
@synchronized
def cache_session() -> dict:
    """缓存当前 tab 的 SESSION/UCTOKEN/cookie_token 到服务器内存，供会话过期时刷新复用。"""
    return session_auth.cache_session()


@mcp.tool()
@synchronized
def refresh_session() -> dict:
    """用缓存 cookie 经 CDP 注入并刷新页面，恢复过期会话。先 cache_session 再调用。"""
    return session_auth.refresh_session()


@mcp.tool()
@synchronized
def login_ocr() -> dict:
    """OCR 识别验证码 + HTTP 登录获取 cookie → 注入 → 导航 SCM Admin。用于首次登录或完全失效。"""
    return session_auth.login_ocr()


@mcp.tool()
@synchronized
def check_session() -> dict:
    """检测 top 层是否出现『登录过期』系统确认弹窗。返回 {expired, detail}。"""
    return session_auth.check_session()


# ==================== 导航与 frame ====================
@mcp.tool()
@synchronized
def expand_filter_area() -> dict:
    """展开筛选区：将弹窗模式切换为内联模式，并展开所有折叠筛选字段。
    使所有筛选字段、运算符、下拉选项暴露在 DOM 中，供后续 click/input 交互。
    若当前已是内联模式或已展开，则自动跳过。
    """
    return filter_area.expand_filter_area()



@mcp.tool()
@synchronized
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
        # 降级：JS 查找点击
        _safe_chars = menu_text.replace('\\', '\\\\').replace("'", "\\'")
        res = tab.run_js(
            "var items=[].slice.call(document.querySelectorAll('.ant-menu-item, li[class*=\"ant-menu\"]'));"
            + f"var m=items.find(function(el){{return el.textContent.trim().indexOf('{_safe_chars}')>=0;}});"
            + "if(m){m.click();return JSON.stringify({ok:true});}"
            + "return JSON.stringify({ok:false});"
        )
        if isinstance(res, str):
            res = json.loads(res)
        if not res or not res.get("ok"):
            return {"ok": False, "reason": "menu not found"}
    else:
        try:
            ele.click()
        except Exception:
            # 无位置/大小，用 JS 点击
            tab.run_js("arguments[0].click();", ele)

    # 2. 等待 iframe 就绪（智能等待：iframe 元素在 DOM 中可见即视为就绪；超时不抛错，由下方 get_active_frame 兜底判定）
    wait_seconds = int(timeout)
    try:
        if old_url is None:
            tab.wait.ele_displayed(config.ACTIVE_FRAME_LOC, timeout=wait_seconds)
        else:
            new_fr = browser_session.get_active_frame(tab)
            if new_fr:
                new_fr.wait.url_change(old_url, timeout=wait_seconds)
            else:
                tab.wait.ele_displayed(config.ACTIVE_FRAME_LOC, timeout=wait_seconds)
    except Exception:
        pass

    if browser_session.get_active_frame(tab) is None:
        return {"ok": True, "entered": menu_text, "iframe_ready": False, "reason": "iframe 未在 %.0fs 内出现" % timeout}

    expand_result = {}
    if expand_filter:
        expand_result = filter_area.expand_filter_area(tab)
        logger.info("expand_filter_area: %s", expand_result.get("reason", ""))
    return {"ok": True, "entered": menu_text, "iframe_ready": True, "expand_filter": expand_result}


@mcp.tool()
@synchronized
def reset_to_initial(module_text: str, timeout: float = 20) -> dict:
    """重置到初始状态：关闭当前业务 tab → 重进模块 → 等 iframe+VTable 就绪。用例间隔离用。"""
    tab = browser_session.get_tab()
    tab.run_js(
        "var b=document.querySelector('.ant-tabs-tab-active.outSide .anticon-close');"
        "if(b){b.click();return JSON.stringify({closed:true});}"
        "return JSON.stringify({closed:false});"
    )
    # 智能等待：业务 iframe 从 DOM 消失即说明 tab 已关闭（最多 10s）；超时不阻断，交给后续 enter_module
    try:
        tab.wait.ele_deleted(config.ACTIVE_FRAME_LOC, timeout=10)
    except Exception:
        pass
    return enter_module(module_text, timeout=timeout)

@mcp.tool()
@synchronized
def scan_filter_fields() -> dict:
    """扫描筛选区所有字段，返回完整字段矩阵（字段名/操作符/输入类型/下拉待选项）。
    自动展开每个下拉字段获取待选项。需先 enter_module 并展开筛选区。
    """
    return filter_area.scan_filter_fields()


@mcp.tool()
@synchronized
def get_active_frame() -> dict:
    """获取当前可见 tabpanel 内的业务 iframe。返回 {ok, url}。"""
    fr = browser_session.get_active_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe，请先 enter_module"}
    return {"ok": True, "url": getattr(fr, "url", "") or ""}


@mcp.tool()
@synchronized
def dom_tree(selector: str = "", max_depth: int = 6, max_children: int = 50,
             text: bool = False, text_limit: int = 100, show_hidden: bool = False,
             save_path: str = "", save_format: str = "yml") -> dict:
    """打印页面或元素的 DOM 树结构（结构化 JSON，便于 AI 识别）。

    Args:
        selector: CSS 选择器，为空则从 body 开始
        max_depth: 最大递归深度（默认 6）
        max_children: 每节点最多收录子节点数（默认 50），超出在 _more 标注
        text: 是否提取元素文本
        text_limit: 每节点文本最大字符数（默认 100），同时整树文本总量限制 5000 字符
        show_hidden: 是否包含 script/style/comment 等隐藏节点（默认 False）
        save_path: 指定文件路径则同时写入磁盘（如 "screenshots/dom-tree.yml"）
        save_format: 输出格式，"json" 或 "yml"（默认 yml，更省 token）
    """
    fr = browser_session.get_active_frame()
    target = fr if fr is not None else browser_session.get_tab()
    try:
        if selector:
            root = target.ele(f'css:{selector}', timeout=3)
            if not root:
                return {"ok": False, "reason": f"selector 未匹配: {selector}"}
        else:
            root = target

        # 用 querySelector 找根元素（与上面 DrissionPage 的 ele 双重验证）
        escaped = selector.replace("'", "\\'")
        find_el = ("var el = document.querySelector('" + escaped + "');"
                   if selector else "var el = document.body;")

        # 跳过标签列表
        skip_tags = "" if show_hidden else (
            "var SKIP = {'script':1,'style':1,'link':1,'meta':1,'noscript':1,"
            "'template':1,'#comment':1};")

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
            if (showT && tag !== 'script' && tag !== 'style') {
                var t = (el.textContent || '').trim().substring(0, txtLim);
                if (t) node.text = t;
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
        js = (find_el + skip_tags + "return JSON.stringify(" +
              js.replace('MAXD', str(max_depth))
                .replace('MAXC', str(max_children))
                .replace('SHOWT', 'true' if text else 'false')
                .replace('TXTLIM', str(text_limit)) + ")")
        res = target.run_js(js)

        tree_dict = json.loads(res) if isinstance(res, str) else res
        result = {"ok": True, "save_format": save_format}

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
                    s = str(obj)
                    return f"'{s}'" if (" " in s or s == "") else s
            result["tree"] = _yaml(tree_dict)
        else:
            result["tree"] = tree_dict

        if save_path:
            os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(result["tree"] if save_format == "yml"
                        else json.dumps(tree_dict, ensure_ascii=False, indent=2))
            result["saved_to"] = os.path.abspath(save_path)

        return result
    except Exception as e:
        return {"ok": False, "reason": str(e)}

# ==================== 通用 DOM 原语 ====================

@mcp.tool()
@synchronized
def scan_page_elements(include_iframe: bool = True, max_items: int = 200) -> dict:
    """扫描页面所有可见交互控件(button/a/input/role=*/canvas)，递归穿透同源 iframe，
    按 frame 分组返回，含中心坐标。进入模块后第一件事。
    max_items 限制返回元素数（超出截断并标 _truncated），避免吃尽上下文。"""
    tab = browser_session.get_tab()
    script = browser_session.load_js("element-scan.js") + "\nreturn JSON.stringify(scanInteractiveControls());"
    res = tab.run_js(script)
    data = json.loads(res) if isinstance(res, str) else res
    if isinstance(data, dict):
        els = data.get("elements", [])
        if len(els) > max_items:
            data["elements"] = els[:max_items]
            data["_truncated"] = True
            data["returned"] = max_items
    return data


@mcp.tool()
@synchronized
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
@synchronized
def click(locator: str, in_frame: bool = True, by_js: bool = False, timeout: float = 5) -> dict:
    """点击元素。locator 为 DrissionPage 定位符(#id/.cls/@attr=v/text:文/css:选择器)。
    in_frame 优先在活动 iframe 内查找。by_js=True 用 JS 点击(绕过遮挡)。timeout 为查找超时秒数。
    text: 定位含空格文本（如「新 增」）时，DP 匹配失败会自动降级为 JS textContent 宽松匹配。"""
    ele = browser_session.find(locator, in_frame=in_frame, timeout=timeout)
    if not ele and locator.startswith("text:") and in_frame and " " in locator[5:]:
        # text: 定位含空格文本（如「新 增」）时 DP 可能匹配不到，降级 JS 宽松查找
        safe_text = locator[5:].replace("\\", "\\\\").replace("'", "\\'")
        target = browser_session.get_active_frame() or browser_session.get_tab()
        res = target.run_js(
            "var els=document.querySelectorAll('*');"
            "for(var i=0;i<els.length;i++){"
            "var t=els[i].textContent.trim().replace(/\\s+/g,'');"
            "if(t==='"+safe_text.replace(" ", "")+"'){els[i].click();return JSON.stringify({ok:true});}"
            "}"
            "return JSON.stringify({ok:false});"
        )
        if isinstance(res, str):
            res = json.loads(res)
        if res and res.get("ok"):
            return {"ok": True, "locator": locator, "fallback": "js-text"}
        return {"ok": False, "reason": "元素未找到: %s（等待 %.1fs，JS 降级也失败）" % (locator, timeout)}
    if not ele:
        return {"ok": False, "reason": "元素未找到: %s（等待 %.1fs）" % (locator, timeout)}
    ele.click(by_js=by_js)
    return {"ok": True, "locator": locator}


@mcp.tool()
@synchronized
def click_xy(x: float, y: float, hover_first: bool = True, duration: float = 0.3) -> dict:
    """按顶层视口坐标点击(用于 canvas)。hover_first 先移动到目标(hover)再点击——VTable 排序图标需要。"""
    tab = browser_session.get_tab()
    tab.actions.move_to((x, y), duration=duration if hover_first else 0).click()
    return {"ok": True, "x": x, "y": y}

@mcp.tool()
@synchronized
def select_date_range(field_name: str, start_date: str, end_date: str) -> dict:
    """选择筛选区中 Ant Design RangePicker 日期范围。
    
    支持字段名匹配（如「领料时间」「发料时间」「创建时间」），自动导航
    到目标年/月，通过 title 属性精确点击开始/结束日期。
    
    Args:
        field_name: 筛选字段名称，如「领料时间」「发料时间」「创建时间」
        start_date: 开始日期，格式 "yyyy/MM/dd"，如 "2026/05/01"
        end_date: 结束日期，格式 "yyyy/MM/dd"，如 "2026/05/31"
    
    Returns:
        {ok, startValue, endValue, reason}
    """
    return filter_area.select_date_range(field_name, start_date, end_date)


@mcp.tool()
@synchronized
def input(locator: str, text: str, in_frame: bool = True, clear: bool = True, timeout: float = 5) -> dict:
    """向输入框填入文本。clear=True 先清空。timeout 为查找超时秒数。"""
    ele = browser_session.find(locator, in_frame=in_frame, timeout=timeout)
    if not ele:
        return {"ok": False, "reason": "元素未找到: %s（等待 %.1fs）" % (locator, timeout)}
    if clear:
        try:
            ele.clear()
        except Exception as e:
            logger.warning("清空输入框失败: %s", e)
    ele.input(text)
    return {"ok": True, "locator": locator}


@mcp.tool()
@synchronized
def insert_text(text: str) -> dict:
    """向当前焦点元素插入文本(动作链)。"""
    tab = browser_session.get_tab()
    tab.actions.input(text)
    return {"ok": True}


@mcp.tool()
@synchronized
def hover(locator: str = None, x: float = None, y: float = None, in_frame: bool = True, duration: float = 0.3, timeout: float = 5) -> dict:
    """鼠标悬停。给 locator 悬停元素；或给 x,y 悬停坐标。timeout 为查找超时秒数。"""
    tab = browser_session.get_tab()
    if locator:
        ele = browser_session.find(locator, in_frame=in_frame, timeout=timeout, wait_clickable=False)
        if not ele:
            return {"ok": False, "reason": "元素未找到: %s（等待 %.1fs）" % (locator, timeout)}
        tab.actions.move_to(ele)
    else:
        tab.actions.move_to((x, y), duration=duration)
    return {"ok": True}


@mcp.tool()
@synchronized
def screenshot(path: str = None, locator: str = None, in_frame: bool = True, timeout: float = 5) -> dict:
    """截图。locator 给定则截元素，否则截全页。path 为空则存 ~/.drission-ui-shots/shot_<时间戳>.png。timeout 为查找超时秒数。"""
    tab = browser_session.get_tab()
    if not path:
        os.makedirs(config.SHOT_DIR, exist_ok=True)
        path = os.path.join(config.SHOT_DIR, "shot_%d.png" % int(time.time()))
    if locator:
        ele = browser_session.find(locator, in_frame=in_frame, timeout=timeout, wait_clickable=False)
        if not ele:
            return {"ok": False, "reason": "元素未找到: %s（等待 %.1fs）" % (locator, timeout)}
        ele.get_screenshot(path=path)
    else:
        tab.get_screenshot(path=path)
    return {"ok": True, "path": path}


@mcp.tool()
@synchronized
def run_js(script: str, in_frame: bool = True, max_chars: int = 4000) -> dict:
    """逃生舱：执行任意 JS。in_frame=True 在活动 iframe 内执行。script 内可用 return 返回值。
    返回值需为 JSON 可序列化(建议 return JSON.stringify(...))。
    max_chars 限制返回文本长度（超出截断并标 _truncated），避免吃尽上下文。"""
    target = browser_session.get_active_frame() if in_frame else None
    if target is None:
        target = browser_session.get_tab()
    res = target.run_js(script)
    try:
        json.dumps(res)
    except (TypeError, ValueError):
        res = str(res)
    truncated = False
    if isinstance(res, str) and len(res) > max_chars:
        res = res[:max_chars]
        truncated = True
    return {"ok": True, "result": res, "_truncated": True} if truncated else {"ok": True, "result": res}


# ==================== VTable（canvas 表格）====================

@mcp.tool()
@synchronized
def mount_vtable() -> dict:
    """挂载 VTable 实例到 iframe 的 window._vtable（遍历 React fiber）。所有 VTable 工具的前置。"""
    return vtable.mount_vtable()


@mcp.tool()
@synchronized
def scan_vtable_columns(max_col: int = 50) -> dict:
    """扫描 VTable 列定义：标题/body 行为(bodyBehavior/bodyType/bodyEditable)/表头图标(含顶层视口坐标 viewportX/Y)。
    图标坐标可直接用于 click_cell/click_xy。"""
    return vtable.scan_vtable_columns(max_col)


@mcp.tool()
@synchronized
def get_column_values(title: str, raw: bool = False) -> dict:
    """按中文列标题取该列所有单元格值。raw=False 视觉文本(与界面一致)；raw=True 原始字段值(如数字码)。筛选断言用。"""
    return vtable.get_column_values(title, raw)


@mcp.tool()
@synchronized
def get_cell_rect(col: int, row: int, scroll: bool = True) -> dict:
    """取单元格中心顶层视口坐标(先 scrollToCell 确保可见)。返回 {viewportX, viewportY}。
    
    Args:
        col: 列索引
        row: 行索引
        scroll: True（默认）先滚动到该单元格再取坐标；
                False 不滚动，取当前位置坐标（可能为负值或超出视口，用于判断是否需要 scroll）。
    """
    return vtable.get_cell_rect(col, row, scroll=scroll)


@mcp.tool()
@synchronized
def scroll_to_cell(col: int, row: int) -> dict:
    """滚动 VTable 使目标单元格进入视口。"""
    return vtable.scroll_to_cell(col, row)


@synchronized
@mcp.tool()
def click_cell(col: int, row: int, icon_name: str = None, hover_first: bool = True, duration: float = 0.3, double_click: bool = False) -> dict:
    """点击 VTable 单元格或其图标。icon_name(如 'sort')给定时点该图标(先 hover 再 click)；否则点单元格中心。

    Args:
        col: 列索引
        row: 行索引
        icon_name: 图标名称，如 'sort'、'filter-icon'
        hover_first: 是否先 hover（排序/筛选图标需要）
        duration: hover 动画时长
        double_click: 是否双击（用于 bodyBehavior='链接/按钮' 的单元格）
    """
    return vtable.click_cell(col, row, icon_name, hover_first, duration, double_click)

@mcp.tool()
@synchronized
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

@mcp.tool()
@synchronized
def detect_modal(timeout: float = 0) -> dict:
    """点击后检测弹窗(三级优先级)：iframe 业务弹窗/消息 → top 层系统确认 → none。每次点击后必调。

    timeout>0 时轮询直到弹窗出现或超时，找到就立即返回（智能等待），不用盲等。
    """
    return modal.detect_modal(timeout=timeout)

@mcp.tool()
@synchronized
def close_modal() -> dict:
    """关闭当前残留的弹窗/通知/消息，避免积累在 DOM 中干扰后续交互。
    每次 detect_modal() 返回非 none 后调用此函数清理。
    通知类 → 点×关闭；业务确认弹窗 → 点取消或×。
    返回 {ok, closed:[...], errors:[...]}，可判断清理是否成功。
    """
    return modal.close_modal()


@mcp.tool()
@synchronized
def listen_start(targets, method: str = None) -> dict:
    """启动网络监听。targets 为 URL 特征：单个字符串、逗号分隔的多个特征、或列表。method 可选 'POST'/'GET'。"""
    tab = browser_session.get_tab()
    # 统一为列表：逗号分隔 → 拆分去空，避免 "a,b" 被当成单个特征串
    if isinstance(targets, str):
        targets = [t.strip() for t in targets.split(",") if t.strip()]
    elif targets is None:
        targets = ""
    if method:
        tab.listen.set_method(method)
    tab.listen.start(targets)
    return {"ok": True, "targets": targets}


@mcp.tool()
@synchronized
def listen_wait(count: int = 1, timeout: float = 10) -> dict:
    """等待监听的数据包。返回 {url, method, status, body}(body 自动解析 JSON)。count>1 返回 packets 列表。"""
    tab = browser_session.get_tab()
    pkt = tab.listen.wait(count=count, timeout=timeout)
    if not pkt:
        return {"ok": False, "reason": "timeout", "hint": "确认 listen_start 的 targets 是否正确，或增大 timeout"}

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
@synchronized
def listen_stop() -> dict:
    """停止网络监听（与 listen_start 配对，避免监听器泄漏）。"""
    tab = browser_session.get_tab()
    try:
        tab.listen.stop()
    except Exception as e:
        return {"ok": False, "reason": "停止监听失败: %s" % e}
    return {"ok": True}


@mcp.tool()
@synchronized
def mouse_trail(on: bool = True) -> dict:
    """开启/关闭鼠标轨迹可视化(红色圆点跟踪 mousemove/click)。调试 canvas 点击落点用。"""
    return modal.mouse_trail(on)


# ==================== 4.2 新增工具 ====================

@mcp.tool()
@synchronized
def download_by_browser(url: str, save_path: str = None, rename: str = None,
                        suffix: str = None, timeout: float = 30,
                        file_exists: str = "rename") -> dict:
    """浏览器触发下载(4.2 新增)。用于 blob / 难以直接 fetch 的 URL。
    file_exists: 'rename'/'overwrite'/'skip' 或 'r'/'o'/'s'。
    返回 {ok, path, file_size, url}。
    """
    tab = browser_session.get_tab()
    kwargs = {"url": url, "timeout": timeout, "file_exists": file_exists}
    if save_path:
        kwargs["save_path"] = save_path
    if rename:
        kwargs["rename"] = rename
    if suffix:
        kwargs["suffix"] = suffix
    try:
        mission = tab.download.by_browser(**kwargs)
        mission.wait()
        return {"ok": True, "path": mission.path, "file_size": mission.file_size, "url": url}
    except Exception as e:
        return {"ok": False, "reason": "下载失败: %s" % e}


@mcp.tool()
@synchronized
def listen_ws_start(targets: str = None) -> dict:
    """启动 WebSocket 监听(4.2 新增)。targets 可选 URL 特征过滤；不传则监听所有 WS 帧。"""
    tab = browser_session.get_tab()
    kwargs = {"ws_only": True}
    if targets:
        kwargs["targets"] = targets
    tab.listen.start(**kwargs)
    return {"ok": True, "targets": targets or "(all)"}


@mcp.tool()
@synchronized
def listen_ws_wait(count: int = 1, timeout: float = 10) -> dict:
    """等待 WebSocket 数据包。返回 {ok, packets:[{is_sent, payload, timestamp}]}。"""
    tab = browser_session.get_tab()
    pkt = tab.listen.wait(count=count, timeout=timeout)
    if not pkt:
        return {"ok": False, "reason": "timeout", "hint": "确认 listen_ws_start 的 targets 是否正确，或增大 timeout"}

    def conv(p):
        return {
            "is_sent": getattr(p, "is_sent", None),
            "payload": getattr(p, "payload", None),
            "timestamp": getattr(p, "timestamp", None),
        }

    if isinstance(pkt, list):
        return {"ok": True, "packets": [conv(p) for p in pkt]}
    return {"ok": True, **conv(pkt)}


@mcp.tool()
@synchronized
def new_context(proxy: str = None) -> dict:
    """创建独立浏览器上下文(4.2 BrowserContext)，隔离 cookie/代理。用于多账号或干净测试环境。
    proxy 格式: 'http://user:password@ip:port'。
    返回稳定 context_id（可传给 switch_context 切换操作）与该上下文 tab 列表。"""
    browser = browser_session.get_browser()
    try:
        ctx = browser.new_context(proxy=proxy) if proxy else browser.new_context()
    except Exception as e:
        return {"ok": False, "reason": "创建上下文失败: %s" % e}
    cid = browser_session.register_context(ctx)
    tids = list(getattr(ctx, "tab_ids", []) or [])
    return {"ok": True, "context_id": cid, "tab_ids": tids,
            "hint": "调用 switch_context(%d) 切换到该上下文操作" % cid if tids
            else "上下文暂无 tab，可能需先在该上下文 new_tab"}


@mcp.tool()
@synchronized
def switch_context(context_id: int) -> dict:
    """切换活动 tab 到指定 context 的首个 tab（配合 new_context）。返回新 tab url。"""
    tab = browser_session.switch_context(context_id)
    if tab is None:
        return {"ok": False, "reason": "context 不存在或无可用 tab", "context_id": context_id}
    return {"ok": True, "url": getattr(tab, "url", "") or "", "context_id": context_id}


@mcp.tool()
@synchronized
def list_contexts() -> dict:
    """列出所有已注册的浏览器上下文（配合 new_context）。"""
    return {"ok": True, "contexts": browser_session.list_contexts()}


@mcp.tool()
@synchronized
def set_permission(perm: str, allow: bool = True) -> dict:
    """设置浏览器权限(4.2 新增)。perm: 'camera'/'geolocation'/'notifications'/'midi' 等。
    allow=False 撤销权限；若该权限仅支持开启（无 deny 形式），返回 ok=False 并说明。"""
    browser = browser_session.get_browser()
    try:
        perm_fn = getattr(browser.set.perm, perm)
    except AttributeError:
        return {"ok": False, "reason": "不支持的权限: %s" % perm}
    try:
        if allow:
            perm_fn()
        else:
            # deny 路径：尝试 (allow=False) 形参；不支持则明确告知而非静默返回 ok
            try:
                perm_fn(allow=False)
            except TypeError:
                return {"ok": False, "reason": "deny 不支持（权限 %s 仅可开启）" % perm,
                        "perm": perm, "allow": False}
    except Exception as e:
        return {"ok": False, "reason": "设置权限失败: %s" % e}
    return {"ok": True, "perm": perm, "allow": allow}


if __name__ == "__main__":
    mcp.run()
