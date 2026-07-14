"""VTable 工具：把脆弱的页面内 JS 包成结构化 Python 工具。

所有 VTable 操作都在活动 iframe(frame) 上下文执行（VTable 实例挂在 frame 的 window）。
坐标换算全部在 JS 端完成：JS 通过 window.frameElement.getBoundingClientRect()
一次算到顶层视口坐标 viewportX/viewportY，Python 不再叠加 iframe 偏移。

JS 参数化：用 json.dumps 把 Python 参数列表序列化为 JS 数组字面量，
通过 fn.apply(null, [...]) 调用，避免 %d / %s 字符串拼接的类型与转义陷阱。
"""

import json
import math
import time

from ..core import ui_contract
from . import browser_session

_VTABLE_RETRY_REASON = (
    "VTable 实例失效且自动重挂载失败，请重新进入模块或刷新 active iframe 后重试"
)
_VTABLE_LOADING_LOCATOR = ui_contract.VTABLE_LOADING


def _index(value, name):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, "%s 必须为非负整数" % name
    if isinstance(value, float) and not value.is_integer():
        return None, "%s 必须为非负整数" % name
    if parsed < 0:
        return None, "%s 必须为非负整数" % name
    return parsed, None


def _frame():
    """取活动 frame，优先使用只读探测，未就绪则抛出明确异常。"""
    tab = browser_session.get_tab()
    fr = browser_session.get_active_frame_ro(tab, timeout=0.5)
    if fr is None:
        fr = browser_session.get_active_frame(tab)
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
    """校验当前可见实例，失效或根元素隐藏时自动重新挂载。"""
    try:
        fr = _frame()
        check = (
            "var t=window._vtable,e=window._vtableElement,s=e&&window.getComputedStyle(e),"
            "r=e&&e.getBoundingClientRect(),l=r&&Math.max(0,r.left),rr=r&&Math.min(window.innerWidth,r.right),"
            "tp=r&&Math.max(0,r.top),b=r&&Math.min(window.innerHeight,r.bottom),"
            "h=(r&&rr>l&&b>tp)?document.elementFromPoint((l+rr)/2,(tp+b)/2):null;"
            "return JSON.stringify({valid:!!(t&&t.scenegraph&&e&&e.isConnected&&s&&"
            "s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0&&h&&"
            "(h===e||e.contains(h)))});"
        )
        res = fr.run_js(check)
        data = json.loads(res) if isinstance(res, str) else (res or {})
        if isinstance(data, dict) and data.get("valid"):
            return True
        mounted = mount_vtable()
        return bool(mounted and mounted.get("ok"))
    except Exception:
        return False


def _visible_vtable_canvas(frame, timeout: float = 1.0):
    """把当前缓存 VTable 根中的未遮挡 canvas 映射为 DrissionPage 元素。"""
    script = r"""
var root = window._vtableElement;
var canvases = [].slice.call(document.querySelectorAll('canvas'));
var index = -1;
if (root && root.isConnected) {
  for (var i = 0; i < canvases.length; i++) {
    var canvas = canvases[i];
    if (!(canvas === root || root.contains(canvas))) continue;
    var style = window.getComputedStyle(canvas), rect = canvas.getBoundingClientRect();
    if (style.display === 'none' || style.visibility === 'hidden' || rect.width <= 0 || rect.height <= 0) continue;
    var left = Math.max(0, rect.left), right = Math.min(window.innerWidth, rect.right);
    var top = Math.max(0, rect.top), bottom = Math.min(window.innerHeight, rect.bottom);
    if (right <= left || bottom <= top) continue;
    var hit = document.elementFromPoint((left + right) / 2, (top + bottom) / 2);
    if (hit && (hit === canvas || canvas.contains(hit))) { index = i; break; }
  }
}
return JSON.stringify(index);
"""
    try:
        raw = frame.run_js(script)
        index = json.loads(raw) if isinstance(raw, str) else raw
        canvases = frame.eles("c:canvas", timeout=timeout) or []
        if isinstance(index, int) and 0 <= index < len(canvases):
            return canvases[index]
    except Exception:
        pass
    return None


def _wait_stable(reader, timeout=3):
    """等待当前可见 VTable canvas 停止移动后再读取场景图。"""
    fr = _frame()
    try:
        canvas = _visible_vtable_canvas(fr, timeout=min(float(timeout), 1.0))
        if not canvas:
            return None
        stable = canvas.wait.stop_moving(timeout=timeout, raise_err=False)
        if stable is False:
            return None
        return reader()
    except Exception:
        return None


def _wait_cell_center_stable(col, row, timeout=3):
    """scrollToCell 后轮询单元格中心顶层视口坐标至稳定（场景图停止动画），返回稳定的坐标 dict 或 None。"""

    def reader():
        ctr = _run(
            "vtable-column-values.js",
            "return JSON.stringify(getCellCenterViewport.apply(null, %s));"
            % _js_args(col, row),
        )
        if not ctr:
            return None
        return (
            round(ctr.get("viewportX", 0), 1),
            round(ctr.get("viewportY", 0), 1),
        ), ctr

    stable = _wait_stable(reader, timeout)
    return stable[1] if stable else None


def wait_for_render_stable(timeout: float = 3) -> dict:
    """通过加载遮罩与 canvas 被动等待器确认当前 VTable 已稳定。"""
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    try:
        limit = min(max(float(timeout or 0), 0.1), 120.0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "timeout 必须为非负数"}
    if not is_loading_complete(timeout=limit):
        return {"ok": False, "reason": "VTable 在 %.1f 秒内未稳定完成" % limit}
    return {"ok": True}


def is_loading_complete(iframe=None, timeout: float = 20) -> bool:
    """在总超时内等待可见加载遮罩消失，并要求 VTable canvas 稳定。"""
    frame = iframe or _frame()
    try:
        limit = min(max(float(timeout or 0), 0.1), 120.0)
    except (TypeError, ValueError):
        return False
    deadline = time.perf_counter() + limit

    def remaining(cap: float) -> float:
        return max(0.05, min(cap, deadline - time.perf_counter()))

    try:
        loading_nodes = (
            frame.eles(_VTABLE_LOADING_LOCATOR, timeout=remaining(0.2)) or []
        )
        for loading in loading_nodes:
            if loading.states.is_displayed:
                if not loading.wait.hidden(timeout=remaining(limit), raise_err=False):
                    return False
        if time.perf_counter() >= deadline:
            return False
        canvas = _visible_vtable_canvas(frame, timeout=remaining(0.5))
        if not canvas:
            return False
        stable = canvas.wait.stop_moving(timeout=remaining(1.0), raise_err=False)
        return stable is not False and time.perf_counter() <= deadline
    except Exception:
        return False


def mount_vtable():
    """挂载 VTable 实例到 frame 的 window._vtable。"""
    try:
        res = _run("vtable-scanner.js", "return JSON.stringify(mountVTable());")
    except Exception as exc:
        return {"ok": False, "reason": "mountVTable failed: %s" % exc}
    if not isinstance(res, dict):
        return {"ok": False, "reason": "mountVTable 返回非 dict: %r" % (res,)}
    if "ok" not in res:
        res["ok"] = True
    return res


def scan_vtable_columns(max_col: int = 50):
    """扫描列定义与表头图标，限制返回规模。"""
    max_col, reason = _index(max_col, "max_col")
    if reason:
        return {"ok": False, "reason": reason}
    max_col = min(max_col, 1000)
    if max_col == 0:
        return {"ok": True, "columns": []}
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    try:
        cols = _run(
            "vtable-scanner.js",
            "return JSON.stringify(scanColumns.apply(null, %s));" % _js_args(max_col),
        )
    except Exception as exc:
        return {"ok": False, "reason": "scanColumns failed: %s" % exc}
    if not isinstance(cols, list) or not cols:
        return {
            "ok": False,
            "reason": "scanColumns 返回空，可能未挂载可见 VTable 或无列",
        }
    return {"ok": True, "columns": cols}


def get_column_values(title: str, raw: bool = False):
    """按唯一列标题读取整列；精确列优先，模糊命中不唯一时拒绝。"""
    title = str(title or "").strip()
    if not title:
        return {"ok": False, "reason": "列标题不能为空"}
    bulk = get_columns_values([title], raw=raw)
    if not bulk.get("ok"):
        return bulk
    return {
        "ok": True,
        "values": bulk["values"][title],
        "title": title,
        "raw": bool(raw),
        "header_rows": bulk.get("header_rows", 1),
    }


def get_columns_values(titles, raw: bool = False):
    """单次浏览器脚本调用批量读取多列，避免逐列重复注入。"""
    normalized = list(
        dict.fromkeys(str(title or "").strip() for title in (titles or []))
    )
    if not normalized or any(not title for title in normalized):
        return {"ok": False, "reason": "列标题列表不能为空"}
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    call = (
        "return JSON.stringify(getColumnsValuesByTitle.apply(null, "
        "[window._vtable].concat(%s)));" % _js_args(normalized, bool(raw))
    )
    try:
        result = _run("vtable-column-values.js", call)
    except Exception as exc:
        return {"ok": False, "reason": "getColumnsValuesByTitle failed: %s" % exc}
    if not isinstance(result, dict):
        return {"ok": False, "reason": "批量列值读取返回非对象"}
    missing = result.get("missing") or []
    try:
        header_rows = max(int(result.get("headerRows") or 1), 1)
    except (TypeError, ValueError):
        header_rows = 1
    if missing:
        return {
            "ok": False,
            "reason": "列标题不存在或匹配不唯一: %s" % ", ".join(missing),
            "missing": missing,
            "values": result.get("values") or {},
            "header_rows": header_rows,
        }
    return {
        "ok": True,
        "values": result.get("values") or {},
        "raw": bool(raw),
        "header_rows": header_rows,
    }


def get_cell_rect(col: int, row: int, scroll: bool = True):
    """取单元格中心的顶层视口坐标。"""
    col, reason = _index(col, "col")
    if reason:
        return {"ok": False, "reason": reason}
    row, reason = _index(row, "row")
    if reason:
        return {"ok": False, "reason": reason}
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    try:
        if scroll:
            scrolled = scroll_to_cell(col, row)
            if not scrolled.get("ok"):
                return scrolled
            res = _wait_cell_center_stable(col, row)
        else:
            res = _run(
                "vtable-column-values.js",
                "return JSON.stringify(getCellCenterViewport.apply(null, %s));"
                % _js_args(col, row),
            )
    except Exception as exc:
        return {"ok": False, "reason": "读取单元格坐标失败: %s" % exc}
    if not isinstance(res, dict):
        return {
            "ok": False,
            "reason": "无法获取单元格坐标，可能 col/row 越界或未挂载 VTable",
        }
    try:
        viewport_x = float(res["viewportX"])
        viewport_y = float(res["viewportY"])
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "reason": "VTable 返回了无效单元格坐标"}
    if not math.isfinite(viewport_x) or not math.isfinite(viewport_y):
        return {"ok": False, "reason": "VTable 返回了非有限单元格坐标"}
    return {
        "ok": True,
        "viewportX": round(viewport_x, 1),
        "viewportY": round(viewport_y, 1),
        "col": col,
        "row": row,
        "scrolled": bool(scroll),
    }


def scroll_to_cell(col: int, row: int):
    col, reason = _index(col, "col")
    if reason:
        return {"ok": False, "reason": reason}
    row, reason = _index(row, "row")
    if reason:
        return {"ok": False, "reason": reason}
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    try:
        res = _run(
            "vtable-column-values.js",
            "return JSON.stringify(scrollToCell.apply(null, %s));" % _js_args(col, row),
        )
    except Exception as exc:
        return {"ok": False, "reason": "scrollToCell failed: %s" % exc}
    if res is None:
        return {
            "ok": False,
            "reason": "scrollToCell 返回空，可能未挂载 VTable 或 col/row 越界",
        }
    if isinstance(res, dict) and "ok" in res:
        return res
    return {"ok": True, "result": res}


def _normalize_detail(detail: str):
    return "full" if (detail or "").strip().lower() == "full" else "summary"


def get_cell_render_info(
    col: int, row: int, detail: str = "summary", scroll: bool = True
):
    """读取 VTable 单元格渲染摘要：文本、字体色、标签底色、单元格背景/边框色。"""
    col, reason = _index(col, "col")
    if reason:
        return {"ok": False, "reason": reason}
    row, reason = _index(row, "row")
    if reason:
        return {"ok": False, "reason": reason}
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    if scroll:
        scrolled = scroll_to_cell(col, row)
        if not scrolled.get("ok"):
            return scrolled
        if not _wait_cell_center_stable(col, row):
            return {
                "ok": False,
                "reason": "VTable 单元格滚动后未稳定",
                "col": col,
                "row": row,
            }
    res = _run(
        "vtable-column-values.js",
        "return JSON.stringify(getCellRenderInfo.apply(null, %s));"
        % _js_args(col, row, _normalize_detail(detail)),
    )
    if not isinstance(res, dict):
        return {"ok": False, "reason": "getCellRenderInfo 返回空或非 dict"}
    res.setdefault("ok", True)
    res.setdefault("col", col)
    res.setdefault("row", row)
    res["scrolled"] = scroll
    return res


def get_cell_icons(
    col: int,
    row: int,
    icon_name: str = None,
    detail: str = "summary",
    scroll: bool = True,
):
    """读取任意 VTable 单元格内的可能图标，返回顶层视口坐标。"""
    col, reason = _index(col, "col")
    if reason:
        return {"ok": False, "reason": reason}
    row, reason = _index(row, "row")
    if reason:
        return {"ok": False, "reason": reason}
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    if scroll:
        scrolled = scroll_to_cell(col, row)
        if not scrolled.get("ok"):
            return scrolled
        if not _wait_cell_center_stable(col, row):
            return {
                "ok": False,
                "reason": "VTable 单元格滚动后未稳定",
                "col": col,
                "row": row,
            }
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
        except (TypeError, ValueError):
            return None, "icon_index 必须是非负整数"
        if (isinstance(icon_index, float) and not icon_index.is_integer()) or idx < 0:
            return None, "icon_index 必须是非负整数"
        if idx >= len(icons):
            return None, "icon_index 越界: %s（可用 0-%s）" % (
                icon_index,
                len(icons) - 1,
            )
        return icons[idx], None
    if icon_name:
        needle = str(icon_name).strip().lower()
        exact = [
            icon
            for icon in icons
            if needle
            in {
                str(icon.get("name") or "").lower(),
                str(icon.get("type") or "").lower(),
                str(icon.get("symbolType") or "").lower(),
                str(icon.get("func") or "").lower(),
            }
        ]
        if len(exact) == 1:
            return exact[0], None
        partial = [
            icon
            for icon in icons
            if needle
            and needle
            in " ".join(
                [
                    str(icon.get("name") or ""),
                    str(icon.get("type") or ""),
                    str(icon.get("symbolType") or ""),
                    str(icon.get("func") or ""),
                ]
            ).lower()
        ]
        matches = exact if exact else partial
        if len(matches) == 1:
            return matches[0], None
        available = [
            icon.get("name") or icon.get("type") or icon.get("symbolType")
            for icon in icons
        ]
        if not matches:
            return None, "图标未找到: %s（可用: %s）" % (icon_name, available)
        return None, "图标匹配不唯一: %s（匹配: %s）" % (icon_name, available)
    if len(icons) == 1:
        return icons[0], None
    return None, "单元格内有多个图标，请提供 icon_name 或 icon_index（可用: %s）" % (
        [i.get("name") or i.get("type") or i.get("symbolType") for i in icons]
    )


def _find_header_icon_point(col: int, row: int, icon_name: str, scroll: bool = True):
    if not icon_name:
        return {"ok": False, "reason": "点击表头图标需要 icon_name"}
    if scroll:
        scrolled = scroll_to_cell(col, row)
        if not scrolled.get("ok"):
            return scrolled
        if not _wait_cell_center_stable(col, row):
            return {
                "ok": False,
                "reason": "VTable 表头滚动后未稳定",
                "col": col,
                "row": row,
            }
    scan = _run(
        "vtable-scanner.js",
        (
            "var a=%s;var r=scanColumns(a[0]);"
            "return r?JSON.stringify(r.filter(function(c){return c.col===a[1]&&c.isHeader;})):null;"
        )
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


def _find_cell_icon_point(
    col: int,
    row: int,
    icon_name: str = None,
    icon_index: int = None,
    scroll: bool = True,
):
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
        "icon": match.get("name")
        or match.get("type")
        or match.get("symbolType")
        or icon_name,
        "icon_index": match.get("index"),
        "icon_info": match,
        "col": col,
        "row": row,
        "scrolled": scan.get("scrolled", scroll),
    }


def _resolve_action_point(
    target: str,
    col: int,
    row: int,
    icon_name: str = None,
    icon_index: int = None,
    scroll: bool = True,
):
    if target == "header-icon":
        return _find_header_icon_point(col, 0, icon_name, scroll=scroll)
    if target == "cell-icon":
        return _find_cell_icon_point(
            col, row, icon_name=icon_name, icon_index=icon_index, scroll=scroll
        )
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


def _perform_pointer_action(
    action: str,
    vx,
    vy,
    hover_first: bool = True,
    duration: float = 0.3,
    drag_to: dict = None,
    source_x: float = None,
    source_y: float = None,
):
    try:
        vx = float(vx)
        vy = float(vy)
        duration = min(max(float(duration or 0), 0.0), 10.0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "VTable 指针坐标或 duration 无效"}
    if not math.isfinite(vx) or not math.isfinite(vy) or not math.isfinite(duration):
        return {"ok": False, "reason": "VTable 指针参数必须为有限数值"}
    tab = browser_session.get_tab()
    move_duration = duration if hover_first or action == "hover" else 0
    try:
        ac = tab.actions
        if action == "hover":
            ac.move_to((vx, vy), duration=duration)
            return {"ok": True}
        if action == "click":
            ac.move_to((vx, vy), duration=move_duration)
            ac.click()
            return {"ok": True}
        if action == "double_click":
            ac.move_to((vx, vy), duration=move_duration).click(times=1).wait(
                0.15
            ).click(times=1)
            return {"ok": True}
        if action == "drag":
            destination_x, destination_y, reason = _resolve_drag_destination(
                vx, vy, drag_to
            )
            if reason:
                return {"ok": False, "reason": reason}
            destination_x = float(destination_x)
            destination_y = float(destination_y)
            if not math.isfinite(destination_x) or not math.isfinite(destination_y):
                return {"ok": False, "reason": "drag 目标坐标必须为有限数值"}
            # VTable 列拖拽：调用侧先 click 选中列头，再调 drag 执行 hold+drag
            #   第一步（调用侧执行）：click 列头 → 列选中
            #   第二步（本函数）：move_to → hold(抓取列) → move_to(拖拽) → release
            src_x = float(source_x) if source_x is not None else vx
            src_y = float(source_y) if source_y is not None else vy
            held = False
            try:
                ac.move_to((src_x, src_y), duration=move_duration)
                ac.hold()
                held = True
                ac.move_to(
                    (destination_x, destination_y), duration=max(duration, 0.1)
                )
            finally:
                if held:
                    ac.release()
            return {
                "ok": True,
                "sourceX": round(src_x, 1),
                "sourceY": round(src_y, 1),
                "destinationX": round(destination_x, 1),
                "destinationY": round(destination_y, 1),
            }
    except Exception as exc:
        return {"ok": False, "reason": "VTable %s failed: %s" % (action, exc)}
    return {"ok": False, "reason": "不支持的 VTable 动作: %s" % action}


def vtable_action(
    action: str = "click",
    col: int = None,
    row: int = 0,
    target: str = "cell",
    icon_name: str = None,
    icon_index: int = None,
    hover_first: bool = True,
    duration: float = 0.3,
    drag_to: dict = None,
    scroll: bool = True,
    source_x: float = None,
    source_y: float = None,
):
    """统一执行 VTable 指针动作。

    流程：确保实例有效 → 必要时 scrollToCell → 重新读取顶层视口坐标 → click/double_click/hover/drag。
    该函数是所有 VTable 坐标型交互的单一入口，调用侧不需要自行计算 iframe/canvas 偏移。

    source_x/source_y: 拖拽动作可选的源位置偏移（覆盖列中心），
                       用于避免列头图标干扰。传 None 则使用列中心。
    """
    if col is None:
        return {"ok": False, "reason": "VTable 动作需要 col"}
    col, reason = _index(col, "col")
    if reason:
        return {"ok": False, "reason": reason}
    row, reason = _index(row, "row")
    if reason:
        return {"ok": False, "reason": reason}
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}
    action_name, reason = _normalize_pointer_action(action)
    if reason:
        return {"ok": False, "reason": reason}
    target_name, reason = _normalize_action_target(target, icon_name=icon_name)
    if reason:
        return {"ok": False, "reason": reason}

    point = _resolve_action_point(
        target_name, col, row, icon_name=icon_name, icon_index=icon_index, scroll=scroll
    )
    if not point.get("ok"):
        return point
    vx = point["viewportX"]
    vy = point["viewportY"]

    # 对 header 拖拽自动偏移源位置：左移 20px 避开列头右侧图标（冻结/筛选等）
    source_pos_x = source_x
    source_pos_y = source_y
    if action_name == "drag" and target_name == "header":
        if source_pos_x is None:
            source_pos_x = vx - 20

    performed = _perform_pointer_action(
        action_name, vx, vy, hover_first=hover_first, duration=duration, drag_to=drag_to,
        source_x=source_pos_x, source_y=source_pos_y,
    )
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


def click_cell(
    col: int,
    row: int,
    icon_name: str = None,
    hover_first: bool = True,
    duration: float = 0.3,
    double_click: bool = False,
):
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
    """用 DrissionPage 动作链拖拽列边界，并回读实际宽度验证结果。"""
    col, reason = _index(col, "col")
    if reason:
        return {"ok": False, "reason": reason}
    try:
        width = int(width)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "width 必须为正整数"}
    if width <= 0 or width > 10_000:
        return {"ok": False, "reason": "width 必须在 1..10000 之间"}
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}

    def _get_bounds():
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
            "return JSON.stringify({rightEdge:ifr.left+vr.left+b.x2,"
            "centerY:ifr.top+vr.top+(b.y1+b.y2)/2,oldWidth:b.x2-b.x1,"
            "viewportLeft:ifr.left,viewportRight:ifr.left+window.innerWidth});"
        ) % col
        return _run("vtable-column-values.js", js)

    try:
        info = _get_bounds()
        if not isinstance(info, dict) or "rightEdge" not in info:
            return {"ok": False, "reason": "无法获取列宽信息"}
        if (
            info["rightEdge"] > info["viewportRight"]
            or info["rightEdge"] < info["viewportLeft"]
        ):
            _run(
                "vtable-column-values.js",
                "scrollToCell.apply(null, %s);" % _js_args(col, 0),
            )

            def read_edge():
                value = _get_bounds()
                if not isinstance(value, dict) or "rightEdge" not in value:
                    return None
                return round(float(value["rightEdge"]), 1), value

            stable = _wait_stable(read_edge, timeout=3)
            if not stable:
                return {"ok": False, "reason": "滚动后仍无法获取列宽信息"}
            info = stable[1]
        old_width = round(float(info["oldWidth"]))
        delta = width - old_width
        if delta == 0:
            return {
                "ok": True,
                "col": col,
                "old_width": old_width,
                "new_width": old_width,
                "target_width": width,
                "delta": 0,
            }
        start_x = round(float(info["rightEdge"]))
        center_y = round(float(info["centerY"]))
        tab = browser_session.get_tab()
        held = False
        try:
            tab.actions.move_to((start_x - 100, center_y), duration=0.1)
            tab.actions.move_to((start_x - 3, center_y), duration=0.15)
            tab.actions.hold()
            held = True
            tab.actions.move_to((start_x - 3 + delta, center_y), duration=0.5)
        finally:
            if held:
                tab.actions.release()
        verified = _wait_stable(lambda: _get_bounds(), timeout=3)
        if not isinstance(verified, dict) or "oldWidth" not in verified:
            return {"ok": False, "reason": "列宽拖拽后无法回读实际宽度"}
        actual_width = round(float(verified["oldWidth"]))
        result = {
            "ok": abs(actual_width - width) <= 2,
            "col": col,
            "old_width": old_width,
            "new_width": actual_width,
            "target_width": width,
            "delta": actual_width - old_width,
        }
        if not result["ok"]:
            result["reason"] = "列宽未达到目标值（允许误差 2px）"
        return result
    except Exception as exc:
        return {"ok": False, "reason": "调整 VTable 列宽失败: %s" % exc}


def reorder_column(col: int, target_col: int, position: str = "after"):
    """拖拽 VTable 列头重排列。

    VTable 拖拽重排列要求三步式鼠标动作：
      move_to → click（选中列头）→ move_to → hold → move_to → release

    区别于普通 drag（move_to → hold → move_to → release），
    少了 click 步骤 VTable 不会触发列拖拽。

    Args:
        col: 要拖动列索引
        target_col: 目标锚点列索引
        position: "after"（拖到 target 右侧, 默认）或 "before"（左侧）

    Returns:
        {ok, action, source_col, target_col, dropX, dropY, ...}
    """
    col, reason = _index(col, "col")
    if reason:
        return {"ok": False, "reason": reason}
    target_col, reason = _index(target_col, "col")
    if reason:
        return {"ok": False, "reason": reason}
    if col == target_col:
        return {"ok": False, "reason": "源列与目标列相同，无需重排"}
    if not _ensure_vtable():
        return {"ok": False, "reason": _VTABLE_RETRY_REASON}

    try:
        # 1. 滚动到源列，获取视口坐标
        source = get_cell_rect(col, 0, scroll=True)
        if not source.get("ok"):
            return {"ok": False, "reason": "无法获取源列坐标: %s" % source.get("reason")}

        # 2. 滚动到目标列，获取视口坐标
        target = get_cell_rect(target_col, 0, scroll=True)
        if not target.get("ok"):
            return {"ok": False, "reason": "无法获取目标列坐标: %s" % target.get("reason")}

        # 3. 获取目标列渲染信息（canvas 宽度，用于计算右侧边界）
        render = get_cell_render_info(target_col, 0, scroll=False, detail="summary")
        if render.get("ok") and render.get("cellBounds"):
            canvas_width = render["cellBounds"].get("width", 120)
            # 假定 canvas 像素与视口像素 1:1
            if position == "after":
                drop_x = round(target["viewportX"] + canvas_width / 2 + 40)
            else:
                drop_x = round(target["viewportX"] - canvas_width / 2 - 40)
        else:
            # 无渲染信息时用默认偏移量（列宽约 120px）
            offset = 110  # 半宽 + 边距
            drop_x = round(target["viewportX"] + (offset if position == "after" else -offset))

        # 4. 重新滚动到源列，获得该 scroll 状态下的最新坐标
        source_fresh = get_cell_rect(col, 0, scroll=True)
        if not source_fresh.get("ok"):
            return {"ok": False, "reason": "无法刷新源列坐标: %s" % source_fresh.get("reason")}
        src_vx = source_fresh["viewportX"]
        src_vy = source_fresh["viewportY"]
        # 左移 20px 避免列头右侧的冻结/筛选图标干扰
        src_vx = src_vx - 20

        # 5. 执行三步式拖拽：click → hold → move_to → release
        tab = browser_session.get_tab()
        held = False
        try:
            tab.actions.move_to((src_vx, src_vy), duration=0.1)
            tab.actions.click()
            tab.actions.move_to((src_vx, src_vy), duration=0.1)
            tab.actions.hold()
            held = True
            tab.actions.move_to((drop_x, src_vy), duration=0.5)
        finally:
            if held:
                tab.actions.release()

        return {
            "ok": True,
            "action": "reorder",
            "source_col": col,
            "target_col": target_col,
            "dropX": round(drop_x, 1),
            "dropY": round(src_vy, 1),
            "position": position,
        }
    except Exception as exc:
        return {"ok": False, "reason": "VTable 列重排失败: %s" % exc}
