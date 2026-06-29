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
    call = ("return JSON.stringify(getColumnValuesByTitle.apply(null, "
            "[window._vtable].concat(%s)));" % _js_args(title, raw))
    res = _run("vtable-column-values.js", call)
    if res is None:
        return {"ok": False, "reason": "getColumnValuesByTitle 返回空，列标题不存在或未挂载 VTable"}
    return {"ok": True, "values": res, "title": title, "raw": raw}


def get_cell_rect(col: int, row: int):
    """取单元格中心【顶层视口坐标】。先 scrollToCell 确保在视口内。"""
    fr = _frame()
    ox, oy = browser_session.frame_offset()
    _run("vtable-column-values.js",
         "scrollToCell.apply(null, %s);" % _js_args(col, row))
    res = _run("vtable-column-values.js",
               "return JSON.stringify(getCellCenterFrame.apply(null, %s));" % _js_args(col, row))
    if not res:
        return {"ok": False, "reason": "无法获取单元格坐标，可能 col/row 越界或未挂载 VTable"}
    return {
        "ok": True,
        "viewportX": round(res.get("frameX", 0) + ox, 1),
        "viewportY": round(res.get("frameY", 0) + oy, 1),
        "col": col, "row": row,
    }


def scroll_to_cell(col: int, row: int):
    res = _run("vtable-column-values.js",
               "return JSON.stringify(scrollToCell.apply(null, %s));" % _js_args(col, row))
    if res is None:
        return {"ok": False, "reason": "scrollToCell 返回空，可能未挂载 VTable 或 col/row 越界"}
    if isinstance(res, dict) and "ok" in res:
        return res
    return {"ok": True, "result": res}


def click_cell(col: int, row: int, icon_name: str = None, hover_first: bool = True, duration: float = 0.3):
    """点击单元格或其图标。icon_name 给定时匹配该名称图标（如 'sort'），否则点单元格中心。

    流程：scrollToCell → 取坐标 → actions.move_to(hover) → click。
    """
    fr = _frame()
    ox, oy = browser_session.frame_offset()

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
        ctr = _run("vtable-column-values.js",
                   "return JSON.stringify(getCellCenterFrame.apply(null, %s));" % _js_args(col, row))
        if not ctr:
            return {"ok": False, "reason": "无法获取单元格坐标"}
        vx = ctr["frameX"] + ox
        vy = ctr["frameY"] + oy

    tab = browser_session.get_tab()
    tab.actions.move_to((vx, vy), duration=duration if hover_first else 0).click()
    return {"ok": True, "viewportX": round(vx, 1), "viewportY": round(vy, 1),
            "icon": icon_name, "col": col, "row": row}
