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

import browser_session

logger = logging.getLogger("drissionpage-mcp")

# JS 注入脚本：扫描 HTML 表格
_SCAN_JS = r"""
return (function(){
    var tables = document.querySelectorAll('.ant-table-wrapper');
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
            tableEl.querySelector('.ant-table-body table');
        if (headerTable) {
            var ths = headerTable.querySelectorAll('thead > tr > th');
            for (var ci = 0; ci < ths.length; ci++) {
                var th = ths[ci];
                var title = (th.textContent || '').trim();
                if (!title) continue;  // skip checkbox col, expand icon col etc

                var sorter = th.querySelector('.ant-table-column-sorter');
                var filterTrigger = th.querySelector('.ant-dropdown-trigger');
                var cls = th.className || '';
                var align = 'left';
                if (cls.indexOf('text-align-center') >= 0) align = 'center';
                else if (cls.indexOf('text-align-right') >= 0) align = 'right';

                entry.columns.push({
                    index: ci,
                    title: title,
                    alignment: align,
                    hasSorter: !!sorter,
                    hasFilter: !!filterTrigger,
                });
            }
        }

        // ---- rows ----
        var bodyTable = tableEl.querySelector('.ant-table-body table');
        if (!bodyTable) bodyTable = tableEl.querySelector('.ant-table-content table');
        if (bodyTable) {
            var rows = bodyTable.querySelectorAll('tbody > tr.ant-table-row');
            if (rows.length === 0) rows = bodyTable.querySelectorAll('tbody > tr');
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


_GET_VALUES_JS = r"""
return (function(){
    var COL_TITLE = %s;
    var TABLE_INDEX = %d;

    var wrappers = document.querySelectorAll('.ant-table-wrapper');
    var wrapper = wrappers[TABLE_INDEX];
    if (!wrapper) return JSON.stringify({error: 'table not found at index ' + TABLE_INDEX});

    var tableEl = wrapper.querySelector('.ant-table');
    if (!tableEl) return JSON.stringify({error: 'no .ant-table inside wrapper'});

    // find column index by title
    var headerTable =
        tableEl.querySelector('.ant-table-header table') ||
        tableEl.querySelector('.ant-table-body table');
    if (!headerTable) return JSON.stringify({error: 'no header table'});

    var ths = headerTable.querySelectorAll('thead > tr > th');
    var colIdx = -1;
    for (var i = 0; i < ths.length; i++) {
        if ((ths[i].textContent || '').trim() === COL_TITLE) {
            colIdx = i; break;
        }
    }
    if (colIdx < 0) return JSON.stringify({error: 'column not found: ' + COL_TITLE});

    // get body rows
    var bodyTable = tableEl.querySelector('.ant-table-body table');
    if (!bodyTable) bodyTable = tableEl.querySelector('.ant-table-content table');
    if (!bodyTable) return JSON.stringify({error: 'no body table'});

    var rows = bodyTable.querySelectorAll('tbody > tr.ant-table-row');
    if (rows.length === 0) rows = bodyTable.querySelectorAll('tbody > tr');

    var values = [];
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
        values.push(cell);
    }
    return JSON.stringify({values: values, column: COL_TITLE, count: values.length});
})();
"""


def get_html_table_values(column_title: str, table_index: int = 0):
    """按列标题获取 HTML 表格中该列所有单元格值。

    Args:
        column_title: 列标题文字（精确匹配）
        table_index: 表格索引（页面有多个 ant-table 时指定，默认 0）

    Returns:
        {ok, values: [{row, text, hasLink, hasButton, hasPopover, hasInput}], column, count}
    """
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



def _get_col_indices(column_title: str, table_index: int, fr) -> dict:
    """小 JS 获取目标列索引和首个有文本列索引（避开 fixed-left/right 重复 th）。"""
    js = r"""return (function(){
        var TI = %d, CT = %s;
        var w = document.querySelectorAll('.ant-table-wrapper')[TI];
        if (!w) return JSON.stringify({error:'table not found'});
        var te = w.querySelector('.ant-table');
        var ht = (te.querySelector('.ant-table-header table') || te.querySelector('.ant-table-body table'));
        var ths = ht.querySelectorAll('thead > tr > th');
        var target = -1, first = -1;
        for (var i = 0; i < ths.length; i++) {
            var t = (ths[i].textContent || '').trim();
            if (t === CT) target = i;
            if (first < 0 && t) first = i;
        }
        return JSON.stringify({target:target, first:first});
    })();""" % (table_index, json.dumps(column_title, ensure_ascii=False))
    res = fr.run_js(js)
    d = json.loads(res) if isinstance(res, str) else res
    if not isinstance(d, dict) or d.get("target", -1) < 0:
        return {"ok": False, "reason": d.get("error", "column not found")}
    return {"ok": True, "target": d["target"], "first": d["first"]}


def _get_td(fr, table_index: int, row: int, col_idx: int):
    """用 DP 原生 API 定位单元格 td。避开 .ant-table-measure-row。"""
    body_css = '.ant-table-body table tbody>tr.ant-table-row'
    rows = fr.eles('c:%s' % body_css)
    if not rows or row >= len(rows):
        return None
    tds = rows[row].eles('t:td')
    if col_idx >= len(tds):
        return None
    return tds[col_idx]


def click_html_table_cell(column_title: str, row: int, table_index: int = 0):
    """点击 HTML 表格中指定单元格。优先点击单元格内的 <a> 或 <button>，否则点 td 本身。

    Args:
        column_title: 列标题文字（精确匹配）
        row: 行索引（0-based，跳过表头）
        table_index: 表格索引（页面有多个 ant-table 时指定，默认 0）

    Returns:
        {ok, row, column, colIdx, element, centerX, centerY}
    """
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}

    # 1. 找列索引
    idx = _get_col_indices(column_title, table_index, fr)
    if not idx["ok"]:
        return idx
    col_idx = idx["target"]

    # 2. 定位 td
    td = _get_td(fr, table_index, row, col_idx)
    if td is None:
        return {"ok": False, "reason": "单元格未找到: row=%d col=%d" % (row, col_idx)}

    # 3. 优先找 a/button 点击，否则点 td 本身
    target_ele = td.ele('t:a', timeout=0) or td.ele('t:button', timeout=0) or td
    try:
        target_ele.click()
    except Exception as e:
        return {"ok": False, "reason": "点击失败: %s" % e}

    rect = target_ele.rect
    return {
        "ok": True, "row": row, "column": column_title, "colIdx": col_idx,
        "element": target_ele.tag,
        "centerX": rect.viewport_midpoint[0],
        "centerY": rect.viewport_midpoint[1],
    }


def hover_html_table_cell(column_title: str, row: int, table_index: int = 0):
    """悬停 HTML 表格指定单元格。鼠标从行首列移动到目标列，duration=1。

    使用 DrissionPage 原生 API 定位元素，fr.actions.move_to 自动处理坐标空间。

    Returns:
        {ok, row, column, viewportX, viewportY, fromX, fromY}
    """
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}

    # 1. 找列索引
    idx = _get_col_indices(column_title, table_index, fr)
    if not idx["ok"]:
        return idx
    target_col = idx["target"]
    first_col = idx["first"]

    # 2. 定位 td
    target_td = _get_td(fr, table_index, row, target_col)
    first_td = _get_td(fr, table_index, row, first_col)
    if target_td is None or first_td is None:
        return {"ok": False, "reason": "单元格未找到: row=%d" % row}

    # 3. 轨迹：瞬移到行首列 → 1 秒到目标列
    fr.actions.move_to(first_td, duration=0).move_to(target_td, duration=1)

    tr = target_td.rect
    frr = first_td.rect
    return {
        "ok": True, "row": row, "column": column_title,
        "viewportX": tr.viewport_midpoint[0],
        "viewportY": tr.viewport_midpoint[1],
        "fromX": frr.viewport_midpoint[0],
        "fromY": frr.viewport_midpoint[1],
    }
def get_html_table_data(table_index: int = 0):
    """从 DOM 读取 HTML 表格的完整数据（表头 + 所有行）。

    列名直接从 <thead> <th> 读取，数据从 <tbody> <tr> 读取，
    列名和数据按 DOM 顺序一一对应，不存在人工对齐错误。
    table_index 指定第几个表格（从 0 开始），默认 0。

    Returns:
        {ok, headers: [str], rows: [[str, ...]], count}
    """
    fr = _get_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}

    js = r"""return (function(){
        var TI = %d;
        var wrappers = document.querySelectorAll('.ant-table-wrapper');
        var wrapper = wrappers[TI];
        if (!wrapper) return JSON.stringify({error: 'table not found'});
        var tableEl = wrapper.querySelector('.ant-table');
        if (!tableEl) return JSON.stringify({error: 'ant-table not found'});
        var headerTable = tableEl.querySelector('.ant-table-header table') || tableEl.querySelector('.ant-table-body table');
        var ths = headerTable.querySelectorAll('thead > tr > th');
        var headers = [];
        ths.forEach(function(th) { headers.push((th.textContent || '').trim()); });
        var bodyTable = tableEl.querySelector('.ant-table-body table') || tableEl.querySelector('.ant-table-content table');
        var rows = bodyTable.querySelectorAll('tbody > tr.ant-table-row');
        if (rows.length === 0) rows = bodyTable.querySelectorAll('tbody > tr');
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
        if not isinstance(result, dict) or "error" in result:
            return {"ok": False, "reason": "读取表格失败: %s" % result.get("error", str(result))}
        return {
            "ok": True,
            "headers": result["headers"],
            "rows": result["rows"],
            "count": len(result["rows"]),
        }
    except Exception as e:
        return {"ok": False, "reason": str(e)}
