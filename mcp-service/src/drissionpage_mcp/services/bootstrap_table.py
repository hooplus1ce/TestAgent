"""Bootstrap Table 工具：处理遗留 jQuery 页的 .bootstrap-table（非 ant-table / VTable）。

典型 DOM（账号管理等）::

  .bootstrap-table
    ├── .fixed-table-toolbar   (搜索/刷新/列)
    ├── .fixed-table-container
    │   ├── .fixed-table-header > table > thead
    │   └── .fixed-table-body   > table > tbody > tr[data-index]
    └── .fixed-table-pagination
"""
from __future__ import annotations

import json
import logging

from ..core import ui_contract_legacy as legacy
from . import browser_session

logger = logging.getLogger("drissionpage-mcp")

_HELPER_JS = r"""
function duVisible(el) {
    if (!el || !el.isConnected) return false;
    var style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    var rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function duClean(t) { return (t || '').replace(/\s+/g, ' ').trim(); }
function duBtRoots() {
    var roots = [].slice.call(document.querySelectorAll('.bootstrap-table'));
    if (!roots.length) {
        // 少数页面只有 fixed-table-container
        roots = [].slice.call(document.querySelectorAll('.fixed-table-container'))
            .map(function(c){ return c.closest('.bootstrap-table') || c.parentElement || c; })
            .filter(Boolean);
    }
    var seen = [];
    roots.forEach(function(r){
        if (r && seen.indexOf(r) < 0 && duVisible(r)) seen.push(r);
    });
    return seen;
}
function duBtHasHeaderCells(table) {
    if (!table) return false;
    var ths = table.querySelectorAll('thead th');
    if (!ths.length) return false;
    // fixed-table-header 在未启用固定头时可能是空壳 display:none
    for (var i = 0; i < ths.length; i++) {
        var text = duClean(ths[i].textContent || '');
        if (text) return true;
        if (ths[i].querySelector('input[type="checkbox"]')) return true;
    }
    return false;
}
function duBtHeaderTable(root) {
    // 多数遗留页 thead 在 body 表内；fixed-table-header 常为空且 display:none
    var bodyTable = root.querySelector('.fixed-table-body table');
    if (duBtHasHeaderCells(bodyTable)) return bodyTable;
    var headerTable = root.querySelector('.fixed-table-header table');
    if (duBtHasHeaderCells(headerTable)) return headerTable;
    var any = root.querySelector('table');
    if (duBtHasHeaderCells(any)) return any;
    return bodyTable || headerTable || any;
}
function duBtBodyTable(root) {
    return root.querySelector('.fixed-table-body table') ||
        root.querySelector('.fixed-table-container table') ||
        root.querySelector('table');
}
function duBtLeafHeaders(headerTable) {
    if (!headerTable) return [];
    var rows = [].slice.call(headerTable.querySelectorAll('thead > tr'));
    if (!rows.length) return [];
    var grid = [];
    rows.forEach(function(tr, rowIndex) {
        if (!grid[rowIndex]) grid[rowIndex] = [];
        var column = 0;
        [].slice.call(tr.querySelectorAll(':scope > th')).forEach(function(th) {
            while (grid[rowIndex][column]) column++;
            var rowSpan = Math.max(parseInt(th.getAttribute('rowspan') || '1', 10), 1);
            var colSpan = Math.max(parseInt(th.getAttribute('colspan') || '1', 10), 1);
            for (var rr = rowIndex; rr < rowIndex + rowSpan; rr++) {
                if (!grid[rr]) grid[rr] = [];
                for (var cc = column; cc < column + colSpan; cc++) {
                    if (!grid[rr][cc]) grid[rr][cc] = th;
                }
            }
            column += colSpan;
        });
    });
    var leaf = grid.length ? grid[grid.length - 1] : [];
    return leaf.map(function(th, index) {
        var titleEl = th ? th.querySelector('.th-inner') : null;
        var title = duClean(titleEl ? titleEl.textContent : (th ? th.textContent : ''));
        // 复选框列表头常无文字
        if (!title && th && th.querySelector('input[type="checkbox"]')) {
            title = '__selection__';
        }
        return {index: index, title: title, element: th};
    });
}
function duBtBusinessRows(bodyTable) {
    if (!bodyTable) return [];
    var preferred = [].slice.call(bodyTable.querySelectorAll('tbody > tr[data-index]'));
    if (preferred.length) return preferred;
    return [].slice.call(bodyTable.querySelectorAll('tbody > tr')).filter(function(tr) {
        var cls = tr.className || '';
        if (cls.indexOf('no-records-found') >= 0) return false;
        if (String(tr.getAttribute('aria-hidden') || '').toLowerCase() === 'true') return false;
        // 跳过纯空占位行
        var tds = tr.querySelectorAll('td');
        return tds.length > 0;
    });
}
"""

_SCAN_JS = _HELPER_JS + r"""
return (function(){
    var roots = duBtRoots();
    var results = [];
    for (var ti = 0; ti < roots.length; ti++) {
        var root = roots[ti];
        var headerTable = duBtHeaderTable(root);
        var bodyTable = duBtBodyTable(root);
        var headers = duBtLeafHeaders(headerTable);
        var rows = duBtBusinessRows(bodyTable);
        var columns = [];
        headers.forEach(function(h) {
            if (!h.title || h.title === '__selection__') {
                columns.push({
                    index: h.index,
                    title: h.title === '__selection__' ? '' : h.title,
                    isSelection: h.title === '__selection__',
                    hasSorter: !!(h.element && h.element.querySelector('.sortable, .both, .asc, .desc')),
                    hasFilter: false
                });
                return;
            }
            columns.push({
                index: h.index,
                title: h.title,
                isSelection: false,
                hasSorter: !!(h.element && h.element.querySelector('.sortable, .both, .asc, .desc')),
                hasFilter: false
            });
        });
        var hasSelection = !!root.querySelector('input[name="btSelectItem"], input[name="btSelectAll"]');
        var pageSizeText = '';
        var pageBtn = root.querySelector('.page-list .dropdown-toggle, .fixed-table-pagination .dropdown-toggle');
        if (pageBtn) pageSizeText = duClean(pageBtn.textContent);
        var search = root.querySelector('.search input, .pull-right.search input, input.form-control[placeholder*="关键词"]');
        results.push({
            index: ti,
            kind: 'bootstrap',
            tableClass: root.className || '',
            columns: columns,
            rowCount: rows.length,
            hasPagination: !!root.querySelector('.fixed-table-pagination, .pagination'),
            hasRowSelection: hasSelection,
            hasToolbar: !!root.querySelector('.fixed-table-toolbar, .btn-group'),
            pageSizeText: pageSizeText,
            searchPlaceholder: search ? (search.getAttribute('placeholder') || '') : '',
            hasFixedHeader: !!root.querySelector('.fixed-table-header')
        });
    }
    return JSON.stringify(results);
})();
"""


def _nonnegative(value, name):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, "%s 必须为非负整数" % name
    if isinstance(value, float) and not value.is_integer():
        return None, "%s 必须为非负整数" % name
    if parsed < 0:
        return None, "%s 必须为非负整数" % name
    return parsed, None


def _get_frame():
    try:
        fr = browser_session.get_active_frame_ro()
        if fr is not None:
            return fr
    except Exception:
        pass
    return browser_session.get_active_frame()


def _run_json(fr, js: str):
    res = fr.run_js(js)
    if res is None:
        return None
    if isinstance(res, str):
        try:
            return json.loads(res)
        except (TypeError, ValueError):
            return res
    return res


def scan_bootstrap_table():
    """扫描页面可见 Bootstrap Table，返回列定义与元数据。"""
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe，请先 enter_module"}
    try:
        tables = _run_json(fr, _SCAN_JS)
        if tables is None:
            return {"ok": False, "reason": "scan 返回 null，可能是 max_chars 截断或 JS 异常"}
        if not isinstance(tables, list):
            return {"ok": False, "reason": "扫描返回非列表: %r" % (tables,)}
        return {
            "ok": True,
            "kind": "bootstrap",
            "tables": tables,
            "contract": {
                "name": legacy.CONTRACT_NAME,
                "version": legacy.CONTRACT_VERSION,
            },
        }
    except Exception as e:
        logger.exception("scan_bootstrap_table 失败")
        return {"ok": False, "reason": str(e)}


_GET_VALUES_JS = _HELPER_JS + r"""
return (function(){
    var COL_TITLE = %s;
    var TABLE_INDEX = %d;
    var roots = duBtRoots();
    var root = roots[TABLE_INDEX];
    if (!root) return JSON.stringify({error: 'visible bootstrap-table not found at index ' + TABLE_INDEX});
    var headerTable = duBtHeaderTable(root);
    var matches = duBtLeafHeaders(headerTable).filter(function(h) {
        return h.title === COL_TITLE;
    });
    if (matches.length !== 1) {
        return JSON.stringify({error: matches.length ?
            'column title is ambiguous: ' + COL_TITLE : 'column not found: ' + COL_TITLE});
    }
    var colIdx = matches[0].index;
    var bodyTable = duBtBodyTable(root);
    var rows = duBtBusinessRows(bodyTable);
    var values = [];
    var cells = [];
    for (var r = 0; r < rows.length; r++) {
        var tds = rows[r].querySelectorAll('td');
        if (colIdx >= tds.length) continue;
        var td = tds[colIdx];
        var text = duClean(td.textContent || '');
        values.push(text);
        cells.push({
            row: r,
            text: text,
            className: td.className || '',
            hasLink: !!td.querySelector('a'),
            hasButton: !!td.querySelector('button'),
            hasInput: !!td.querySelector('input'),
            dataIndex: rows[r].getAttribute('data-index')
        });
    }
    return JSON.stringify({values: values, cells: cells, column: COL_TITLE, count: values.length});
})();
"""


def get_bootstrap_table_values(column_title: str, table_index: int = 0):
    column_title = str(column_title or "").strip()
    if not column_title:
        return {"ok": False, "reason": "column_title 不能为空"}
    table_index, reason = _nonnegative(table_index, "table_index")
    if reason:
        return {"ok": False, "reason": reason}
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}
    js = _GET_VALUES_JS % (json.dumps(column_title, ensure_ascii=False), table_index)
    try:
        data = _run_json(fr, js)
        if not isinstance(data, dict):
            return {"ok": False, "reason": "返回值非 dict: %r" % (data,)}
        if "error" in data:
            return {"ok": False, "reason": data["error"]}
        return {"ok": True, "kind": "bootstrap", **data}
    except Exception as e:
        logger.debug("get_bootstrap_table_values 失败: %s", e)
        return {"ok": False, "reason": str(e)}


_GET_DATA_JS = _HELPER_JS + r"""
return (function(){
    var TI = %d;
    var roots = duBtRoots();
    var root = roots[TI];
    if (!root) return JSON.stringify({error: 'visible bootstrap-table not found at index ' + TI});
    var headerTable = duBtHeaderTable(root);
    var headers = duBtLeafHeaders(headerTable).map(function(h) {
        return h.title === '__selection__' ? '' : h.title;
    });
    var bodyTable = duBtBodyTable(root);
    var rows = duBtBusinessRows(bodyTable);
    var data = [];
    rows.forEach(function(tr) {
        var cells = tr.querySelectorAll('td');
        var rowData = [];
        for (var i = 0; i < cells.length; i++) {
            rowData.push(duClean(cells[i].textContent || ''));
        }
        data.push(rowData);
    });
    return JSON.stringify({headers: headers, rows: data});
})();
"""


def get_bootstrap_table_data(table_index: int = 0):
    table_index, reason = _nonnegative(table_index, "table_index")
    if reason:
        return {"ok": False, "reason": reason}
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}
    try:
        result = _run_json(fr, _GET_DATA_JS % table_index)
        if not isinstance(result, dict):
            return {"ok": False, "reason": "读取表格返回非对象: %r" % (result,)}
        if "error" in result:
            return {"ok": False, "reason": "读取表格失败: %s" % result.get("error")}
        return {
            "ok": True,
            "kind": "bootstrap",
            "headers": result.get("headers") or [],
            "rows": result.get("rows") or [],
            "count": len(result.get("rows") or []),
        }
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def _resolve_col(fr, column_title: str, table_index: int) -> dict:
    js = _HELPER_JS + r"""
return (function(){
    var COL_TITLE = %s;
    var TI = %d;
    var roots = duBtRoots();
    var root = roots[TI];
    if (!root) return JSON.stringify({error: 'table not found'});
    var headers = duBtLeafHeaders(duBtHeaderTable(root));
    var matches = [];
    headers.forEach(function(h){
        if (h.title === COL_TITLE) matches.push(h.index);
    });
    if (matches.length !== 1) {
        return JSON.stringify({error: matches.length ?
            'column title is ambiguous: ' + COL_TITLE : 'column not found: ' + COL_TITLE,
            columns: headers.map(function(h){ return h.title; })});
    }
    return JSON.stringify({ok:true, colIdx: matches[0], rowCount: duBtBusinessRows(duBtBodyTable(root)).length});
})();
""" % (json.dumps(column_title, ensure_ascii=False), table_index)
    data = _run_json(fr, js)
    if not isinstance(data, dict):
        return {"ok": False, "reason": "列解析失败"}
    if data.get("error"):
        return {"ok": False, "reason": data["error"], "columns": data.get("columns")}
    return {"ok": True, "colIdx": data["colIdx"], "rowCount": data.get("rowCount", 0)}


def click_bootstrap_table_cell(column_title: str, row: int, table_index: int = 0):
    """点击 Bootstrap Table 单元格；优先内部 a/button。"""
    row, reason = _nonnegative(row, "row")
    if reason:
        return {"ok": False, "reason": reason}
    table_index, reason = _nonnegative(table_index, "table_index")
    if reason:
        return {"ok": False, "reason": reason}
    column_title = str(column_title or "").strip()
    if not column_title:
        return {"ok": False, "reason": "column_title 不能为空"}
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}
    resolved = _resolve_col(fr, column_title, table_index)
    if not resolved.get("ok"):
        return resolved
    col_idx = resolved["colIdx"]
    # 用 CSS nth 定位可见业务行
    js_locate = _HELPER_JS + r"""
return (function(){
    var TI = %d, ROW = %d, COL = %d;
    var roots = duBtRoots();
    var root = roots[TI];
    if (!root) return JSON.stringify({error:'table not found'});
    var rows = duBtBusinessRows(duBtBodyTable(root));
    if (ROW >= rows.length) return JSON.stringify({error:'row not found: ' + ROW});
    var tds = rows[ROW].querySelectorAll('td');
    if (COL >= tds.length) return JSON.stringify({error:'cell not found'});
    var td = tds[COL];
    var clickable = td.querySelector('a,button') || td;
    var r = clickable.getBoundingClientRect();
    // 标记以便 DP 侧二次定位
    clickable.setAttribute('data-du-bt-click', '1');
    td.setAttribute('data-du-bt-td', '1');
    return JSON.stringify({
        ok:true,
        text: duClean(td.textContent||''),
        cx: Math.round((r.left + r.width/2)*10)/10,
        cy: Math.round((r.top + r.height/2)*10)/10
    });
})();
""" % (table_index, row, col_idx)
    try:
        meta = _run_json(fr, js_locate)
        if not isinstance(meta, dict) or meta.get("error"):
            return {"ok": False, "reason": (meta or {}).get("error", "定位单元格失败")}
        target = fr.ele("css:[data-du-bt-click='1']", timeout=1) or fr.ele(
            "css:[data-du-bt-td='1']", timeout=1
        )
        if not target:
            # 回退坐标点击
            tab = browser_session.get_tab()
            tab.actions.move_to(meta["cx"], meta["cy"]).click()
            return {
                "ok": True,
                "kind": "bootstrap",
                "row": row,
                "column": column_title,
                "colIdx": col_idx,
                "element": "xy",
                "centerX": meta["cx"],
                "centerY": meta["cy"],
            }
        try:
            tag = target.tag
            midpoint = target.rect.viewport_midpoint
            target.click(by_js=False, timeout=2)
        except TypeError:
            target.click()
            tag = getattr(target, "tag", "unknown")
            midpoint = (meta["cx"], meta["cy"])
        finally:
            try:
                fr.run_js(
                    "document.querySelectorAll('[data-du-bt-click],[data-du-bt-td]')"
                    ".forEach(function(e){e.removeAttribute('data-du-bt-click');"
                    "e.removeAttribute('data-du-bt-td');});"
                )
            except Exception:
                pass
        return {
            "ok": True,
            "kind": "bootstrap",
            "row": row,
            "column": column_title,
            "colIdx": col_idx,
            "element": tag,
            "centerX": midpoint[0],
            "centerY": midpoint[1],
        }
    except Exception as e:
        return {"ok": False, "reason": "点击失败: %s" % e}


def hover_bootstrap_table_cell(
    column_title: str, row: int, table_index: int = 0, duration: float = 0.3,
) -> dict:
    """悬停 Bootstrap Table 单元格。"""
    row, reason = _nonnegative(row, "row")
    if reason:
        return {"ok": False, "reason": reason}
    table_index, reason = _nonnegative(table_index, "table_index")
    if reason:
        return {"ok": False, "reason": reason}
    column_title = str(column_title or "").strip()
    if not column_title:
        return {"ok": False, "reason": "column_title 不能为空"}
    try:
        duration = min(max(float(duration or 0), 0.0), 10.0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "duration 必须为非负数"}
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}
    resolved = _resolve_col(fr, column_title, table_index)
    if not resolved.get("ok"):
        return resolved
    col_idx = resolved["colIdx"]
    js_locate = _HELPER_JS + r"""
return (function(){
    var TI = %d, ROW = %d, COL = %d;
    var roots = duBtRoots();
    var root = roots[TI];
    if (!root) return JSON.stringify({error:'table not found'});
    var rows = duBtBusinessRows(duBtBodyTable(root));
    if (ROW >= rows.length) return JSON.stringify({error:'row not found: ' + ROW});
    var tds = rows[ROW].querySelectorAll('td');
    if (COL >= tds.length) return JSON.stringify({error:'cell not found'});
    var td = tds[COL];
    var first = tds[0] || td;
    td.setAttribute('data-du-bt-hover', '1');
    first.setAttribute('data-du-bt-hover-first', '1');
    var r = td.getBoundingClientRect();
    var fr = first.getBoundingClientRect();
    return JSON.stringify({
        ok:true,
        text: duClean(td.textContent||''),
        cx: Math.round((r.left + r.width/2)*10)/10,
        cy: Math.round((r.top + r.height/2)*10)/10,
        fromX: Math.round((fr.left + fr.width/2)*10)/10,
        fromY: Math.round((fr.top + fr.height/2)*10)/10
    });
})();
""" % (table_index, row, col_idx)
    try:
        meta = _run_json(fr, js_locate)
        if not isinstance(meta, dict) or meta.get("error"):
            return {"ok": False, "reason": (meta or {}).get("error", "定位单元格失败")}
        target_td = fr.ele("css:[data-du-bt-hover='1']", timeout=1)
        first_td = fr.ele("css:[data-du-bt-hover-first='1']", timeout=0.5) or target_td
        if not target_td:
            return {"ok": False, "reason": "单元格元素未找到"}
        try:
            fr.actions.move_to(first_td, duration=0).move_to(target_td, duration=duration)
            midpoint = target_td.rect.viewport_midpoint
            first_mid = first_td.rect.viewport_midpoint
        except Exception as exc:
            return {"ok": False, "reason": "悬停失败: %s" % exc}
        finally:
            try:
                fr.run_js(
                    "document.querySelectorAll('[data-du-bt-hover],[data-du-bt-hover-first]')"
                    ".forEach(function(e){e.removeAttribute('data-du-bt-hover');"
                    "e.removeAttribute('data-du-bt-hover-first');});"
                )
            except Exception:
                pass
        return {
            "ok": True,
            "kind": "bootstrap",
            "row": row,
            "column": column_title,
            "colIdx": col_idx,
            "viewportX": midpoint[0],
            "viewportY": midpoint[1],
            "fromX": first_mid[0],
            "fromY": first_mid[1],
        }
    except Exception as e:
        return {"ok": False, "reason": "悬停失败: %s" % e}


def click_bootstrap_row_selection(row: int = 0, table_index: int = 0) -> dict:
    """勾选 Bootstrap Table 行复选框（btSelectItem）。"""
    row, reason = _nonnegative(row, "row")
    if reason:
        return {"ok": False, "reason": reason}
    table_index, reason = _nonnegative(table_index, "table_index")
    if reason:
        return {"ok": False, "reason": reason}
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}
    js = _HELPER_JS + r"""
return (function(){
    var TI = %d, ROW = %d;
    var roots = duBtRoots();
    var root = roots[TI];
    if (!root) return JSON.stringify({error:'table not found'});
    var rows = duBtBusinessRows(duBtBodyTable(root));
    if (ROW >= rows.length) return JSON.stringify({error:'row not found: ' + ROW});
    var cb = rows[ROW].querySelector('input[name="btSelectItem"], input[type="checkbox"]');
    if (!cb) return JSON.stringify({error:'row checkbox not found'});
    cb.setAttribute('data-du-bt-select', '1');
    return JSON.stringify({ok:true, checked: !!cb.checked, dataIndex: rows[ROW].getAttribute('data-index')});
})();
""" % (table_index, row)
    try:
        meta = _run_json(fr, js)
        if not isinstance(meta, dict) or meta.get("error"):
            return {"ok": False, "reason": (meta or {}).get("error", "定位复选框失败")}
        cb = fr.ele("css:input[data-du-bt-select='1']", timeout=1)
        if not cb:
            return {"ok": False, "reason": "row checkbox element not found"}
        try:
            if not cb.wait.clickable(timeout=1.0, raise_err=False):
                # bootstrap-table 有时要点父级
                parent = fr.ele("css:td.bs-checkbox input[data-du-bt-select='1']", timeout=0.2)
                (parent or cb).click(by_js=True, timeout=2)
            else:
                cb.click(by_js=False, timeout=2)
        except Exception:
            cb.click(by_js=True, timeout=2)
        try:
            fr.run_js(
                "document.querySelectorAll('[data-du-bt-select]')"
                ".forEach(function(e){e.removeAttribute('data-du-bt-select');});"
            )
        except Exception:
            pass
        return {"ok": True, "kind": "bootstrap", "row": row, "table_index": table_index}
    except Exception as e:
        return {"ok": False, "reason": "row checkbox click failed: %s" % e}
