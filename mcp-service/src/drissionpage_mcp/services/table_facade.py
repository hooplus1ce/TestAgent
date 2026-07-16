"""Unified table facade (VTable / Bootstrap Table / HTML ant-table).

Extracted from server.py so MCP components and recipes can share one implementation.
Locking remains the caller's responsibility (server synchronized wrappers / components).
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Literal

from ..core import page_family
from ..resources import resource_store
from . import bootstrap_table, browser_session, html_table, modal, observe, page_model, vtable

logger = logging.getLogger("drissionpage-mcp")


def pre_click_cleanup(clean_overlays: bool = True):
    """Remove transient notification/message overlays before a new click."""
    if not clean_overlays:
        return None
    try:
        return modal.clear_transient_overlays()
    except Exception as e:
        logger.debug("点击前清理通知失败: %s", e)
        return {"ok": False, "closed": [], "errors": [str(e)]}


def attach_cleanup(result: dict, cleanup: dict = None) -> dict:
    if cleanup and cleanup.get("closed"):
        result["pre_cleaned"] = cleanup.get("closed")
    if cleanup and cleanup.get("errors"):
        result["pre_clean_errors"] = cleanup.get("errors")
    return result


def _click_table_cell_raw(row: int, col: int = None, column_title: str = None,
                          kind: str = "auto", table_index: int = 0,
                          icon_name: str = None, hover_first: bool = True,
                          duration: float = 0.3, double_click: bool = False) -> dict:
    """Undecorated table click helper for aggregate tools."""
    kind = _normalize_table_kind(kind)

    def _click_vtable():
        target_col = col
        if target_col is None and column_title:
            target_col, reason = _find_vtable_col(column_title)
            if target_col is None:
                return {"ok": False, "kind": "vtable", "reason": reason}
        if target_col is None:
            return {"ok": False, "kind": "vtable", "reason": "VTable 点击需要 col 或 column_title"}
        return _tag_table_result("vtable", vtable.click_cell(target_col, row, icon_name, hover_first, duration, double_click))

    def _click_bootstrap():
        if not column_title:
            return _tag_table_result(
                "bootstrap",
                bootstrap_table.click_bootstrap_row_selection(row, table_index),
            )
        return _tag_table_result(
            "bootstrap",
            bootstrap_table.click_bootstrap_table_cell(column_title, row, table_index),
        )

    def _click_html():
        if not column_title:
            return {"ok": False, "kind": "html", "reason": "HTML 表格点击需要 column_title"}
        return _tag_table_result("html", html_table.click_html_table_cell(column_title, row, table_index))

    if kind == "vtable":
        return _click_vtable()
    if kind == "bootstrap":
        return _click_bootstrap()
    if kind == "html":
        return _click_html()

    reasons = {}
    for item in _auto_table_scan_order():
        if item == "vtable":
            candidate = _click_vtable()
        elif item == "bootstrap":
            candidate = _click_bootstrap()
        else:
            candidate = _click_html()
        reasons[item] = candidate.get("reason", "")
        if candidate.get("ok"):
            return candidate
    return {"ok": False, "kind": "auto", "reason": "表格单元格点击失败", "details": reasons}


def _normalize_table_kind(kind: str) -> str:
    return page_family.normalize_table_kind(kind)


def _tag_table_result(kind: str, result: dict) -> dict:
    if not isinstance(result, dict):
        return {"ok": False, "kind": kind, "reason": "表格后端返回非 dict: %r" % (result,)}
    tagged = dict(result)
    tagged.setdefault("kind", kind)
    return tagged


def _scan_table_bootstrap(table_index: int = 0) -> dict:
    result = bootstrap_table.scan_bootstrap_table()
    tagged = _tag_table_result("bootstrap", result)
    if not tagged.get("ok"):
        return tagged
    tables = tagged.get("tables") or []
    if table_index >= len(tables):
        return {
            "ok": False,
            "kind": "bootstrap",
            "reason": "visible bootstrap-table not found at index %s" % table_index,
            "table_count": len(tables),
        }
    tagged["tables"] = [tables[table_index]]
    tagged["table_index"] = table_index
    tagged["table_count"] = len(tables)
    return tagged


def _auto_table_scan_order() -> list[str]:
    """根据页面族决定 auto 扫描顺序。"""
    preferred = "auto"
    try:
        family = page_family.detect_page_family()
        preferred = (family or {}).get("preferred_table_kind") or "auto"
    except Exception:
        preferred = "auto"
    return page_family.auto_table_scan_order(preferred)


def _find_vtable_col(column_title: str, max_col: int = 100):
    scan = vtable.scan_vtable_columns(max_col)
    if not scan.get("ok"):
        return None, scan.get("reason", "VTable 扫描失败")
    expected = str(column_title or "").strip()
    matches = {
        info.get("col") for info in scan.get("columns", [])
        if str(info.get("title") or info.get("field") or "").strip() == expected
    }
    matches.discard(None)
    if len(matches) == 1:
        return next(iter(matches)), None
    if matches:
        return None, "VTable 列标题匹配不唯一: %s（匹配列 %s）" % (expected, sorted(matches))
    return None, "VTable 列未找到: %s" % expected


def _build_vtable_drag_to(drag_to_x=None, drag_to_y=None, drag_by_x=None, drag_by_y=None):
    has_absolute = drag_to_x is not None or drag_to_y is not None
    has_relative = drag_by_x is not None or drag_by_y is not None
    if has_absolute and has_relative:
        return None, "drag_to_x/drag_to_y 与 drag_by_x/drag_by_y 不能混用"
    if has_absolute:
        drag_to = {}
        if drag_to_x is not None:
            drag_to["x"] = drag_to_x
        if drag_to_y is not None:
            drag_to["y"] = drag_to_y
        return drag_to, None
    if has_relative:
        drag_to = {}
        if drag_by_x is not None:
            drag_to["dx"] = drag_by_x
        if drag_by_y is not None:
            drag_to["dy"] = drag_by_y
        return drag_to, None
    return None, None


def _resolve_vtable_action_col(col: int = None, column_title: str = None):
    target_col = col
    if target_col is None and column_title:
        target_col, reason = _find_vtable_col(column_title)
        if target_col is None:
            return None, reason
    if target_col is None:
        return None, "VTable 动作需要 col 或 column_title"
    return target_col, None


def _scan_table_vtable(max_col: int) -> dict:
    return _tag_table_result("vtable", vtable.scan_vtable_columns(max_col))


def _scan_table_html(table_index: int = 0) -> dict:
    result = html_table.scan_html_table()
    tagged = _tag_table_result("html", result)
    if not tagged.get("ok"):
        return tagged
    tables = tagged.get("tables") or []
    if table_index >= len(tables):
        return {"ok": False, "kind": "html",
                "reason": "visible table not found at index %s" % table_index,
                "table_count": len(tables)}
    tagged["tables"] = [tables[table_index]]
    tagged["table_index"] = table_index
    tagged["table_count"] = len(tables)
    return tagged


# ==================== 统一表格 facade（VTable / HTML Table）====================

def scan_table(kind: str = "auto", max_col: int = 50, table_index: int = 0, filename: str = None) -> dict:
    """扫描当前可见表格。

    kind:
      - auto: 按页面族优先（VTable / Bootstrap Table / ant-table）回退
      - vtable / html / bootstrap: 指定后端
    filename 提供时保存到文件，不返回大 JSON。
    """
    try:
        parsed_table_index = int(table_index)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "table_index 必须为非负整数"}
    if (isinstance(table_index, float) and not table_index.is_integer()) or parsed_table_index < 0:
        return {"ok": False, "reason": "table_index 必须为非负整数"}
    table_index = parsed_table_index
    kind = _normalize_table_kind(kind)
    if kind == "vtable":
        result = _scan_table_vtable(max_col)
    elif kind == "bootstrap":
        result = _scan_table_bootstrap(table_index)
    elif kind == "html":
        result = _scan_table_html(table_index)
    else:
        reasons = {}
        result = {
            "ok": False,
            "kind": "auto",
            "reason": "未识别到 VTable / Bootstrap Table / ant-table",
        }
        order = _auto_table_scan_order()
        for item in order:
            if item == "vtable":
                candidate = _scan_table_vtable(max_col)
            elif item == "bootstrap":
                candidate = _scan_table_bootstrap(table_index)
            else:
                candidate = _scan_table_html(table_index)
            reasons[item] = candidate.get("reason", "")
            ok = candidate.get("ok")
            if ok and item in {"html", "bootstrap"} and not (candidate.get("tables") or []):
                ok = False
                reasons[item] = reasons[item] or "tables empty"
            if ok:
                if item != order[0]:
                    candidate["fallback_from"] = order[0]
                    candidate["scan_order"] = order
                result = candidate
                break
        if not result.get("ok"):
            result["details"] = reasons
            result["scan_order"] = order

    # filename 参数优先
    if filename and result.get("ok"):
        full_path = resource_store.resolve_path(filename)
        with open(full_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return {
            "ok": True,
            "saved_to": os.path.abspath(full_path),
            "kind": result.get("kind"),
        }

    return result


def get_table_values(column_title: str, kind: str = "auto", raw: bool = False, table_index: int = 0, filename: str = None) -> dict:
    """按列标题读取标量值列表；HTML 同时返回 cells 元数据，raw=true 仅支持 VTable。
    kind=auto 优先 VTable，失败后回退当前可见 HTML Table；filename 可保存大结果。"""
    column_title = str(column_title or "").strip()
    if not column_title:
        return {"ok": False, "reason": "column_title 不能为空"}
    try:
        parsed_table_index = int(table_index)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "table_index 必须为非负整数"}
    if (isinstance(table_index, float) and not table_index.is_integer()) or parsed_table_index < 0:
        return {"ok": False, "reason": "table_index 必须为非负整数"}
    table_index = parsed_table_index
    kind = _normalize_table_kind(kind)
    if kind == "vtable":
        result = _tag_table_result("vtable", vtable.get_column_values(column_title, raw))
    elif kind == "bootstrap":
        if raw:
            return {"ok": False, "kind": "bootstrap",
                    "reason": "Bootstrap Table 仅支持界面文本，raw=true 只适用于 VTable"}
        result = _tag_table_result(
            "bootstrap",
            bootstrap_table.get_bootstrap_table_values(column_title, table_index),
        )
        result.setdefault("raw", False)
    elif kind == "html":
        if raw:
            return {"ok": False, "kind": "html", "reason": "HTML 表格仅支持界面文本，raw=true 只适用于 VTable"}
        result = _tag_table_result("html", html_table.get_html_table_values(column_title, table_index))
        result.setdefault("raw", False)
    else:
        reasons = {}
        result = {"ok": False, "kind": "auto", "reason": "列值读取失败"}
        for item in _auto_table_scan_order():
            if item == "vtable":
                candidate = _tag_table_result("vtable", vtable.get_column_values(column_title, raw))
            elif item == "bootstrap":
                if raw:
                    reasons["bootstrap"] = "raw=true 不支持"
                    continue
                candidate = _tag_table_result(
                    "bootstrap",
                    bootstrap_table.get_bootstrap_table_values(column_title, table_index),
                )
                candidate.setdefault("raw", False)
            else:
                if raw:
                    reasons["html"] = "raw=true 不支持"
                    continue
                candidate = _tag_table_result(
                    "html", html_table.get_html_table_values(column_title, table_index),
                )
                candidate.setdefault("raw", False)
            reasons[item] = candidate.get("reason", "")
            if candidate.get("ok"):
                result = candidate
                break
        if not result.get("ok"):
            if raw:
                return {
                    "ok": False,
                    "kind": "auto",
                    "reason": "未找到可读取原始值的 VTable；其它表格不支持 raw=true",
                    "details": reasons,
                }
            result["details"] = reasons

    # filename 参数优先
    if filename and result.get("ok"):
        full_path = resource_store.resolve_path(filename)
        with open(full_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return {
            "ok": True,
            "saved_to": os.path.abspath(full_path),
            "kind": result.get("kind"),
        }

    return result


def find_vtable_row(column_title: str, value: str, raw: bool = False,
                    match: str = "equals", header_rows: int = None,
                    timeout: float = 0) -> dict:
    """按唯一列值解析 VTable 画布行号；默认从实例自动读取表头层数。"""
    match = str(match or "equals").lower()
    if match not in {"equals", "contains"}:
        return {"ok": False, "reason": "unsupported row match: %s" % match}
    expected = str(value or "").strip()
    if match == "contains" and not expected:
        return {"ok": False, "reason": "contains 匹配值不能为空"}
    try:
        timeout_value = float(timeout or 0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "timeout 必须为非负数"}
    if not math.isfinite(timeout_value) or timeout_value < 0:
        return {"ok": False, "reason": "timeout 必须为非负有限数值"}
    explicit_header_rows = None
    if header_rows is not None:
        try:
            explicit_header_rows = int(header_rows)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "header_rows 必须为非负整数"}
        if (isinstance(header_rows, float) and not header_rows.is_integer()) or explicit_header_rows < 0:
            return {"ok": False, "reason": "header_rows 必须为非负整数"}
    if timeout_value > 0:
        settled = vtable.wait_for_render_stable(timeout=timeout_value)
        if not settled.get("ok"):
            return settled
    scanned = get_table_values(column_title=column_title, kind="vtable", raw=raw)
    if not scanned.get("ok"):
        return scanned
    resolved_header_rows = (explicit_header_rows if explicit_header_rows is not None
                            else max(int(scanned.get("header_rows") or 1), 1))
    matches = []
    for index, actual in enumerate(scanned.get("values") or []):
        normalized = str(actual if actual is not None else "").strip()
        if normalized == expected if match == "equals" else expected in normalized:
            matches.append({"data_index": index, "row": index + resolved_header_rows,
                            "actual": actual})
    if len(matches) != 1:
        return {
            "ok": False,
            "kind": "vtable",
            "reason": "列值未唯一定位到 VTable 行: %s=%s（匹配 %d 行）" % (
                column_title, expected, len(matches),
            ),
            "column_title": column_title,
            "value": value,
            "match": match,
            "match_count": len(matches),
            "matches": matches,
            "header_rows": resolved_header_rows,
        }
    found = matches[0]
    return {
        "ok": True,
        "kind": "vtable",
        "column_title": column_title,
        "value": value,
        "row": found["row"],
        "data_index": found["data_index"],
        "match": match,
        "header_rows": resolved_header_rows,
    }


def count_vtable_rows(column_title: str, value: str, raw: bool = False,
                      match: str = "equals", expected_count: int = None,
                      timeout: float = 0) -> dict:
    """统计 VTable 指定列的匹配行数，用于新增存在性与删除完成断言。"""
    match = str(match or "equals").lower()
    if match not in {"equals", "contains"}:
        return {"ok": False, "reason": "unsupported row match: %s" % match}
    expected = str(value or "").strip()
    if match == "contains" and not expected:
        return {"ok": False, "reason": "contains 匹配值不能为空"}
    if expected_count is not None:
        try:
            parsed_count = int(expected_count)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "expected_count 必须为非负整数"}
        if (isinstance(expected_count, float) and not expected_count.is_integer()) or parsed_count < 0:
            return {"ok": False, "reason": "expected_count 必须为非负整数"}
        expected_count = parsed_count
    try:
        timeout_value = float(timeout or 0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "timeout 必须为非负数"}
    if not math.isfinite(timeout_value) or timeout_value < 0:
        return {"ok": False, "reason": "timeout 必须为非负有限数值"}
    if timeout_value > 0:
        settled = vtable.wait_for_render_stable(timeout=timeout_value)
        if not settled.get("ok"):
            return settled
    scanned = get_table_values(column_title=column_title, kind="vtable", raw=raw)
    if not scanned.get("ok"):
        return scanned
    matched_indexes = []
    for index, actual in enumerate(scanned.get("values") or []):
        normalized = str(actual if actual is not None else "").strip()
        if normalized == expected if match == "equals" else expected in normalized:
            matched_indexes.append(index)
    return {
        "ok": True, "kind": "vtable", "column_title": column_title,
        "value": value, "match": match, "match_count": len(matched_indexes),
        "data_indexes": matched_indexes, "expected_count": expected_count,
        "matches_expected": (len(matched_indexes) == expected_count
                             if expected_count is not None else None),
    }


def get_vtable_row_values(key_column: str, key_value: str, column_titles: list[str],
                          raw: bool = False, match: str = "equals",
                          timeout: float = 0) -> dict:
    """按唯一业务键读取同一 VTable 行的多列值；目标列通过一次脚本批量读取。"""
    titles = list(dict.fromkeys(str(title or "").strip() for title in (column_titles or [])))
    key_column = str(key_column or "").strip()
    if not key_column:
        return {"ok": False, "reason": "key_column 不能为空"}
    if not titles or any(not title for title in titles):
        return {"ok": False, "reason": "column_titles 不能为空"}
    found = find_vtable_row(
        column_title=key_column, value=key_value, raw=raw, match=match,
        timeout=timeout,
    )
    if not found.get("ok"):
        return found
    data_index = found["data_index"]
    scan_titles = list(dict.fromkeys([key_column] + titles))
    scanned = vtable.get_columns_values(scan_titles, raw=raw)
    if not scanned.get("ok"):
        return {"ok": False, "reason": "批量读取目标列失败", "detail": scanned}
    columns = scanned.get("values") or {}
    key_values = columns.get(key_column) or []
    expected = str(key_value or "").strip()
    resolved_match = found.get("match", "equals")
    matching_indexes = []
    for index, actual in enumerate(key_values):
        normalized = str(actual if actual is not None else "").strip()
        if normalized == expected if resolved_match == "equals" else expected in normalized:
            matching_indexes.append(index)
    if matching_indexes != [data_index]:
        return {"ok": False, "kind": "vtable",
                "reason": "VTable 在行定位后发生变化，业务键不再唯一指向原数据行",
                "key_column": key_column, "key_value": key_value,
                "previous_data_index": data_index, "matching_indexes": matching_indexes}
    values = {}
    for title in titles:
        column_values = columns.get(title) or []
        if data_index >= len(column_values):
            return {"ok": False, "reason": "列数据行数不一致: %s" % title,
                    "column": title, "data_index": data_index,
                    "value_count": len(column_values)}
        values[title] = column_values[data_index]
    return {
        "ok": True, "kind": "vtable", "key_column": key_column,
        "key_value": key_value, "row": found["row"], "data_index": data_index,
        "header_rows": found.get("header_rows"), "values": values,
    }


def get_table_data(kind: str = "auto", table_index: int = 0,
                   filename: str = None) -> dict:
    """统一读取当前表格完整可读数据，HTML 与 VTable 均受支持。"""
    kind = _normalize_table_kind(kind)
    return page_model.get_all_table_data(
        kind=kind,
        table_index=table_index,
        max_pages=1,
        max_rows=100_000,
        max_columns=1000,
        raw=False,
        filename=filename,
    )


def get_vtable_cell_render_info(row: int, col: int = None, column_title: str = None,
                                detail: str = "summary") -> dict:
    """读取 VTable 单元格渲染信息：文本、字体色、标签底色、单元格背景色/边框色。"""
    target_col, reason = _resolve_vtable_action_col(col=col, column_title=column_title)
    if target_col is None:
        return {"ok": False, "kind": "vtable", "reason": reason}
    return _tag_table_result("vtable", vtable.get_cell_render_info(target_col, row, detail=detail))


def get_vtable_cell_icons(row: int, col: int = None, column_title: str = None,
                          icon_name: str = None, detail: str = "summary") -> dict:
    """读取任意 VTable 单元格内可能存在的图标，返回图标名称/类型和顶层视口坐标。"""
    target_col, reason = _resolve_vtable_action_col(col=col, column_title=column_title)
    if target_col is None:
        return {"ok": False, "kind": "vtable", "reason": reason}
    return _tag_table_result(
        "vtable",
        vtable.get_cell_icons(target_col, row, icon_name=icon_name, detail=detail),
    )


def vtable_action(action: str = "click", row: int = 0, col: int = None,
                  column_title: str = None, target: str = "cell",
                  icon_name: str = None, icon_index: int = None,
                  hover_first: bool = True,
                  duration: float = 0.3, drag_to_x: float = None,
                  drag_to_y: float = None, drag_by_x: float = None,
                  drag_by_y: float = None, clean_overlays: bool = True,
                  source_x: float = None, source_y: float = None) -> dict:
    """VTable 专项指针动作。工具内部负责滚动到可见、重算顶层视口坐标，再执行 click/double_click/hover/drag。

    source_x/source_y: 拖拽源位置偏移（覆盖列中心），用于避免列头图标干扰拖拽。
    """
    action_key = (action or "click").strip().lower().replace("-", "_")
    cleanup = None if action_key in {"hover", "move", "move_to", "mouseover", "mouse_over"} else pre_click_cleanup(clean_overlays)
    target_col, reason = _resolve_vtable_action_col(col=col, column_title=column_title)
    if target_col is None:
        return attach_cleanup({"ok": False, "kind": "vtable", "reason": reason}, cleanup)
    drag_to, reason = _build_vtable_drag_to(drag_to_x=drag_to_x, drag_to_y=drag_to_y,
                                           drag_by_x=drag_by_x, drag_by_y=drag_by_y)
    if reason:
        return attach_cleanup({"ok": False, "kind": "vtable", "reason": reason}, cleanup)
    result = _tag_table_result(
        "vtable",
        vtable.vtable_action(
            action=action,
            col=target_col,
            row=row,
            target=target,
            icon_name=icon_name,
            icon_index=icon_index,
            hover_first=hover_first,
            duration=duration,
            drag_to=drag_to,
            source_x=source_x,
            source_y=source_y,
        ),
    )
    return attach_cleanup(result, cleanup)


def _hover_table_cell_raw(
    row: int, col: int = None, column_title: str = None, kind: str = "auto",
    table_index: int = 0, duration: float = 0.3,
) -> dict:
    """Undecorated table hover helper（VTable / Bootstrap / ant-table）。"""
    kind = _normalize_table_kind(kind)

    def _hover_vtable():
        target_col = col
        if target_col is None and column_title:
            target_col, reason = _find_vtable_col(column_title)
            if target_col is None:
                return {"ok": False, "kind": "vtable", "reason": reason}
        if target_col is None:
            return {"ok": False, "kind": "vtable", "reason": "VTable 悬停需要 col 或 column_title"}
        return _tag_table_result(
            "vtable",
            vtable.vtable_action(
                action="hover", col=target_col, row=row, target="cell", duration=duration,
            ),
        )

    def _hover_bootstrap():
        if not column_title:
            return {"ok": False, "kind": "bootstrap", "reason": "Bootstrap Table 悬停需要 column_title"}
        return _tag_table_result(
            "bootstrap",
            bootstrap_table.hover_bootstrap_table_cell(
                column_title, row, table_index, duration=duration,
            ),
        )

    def _hover_html():
        if not column_title:
            return {"ok": False, "kind": "html", "reason": "HTML 表格悬停需要 column_title"}
        return _tag_table_result(
            "html",
            html_table.hover_html_table_cell(
                column_title, row, table_index, duration=duration,
            ),
        )

    if kind == "vtable":
        return _hover_vtable()
    if kind == "bootstrap":
        return _hover_bootstrap()
    if kind == "html":
        return _hover_html()

    reasons = {}
    for item in _auto_table_scan_order():
        if item == "vtable":
            candidate = _hover_vtable()
        elif item == "bootstrap":
            candidate = _hover_bootstrap()
        else:
            candidate = _hover_html()
        reasons[item] = candidate.get("reason", "")
        if candidate.get("ok"):
            return candidate
    return {"ok": False, "kind": "auto", "reason": "表格单元格悬停失败", "details": reasons}


def click_table_cell(row: int, col: int = None, column_title: str = None, kind: str = "auto",
                     table_index: int = 0, icon_name: str = None, hover_first: bool = True,
                     duration: float = 0.3, double_click: bool = False,
                     clean_overlays: bool = True) -> dict:
    """统一点击表格单元格。

    kind: auto | vtable | html | bootstrap。
    VTable 可用 col 或 column_title；HTML / Bootstrap Table 使用 column_title。
    clean_overlays=True 时先清理上一操作残留的 Ant notification/message。
    """
    cleanup = pre_click_cleanup(clean_overlays)
    result = _click_table_cell_raw(
        row=row, col=col, column_title=column_title, kind=kind,
        table_index=table_index, icon_name=icon_name, hover_first=hover_first,
        duration=duration, double_click=double_click,
    )
    return attach_cleanup(result, cleanup)


def hover_table_cell(row: int, col: int = None, column_title: str = None, kind: str = "auto",
                     table_index: int = 0, duration: float = 0.3) -> dict:
    """统一悬停表格单元格。kind: auto | vtable | html | bootstrap。"""
    return _hover_table_cell_raw(
        row=row, col=col, column_title=column_title, kind=kind,
        table_index=table_index, duration=duration,
    )


def resize_table_column(width: int, col: int = None, column_title: str = None, kind: str = "vtable") -> dict:
    """统一调整表格列宽。目前仅 VTable 支持列宽拖拽，HTML/Bootstrap Table 返回不支持。"""
    kind = _normalize_table_kind(kind)
    if kind in {"html", "bootstrap"}:
        return {"ok": False, "kind": kind, "reason": "%s 表格暂不支持列宽调整" % kind}
    target_col = col
    if target_col is None and column_title:
        target_col, reason = _find_vtable_col(column_title)
        if target_col is None:
            return {"ok": False, "kind": "vtable", "reason": reason}
    if target_col is None:
        return {"ok": False, "kind": "vtable", "reason": "调整列宽需要 col 或 column_title"}
    return _tag_table_result("vtable", vtable.resize_column(target_col, width))


def reorder_vtable_column(
    column_title: str = None, col: int = None,
    target_column_title: str = None, target_col: int = None,
    position: Literal["after", "before"] = "after",
) -> dict:
    """拖拽 VTable 列头重排列（仅 VTable 支持）。

    VTable 列重排需要三步式鼠标动作：click 选中列头 → hold → move_to → release。
    本工具封装此流程，只需指定要拖动的列和目标锚点列。

    Args:
        column_title: 要拖动的列标题（与 col 二选一）
        col: 要拖动的列索引（与 column_title 二选一）
        target_column_title: 目标锚点列标题（与 target_col 二选一）
        target_col: 目标锚点列索引（与 target_column_title 二选一）
        position: "after"（默认，拖到目标列右侧）或 "before"（拖到左侧）

    Returns:
        {ok, source_col, target_col, dropX, dropY, position}
    """
    source_col, reason = _resolve_vtable_action_col(col=col, column_title=column_title)
    if source_col is None:
        return {"ok": False, "kind": "vtable", "reason": reason}
    target_col_resolved, reason = _resolve_vtable_action_col(
        col=target_col, column_title=target_column_title
    )
    if target_col_resolved is None:
        return {"ok": False, "kind": "vtable", "reason": reason}
    return _tag_table_result(
        "vtable",
        vtable.reorder_column(source_col, target_col_resolved, position),
    )


def query_table(operation: Literal["values", "data", "find", "count", "row"] = "values",
                column_title: str = None,
                value: str = None, key_column: str = None, key_value: str = None,
                column_titles: list[str] = None,
                kind: Literal["auto", "html", "vtable", "bootstrap"] = "auto",
                table_index: int = 0, raw: bool = False,
                match: Literal["equals", "contains"] = "equals",
                expected_count: int = None, timeout: float = 0,
                filename: str = None) -> dict:
    """统一表格读取入口。

    kind: auto | html | vtable | bootstrap。
    operation 只能是：
    - values：读取 column_title 的全部可见值（HTML/VTable/Bootstrap）。
    - data：读取当前表格完整可读数据（HTML/VTable/Bootstrap）。
    - find：按 column_title/value 唯一定位 VTable 行。
    - count：统计 column_title/value 匹配的 VTable 行数。
    - row：按 key_column/key_value 读取 column_titles 指定的同一 VTable 行。
    """
    operation_key = str(operation or "values").strip().lower()
    if operation_key == "values":
        result = get_table_values(
            column_title=column_title, kind=kind, raw=raw,
            table_index=table_index, filename=filename,
        )
    elif operation_key == "data":
        result = get_table_data(kind=kind, table_index=table_index, filename=filename)
    elif operation_key == "find":
        result = find_vtable_row(
            column_title=column_title, value=value, raw=raw,
            match=match, timeout=timeout,
        )
    elif operation_key == "count":
        result = count_vtable_rows(
            column_title=column_title, value=value, raw=raw, match=match,
            expected_count=expected_count, timeout=timeout,
        )
    elif operation_key == "row":
        result = get_vtable_row_values(
            key_column=key_column or column_title,
            key_value=key_value if key_value is not None else value,
            column_titles=column_titles, raw=raw, match=match, timeout=timeout,
        )
    else:
        return {
            "ok": False,
            "reason": "operation 必须是 values/data/find/count/row",
            "operation": operation_key,
        }
    return {**result, "operation": operation_key}


def inspect_table_cell(row: int, col: int = None, column_title: str = None,
                       aspect: Literal["all", "render", "icons"] = "all",
                       icon_name: str = None,
                       detail: str = "summary") -> dict:
    """统一读取 VTable 单元格的渲染样式和图标。

    aspect 可选 all/render/icons；all 同时返回 render 和 icons。
    """
    aspect_key = str(aspect or "all").strip().lower()
    if aspect_key not in {"all", "render", "icons"}:
        return {"ok": False, "reason": "aspect 必须是 all/render/icons", "aspect": aspect_key}
    result = {"ok": True, "kind": "vtable", "aspect": aspect_key}
    if aspect_key in {"all", "render"}:
        result["render"] = get_vtable_cell_render_info(
            row=row, col=col, column_title=column_title, detail=detail,
        )
        result["ok"] = result["ok"] and bool(result["render"].get("ok"))
    if aspect_key in {"all", "icons"}:
        result["icons"] = get_vtable_cell_icons(
            row=row, col=col, column_title=column_title,
            icon_name=icon_name, detail=detail,
        )
        result["ok"] = result["ok"] and bool(result["icons"].get("ok"))
    return result


def table_action(action: Literal["click", "double_click", "hover", "drag", "resize"] = "click",
                 row: int = 0, col: int = None,
                 column_title: str = None,
                 kind: Literal["auto", "html", "vtable", "bootstrap"] = "auto",
                 table_index: int = 0,
                 target: Literal["cell", "header", "header-icon", "cell-icon"] = "cell",
                 icon_name: str = None, icon_index: int = None,
                 width: int = None, hover_first: bool = True,
                 duration: float = 0.3, drag_to_x: float = None,
                 drag_to_y: float = None, drag_by_x: float = None,
                 drag_by_y: float = None, clean_overlays: bool = True,
                 signals: list[str] = None, listen_targets: str = None,
                 timeout: float = 8, include_snapshot: bool = True,
                 detail: str = "summary",
                 drag_from_x: float = None, drag_from_y: float = None,
                 native_wait: bool = False) -> dict:
    """统一表格动作入口。

    kind: auto | html | vtable | bootstrap。
    action 可选 click/double_click/hover/drag/resize。普通单元格支持 HTML、
    Bootstrap Table 与 VTable；header/header-icon/cell-icon、drag 和 resize 仅支持 VTable。
    """
    action_key = str(action or "click").strip().lower().replace("-", "_")
    target_key = str(target or "cell").strip().lower().replace("_", "-")
    kind_key = _normalize_table_kind(kind)
    cleanup = (None if action_key == "hover" else pre_click_cleanup(clean_overlays))
    effective_signals = signals
    if effective_signals is None and listen_targets:
        effective_signals = ["overlay", "notification", "message", "tab", "url", "network"]
    observed = observe.observe_start(
        signals=effective_signals,
        listen_targets=listen_targets,
        native_wait=native_wait,
    )
    if action_key == "resize":
        if width is None:
            result = {"ok": False, "reason": "resize 必须提供 width", "action": action_key}
        else:
            result = resize_table_column(
                width=width, col=col, column_title=column_title, kind=kind_key,
            )
    elif action_key == "hover" and target_key == "cell" and icon_index is None:
        result = hover_table_cell(
            row=row, col=col, column_title=column_title, kind=kind_key,
            table_index=table_index, duration=duration,
        )
    elif action_key in {"click", "double_click"} and target_key == "cell" and icon_index is None:
        result = click_table_cell(
            row=row, col=col, column_title=column_title, kind=kind_key,
            table_index=table_index, icon_name=icon_name,
            hover_first=hover_first, duration=duration,
            double_click=action_key == "double_click",
            clean_overlays=False,
        )
    elif action_key in {"click", "double_click", "hover", "drag"}:
        if kind_key in {"html", "bootstrap"}:
            result = {
                "ok": False, "kind": kind_key, "action": action_key,
                "reason": "该 target/action 组合仅支持 VTable",
            }
        else:
            result = vtable_action(
                action=action_key, row=row, col=col, column_title=column_title,
                target=target_key, icon_name=icon_name, icon_index=icon_index,
                hover_first=hover_first, duration=duration,
                drag_to_x=drag_to_x, drag_to_y=drag_to_y,
                drag_by_x=drag_by_x, drag_by_y=drag_by_y,
                clean_overlays=False,
                source_x=drag_from_x, source_y=drag_from_y,
            )
    else:
        result = {
            "ok": False,
            "reason": "action 必须是 click/double_click/hover/drag/resize",
            "action": action_key,
        }
    result = attach_cleanup(result, cleanup)
    signal = observe.observe_wait(
        timeout=timeout if result.get("ok") else 0,
        include_snapshot=include_snapshot,
        detail=detail,
        native_wait=native_wait,
    )
    return {
        "ok": bool(result.get("ok")),
        "action": action_key,
        "target": target_key,
        "result": result,
        "observe_start": observed,
        "signal": signal,
    }


# ==================== VTable（canvas 表格）====================

def _action_disabled_diff(before: dict, after: dict) -> list:
    def key(item):
        label = (item.get("text") or item.get("title") or item.get("selectorHint") or "").strip()
        return (item.get("area") or "", label) if label else None

    before_map = {key(item): item for item in before.get("actions", []) if key(item)}
    after_map = {key(item): item for item in after.get("actions", []) if key(item)}
    changes = []
    for name, b in before_map.items():
        if name not in after_map:
            continue
        a = after_map[name]
        if bool(b.get("disabled")) != bool(a.get("disabled")):
            changes.append({
                "action": name[1],
                "before_disabled": bool(b.get("disabled")),
                "after_disabled": bool(a.get("disabled")),
                "area": a.get("area") or b.get("area"),
            })
    return changes


def scan_action_availability_by_selection(row: int = 0, col: int = 0,
                                          kind: str = "auto", table_index: int = 0,
                                          select_row: bool = True,
                                          wait_after_click: float = 0.3) -> dict:
    """扫描选中表格行前后工具栏按钮禁用态变化，用于批量/行选择场景设计。

    select_row=True 时会尝试点击 VTable 的 col,row 或 HTML 表格行复选框。
    """
    parsed = {}
    for name, value in (("row", row), ("col", col), ("table_index", table_index)):
        try:
            item = int(value)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "%s 必须为非负整数" % name}
        if (isinstance(value, float) and not value.is_integer()) or item < 0:
            return {"ok": False, "reason": "%s 必须为非负整数" % name}
        parsed[name] = item
    row, col, table_index = parsed["row"], parsed["col"], parsed["table_index"]
    try:
        wait_after_click = float(wait_after_click or 0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "wait_after_click 必须为非负数"}
    if not math.isfinite(wait_after_click) or wait_after_click < 0:
        return {"ok": False, "reason": "wait_after_click 必须为非负有限数值"}
    wait_after_click = min(wait_after_click, 30.0)
    before = page_model.scan_toolbar_actions(scope="all", max_items=160)
    if not before.get("ok"):
        return {"ok": False, "reason": "选择前工具栏扫描失败", "before": before,
                "mutated_page": False}
    select_result = {"ok": True, "skipped": True}
    post_selection_wait = {"ok": True, "skipped": True}
    if select_row:
        cleanup = pre_click_cleanup(True)
        table_kind = _normalize_table_kind(kind)
        if table_kind == "vtable":
            select_result = _tag_table_result("vtable", vtable.click_cell(col, row, hover_first=True))
        elif table_kind == "bootstrap":
            select_result = _tag_table_result(
                "bootstrap",
                bootstrap_table.click_bootstrap_row_selection(row=row, table_index=table_index),
            )
        elif table_kind == "html":
            select_result = _tag_table_result(
                "html", page_model.click_html_row_selection(row=row, table_index=table_index),
            )
        else:
            select_result = {"ok": False, "reason": "row selection failed"}
            for item in _auto_table_scan_order():
                if item == "vtable":
                    candidate = _tag_table_result(
                        "vtable", vtable.click_cell(col, row, hover_first=True),
                    )
                elif item == "bootstrap":
                    candidate = _tag_table_result(
                        "bootstrap",
                        bootstrap_table.click_bootstrap_row_selection(
                            row=row, table_index=table_index,
                        ),
                    )
                else:
                    candidate = _tag_table_result(
                        "html",
                        page_model.click_html_row_selection(row=row, table_index=table_index),
                    )
                if candidate.get("ok"):
                    select_result = candidate
                    break
                select_result = candidate
        select_result = attach_cleanup(select_result, cleanup)
        if select_result.get("ok"):
            if select_result.get("kind") == "vtable":
                post_selection_wait = vtable.wait_for_render_stable(timeout=max(wait_after_click, 0.1))
            else:
                target = browser_session.get_active_frame() or browser_session.get_tab()
                waited = target.wait.doc_loaded(timeout=max(wait_after_click, 0.1), raise_err=False)
                post_selection_wait = {"ok": waited is not False}
    after = page_model.scan_toolbar_actions(scope="all", max_items=160)
    return {
        "ok": bool(after.get("ok")
                   and (not select_row or (select_result.get("ok") and post_selection_wait.get("ok")))),
        "selection": select_result,
        "post_selection_wait": post_selection_wait,
        "changes": _action_disabled_diff(before, after),
        "before": before,
        "after": after,
        "mutated_page": bool(select_row and select_result.get("ok")),
        "state_note": "选中状态保留在页面中" if select_row and select_result.get("ok") else "页面选择状态未改变",
    }


