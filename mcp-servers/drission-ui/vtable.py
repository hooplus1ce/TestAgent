"""VTable 工具：把脆弱的页面内 JS 包成结构化 Python 工具。

所有 VTable 操作都在活动 iframe(frame) 上下文执行（VTable 实例挂在 frame 的 window）。
坐标换算：JS 产出帧内坐标 frameX/frameY → Python 叠加 frame 在顶层视口的偏移 →
得到可供 tab.actions.move_to((x,y)) 点击的顶层视口坐标。

JS 参数化：用 json.dumps 把 Python 参数列表序列化为 JS 数组字面量，
通过 fn.apply(null, [...]) 调用，避免 %d / %s 字符串拼接的类型与转义陷阱。
"""
import json

import browser_session


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
    """轮询 reader() 返回值，连续两次相等即认为稳定，返回稳定值；超时返回最后一次（可能仍在变化，但优于立即取）。

    reader 返回 None 表示尚未就绪，继续轮询不中止。用于 scrollToCell 后等待场景图动画停止。
    """
    import time as _time
    prev, last, deadline = None, None, _time.time() + timeout
    while _time.time() < deadline:
        cur = reader()
        if cur is not None:
            if prev is not None and cur == prev:
                return cur
            prev = cur
            last = cur
        _time.sleep(0.05)
    return last


def _wait_cell_center_stable(col, row, timeout=3):
    """scrollToCell 后轮询单元格中心帧内坐标至稳定（场景图停止动画），返回稳定的坐标 dict 或 None。"""
    def reader():
        ctr = _run("vtable-column-values.js",
                   "return JSON.stringify(getCellCenterFrame.apply(null, %s));" % _js_args(col, row))
        if not ctr:
            return None
        return (round(ctr.get("frameX", 0), 1), round(ctr.get("frameY", 0), 1)), ctr
    stable = _wait_stable(reader, timeout)
    return stable[1] if stable else None


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

    图标坐标已叠加 frame 偏移，可直接用于 click_xy / actions.move_to。
    """
    if not _ensure_vtable():
        return {"ok": False, "reason": "VTable 实例失效且自动重挂载失败，请显式调用 mount_vtable"}
    fr = _frame()
    ox, oy = browser_session.frame_offset()
    cols = _run("vtable-scanner.js",
                "return JSON.stringify(scanColumns.apply(null, %s));" % _js_args(max_col))
    if not cols:
        return {"ok": False, "reason": "scanColumns 返回空，可能未挂载 VTable 或无列"}
    for c in cols:
        for ic in c.get("icons", []):
            fx = ic.pop("frameX", None)
            fy = ic.pop("frameY", None)
            if fx is not None and fy is not None:
                ic["viewportX"] = round(fx + ox, 1)
                ic["viewportY"] = round(fy + oy, 1)
    return {"ok": True, "columns": cols}


def get_column_values(title: str, raw: bool = False):
    """按中文列标题取该列所有单元格值（筛选断言用）。raw=True 返回原始字段值。"""
    if not _ensure_vtable():
        return {"ok": False, "reason": "VTable 实例失效且自动重挂载失败，请显式调用 mount_vtable"}
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
    fr = _frame()
    ox, oy = browser_session.frame_offset()
    if not _ensure_vtable():
        return {"ok": False, "reason": "VTable 实例失效且自动重挂载失败，请显式调用 mount_vtable"}
    if scroll:
        _run("vtable-column-values.js",
             "scrollToCell.apply(null, %s);" % _js_args(col, row))
        # 智能等待：轮询单元格中心坐标至稳定（场景图停止动画），避免取到动画中途坐标导致落点偏
        res = _wait_cell_center_stable(col, row)
    else:
        res = _run("vtable-column-values.js",
                   "return JSON.stringify(getCellCenterFrame.apply(null, %s));" % _js_args(col, row))
    if not res:
        return {"ok": False, "reason": "无法获取单元格坐标，可能 col/row 越界或未挂载 VTable"}
    return {
        "ok": True,
        "viewportX": round(res.get("frameX", 0) + ox, 1),
        "viewportY": round(res.get("frameY", 0) + oy, 1),
        "col": col, "row": row,
        "scrolled": scroll,
    }


def scroll_to_cell(col: int, row: int):
    if not _ensure_vtable():
        return {"ok": False, "reason": "VTable 实例失效且自动重挂载失败，请显式调用 mount_vtable"}
    res = _run("vtable-column-values.js",
               "return JSON.stringify(scrollToCell.apply(null, %s));" % _js_args(col, row))
    if res is None:
        return {"ok": False, "reason": "scrollToCell 返回空，可能未挂载 VTable 或 col/row 越界"}
    if isinstance(res, dict) and "ok" in res:
        return res
    return {"ok": True, "result": res}


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
    fr = _frame()
    ox, oy = browser_session.frame_offset()
    if not _ensure_vtable():
        return {"ok": False, "reason": "VTable 实例失效且自动重挂载失败，请显式调用 mount_vtable"}

    if icon_name:
        scan = _run(
            "vtable-scanner.js",
            ("var a=%s;var r=scanColumns(a[0]);"
             "return r?JSON.stringify(r.filter(function(c){return c.col===a[1]&&c.isHeader;})):null;")
            % _js_args(col + 1, col),
        )
        header_entry = scan[0] if isinstance(scan, list) and scan else None
        icons = (header_entry or {}).get("icons", [])
        low = (icon_name or "").lower()
        match = next((i for i in icons if low in (i.get("name") or "").lower()), None)
        if not match:
            return {"ok": False, "reason": "图标未找到: %s（可用: %s）" % (
                icon_name, [i.get("name") for i in icons])}
        vx = match["frameX"] + ox
        vy = match["frameY"] + oy
    else:
        _run("vtable-column-values.js",
             "scrollToCell.apply(null, %s);" % _js_args(col, row))
        # 智能等待：滚动后轮询单元格中心坐标至稳定，避免取到动画中途坐标导致落点偏
        ctr = _wait_cell_center_stable(col, row)
        if not ctr:
            return {"ok": False, "reason": "无法获取单元格坐标"}
        vx = ctr["frameX"] + ox
        vy = ctr["frameY"] + oy

    tab = browser_session.get_tab()
    if double_click:
        # 双击：模拟测试人员两次物理鼠标点击，.wait(0.15) 在动作链内保持 150ms 间隔（VTable 原生 dblclick 识别所需）
        tab.actions.move_to((vx, vy), duration=duration if hover_first else 0).click().wait(0.15).click()
    else:
        tab.actions.move_to((vx, vy), duration=duration if hover_first else 0).click()
    return {"ok": True, "viewportX": round(vx, 1), "viewportY": round(vy, 1),
            "icon": icon_name, "col": col, "row": row}

def resize_column(col: int, width: int):
    """拖拽调整 VTable 列宽（DrissionPage actions 模拟鼠标拖拽）。
    
    判断流程：
      1. 取列头右边框的场景图坐标（帧内坐标）
      2. 若超出当前 iframe 视口 → scrollToCell 滚动 → 重新取坐标
      3. DrissionPage actions.move_to → hold → move_to → release
    
    Args:
        col: 列索引（从 0 开始，含复选框列）
        width: 目标宽度（像素）
    """
    fr = _frame()
    ox, oy = browser_session.frame_offset()
    if not _ensure_vtable():
        return {"ok": False, "reason": "VTable 实例失效且自动重挂载失败，请显式调用 mount_vtable"}

    def _get_bounds():
        """读取列头右边框的帧内坐标（仅数据读取，非 UI 操作）。"""
        js = (
            "var t=window._vtable;"
            "if(!t||!t.scenegraph)return JSON.stringify({error:'no vtable'});"
            "var cg=t.scenegraph.getCell(%d,0);"
            "if(!cg)return JSON.stringify({error:'no cell'});"
            "var b=cg.globalAABBBounds;"
            "if(!b)return JSON.stringify({error:'no bounds'});"
            "var vtEl=document.querySelector('.vtable')||document.querySelector('[class*=\"vtable\"]');"
            "var vr=vtEl?vtEl.getBoundingClientRect():{left:0,top:0};"
            "var rightEdge=Math.round((vr.left+b.x2)*10)/10;"
            "return JSON.stringify({"
            "  rightEdge:rightEdge,"
            "  centerY:Math.round((vr.top+(b.y1+b.y2)/2)*10)/10,"
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
    
    # 3. 视口坐标 = 帧内坐标 + iframe 偏移
    start_x = round(info["rightEdge"] + ox)
    center_y = round(info["centerY"] + oy)
    
    # 4. DrissionPage 动作链拖拽
    tab = browser_session.get_tab()
    tab.actions.move_to((start_x - 100, center_y), duration=0.1)
    tab.actions.move_to((start_x - 3, center_y), duration=0.15)
    tab.actions.hold()
    tab.actions.move_to((start_x - 3 + delta, center_y), duration=0.5)
    tab.actions.release()
    
    return {"ok": True, "col": col, "old_width": old_width, "new_width": width, "delta": delta}
