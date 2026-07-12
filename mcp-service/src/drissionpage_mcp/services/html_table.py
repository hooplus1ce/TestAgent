"""HTML 表格工具：处理 ant-design 原生 HTML 表格（非 VTable canvas 表格）。

ant-table 结构:
  .ant-table-wrapper > .ant-spin-nested-loading > .ant-spin-container > .ant-table
    ├── .ant-table-content > .ant-table-scroll
    │   ├── .ant-table-header > table > thead > tr > th   (列标题)
    │   ├── .ant-table-body   > table > tbody > tr > td    (数据行)
    │   ├── .ant-table-fixed-left  (固定左侧列，可选)
    │   └── .ant-table-fixed-right (固定右侧列，可选)
    └── .ant-pagination (分页，可选)
"""
import json
import logging

from . import browser_session

logger = logging.getLogger("drissionpage-mcp")

_HEADER_MAP_JS = r"""
function duLeafHeaders(headerTable) {
    if (!headerTable) return [];
    var rows = [].slice.call(headerTable.querySelectorAll('thead > tr'));
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
        return {index: index, title: (th.textContent || '').trim(), element: th};
    });
}
function duVisible(el) {
    if (!el || !el.isConnected) return false;
    var style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    var rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    var left = Math.max(0, rect.left), right = Math.min(window.innerWidth, rect.right);
    var top = Math.max(0, rect.top), bottom = Math.min(window.innerHeight, rect.bottom);
    if (right <= left || bottom <= top) return false;
    var hit = document.elementFromPoint((left + right) / 2, (top + bottom) / 2);
    return !!(hit && (hit === el || el.contains(hit)));
}
function duBusinessRows(bodyTable) {
    if (!bodyTable) return [];
    var preferred = [].slice.call(bodyTable.querySelectorAll('tbody > tr.ant-table-row'));
    if (preferred.length) return preferred;
    return [].slice.call(bodyTable.querySelectorAll('tbody > tr')).filter(function(tr) {
        var cls = tr.className || '';
        return cls.indexOf('ant-table-placeholder') < 0 &&
               cls.indexOf('ant-table-expanded-row') < 0 &&
               cls.indexOf('ant-table-measure-row') < 0 &&
               String(tr.getAttribute('aria-hidden') || '').toLowerCase() !== 'true';
    });
}
"""


# JS 注入脚本：扫描 HTML 表格
_SCAN_JS = _HEADER_MAP_JS + r"""
return (function(){
    var tables = [].slice.call(document.querySelectorAll('.ant-table-wrapper')).filter(duVisible);
    var results = [];
    for (var ti = 0; ti < tables.length; ti++) {
        var wrapper = tables[ti];
        var tableEl = wrapper.querySelector('.ant-table');
        if (!tableEl) continue;

        var entry = {
            index: ti,
            tableClass: tableEl.className,
            hasPagination: !!wrapper.querySelector('.ant-pagination'),
            hasFixedHeader: tableEl.classList.contains('ant-table-fixed-header'),
            isBordered: tableEl.classList.contains('ant-table-bordered'),
            columns: [],
            rowCount: 0,
            hasRowSelection: false,
            hasExpandIcon: false,
        };

        // ---- columns ----
        var headerTable =
            tableEl.querySelector('.ant-table-header table') ||
            tableEl.querySelector('.ant-table-body table') ||
            tableEl.querySelector('.ant-table-content table');
        duLeafHeaders(headerTable).forEach(function(header) {
            var th = header.element;
            var title = header.title;
            if (!title) return;
            var cls = th.className || '';
            var align = 'left';
            if (cls.indexOf('text-align-center') >= 0) align = 'center';
            else if (cls.indexOf('text-align-right') >= 0) align = 'right';
            entry.columns.push({
                index: header.index,
                title: title,
                alignment: align,
                hasSorter: !!th.querySelector('.ant-table-column-sorter'),
                hasFilter: !!th.querySelector('.ant-dropdown-trigger'),
            });
        });

        // ---- rows ----
        var bodyTable = tableEl.querySelector('.ant-table-body table');
        if (!bodyTable) bodyTable = tableEl.querySelector('.ant-table-content table');
        if (bodyTable) {
            var rows = duBusinessRows(bodyTable);
            entry.rowCount = rows.length;

            if (rows[0]) {
                entry.hasExpandIcon = !!rows[0].querySelector('.ant-table-row-expand-icon');
                entry.hasRowSelection = !!bodyTable.querySelector('.ant-checkbox-wrapper');
            }
        }

        // ---- fixed columns ----
        entry.hasFixedLeft = !!tableEl.querySelector('.ant-table-fixed-left');
        entry.hasFixedRight = !!tableEl.querySelector('.ant-table-fixed-right');

        results.push(entry);
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
    """取活动 frame（优先只读，失败回退到加锁版本）。"""
    try:
        fr = browser_session.get_active_frame_ro()
        if fr is not None:
            return fr
    except Exception:
        pass
    return browser_session.get_active_frame()


def scan_html_table():
    """扫描页面所有 ant-design HTML 表格，返回列定义和元数据。

    返回:
      {ok, tables: [{index, columns:[{index, title, alignment, hasSorter, hasFilter}],
                     rowCount, hasPagination, hasFixedHeader, hasRowSelection, ...}]}
    """
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe，请先 enter_module"}

    try:
        res = fr.run_js(_SCAN_JS)
        if res is None:
            return {"ok": False, "reason": "scan 返回 null，可能是 max_chars 截断或 JS 异常"}
        tables = json.loads(res) if isinstance(res, str) else res
        if not isinstance(tables, list):
            return {"ok": False, "reason": "扫描返回非列表: %r" % (res,)}
        return {"ok": True, "tables": tables}
    except Exception as e:
        logger.exception("scan_html_table 失败")
        return {"ok": False, "reason": str(e)}


_GET_VALUES_JS = _HEADER_MAP_JS + r"""
return (function(){
    var COL_TITLE = %s;
    var TABLE_INDEX = %d;

    var wrappers = [].slice.call(document.querySelectorAll('.ant-table-wrapper')).filter(duVisible);
    var wrapper = wrappers[TABLE_INDEX];
    if (!wrapper) return JSON.stringify({error: 'visible table not found at index ' + TABLE_INDEX});

    var tableEl = wrapper.querySelector('.ant-table');
    if (!tableEl) return JSON.stringify({error: 'no .ant-table inside wrapper'});

    // find column index by title
    var headerTable =
        tableEl.querySelector('.ant-table-header table') ||
        tableEl.querySelector('.ant-table-body table') ||
        tableEl.querySelector('.ant-table-content table');
    if (!headerTable) return JSON.stringify({error: 'no header table'});

    var matches = duLeafHeaders(headerTable).filter(function(header) {
        return header.title === COL_TITLE;
    });
    if (matches.length !== 1) {
        return JSON.stringify({error: matches.length ?
            'column title is ambiguous: ' + COL_TITLE : 'column not found: ' + COL_TITLE});
    }
    var colIdx = matches[0].index;

    // get body rows
    var bodyTable = tableEl.querySelector('.ant-table-body table');
    if (!bodyTable) bodyTable = tableEl.querySelector('.ant-table-content table');
    if (!bodyTable) return JSON.stringify({error: 'no body table'});

    var rows = duBusinessRows(bodyTable);

    var values = [];
    var cells = [];
    for (var r = 0; r < rows.length; r++) {
        var tds = rows[r].querySelectorAll('td');
        if (colIdx >= tds.length) continue;
        var td = tds[colIdx];

        var cell = {
            row: r,
            text: (td.textContent || '').trim(),
            className: td.className || '',
            hasLink: !!td.querySelector('a'),
            hasButton: !!td.querySelector('button'),
            hasPopover: !!td.querySelector('.ant-popover-trigger'),
            hasInput: !!td.querySelector('input'),
        };
        values.push(cell.text);
        cells.push(cell);
    }
    return JSON.stringify({values: values, cells: cells, column: COL_TITLE, count: values.length});
})();
"""


def get_html_table_values(column_title: str, table_index: int = 0):
    """按列标题获取 HTML 表格中该列所有单元格值。

    Args:
        column_title: 列标题文字（精确匹配）
        table_index: 表格索引（页面有多个 ant-table 时指定，默认 0）

    Returns:
        {ok, values: [str], cells: [{row, text, hasLink, ...}], column, count}
    """
    column_title = str(column_title or "").strip()
    if not column_title:
        return {"ok": False, "reason": "column_title 不能为空"}
    table_index, reason = _nonnegative(table_index, "table_index")
    if reason:
        return {"ok": False, "reason": reason}
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}

    js_title = json.dumps(column_title, ensure_ascii=False)
    js = _GET_VALUES_JS % (js_title, table_index)

    try:
        res = fr.run_js(js)
        data = json.loads(res) if isinstance(res, str) else res
        if not isinstance(data, dict):
            return {"ok": False, "reason": "返回值非 dict: %r" % (data,)}
        if "error" in data:
            return {"ok": False, "reason": data["error"]}
        return {"ok": True, **data}
    except Exception as e:
        logger.debug("get_html_table_values 失败: %s", e)
        return {"ok": False, "reason": str(e)}



def _visible_table_wrappers(fr):
    """用页面命中测试选出未被浮层遮挡的 wrapper，再映射为 DrissionPage 元素。"""
    js = _HEADER_MAP_JS + r"""
var wrappers = [].slice.call(document.querySelectorAll('.ant-table-wrapper'));
var indexes = [];
wrappers.forEach(function(wrapper, index) { if (duVisible(wrapper)) indexes.push(index); });
return JSON.stringify(indexes);
"""
    try:
        raw = fr.run_js(js)
        indexes = json.loads(raw) if isinstance(raw, str) else raw
        wrappers = fr.eles('c:.ant-table-wrapper', timeout=1) or []
        if not isinstance(indexes, list):
            return []
        return [wrappers[index] for index in indexes
                if isinstance(index, int) and 0 <= index < len(wrappers)]
    except Exception as exc:
        logger.debug("读取可见 HTML 表格失败: %s", exc)
        return []


def _table_wrapper(fr, table_index: int):
    table_index, reason = _nonnegative(table_index, "table_index")
    if reason:
        return None
    wrappers = _visible_table_wrappers(fr)
    return wrappers[table_index] if table_index < len(wrappers) else None


def _business_rows(wrapper):
    body_table = (
        wrapper.ele('c:.ant-table-body table', timeout=0.5)
        or wrapper.ele('c:.ant-table-content table', timeout=0.5)
    )
    if not body_table:
        return []
    rows = body_table.eles('css:tbody > tr.ant-table-row', timeout=0.5) or []
    if rows:
        return rows
    return body_table.eles(
        'css:tbody > tr:not(.ant-table-placeholder):not(.ant-table-expanded-row):not(.ant-table-measure-row):not([aria-hidden="true"])',
        timeout=0.5,
    ) or []


def _get_col_indices(column_title: str, table_index: int, fr) -> dict:
    """用 DrissionPage 元素 API 展开 rowSpan/colSpan 后解析唯一叶子列。"""
    wrapper = _table_wrapper(fr, table_index)
    if not wrapper:
        return {"ok": False, "reason": "table not found at index %s" % table_index}
    header_table = (
        wrapper.ele('c:.ant-table-header table', timeout=0.5)
        or wrapper.ele('c:.ant-table-body table', timeout=0.5)
        or wrapper.ele('c:.ant-table-content table', timeout=0.5)
    )
    if not header_table:
        return {"ok": False, "reason": "header table not found"}
    header_rows = header_table.eles('css:thead > tr', timeout=0.5) or []
    grid = []
    for row_index, header_row in enumerate(header_rows):
        while len(grid) <= row_index:
            grid.append([])
        column = 0
        for header in header_row.eles('t:th', timeout=0.5) or []:
            while column < len(grid[row_index]) and grid[row_index][column] is not None:
                column += 1
            attrs = header.attrs or {}
            try:
                row_span = max(int(attrs.get("rowspan") or 1), 1)
                col_span = max(int(attrs.get("colspan") or 1), 1)
            except (TypeError, ValueError):
                row_span = col_span = 1
            while len(grid) < row_index + row_span:
                grid.append([])
            for target_row in range(row_index, row_index + row_span):
                if len(grid[target_row]) < column + col_span:
                    grid[target_row].extend([None] * (column + col_span - len(grid[target_row])))
                for target_col in range(column, column + col_span):
                    if grid[target_row][target_col] is None:
                        grid[target_row][target_col] = header
            column += col_span
    leaf_headers = grid[-1] if grid else []
    expected = str(column_title or "").strip()
    matches = [
        index for index, header in enumerate(leaf_headers)
        if header and (header.text or "").strip() == expected
    ]
    if len(matches) != 1:
        return {"ok": False, "reason": (
            "column title is ambiguous: %s" if matches else "column not found: %s"
        ) % expected}
    first = next((
        index for index, header in enumerate(leaf_headers)
        if header and (header.text or "").strip()
    ), matches[0])
    return {"ok": True, "target": matches[0], "first": first, "wrapper": wrapper}


def _get_td(wrapper, row: int, col_idx: int):
    """在指定可见表格 wrapper 内定位业务行单元格，排除占位与展开行。"""
    if row < 0 or col_idx < 0:
        return None
    rows = _business_rows(wrapper)
    if row >= len(rows):
        return None
    cells = rows[row].eles('t:td', timeout=0.5) or []
    return cells[col_idx] if col_idx < len(cells) else None


def click_html_table_cell(column_title: str, row: int, table_index: int = 0):
    """点击 HTML 表格中指定单元格。优先点击单元格内的 <a> 或 <button>，否则点 td 本身。

    Args:
        column_title: 列标题文字（精确匹配）
        row: 行索引（0-based，跳过表头）
        table_index: 表格索引（页面有多个 ant-table 时指定，默认 0）

    Returns:
        {ok, row, column, colIdx, element, centerX, centerY}
    """
    row, reason = _nonnegative(row, "row")
    if reason:
        return {"ok": False, "reason": reason}
    table_index, reason = _nonnegative(table_index, "table_index")
    if reason:
        return {"ok": False, "reason": reason}
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}

    # 1. 找列索引
    idx = _get_col_indices(column_title, table_index, fr)
    if not idx["ok"]:
        return idx
    col_idx = idx["target"]

    # 2. 定位 td
    td = _get_td(idx["wrapper"], row, col_idx)
    if not td:
        return {"ok": False, "reason": "单元格未找到: row=%d col=%d" % (row, col_idx)}

    # 先取几何与标签；点击可能让元素随导航/弹窗更新而失效。
    target_ele = td.ele('t:a', timeout=0) or td.ele('t:button', timeout=0) or td
    try:
        tag = target_ele.tag
        midpoint = target_ele.rect.viewport_midpoint
        target_ele.click(by_js=False, timeout=2)
    except TypeError:
        try:
            target_ele.click()
        except Exception as exc:
            return {"ok": False, "reason": "点击失败: %s" % exc}
    except Exception as exc:
        return {"ok": False, "reason": "点击失败: %s" % exc}
    return {
        "ok": True, "row": row, "column": column_title, "colIdx": col_idx,
        "element": tag, "centerX": midpoint[0], "centerY": midpoint[1],
    }


def hover_html_table_cell(column_title: str, row: int, table_index: int = 0,
                          duration: float = 0.3):
    """通过 DrissionPage 动作链从行首移动到目标 HTML 表格单元格。"""
    row, reason = _nonnegative(row, "row")
    if reason:
        return {"ok": False, "reason": reason}
    table_index, reason = _nonnegative(table_index, "table_index")
    if reason:
        return {"ok": False, "reason": reason}
    try:
        duration = min(max(float(duration or 0), 0.0), 10.0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "duration 必须为非负数"}
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}
    idx = _get_col_indices(column_title, table_index, fr)
    if not idx["ok"]:
        return idx
    target_td = _get_td(idx["wrapper"], row, idx["target"])
    first_td = _get_td(idx["wrapper"], row, idx["first"])
    if not target_td or not first_td:
        return {"ok": False, "reason": "单元格未找到: row=%d" % row}
    try:
        fr.actions.move_to(first_td, duration=0).move_to(target_td, duration=duration)
        target_midpoint = target_td.rect.viewport_midpoint
        first_midpoint = first_td.rect.viewport_midpoint
    except Exception as exc:
        return {"ok": False, "reason": "悬停失败: %s" % exc}
    return {
        "ok": True, "row": row, "column": column_title,
        "viewportX": target_midpoint[0], "viewportY": target_midpoint[1],
        "fromX": first_midpoint[0], "fromY": first_midpoint[1],
    }


def click_html_row_selection(row: int = 0, table_index: int = 0) -> dict:
    """点击可见 HTML 表格业务行的复选框，不允许负索引落到末行。"""
    row, reason = _nonnegative(row, "row")
    if reason:
        return {"ok": False, "reason": reason}
    table_index, reason = _nonnegative(table_index, "table_index")
    if reason:
        return {"ok": False, "reason": reason}
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}
    wrapper = _table_wrapper(fr, table_index)
    if not wrapper:
        return {"ok": False, "reason": "visible table not found at index %s" % table_index}
    rows = _business_rows(wrapper)
    if row >= len(rows):
        return {"ok": False, "reason": "row not found: %s" % row}
    tr = rows[row]
    checkbox = (
        tr.ele('c:.ant-checkbox-wrapper', timeout=0)
        or tr.ele('c:.ant-checkbox', timeout=0)
        or tr.ele('css:input[type="checkbox"]', timeout=0)
    )
    if not checkbox:
        return {"ok": False, "reason": "row checkbox not found"}
    try:
        if not checkbox.wait.clickable(timeout=1.0, raise_err=False):
            return {"ok": False, "reason": "row checkbox not clickable"}
        checkbox.click(by_js=False, timeout=2)
        wrapper.wait.stop_moving(timeout=2, raise_err=False)
    except TypeError:
        try:
            checkbox.click()
        except Exception as exc:
            return {"ok": False, "reason": "row checkbox click failed: %s" % exc}
    except Exception as exc:
        return {"ok": False, "reason": "row checkbox click failed: %s" % exc}
    return {"ok": True, "row": row, "table_index": table_index}

def get_html_table_data(table_index: int = 0):
    """从 DOM 读取 HTML 表格的完整数据（表头 + 所有行）。

    列名直接从 <thead> <th> 读取，数据从 <tbody> <tr> 读取，
    列名和数据按 DOM 顺序一一对应，不存在人工对齐错误。
    table_index 指定第几个表格（从 0 开始），默认 0。

    Returns:
        {ok, headers: [str], rows: [[str, ...]], count}
    """
    table_index, reason = _nonnegative(table_index, "table_index")
    if reason:
        return {"ok": False, "reason": reason}
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}

    js = _HEADER_MAP_JS + r"""return (function(){
        var TI = %d;
        var wrappers = [].slice.call(document.querySelectorAll('.ant-table-wrapper')).filter(duVisible);
        var wrapper = wrappers[TI];
        if (!wrapper) return JSON.stringify({error: 'visible table not found at index ' + TI});
        var tableEl = wrapper.querySelector('.ant-table');
        if (!tableEl) return JSON.stringify({error: 'ant-table not found'});
        var headerTable = tableEl.querySelector('.ant-table-header table') ||
            tableEl.querySelector('.ant-table-body table') ||
            tableEl.querySelector('.ant-table-content table');
        if (!headerTable) return JSON.stringify({error: 'header table not found'});
        var headers = duLeafHeaders(headerTable).map(function(header) { return header.title; });
        var bodyTable = tableEl.querySelector('.ant-table-body table') || tableEl.querySelector('.ant-table-content table');
        if (!bodyTable) return JSON.stringify({error: 'body table not found'});
        var rows = duBusinessRows(bodyTable);
        var data = [];
        rows.forEach(function(tr) {
            var cells = tr.querySelectorAll('td');
            var rowData = [];
            cells.forEach(function(td) { rowData.push((td.textContent || '').trim()); });
            data.push(rowData);
        });
        return JSON.stringify({headers: headers, rows: data});
    })();""" % (table_index,)

    try:
        res = fr.run_js(js)
        result = json.loads(res) if isinstance(res, str) else res
        if not isinstance(result, dict):
            return {"ok": False, "reason": "读取表格返回非对象: %r" % (result,)}
        if "error" in result:
            return {"ok": False, "reason": "读取表格失败: %s" % result.get("error")}
        return {
            "ok": True,
            "headers": result["headers"],
            "rows": result["rows"],
            "count": len(result["rows"]),
        }
    except Exception as e:
        return {"ok": False, "reason": str(e)}
