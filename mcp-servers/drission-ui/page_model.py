"""Structured page evidence scanners for enterprise UI test design.

The functions in this module are plain helpers, not MCP tools.  server.py adds
locking and exposes a stable public surface.  Keep the logic here reusable so
aggregate tools do not call decorated MCP functions and deadlock the server RW
lock.
"""
import json
import os
import time

import browser_session
import filter_area
import html_table
import resource_store
import vtable


def _parse_json(res, default):
    if res is None:
        return default
    if isinstance(res, str):
        try:
            return json.loads(res)
        except (TypeError, ValueError):
            return default
    return res


def _target(in_frame: bool = True):
    tab = browser_session.get_tab()
    fr = browser_session.get_active_frame(tab)
    if in_frame and fr is not None:
        return tab, fr, fr
    return tab, fr, tab


def _targets(include_top: bool = True, include_frame: bool = True):
    tab = browser_session.get_tab()
    fr = browser_session.get_active_frame(tab)
    out = []
    if include_frame and fr is not None:
        out.append(("iframe", fr))
    if include_top:
        out.append(("top", tab))
    return tab, fr, out


def _run_json(target, js: str, default):
    try:
        return _parse_json(target.run_js(js), default)
    except Exception as e:
        return {"ok": False, "reason": str(e)}



def get_element_center(el):
    """获取元素在顶层视口的中心坐标。

    注意: 不可用 rect.click_point —— 它返回的不是几何中心，
    而是元素内第一个可交互子元素的中心（偏上偏左）。
    始终使用 location + size/2 计算几何中心。

    Args:
        el: DrissionPage ChromiumElement 对象

    Returns:
        {"cx": float, "cy": float} | None
    """
    try:
        loc = el.rect.location
        sz = el.rect.size
        return {"cx": round(loc[0] + sz[0] / 2, 1),
                "cy": round(loc[1] + sz[1] / 2, 1)}
    except Exception:
        pass
    # fallback: JS getBoundingClientRect
    try:
        js = (
            "var r = this.getBoundingClientRect();"
            "return JSON.stringify({x: r.left, y: r.top, w: r.width, h: r.height});"
        )
        res = el.run_js(js)
        if isinstance(res, str):
            import json
            d = json.loads(res)
        else:
            d = res
        if d and 'w' in d and d['w'] > 0:
            cx = d['x'] + d['w'] / 2
            cy = d['y'] + d['h'] / 2
            # 叠加 iframe 偏移
            try:
                owner = el.owner
                off_js = (
                    "var f = window.frameElement;"
                    "if (!f) return '{\"x\":0,\"y\":0}';"
                    "var r = f.getBoundingClientRect();"
                    "return JSON.stringify({x: r.left, y: r.top});"
                )
                off_res = owner.run_js(off_js)
                if isinstance(off_res, str):
                    off = json.loads(off_res)
                else:
                    off = off_res
                if off:
                    cx += off.get('x', 0)
                    cy += off.get('y', 0)
            except Exception:
                pass
            return {"cx": round(cx, 1), "cy": round(cy, 1)}
    except Exception:
        pass
    return None


def save_json_result(data: dict, filename: str) -> dict:
    full_path = resource_store.resolve_path(filename)
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {
        "ok": True,
        "saved_to": os.path.abspath(full_path),
        "content_length": os.path.getsize(full_path),
    }


_COMMON_JS = r"""
function duCleanText(t){
  return (t || '').replace(/\s+/g, ' ').trim();
}
function duVisible(el){
  if (!el || !el.isConnected) return false;
  var cur = el;
  while (cur && cur.nodeType === 1) {
    var s = window.getComputedStyle(cur);
    if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse') return false;
    cur = cur.parentElement;
  }
  var r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
function duRect(el){
  var r = el.getBoundingClientRect();
  return {
    x: Math.round(r.left * 10) / 10,
    y: Math.round(r.top * 10) / 10,
    width: Math.round(r.width * 10) / 10,
    height: Math.round(r.height * 10) / 10
  };
}
function duDisabled(el){
  return !!(el.disabled || el.getAttribute('disabled') !== null ||
    el.getAttribute('aria-disabled') === 'true' ||
    /\bdisabled\b|ant-btn-disabled|ant-select-disabled/.test(el.className || ''));
}
function duCssHint(el){
  if (!el || !el.tagName) return '';
  var tag = el.tagName.toLowerCase();
  if (el.id) return tag + '#' + el.id;
  var data = el.getAttribute('data-row-key') || el.getAttribute('data-testid') ||
             el.getAttribute('data-test') || el.getAttribute('name');
  if (data) return tag + '[' + (el.getAttribute('data-row-key') ? 'data-row-key' :
             (el.getAttribute('data-testid') ? 'data-testid' :
             (el.getAttribute('data-test') ? 'data-test' : 'name'))) + '="' + data + '"]';
  var cls = (el.className && typeof el.className === 'string') ?
    el.className.split(/\s+/).filter(Boolean).slice(0, 3).join('.') : '';
  return cls ? tag + '.' + cls : tag;
}
function duArea(el){
  if (el.closest('.ant-modal')) return 'modal';
  if (el.closest('.ant-drawer')) return 'drawer';
  if (el.closest('.ant-pagination')) return 'pagination';
  if (el.closest('.ant-select-dropdown,.ant-dropdown')) return 'dropdown';
  if (el.closest('.page-query,.legions-pro-quick-filter')) return 'filter';
  if (el.closest('.ant-table-wrapper,.vtable,[class*="vtable"]')) return 'table';
  if (el.closest('[class*="toolbar"],.ant-card-extra,.ant-tabs-extra-content')) return 'toolbar';
  if (el.closest('.ant-menu')) return 'menu';
  return 'page';
}
function duButtonText(el){
  return duCleanText(el.getAttribute('aria-label') || el.getAttribute('title') ||
    el.textContent || el.value || '');
}
function duSelectText(sel){
  var n = sel.querySelector('.ant-select-selection-selected-value,.ant-select-selection__rendered,.ant-select-selector');
  return duCleanText(n ? n.textContent : sel.textContent);
}
function duLabelFor(container, control){
  var label = container.querySelector('.ant-form-item-label label,.ant-form-item-label,label,[class*="label"]');
  var txt = duCleanText(label ? label.textContent : '');
  if (!txt && control) txt = duCleanText(control.getAttribute('aria-label') || control.getAttribute('placeholder') ||
    control.getAttribute('name') || control.getAttribute('title') || '');
  return txt.replace(/[：:]\s*$/, '');
}
function duControlIn(container){
  return container.querySelector(
    'input:not([type="hidden"]),textarea,.ant-select,.ant-calendar-picker,.ant-picker,' +
    '.ant-input-number,.ant-checkbox-wrapper,.ant-radio-group,.ant-switch,[role="combobox"]'
  );
}
function duControlType(container, control){
  if (!control) return 'unknown';
  if (container.querySelector('.ant-calendar-picker,.ant-picker')) return 'date-picker';
  if (container.querySelector('.ant-select')) {
    var sel = container.querySelector('.ant-select');
    return sel.querySelector('.ant-select-search__field,input[role="combobox"]') ? 'searchable-select' : 'select';
  }
  if (container.querySelector('textarea')) return 'textarea';
  if (container.querySelector('.ant-input-number')) return 'number';
  if (container.querySelector('.ant-checkbox-wrapper,input[type="checkbox"]')) return 'checkbox';
  if (container.querySelector('.ant-radio-group,input[type="radio"]')) return 'radio';
  if (container.querySelector('.ant-switch')) return 'switch';
  var input = container.querySelector('input:not([type="hidden"])');
  return input ? (input.getAttribute('type') || 'text') : 'unknown';
}
function duControlValue(container){
  var inputs = [].slice.call(container.querySelectorAll('input:not([type="hidden"]),textarea'));
  if (inputs.length > 1) return inputs.map(function(i){ return i.value || i.getAttribute('value') || ''; });
  if (inputs.length === 1) return inputs[0].value || inputs[0].getAttribute('value') || '';
  var sel = container.querySelector('.ant-select');
  if (sel) return duSelectText(sel);
  var sw = container.querySelector('.ant-switch');
  if (sw) return sw.className.indexOf('ant-switch-checked') >= 0;
  return '';
}
function duFieldFrom(container, idx, includeHidden){
  var control = duControlIn(container);
  if (!control) return null;
  if (!includeHidden && !duVisible(control)) return null;
  var input = container.querySelector('input:not([type="hidden"]),textarea');
  var item = {
    index: idx,
    label: duLabelFor(container, input || control),
    type: duControlType(container, control),
    value: duControlValue(container),
    placeholder: input ? (input.getAttribute('placeholder') || '') : '',
    required: !!(container.querySelector('.ant-form-item-required,[required]') || (input && input.required)),
    disabled: duDisabled(control) || !!(input && input.disabled),
    readOnly: !!(input && input.readOnly),
    area: duArea(container),
    selectorHint: duCssHint(container),
    rect: duRect(control)
  };
  if (item.type === 'select' || item.type === 'searchable-select') {
    var sel = container.querySelector('.ant-select');
    item.selectedText = sel ? duSelectText(sel) : '';
    item.hasDropdown = !!(sel && sel.querySelector('[role="combobox"],.ant-select-selection,.ant-select-selector'));
  }
  return item;
}
function duScanFields(root, includeHidden, maxFields){
  var out = [];
  var seen = [];
  var nodes = root.querySelectorAll(
    '.ant-form-item,.legions-pro-quick-filter-row > div[class*="ant-col-"],' +
    'input:not([type="hidden"]),textarea,.ant-select,.ant-calendar-picker,.ant-picker,' +
    '.ant-input-number,.ant-checkbox-wrapper,.ant-radio-group,.ant-switch'
  );
  for (var i = 0; i < nodes.length && out.length < maxFields; i++) {
    var n = nodes[i];
    var c = n.closest('.ant-form-item') || n.closest('.legions-pro-quick-filter-row > div[class*="ant-col-"]') ||
            n.closest('[class*="ant-col-"]') || n.parentElement || n;
    if (seen.indexOf(c) >= 0) continue;
    seen.push(c);
    var item = duFieldFrom(c, out.length, includeHidden);
    if (item) out.push(item);
  }
  return out;
}
function duScanButtons(root, maxItems){
  var out = [];
  var nodes = root.querySelectorAll('button,.ant-btn,[role="button"],a[href],.ant-dropdown-trigger');
  for (var i = 0; i < nodes.length && out.length < maxItems; i++) {
    var el = nodes[i];
    if (!duVisible(el)) continue;
    var txt = duButtonText(el);
    var cls = el.className || '';
    var icons = [].slice.call(el.querySelectorAll('i,[class*="anticon"]')).map(function(ic){
      return (ic.className || '').toString().slice(0, 80);
    }).filter(Boolean).slice(0, 3);
    if (!txt && !icons.length) continue;
    out.push({
      index: out.length,
      text: txt,
      tag: (el.tagName || '').toLowerCase(),
      role: el.getAttribute('role') || '',
      title: el.getAttribute('title') || '',
      disabled: duDisabled(el),
      area: duArea(el),
      kind: cls.indexOf('ant-btn-primary') >= 0 ? 'primary' :
            (cls.indexOf('danger') >= 0 ? 'danger' : 'default'),
      hasDropdown: !!(el.querySelector('.anticon-down') || el.getAttribute('aria-haspopup') === 'true' ||
        el.className.indexOf('dropdown') >= 0),
      icons: icons,
      selectorHint: duCssHint(el),
      rect: duRect(el)
    });
  }
  return out;
}
"""


def scan_toolbar_actions(scope: str = "page", in_frame: bool = True, max_items: int = 120) -> dict:
    """Scan visible page actions with disabled state and dropdown hints."""
    _, _, target = _target(in_frame=in_frame)
    scope = (scope or "page").lower()
    allowed = {
        "toolbar": ["toolbar", "page"],
        "page": ["toolbar", "page", "table"],
        "all": None,
    }.get(scope, ["toolbar", "page", "table"])
    js = _COMMON_JS + """
var actions = duScanButtons(document.body, MAX_ITEMS);
var allowed = ALLOWED;
if (allowed) actions = actions.filter(function(a){ return allowed.indexOf(a.area) >= 0; });
return JSON.stringify({ok:true, scope:SCOPE, total:actions.length, actions:actions.slice(0, MAX_ITEMS)});
""".replace("MAX_ITEMS", str(max_items)).replace("SCOPE", json.dumps(scope)).replace(
        "ALLOWED", "null" if allowed is None else json.dumps(allowed, ensure_ascii=False)
    )
    data = _run_json(target, js, {"ok": False, "reason": "scan failed"})
    if isinstance(data, dict):
        data["target"] = "iframe" if target is not browser_session.get_tab_ro() else "top"
    return data


def scan_form_fields(scope: str = "page", include_hidden: bool = False,
                     in_frame: bool = True, max_fields: int = 200) -> dict:
    """Scan form-like controls outside or inside overlays."""
    _, _, target = _target(in_frame=in_frame)
    selector_map = {
        "page": "body",
        "filter": ".page-query,.legions-pro-quick-filter",
        "modal": ".ant-modal:not([style*='display: none'])",
        "drawer": ".ant-drawer:not(.ant-drawer-hidden)",
        "all": "body",
    }
    selector = selector_map.get((scope or "page").lower(), scope or "body")
    js = _COMMON_JS + """
var roots = [].slice.call(document.querySelectorAll(SELECTOR));
if (!roots.length) roots = [document.body];
var fields = [];
for (var i = 0; i < roots.length && fields.length < MAX_FIELDS; i++) {
  fields = fields.concat(duScanFields(roots[i], INCLUDE_HIDDEN, MAX_FIELDS - fields.length));
}
return JSON.stringify({ok:true, scope:SCOPE, total:fields.length, fields:fields.slice(0, MAX_FIELDS)});
""".replace("SELECTOR", json.dumps(selector)).replace("SCOPE", json.dumps(scope)).replace(
        "MAX_FIELDS", str(max_fields)
    ).replace("INCLUDE_HIDDEN", "true" if include_hidden else "false")
    return _run_json(target, js, {"ok": False, "reason": "scan failed"})


def _scan_overlay(kind: str, max_items: int = 20) -> dict:
    overlay_selector = ".ant-modal" if kind == "modal" else ".ant-drawer"
    title_selector = ".ant-modal-title" if kind == "modal" else ".ant-drawer-title"
    body_selector = ".ant-modal-body" if kind == "modal" else ".ant-drawer-body"
    tab, fr, target_list = _targets(include_top=True, include_frame=True)
    overlays = []
    errors = []
    for scope, target in target_list:
        js = _COMMON_JS + """
var nodes = [].slice.call(document.querySelectorAll(OVERLAY_SELECTOR)).filter(duVisible);
var out = [];
for (var i = 0; i < nodes.length && out.length < MAX_ITEMS; i++) {
  var n = nodes[i];
  var title = n.querySelector(TITLE_SELECTOR);
  var body = n.querySelector(BODY_SELECTOR) || n;
  var close = n.querySelector('.ant-modal-close,.ant-drawer-close');
  out.push({
    index: out.length,
    title: duCleanText(title ? title.textContent : ''),
    text: duCleanText(body ? body.textContent : '').slice(0, 800),
    fields: duScanFields(n, false, 120),
    buttons: duScanButtons(n, 80),
    tableCount: n.querySelectorAll('.ant-table-wrapper,.vtable,[class*="vtable"]').length,
    hasClose: !!close,
    rect: duRect(n)
  });
}
return JSON.stringify({ok:true, overlays:out});
""".replace("OVERLAY_SELECTOR", json.dumps(overlay_selector)).replace(
            "TITLE_SELECTOR", json.dumps(title_selector)
        ).replace("BODY_SELECTOR", json.dumps(body_selector)).replace("MAX_ITEMS", str(max_items))
        data = _run_json(target, js, {"ok": False, "reason": "scan failed"})
        if data.get("ok"):
            for item in data.get("overlays", []):
                item["scope"] = scope
                item["kind"] = kind
                overlays.append(item)
        else:
            errors.append({"scope": scope, "reason": data.get("reason", "")})
    result = {"ok": True, "kind": kind, "count": len(overlays), "overlays": overlays}
    if errors:
        result["errors"] = errors
    return result


def scan_modal(max_items: int = 20) -> dict:
    return _scan_overlay("modal", max_items=max_items)






def scan_floats(only_visible: bool = True, include_table_data: bool = True) -> dict:
    """扫描所有可见浮窗（modal/drawer/popover/tooltip/dropdown/message/notification）。

    单次 JS 注入完成。返回浮窗内所有操作按钮的位置（可点击）、
    关闭按钮的 CSS 定位符（可供 click 工具使用）、以及内部表格结构。

    Args:
        only_visible: 过滤不可见元素
        include_table_data: 是否自动提取 HTML 表格全量行数据

    Returns:
        {ok, count, floats: [{title, type, text, rect, scope,
            buttons: [{text, rect, selectorHint, disabled, ...}],
            closeButton: {selectorHint, rect} | null,
            tableCount, tables: [{index, kind, headers, rowCount,
                                  data: [[str]]?}]}]}
    """
    tab, fr, target_list = _targets(include_top=True, include_frame=True)
    # 记录当前活跃 frame 信息，供调用方判断是否发生了 tabpanel 切换
    frame_url = getattr(fr, "url", "") if fr is not None else ""
    all_floats = []
    errors = []
    for scope, target in target_list:
        js = _COMMON_JS + """
var nodeList = [].slice.call(document.querySelectorAll(
  '.ant-modal, .ant-drawer, .ant-popover, .ant-tooltip, '
  + '.ant-dropdown, .ant-message-notice, .ant-notification-notice'
));
var nodes = ONLY_VISIBLE ? nodeList.filter(duVisible) : nodeList;
var allWrappers = [].slice.call(document.querySelectorAll('.ant-table-wrapper'));
// iframe 偏移：叠加到坐标使结果始终为 top-viewport 坐标
var ifrOff = {left:0, top:0};
var _ifrEl = window.frameElement;
if (_ifrEl) { var _r = _ifrEl.getBoundingClientRect(); ifrOff = {left: _r.left, top: _r.top}; }
function frRect(el) { var r = duRect(el); return {x: r.x + ifrOff.left, y: r.y + ifrOff.top, width: r.width, height: r.height}; }
function frCenter(el) { var r = duRect(el); return {cx: Math.round((r.x + r.width/2 + ifrOff.left)*10)/10, cy: Math.round((r.y + r.height/2 + ifrOff.top)*10)/10}; }
var out = [];
for (var i = 0; i < nodes.length; i++) {
  var n = nodes[i];
  var cls = n.className || '';
  var kind = 'unknown';
  if (/\\bant-modal\\b/.test(cls)) kind = 'modal';
  else if (/\\bant-drawer\\b/.test(cls)) kind = 'drawer';
  else if (/\\bant-popover\\b/.test(cls)) kind = 'popover';
  else if (/\\bant-tooltip\\b/.test(cls)) kind = 'tooltip';
  else if (/\\bant-dropdown\\b/.test(cls)) kind = 'dropdown';
  else if (/\\bant-message-notice\\b/.test(cls)) kind = 'message';
  else if (/\\bant-notification-notice\\b/.test(cls)) kind = 'notification';

  // 标题提取
  var titleEl = n.querySelector('.ant-modal-title, .ant-drawer-title, .ant-modal-header');
  var title = titleEl ? duCleanText(titleEl.textContent) : '';
  if (!title) {
    var raw = n.innerText || n.textContent || '';
    var parts = raw.split(/\\n/).map(function(s){ return s.trim(); }).filter(function(s){ return s.length > 0; });
    for (var p = 0; p < parts.length && p < 10; p++) {
      var t = parts[p];
      if (/[\\u4e00-\\u9fff\\w]{2,}/.test(t) && t.length < 60) { title = t; break; }
    }
    if (!title && parts.length > 0) title = parts[0].slice(0, 60);
  }

  var text = duCleanText(n.innerText || n.textContent || '');
  var buttons = duScanButtons(n, 30);
  // 对 duScanButtons 的 rect 叠加 iframe 偏移（它在 _COMMON_JS 中用的 duRect 无偏移）
  for (var bii = 0; bii < buttons.length; bii++) {
    var br2 = buttons[bii].rect;
    br2.x = Math.round((br2.x + ifrOff.left) * 10) / 10;
    br2.y = Math.round((br2.y + ifrOff.top) * 10) / 10;
    br2.cx = Math.round((br2.x + br2.width/2) * 10) / 10;
    br2.cy = Math.round((br2.y + br2.height/2) * 10) / 10;
  }
  var clickableExtras = [];
  var extraNodes = n.querySelectorAll('a:not([href]),span:not([role])');
  for (var ei = 0; ei < extraNodes.length && clickableExtras.length < 20; ei++) {
    var en = extraNodes[ei];
    if (!duVisible(en)) continue;
    if (en.closest('button,.ant-btn,[role="button"],a[href],.ant-dropdown-trigger')) continue;
    var isClickable = false;
    if (en.onclick) { isClickable = true; }
    else if (en.getAttribute('tabindex') !== null) { isClickable = true; }
    else { var enStyle = window.getComputedStyle(en); if (enStyle.cursor === 'pointer') isClickable = true; }
    if (!isClickable) continue;
    var txt = duCleanText(en.innerText || en.textContent);
    if (!txt || txt.length > 40) continue;
    var dup = false;
    for (var bi = 0; bi < buttons.length; bi++) { if (buttons[bi].text === txt) { dup = true; break; } }
    if (dup) continue;
    clickableExtras.push({
      text: txt, tag: en.tagName.toLowerCase(),
      selectorHint: duCssHint(en), center: frCenter(en), rect: frRect(en),
      kind: 'clickable-link', extra: true
    });
  }

  // 可展开行图标（ant-table-row-expand-icon，无文本纯图标）
  var expandIcons = n.querySelectorAll('span.ant-table-row-expand-icon');
  for (var xi = 0; xi < expandIcons.length; xi++) {
    var xiEl = expandIcons[xi];
    if (!duVisible(xiEl)) continue;
    var xiTxt = xiEl.classList.contains('ant-table-row-collapsed') ? '展开子表' : '收起子表';
    var dup2 = false;
    for (var bi = 0; bi < buttons.length; bi++) { if (buttons[bi].text === xiTxt) { dup2 = true; break; } }
    if (dup2) continue;
    clickableExtras.push({
      text: xiTxt, tag: 'span',
      selectorHint: duCssHint(xiEl), center: frCenter(xiEl), rect: frRect(xiEl),
      kind: 'expand-icon', extra: true
    });
  }
  buttons = buttons.concat(clickableExtras);

  // 表单字段（input/select/datepicker/checkbox/radio/switch 等）
  var fields = duScanFields(n, false, 50);
  // 给字段叠加 iframe 偏移
  for (var fi = 0; fi < fields.length; fi++) {
    var fld = fields[fi];
    if (fld.rect) {
      fld.rect.x = Math.round((fld.rect.x + ifrOff.left) * 10) / 10;
      fld.rect.y = Math.round((fld.rect.y + ifrOff.top) * 10) / 10;
      fld.rect.cx = Math.round((fld.rect.x + fld.rect.width/2) * 10) / 10;
      fld.rect.cy = Math.round((fld.rect.y + fld.rect.height/2) * 10) / 10;
    }
  }

  // 关闭按钮
  var closeBtn = n.querySelector('.ant-modal-close, .ant-drawer-close');
  var closeButton = null;
  if (closeBtn) {
    closeButton = {
      center: frCenter(closeBtn), rect: frRect(closeBtn)
    };
  }

  // 表格检测 + 数据提取
  var tableWrappers = [].slice.call(n.querySelectorAll('.ant-table-wrapper'));
  var tables = [];
  tableWrappers.forEach(function(tw){
    var globalIdx = allWrappers.indexOf(tw);
    var headerTr = tw.querySelector('.ant-table-thead tr');
    var headers = [];
    if (headerTr) {
      headers = [].slice.call(headerTr.querySelectorAll('th')).map(function(th){
        return (th.textContent || '').trim();
      }).filter(function(h){ return h.length > 0; });
    }
    var bodyRows = tw.querySelectorAll('.ant-table-tbody tr.ant-table-row').length;
    if (bodyRows === 0) bodyRows = tw.querySelectorAll('.ant-table-tbody tr').length;
    var hasVTable = n.querySelectorAll('canvas.vtable, [class*="vtable"]').length > 0;

    var rowData = [];
    if (INCLUDE_TABLE_DATA) {
      var bodyTable = tw.querySelector('.ant-table-body table') || tw.querySelector('.ant-table-content table');
      if (bodyTable) {
        var trs = bodyTable.querySelectorAll('tbody > tr.ant-table-row');
        if (trs.length === 0) trs = bodyTable.querySelectorAll('tbody > tr');
        trs.forEach(function(tr){
          var cells = tr.querySelectorAll('td');
          var row = [];
          cells.forEach(function(td){ row.push((td.textContent || '').trim()); });
          rowData.push(row);
        });
      }
    }

    tables.push({
      index: globalIdx, kind: hasVTable ? 'vtable' : 'html',
      headers: headers, rowCount: bodyRows, data: rowData
    });
  });

  out.push({
    title: title, type: kind, text: text.slice(0, 800),
    buttons: buttons, fields: fields, closeButton: closeButton,
    center: frCenter(n), rect: frRect(n)
  });
}
return JSON.stringify({ok:true, floats:out});
""".replace("ONLY_VISIBLE", "true" if only_visible else "false").replace(
    "INCLUDE_TABLE_DATA", "true" if include_table_data else "false")
        data = _run_json(target, js, {"ok": False, "reason": "scan_floats JS failed"})
        if data.get("ok"):
            for item in data.get("floats", []):
                item["scope"] = scope
                all_floats.append(item)
        else:
            errors.append({"scope": scope, "reason": data.get("reason", "")})
    result = {"ok": True, "count": len(all_floats), "floats": all_floats,
              "has_active_frame": fr is not None, "frame_url": frame_url}
    if errors:
        result["errors"] = errors
    return result

def scan_drawer(max_items: int = 20) -> dict:
    return _scan_overlay("drawer", max_items=max_items)


def scan_pagination(in_frame: bool = True) -> dict:
    _, _, target = _target(in_frame=in_frame)
    js = _COMMON_JS + r"""
var pages = [];
var nodes = [].slice.call(document.querySelectorAll('.ant-pagination')).filter(duVisible);
nodes.forEach(function(p, idx){
  var active = p.querySelector('.ant-pagination-item-active');
  var total = p.querySelector('.ant-pagination-total-text');
  var size = p.querySelector('.ant-select-selection-selected-value,.ant-select-selection__rendered,.ant-select-selector');
  var prev = p.querySelector('.ant-pagination-prev');
  var next = p.querySelector('.ant-pagination-next');
  var items = [].slice.call(p.querySelectorAll('.ant-pagination-item')).map(function(it){
    return {page: duCleanText(it.textContent), active: it.className.indexOf('ant-pagination-item-active') >= 0};
  });
  pages.push({
    index: idx,
    current: active ? parseInt(duCleanText(active.textContent), 10) || duCleanText(active.textContent) : null,
    pageSize: size ? duCleanText(size.textContent) : '',
    totalText: total ? duCleanText(total.textContent) : '',
    prevDisabled: !!(prev && prev.className.indexOf('ant-pagination-disabled') >= 0),
    nextDisabled: !!(next && next.className.indexOf('ant-pagination-disabled') >= 0),
    items: items,
    rect: duRect(p)
  });
});
return JSON.stringify({ok:true, count:pages.length, paginations:pages});
"""
    return _run_json(target, js, {"ok": False, "reason": "scan failed"})


def select_option(field_name: str, option_text: str, select_index: int = 0,
                  scope: str = "auto", timeout: float = 5.0) -> dict:
    """Select an Ant Design option by field label and visible option text."""
    if not option_text:
        return {"ok": False, "reason": "option_text is required"}

    tab, fr, _ = _target(in_frame=True)
    candidates = []
    if scope in ("auto", "frame", "iframe") and fr is not None:
        candidates.append(("iframe", fr))
    if scope in ("auto", "top"):
        candidates.append(("top", tab))
    if not candidates:
        candidates.append(("top", tab))

    open_js = _COMMON_JS + """
var FIELD = FIELD_NAME;
var SELECT_INDEX = SELECT_INDEX_VALUE;
function findContainer(){
  var roots = [].slice.call(document.querySelectorAll(
    '.ant-form-item,.legions-pro-quick-filter-row > div[class*="ant-col-"],[class*="ant-col-"],body'
  ));
  if (!FIELD) {
    return roots.find(function(r){ return r.querySelector && r.querySelector('.ant-select:not(.ant-select-disabled)'); }) || document.body;
  }
  return roots.find(function(r){
    return r.querySelector && r.querySelector('.ant-select:not(.ant-select-disabled)') &&
      duCleanText(r.textContent).indexOf(FIELD) >= 0;
  });
}
var c = findContainer();
if (!c) return JSON.stringify({ok:false, reason:'field not found: ' + FIELD});
var selects = [].slice.call(c.querySelectorAll('.ant-select:not(.ant-select-disabled)'));
if (!selects.length) return JSON.stringify({ok:false, reason:'select not found in field: ' + FIELD});
var sel = selects[Math.min(SELECT_INDEX, selects.length - 1)];
var opener = sel.querySelector('[role="combobox"],.ant-select-selection,.ant-select-selector') || sel;
var r = opener.getBoundingClientRect();
opener.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
opener.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window}));
opener.click();
return JSON.stringify({ok:true, field:FIELD, selectIndex:SELECT_INDEX, rect:{left:r.left, top:r.top, height:r.height, width:r.width}});
""".replace("FIELD_NAME", json.dumps(field_name or "", ensure_ascii=False)).replace(
        "SELECT_INDEX_VALUE", str(max(0, select_index))
    )

    last_open = None
    used_scope = ""
    target = None
    for scope_name, candidate in candidates:
        opened = _run_json(candidate, open_js, {"ok": False, "reason": "open failed"})
        last_open = opened
        if opened.get("ok"):
            used_scope = scope_name
            target = candidate
            break
    if target is None:
        return last_open or {"ok": False, "reason": "select open failed"}

    try:
        target.wait.ele_displayed('c:.ant-select-dropdown:not(.ant-select-dropdown-hidden)', timeout=min(timeout, 2))
    except Exception:
        pass

    search_js = """
var text = OPTION_TEXT;
var input = document.querySelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden) input,' +
  '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-search__field');
if (input) {
  input.focus();
  input.value = text;
  input.dispatchEvent(new Event('input', {bubbles:true}));
  input.dispatchEvent(new Event('change', {bubbles:true}));
}
return JSON.stringify({ok:true, hasSearch:!!input});
""".replace("OPTION_TEXT", json.dumps(option_text, ensure_ascii=False))
    _run_json(target, search_js, {"ok": True})
    time.sleep(min(max(timeout, 0), 0.2))

    click_js = _COMMON_JS + """
var OPTION = OPTION_TEXT;
var drops = [].slice.call(document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden)')).filter(duVisible);
var items = [];
drops.forEach(function(d){
  items = items.concat([].slice.call(d.querySelectorAll('.ant-select-dropdown-menu-item,.ant-select-item-option')).filter(duVisible));
});
var enabled = items.filter(function(it){
  return it.className.indexOf('disabled') < 0 && it.getAttribute('aria-disabled') !== 'true';
});
var exact = enabled.find(function(it){ return duCleanText(it.textContent) === OPTION; });
var match = exact || enabled.find(function(it){ return duCleanText(it.textContent).indexOf(OPTION) >= 0; });
if (!match) {
  return JSON.stringify({ok:false, reason:'option not found: ' + OPTION,
    available: enabled.map(function(it){return duCleanText(it.textContent);}).filter(Boolean).slice(0, 50)});
}
var text = duCleanText(match.textContent);
match.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
match.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window}));
match.click();
return JSON.stringify({ok:true, selected:text, exact:!!exact});
""".replace("OPTION_TEXT", json.dumps(option_text, ensure_ascii=False))
    result = _run_json(target, click_js, {"ok": False, "reason": "option click failed"})
    if result.get("ok"):
        result["scope"] = used_scope
        result["field"] = field_name
    return result


def _read_vtable_rows(max_columns: int = 50, max_rows: int = 500, raw: bool = False) -> dict:
    scan = vtable.scan_vtable_columns(max_columns)
    if not scan.get("ok"):
        return scan

    columns = []
    seen = set()
    for col in scan.get("columns", []):
        title = (col.get("title") or col.get("field") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        columns.append({"title": title, "col": col.get("col"), "bodyBehavior": col.get("bodyBehavior", "")})

    values_by_title = []
    row_count = 0
    for col in columns:
        values = vtable.get_column_values(col["title"], raw=raw)
        if not values.get("ok"):
            continue
        vals = values.get("values") or []
        values_by_title.append((col["title"], vals))
        row_count = max(row_count, len(vals))

    row_count = min(row_count, max_rows)
    rows = []
    for i in range(row_count):
        row = {}
        for title, vals in values_by_title:
            row[title] = vals[i] if i < len(vals) else None
        rows.append(row)

    return {
        "ok": True,
        "kind": "vtable",
        "columns": columns,
        "rows": rows,
        "count": len(rows),
        "raw": raw,
        "limitation": "VTable 数据由列值重建，覆盖当前 VTable 实例可读数据；虚拟滚动或懒加载未加载行需结合分页/滚动继续采集。",
    }


def _click_next_page(target, table_index: int = 0) -> dict:
    js = r"""
var TI = TABLE_INDEX;
function visible(el){
  if(!el) return false;
  var s = getComputedStyle(el);
  var r = el.getBoundingClientRect();
  return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
}
var wrappers = [].slice.call(document.querySelectorAll('.ant-table-wrapper'));
var root = wrappers[TI] || document;
var next = root.querySelector('.ant-pagination-next');
if (!next) return JSON.stringify({ok:false, reason:'no pagination next'});
if (next.className.indexOf('ant-pagination-disabled') >= 0 || next.getAttribute('aria-disabled') === 'true') {
  return JSON.stringify({ok:false, done:true, reason:'next disabled'});
}
var btn = next.querySelector('a,button') || next;
btn.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
btn.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window}));
btn.click();
return JSON.stringify({ok:true});
""".replace("TABLE_INDEX", str(table_index))
    return _run_json(target, js, {"ok": False, "reason": "next click failed"})


def get_all_table_data(kind: str = "auto", table_index: int = 0, max_pages: int = 1,
                       max_rows: int = 1000, max_columns: int = 50,
                       raw: bool = False, filename: str = None) -> dict:
    """Read table rows, optionally walking HTML pagination."""
    kind = (kind or "auto").lower()
    if kind not in {"auto", "html", "vtable"}:
        kind = "auto"

    if kind == "vtable":
        result = _read_vtable_rows(max_columns=max_columns, max_rows=max_rows, raw=raw)
    elif kind == "html":
        result = _read_html_pages(table_index, max_pages=max_pages, max_rows=max_rows)
    else:
        vt = _read_vtable_rows(max_columns=max_columns, max_rows=max_rows, raw=raw)
        if vt.get("ok"):
            result = vt
        else:
            html = _read_html_pages(table_index, max_pages=max_pages, max_rows=max_rows)
            if html.get("ok"):
                html["fallback_from"] = "vtable"
                html["vtable_reason"] = vt.get("reason", "")
            result = html

    if filename and result.get("ok"):
        saved = save_json_result(result, filename)
        saved["kind"] = result.get("kind")
        saved["count"] = result.get("count", 0)
        return saved
    return result


def _read_html_pages(table_index: int = 0, max_pages: int = 1, max_rows: int = 1000) -> dict:
    tab, fr, target = _target(in_frame=True)
    headers = []
    rows = []
    pages_read = 0
    max_pages = max(1, max_pages)

    while pages_read < max_pages and len(rows) < max_rows:
        data = html_table.get_html_table_data(table_index)
        if not data.get("ok"):
            if pages_read == 0:
                return data
            break
        if not headers:
            headers = data.get("headers") or []
        for row in data.get("rows") or []:
            rows.append(row)
            if len(rows) >= max_rows:
                break
        pages_read += 1
        if pages_read >= max_pages or len(rows) >= max_rows:
            break
        moved = _click_next_page(target, table_index)
        if not moved.get("ok"):
            break
        time.sleep(0.35)

    return {
        "ok": True,
        "kind": "html",
        "headers": headers,
        "rows": rows,
        "count": len(rows),
        "pages_read": pages_read,
        "mutated_page": pages_read > 1,
    }


def click_html_row_selection(row: int = 0, table_index: int = 0) -> dict:
    """Click a row checkbox in an Ant Design HTML table."""
    _, _, target = _target(in_frame=True)
    js = r"""
var TI = TABLE_INDEX, ROW = ROW_INDEX;
var wrapper = document.querySelectorAll('.ant-table-wrapper')[TI];
if (!wrapper) return JSON.stringify({ok:false, reason:'table not found'});
var rows = wrapper.querySelectorAll('tbody > tr.ant-table-row');
if (!rows.length) rows = wrapper.querySelectorAll('tbody > tr');
var tr = rows[ROW];
if (!tr) return JSON.stringify({ok:false, reason:'row not found'});
var cb = tr.querySelector('.ant-checkbox-wrapper,input[type="checkbox"],.ant-checkbox');
if (!cb) return JSON.stringify({ok:false, reason:'row checkbox not found'});
var target = cb.querySelector('input[type="checkbox"]') || cb;
target.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
target.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window}));
target.click();
return JSON.stringify({ok:true, row:ROW, table_index:TI});
""".replace("TABLE_INDEX", str(table_index)).replace("ROW_INDEX", str(row))
    return _run_json(target, js, {"ok": False, "reason": "row selection click failed"})


def capture_page_model(include_filters: bool = True, include_tables: bool = True,
                       include_table_data: bool = True, max_table_rows: int = 80,
                       max_elements: int = 120, filename: str = None) -> dict:
    """Capture a structured snapshot of the active business page."""
    tab = browser_session.get_tab()
    fr = browser_session.get_active_frame(tab)
    model = {
        "ok": True,
        "captured_at": int(time.time()),
        "page": {
            "url": getattr(tab, "url", "") or "",
            "title": getattr(tab, "title", "") or "",
            "frame_url": getattr(fr, "url", "") if fr is not None else "",
            "has_active_frame": fr is not None,
        },
        "actions": {},
        "fields": {},
        "modals": {},
        "drawers": {},
        "pagination": {},
        "tables": {},
    }

    def _safe(key, fn):
        try:
            model[key] = fn()
        except Exception as e:
            model[key] = {"ok": False, "reason": str(e)}

    _safe("actions", lambda: scan_toolbar_actions(scope="all", max_items=max_elements))
    _safe("fields", lambda: scan_form_fields(scope="all", max_fields=max_elements))
    _safe("modals", scan_modal)
    _safe("drawers", scan_drawer)
    _safe("pagination", scan_pagination)

    if include_filters:
        try:
            model["filters"] = filter_area.scan_filter_fields(tab)
        except Exception as e:
            model["filters"] = {"ok": False, "reason": str(e)}

    if include_tables:
        try:
            vt = vtable.scan_vtable_columns(50)
            if vt.get("ok"):
                model["tables"] = {"ok": True, "kind": "vtable", "scan": vt}
            else:
                ht = html_table.scan_html_table()
                model["tables"] = {"ok": ht.get("ok", False), "kind": "html", "scan": ht,
                                   "vtable_reason": vt.get("reason", "")}
        except Exception as e:
            model["tables"] = {"ok": False, "reason": str(e)}

    if include_table_data:
        try:
            model["table_data"] = get_all_table_data(kind="auto", max_pages=1, max_rows=max_table_rows)
        except Exception as e:
            model["table_data"] = {"ok": False, "reason": str(e)}

    if filename:
        saved = save_json_result(model, filename)
        saved["sections"] = sorted(model.keys())
        return saved
    return model
