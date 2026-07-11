"""VTable 工具：把脆弱的页面内 JS 包成结构化 Python 工具。

所有 VTable 操作都在活动 iframe(frame) 上下文执行（VTable 实例挂在 frame 的 window）。
坐标换算全部在 JS 端完成：JS 通过 window.frameElement.getBoundingClientRect()
一次算到顶层视口坐标 viewportX/viewportY，Python 不再叠加 iframe 偏移。

JS 参数化：用 json.dumps 把 Python 参数列表序列化为 JS 数组字面量，
通过 fn.apply(null, [...]) 调用，避免 %d / %s 字符串拼接的类型与转义陷阱。
"""
import json

import browser_session


_VTABLE_RETRY_REASON = "VTable 实例失效且自动重挂载失败，请重新进入模块或刷新 active iframe 后重试"
_VTABLE_LOADING_LOCATOR = (
    "xpath://div[@class='page-content']//div[contains(@class, 'vtable-loading')]"
)


def _frame():
    """取活动 frame，未就绪抛异常。"""
    fr = browser_session.get_active_frame()
    if fr is None:
        raise RuntimeError("未找到活动业务 iframe，请先 enter_module 进入模块")
    return fr


def _run(js_file: str, call: str):
    """在 frame 上下文注入脚本并执行 call 段（末尾 return ...），解析 JSON。"""
    fr = _frame()
    script = browser_session.load_js(js_file) + "\n" + call
    res = fr.run_js(script)
    if isinstance(res, str):
        return json.loads(res) if res else None
    return res


def _js_args(*args):
    """把 Python 参数列表序列化为 JS 数组字面量，供 fn.apply(null, [...]) 调用。"""
    return json.dumps(list(args), ensure_ascii=False)


def _ensure_vtable():
    """检查 window._vtable 是否仍有效（iframe 未导航走导致实例陈旧）。

    enter_module 切模块会让 iframe 重新加载，旧 _vtable 指向已卸载实例。
    本函数用轻量 JS 校验：实例存在、scenegraph 存在、.vtable 元素仍在 DOM。
    失效则自动重新 mount；仍失败返回 False（调用方应提示重新 mount_vtable）。
    """
    fr = _frame()
    check = (
        "var t=window._vtable;return JSON.stringify({valid:!!(t&&t.scenegraph&&"
        "(document.querySelector('.vtable')||document.querySelector('[class*=\"vtable\"]')))});"
    )
    res = fr.run_js(check)
    d = json.loads(res) if isinstance(res, str) else (res or {})
    if d.get("valid"):
        return True
    mv = mount_vtable()
    return bool(mv and mv.get("ok"))


def _wait_stable(reader, timeout=3):
    """Use DrissionPage's passive waiter before reading a VTable render state.

    VTable is canvas-based, so there is no semantic row element to wait on. The
    canvas geometry is the closest native synchronization target after
    ``scrollToCell``; `stop_moving()` performs DrissionPage-managed polling
    instead of a Python fixed-delay loop.
    """
    fr = _frame()
    try:
        canvas = fr.ele('c:canvas', timeout=min(float(timeout), 1.0))
        if canvas:
            canvas.wait.stop_moving(timeout=timeout, raise_err=False)
        else:
            fr.wait.doc_loaded(timeout=timeout, raise_err=False)
    except Exception:
        pass
    return reader()


def _wait_cell_center_stable(col, row, timeout=3):
    """scrollToCell 后轮询单元格中心顶层视口坐标至稳定（场景图停止动画），返回稳定的坐标 dict 或 None。"""
    def reader():
        ctr = _run("vtable-column-values.js",
                   "return JSON.stringify(getCellCenterViewport.apply(null, %s));" % _js_args(col, row))
        if not ctr:
            return None
        return (round(ctr.get("viewportX", 0), 1), round(ctr.get("viewportY", 0), 1)), ctr
    stable = _wait_stable(reader, timeout)
    return stable[1] if stable else None


def wait_for_render_stable(timeout: float = 3) -> dict:
    """Passively wait for the VTable canvas to settle through DrissionPage."""
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    _wait_stable(lambda: True, timeout=max(float(timeout or 0), 0.1))
    return {"ok": True}


def is_loading_complete(iframe=None) -> bool:
    """Return whether the page VTable loading mask has been removed.

    The waiters are DrissionPage-native and passive: first allow the loading
    element to appear, then wait for it to be deleted. When a page does not
    render a loading mask, ``ele_deleted`` succeeds immediately.
    """
    frame = iframe or _frame()
    try:
        frame.wait.eles_loaded(_VTABLE_LOADING_LOCATOR, timeout=3, raise_err=False)
        return bool(frame.wait.ele_deleted(_VTABLE_LOADING_LOCATOR, timeout=20, raise_err=False))
    except Exception:
        return False


def mount_vtable():
    """挂载 VTable 实例到 frame 的 window._vtable。返回 {ok, levels|reason}。"""
    res = _run("vtable-scanner.js", "return JSON.stringify(mountVTable());")
    if not isinstance(res, dict):
        return {"ok": False, "reason": "mountVTable 返回非 dict: %r" % (res,)}
    if "ok" not in res:
        res["ok"] = True
    return res


def scan_vtable_columns(max_col: int = 50):
    """扫描列定义 + 表头图标（含顶层视口坐标 viewportX/viewportY）。

    图标坐标已在 JS 端叠加 window.frameElement 偏移，可直接用于 click_xy / actions.move_to。
    """
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    cols = _run("vtable-scanner.js",
                "return JSON.stringify(scanColumns.apply(null, %s));" % _js_args(max_col))
    if not cols:
        return {"ok": False, "reason": "scanColumns 返回空，可能未挂载 VTable 或无列"}
    return {"ok": True, "columns": cols}


def get_column_values(title: str, raw: bool = False):
    """按中文列标题取该列所有单元格值（筛选断言用）。raw=True 返回原始字段值。"""
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    call = ("return JSON.stringify(getColumnValuesByTitle.apply(null, "
            "[window._vtable].concat(%s)));" % _js_args(title, raw))
    res = _run("vtable-column-values.js", call)
    if res is None:
        return {"ok": False, "reason": "getColumnValuesByTitle 返回空，列标题不存在或未挂载 VTable"}
    return {"ok": True, "values": res, "title": title, "raw": raw}


def get_cell_rect(col: int, row: int, scroll: bool = True):
    """取单元格中心【顶层视口坐标】。
    
    Args:
        col: 列索引
        row: 行索引
        scroll: 是否先滚动到该单元格再取坐标。
            True（默认）— 先 scrollToCell 确保在视口内，返回可见区域的坐标。
            False — 不滚动，返回当前渲染位置的坐标（可能在视口外，用于判断是否需要 scroll）。
    """
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    if scroll:
        _run("vtable-column-values.js",
             "scrollToCell.apply(null, %s);" % _js_args(col, row))
        # 智能等待：轮询单元格中心坐标至稳定（场景图停止动画），避免取到动画中途坐标导致落点偏
        res = _wait_cell_center_stable(col, row)
    else:
        res = _run("vtable-column-values.js",
                   "return JSON.stringify(getCellCenterViewport.apply(null, %s));" % _js_args(col, row))
    if not res:
        return {"ok": False, "reason": "无法获取单元格坐标，可能 col/row 越界或未挂载 VTable"}
    return {
        "ok": True,
        "viewportX": round(res.get("viewportX", 0), 1),
        "viewportY": round(res.get("viewportY", 0), 1),
        "col": col, "row": row,
        "scrolled": scroll,
    }


def scroll_to_cell(col: int, row: int):
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    res = _run("vtable-column-values.js",
               "return JSON.stringify(scrollToCell.apply(null, %s));" % _js_args(col, row))
    if res is None:
        return {"ok": False, "reason": "scrollToCell 返回空，可能未挂载 VTable 或 col/row 越界"}
    if isinstance(res, dict) and "ok" in res:
        return res
    return {"ok": True, "result": res}


def _normalize_detail(detail: str):
    return "full" if (detail or "").strip().lower() == "full" else "summary"


def get_cell_render_info(col: int, row: int, detail: str = "summary", scroll: bool = True):
    """读取 VTable 单元格渲染摘要：文本、字体色、标签底色、单元格背景/边框色。"""
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    if scroll:
        scrolled = scroll_to_cell(col, row)
        if not scrolled.get("ok"):
            return scrolled
        _wait_cell_center_stable(col, row)
    res = _run(
        "vtable-column-values.js",
        "return JSON.stringify(getCellRenderInfo.apply(null, %s));" % _js_args(col, row, _normalize_detail(detail)),
    )
    if not isinstance(res, dict):
        return {"ok": False, "reason": "getCellRenderInfo 返回空或非 dict"}
    res.setdefault("ok", True)
    res.setdefault("col", col)
    res.setdefault("row", row)
    res["scrolled"] = scroll
    return res


def get_cell_icons(col: int, row: int, icon_name: str = None,
                   detail: str = "summary", scroll: bool = True):
    """读取任意 VTable 单元格内的可能图标，返回顶层视口坐标。"""
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    if scroll:
        scrolled = scroll_to_cell(col, row)
        if not scrolled.get("ok"):
            return scrolled
        _wait_cell_center_stable(col, row)
    res = _run(
        "vtable-column-values.js",
        "return JSON.stringify(getCellIconsViewport.apply(null, %s));"
        % _js_args(col, row, icon_name, _normalize_detail(detail)),
    )
    if not isinstance(res, dict):
        return {"ok": False, "reason": "getCellIconsViewport 返回空或非 dict"}
    res.setdefault("ok", True)
    res.setdefault("col", col)
    res.setdefault("row", row)
    res["scrolled"] = scroll
    return res


def _normalize_pointer_action(action: str):
    raw = (action or "click").strip().lower().replace("-", "_")
    aliases = {
        "dblclick": "double_click",
        "doubleclick": "double_click",
        "double": "double_click",
        "move": "hover",
        "move_to": "hover",
        "mouseover": "hover",
        "mouse_over": "hover",
    }
    normalized = aliases.get(raw, raw)
    allowed = {"click", "double_click", "hover", "drag"}
    if normalized not in allowed:
        return None, "不支持的 VTable 动作: %s（支持: %s）" % (action, sorted(allowed))
    return normalized, None


def _normalize_action_target(target: str, icon_name: str = None):
    raw = (target or "cell").strip().lower().replace("_", "-")
    aliases = {
        "icon": "header-icon",
        "headericon": "header-icon",
        "header-icon": "header-icon",
        "cell-icon": "cell-icon",
        "body-icon": "cell-icon",
        "data-icon": "cell-icon",
        "header": "header",
        "cell": "cell",
    }
    normalized = aliases.get(raw)
    if icon_name and normalized == "cell":
        normalized = "header-icon"
    allowed = {"cell", "header", "header-icon", "cell-icon"}
    if normalized not in allowed:
        return None, "不支持的 VTable 目标: %s（支持: %s）" % (target, sorted(allowed))
    return normalized, None


def _match_icon(icons, icon_name: str = None, icon_index: int = None):
    if not icons:
        return None, "图标未找到"
    if icon_index is not None:
        try:
            idx = int(icon_index)
        except Exception:
            return None, "icon_index 必须是整数"
        if idx < 0 or idx >= len(icons):
            return None, "icon_index 越界: %s（可用 0-%s）" % (icon_index, len(icons) - 1)
        return icons[idx], None
    if icon_name:
        low = (icon_name or "").lower()
        match = next(
            (
                i for i in icons
                if low in " ".join([
                    str(i.get("name") or ""),
                    str(i.get("type") or ""),
                    str(i.get("symbolType") or ""),
                    str(i.get("func") or ""),
                ]).lower()
            ),
            None,
        )
        if match:
            return match, None
        return None, "图标未找到: %s（可用: %s）" % (
            icon_name, [i.get("name") or i.get("type") or i.get("symbolType") for i in icons])
    if len(icons) == 1:
        return icons[0], None
    return None, "单元格内有多个图标，请提供 icon_name 或 icon_index（可用: %s）" % (
        [i.get("name") or i.get("type") or i.get("symbolType") for i in icons])


def _find_header_icon_point(col: int, row: int, icon_name: str, scroll: bool = True):
    if not icon_name:
        return {"ok": False, "reason": "点击表头图标需要 icon_name"}
    if scroll:
        scrolled = scroll_to_cell(col, row)
        if not scrolled.get("ok"):
            return scrolled
        _wait_cell_center_stable(col, row)
    scan = _run(
        "vtable-scanner.js",
        ("var a=%s;var r=scanColumns(a[0]);"
         "return r?JSON.stringify(r.filter(function(c){return c.col===a[1]&&c.isHeader;})):null;")
        % _js_args(col + 1, col),
    )
    header_entry = scan[0] if isinstance(scan, list) and scan else None
    icons = (header_entry or {}).get("icons", [])
    match, reason = _match_icon(icons, icon_name=icon_name)
    if reason:
        return {"ok": False, "reason": reason}
    return {
        "ok": True,
        "viewportX": match["viewportX"],
        "viewportY": match["viewportY"],
        "icon": icon_name,
        "col": col,
        "row": row,
        "scrolled": scroll,
    }


def _find_cell_icon_point(col: int, row: int, icon_name: str = None,
                          icon_index: int = None, scroll: bool = True):
    scan = get_cell_icons(col, row, icon_name=icon_name, scroll=scroll)
    if not scan.get("ok"):
        return scan
    icons = scan.get("icons") or []
    match, reason = _match_icon(icons, icon_name=icon_name, icon_index=icon_index)
    if reason:
        data = {"ok": False, "reason": reason, "icons": icons, "col": col, "row": row}
        return data
    return {
        "ok": True,
        "viewportX": match["viewportX"],
        "viewportY": match["viewportY"],
        "icon": match.get("name") or match.get("type") or match.get("symbolType") or icon_name,
        "icon_index": match.get("index"),
        "icon_info": match,
        "col": col,
        "row": row,
        "scrolled": scan.get("scrolled", scroll),
    }


def _resolve_action_point(target: str, col: int, row: int, icon_name: str = None,
                          icon_index: int = None, scroll: bool = True):
    if target == "header-icon":
        return _find_header_icon_point(col, 0, icon_name, scroll=scroll)
    if target == "cell-icon":
        return _find_cell_icon_point(col, row, icon_name=icon_name, icon_index=icon_index, scroll=scroll)
    target_row = 0 if target == "header" else row
    return get_cell_rect(col, target_row, scroll=scroll)


def _resolve_drag_destination(vx, vy, drag_to):
    if not isinstance(drag_to, dict):
        return None, None, "drag 动作需要 drag_to，格式为 {'x','y'} 或 {'dx','dy'}"
    if "x" in drag_to or "y" in drag_to:
        x = drag_to.get("x")
        y = drag_to.get("y")
        return vx if x is None else x, vy if y is None else y, None
    if "dx" in drag_to or "dy" in drag_to:
        dx = drag_to.get("dx")
        dy = drag_to.get("dy")
        return vx + (0 if dx is None else dx), vy + (0 if dy is None else dy), None
    return None, None, "drag_to 需要包含 x/y 或 dx/dy"


def _perform_pointer_action(action: str, vx, vy, hover_first: bool = True,
                            duration: float = 0.3, drag_to: dict = None):
    tab = browser_session.get_tab()
    move_duration = duration if hover_first or action == "hover" else 0
    if action == "hover":
        tab.actions.move_to((vx, vy), duration=duration)
        return {"ok": True}
    if action == "click":
        tab.actions.move_to((vx, vy), duration=move_duration).click()
        return {"ok": True}
    if action == "double_click":
        tab.actions.move_to((vx, vy), duration=move_duration).click(times=2)
        return {"ok": True}
    if action == "drag":
        dx, dy, reason = _resolve_drag_destination(vx, vy, drag_to)
        if reason:
            return {"ok": False, "reason": reason}
        tab.actions.move_to((vx, vy), duration=move_duration)
        tab.actions.hold()
        tab.actions.move_to((dx, dy), duration=max(duration, 0.1))
        tab.actions.release()
        return {"ok": True, "destinationX": round(dx, 1), "destinationY": round(dy, 1)}
    return {"ok": False, "reason": "不支持的 VTable 动作: %s" % action}


def vtable_action(action: str = "click", col: int = None, row: int = 0,
                  target: str = "cell", icon_name: str = None,
                  icon_index: int = None,
                  hover_first: bool = True, duration: float = 0.3,
                  drag_to: dict = None, scroll: bool = True):
    """统一执行 VTable 指针动作。

    流程：确保实例有效 → 必要时 scrollToCell → 重新读取顶层视口坐标 → click/double_click/hover/drag。
    该函数是所有 VTable 坐标型交互的单一入口，调用侧不需要自行计算 iframe/canvas 偏移。
    """
    if col is None:
        return {"ok": False, "reason": "VTable 动作需要 col"}
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    action_name, reason = _normalize_pointer_action(action)
    if reason:
        return {"ok": False, "reason": reason}
    target_name, reason = _normalize_action_target(target, icon_name=icon_name)
    if reason:
        return {"ok": False, "reason": reason}

    point = _resolve_action_point(target_name, col, row, icon_name=icon_name,
                                  icon_index=icon_index, scroll=scroll)
    if not point.get("ok"):
        return point
    vx = point["viewportX"]
    vy = point["viewportY"]
    performed = _perform_pointer_action(action_name, vx, vy, hover_first=hover_first,
                                        duration=duration, drag_to=drag_to)
    if not performed.get("ok"):
        return performed

    result = {
        "ok": True,
        "action": action_name,
        "target": target_name,
        "viewportX": round(vx, 1),
        "viewportY": round(vy, 1),
        "icon": point.get("icon", icon_name),
        "icon_index": point.get("icon_index", icon_index),
        "icon_info": point.get("icon_info"),
        "col": col,
        "row": row,
        "scrolled": point.get("scrolled", scroll),
        "coordinate_space": "top_viewport",
    }
    result.update({k: v for k, v in performed.items() if k != "ok"})
    return result


def click_cell(col: int, row: int, icon_name: str = None, hover_first: bool = True, duration: float = 0.3, double_click: bool = False):
    """点击单元格或其图标。icon_name 给定时匹配该名称图标（如 'sort'），否则点单元格中心。

    流程：scrollToCell → 取坐标 → actions.move_to(hover) → click。

    Args:
        col: 列索引
        row: 行索引
        icon_name: 图标名称（如 'sort'、'filter-icon'），不传则点单元格中心
        hover_first: 是否先 hover 再点击（排序/筛选图标需要 hover 才出现）
        duration: hover 动画时长
        double_click: 是否双击（用于 bodyBehavior='链接/按钮' 的单元格，单击无效）
    """
    return vtable_action(
        action="double_click" if double_click else "click",
        col=col,
        row=row,
        target="header-icon" if icon_name else "cell",
        icon_name=icon_name,
        hover_first=hover_first,
        duration=duration,
    )


def hover_cell(col: int, row: int, icon_name: str = None, duration: float = 0.3):
    """悬停 VTable 单元格或表头图标，复用统一坐标动作管线。"""
    return vtable_action(
        action="hover",
        col=col,
        row=row,
        target="header-icon" if icon_name else "cell",
        icon_name=icon_name,
        duration=duration,
    )


def resize_column(col: int, width: int):
    """拖拽调整 VTable 列宽（DrissionPage actions 模拟鼠标拖拽）。
    
    判断流程：
      1. 取列头右边框的场景图坐标（JS 已包含 iframe 偏移，直接是视口坐标）
      2. 若超出当前 iframe 视口 → scrollToCell 滚动 → 重新取坐标
      3. DrissionPage actions.move_to → hold → move_to → release
    
    Args:
        col: 列索引（从 0 开始，含复选框列）
        width: 目标宽度（像素）
    """
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}

    def _get_bounds():
        """读取列头右边框的视口坐标（JS 内已加 iframe 偏移）。"""
        js = (
            "var t=window._vtable;"
            "if(!t||!t.scenegraph)return JSON.stringify({error:'no vtable'});"
            "var cg=t.scenegraph.getCell(%d,0);"
            "if(!cg)return JSON.stringify({error:'no cell'});"
            "var b=cg.globalAABBBounds;"
            "if(!b)return JSON.stringify({error:'no bounds'});"
            "var ifr=window.frameElement?window.frameElement.getBoundingClientRect():{left:0,top:0};"
            "var vtEl=document.querySelector('.vtable')||document.querySelector('[class*=\"vtable\"]');"
            "var vr=vtEl?vtEl.getBoundingClientRect():{left:0,top:0};"
            "var rightEdge=Math.round((ifr.left+vr.left+b.x2)*10)/10;"
            "return JSON.stringify({"
            "  rightEdge:rightEdge,"
            "  centerY:Math.round((ifr.top+vr.top+(b.y1+b.y2)/2)*10)/10,"
            "  oldWidth:Math.round(b.x2-b.x1),"
            "  viewportW:window.innerWidth});"
        ) % col
        return _run("vtable-column-values.js", js)
    
    # 1. 读取列头右边框坐标
    info = _get_bounds()
    if not info or not isinstance(info, dict) or "rightEdge" not in info:
        return {"ok": False, "reason": "无法获取列宽信息"}
    
    old_width = info["oldWidth"]
    delta = width - old_width
    if delta == 0:
        return {"ok": True, "reason": "列宽已是目标值", "width": old_width}
    
    # 2. 检查列是否在视口内（超出右侧或滑出左侧都需要滚动）
    viewport_w = info["viewportW"]
    right_edge = info["rightEdge"]
    if right_edge > viewport_w or right_edge < 0:
        _run("vtable-column-values.js",
             "scrollToCell.apply(null, %s);" % _js_args(col, 0))
        # 智能等待：轮询列头右边框坐标至稳定（场景图停止动画），复用 _wait_stable
        def _edge_reader():
            b = _get_bounds()
            if not b or not isinstance(b, dict) or "rightEdge" not in b:
                return None
            return round(b["rightEdge"], 1), b
        stable = _wait_stable(_edge_reader, timeout=3)
        if not stable:
            return {"ok": False, "reason": "滚动后仍无法获取列宽信息"}
        info = stable[1]
        old_width = info["oldWidth"]
        delta = width - old_width
        if delta == 0:
            return {"ok": True, "reason": "滚动后列宽已是目标值", "width": old_width}
    
    # 3. 视口坐标（JS 已将帧内坐标转为顶层视口坐标）
    start_x = round(info["rightEdge"])
    center_y = round(info["centerY"])
    
    # 4. DrissionPage 动作链拖拽
    tab = browser_session.get_tab()
    tab.actions.move_to((start_x - 100, center_y), duration=0.1)
    tab.actions.move_to((start_x - 3, center_y), duration=0.15)
    tab.actions.hold()
    tab.actions.move_to((start_x - 3 + delta, center_y), duration=0.5)
    tab.actions.release()
    
    return {"ok": True, "col": col, "old_width": old_width, "new_width": width, "delta": delta}
