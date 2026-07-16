// VTable 列行为分类 + 图标坐标扫描器（DrissionPage 移植版）
// =============================================================
// 在 *iframe(frame) 上下文* 中通过 frame.run_js 执行：
//   1. mountVTable()        → 把 VTable 实例挂到 window._vtable
//   2. scanColumns(maxCol)  → 返回列定义 + 表头图标的【顶层视口坐标】viewportX/viewportY
// 坐标说明：本脚本通过 window.frameElement.getBoundingClientRect()
//          直接产出顶层视口坐标(viewportX/viewportY)，Python 侧不再叠加偏移。
//          （避免在 iframe 内反查顶层 iframe 元素造成坐标偏移。）

// ============ 1. 挂载 VTable 实例 ============
function _duVisibleVTableElement(el) {
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

function findVisibleVTableElement() {
  var selectors = ['.vtable', '[class*="vtable"]'];
  for (var si = 0; si < selectors.length; si++) {
    var candidates = [].slice.call(document.querySelectorAll(selectors[si]));
    for (var ci = 0; ci < candidates.length; ci++) {
      var candidate = candidates[ci];
      if (!_duVisibleVTableElement(candidate)) continue;
      if (candidate.tagName === 'CANVAS' || candidate.querySelector('canvas')) return candidate;
    }
  }
  var canvases = [].slice.call(document.querySelectorAll('canvas'));
  for (var i = 0; i < canvases.length; i++) {
    if (_duVisibleVTableElement(canvases[i])) return canvases[i].parentElement || canvases[i];
  }
  return null;
}

function _duFiberKey(node) {
  if (!node) return null;
  return Object.keys(node).find(
    function (k) { return k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance'); }
  ) || null;
}

function _duFindVTableFromFiber(startNode, maxLevels) {
  var fk = _duFiberKey(startNode);
  if (!fk) return null;
  var fiber = startNode[fk];
  for (var count = 0; fiber && count < maxLevels; count++) {
    if (fiber.stateNode && fiber.stateNode.vtableInstance) {
      return { instance: fiber.stateNode.vtableInstance, levels: count };
    }
    fiber = fiber.return;
  }
  return null;
}

function mountVTable(force) {
  // Reuse cached instance when still valid for the same connected root.
  if (!force && window._vtable && window._vtableElement && window._vtableElement.isConnected
      && window._vtable.scenegraph && _duVisibleVTableElement(window._vtableElement)) {
    return {
      ok: true,
      reused: true,
      levels: 0,
      mountToken: window._vtableMountToken || null
    };
  }

  var el = findVisibleVTableElement();
  if (!el || !el.parentElement) return { ok: false, reason: 'visible .vtable not found' };

  // Walk up a few parents — fiber may sit on wrapper, not immediate parent.
  var anchors = [];
  var current = el;
  for (var i = 0; i < 8 && current; i++) {
    anchors.push(current);
    current = current.parentElement;
  }

  var found = null;
  for (var ai = 0; ai < anchors.length; ai++) {
    found = _duFindVTableFromFiber(anchors[ai], 40);
    if (found) break;
  }
  if (!found) {
    return { ok: false, reason: 'vtableInstance not found in fiber walk' };
  }

  var token = String(Date.now()) + '-' + Math.random().toString(36).slice(2, 8);
  window._vtable = found.instance;
  window._vtableElement = el;
  window._vtableMountToken = token;
  try {
    if (el && el.dataset) el.dataset.duVtableToken = token;
  } catch (e) {}
  return { ok: true, reused: false, levels: found.levels, mountToken: token };
}

function validateMountedVTable() {
  var t = window._vtable;
  var e = window._vtableElement;
  if (!t || !t.scenegraph || !e || !e.isConnected) {
    return { valid: false, reason: 'missing_instance_or_element' };
  }
  if (!_duVisibleVTableElement(e)) {
    return { valid: false, reason: 'root_not_visible' };
  }
  var style = window.getComputedStyle(e);
  var r = e.getBoundingClientRect();
  var left = Math.max(0, r.left), right = Math.min(window.innerWidth, r.right);
  var top = Math.max(0, r.top), bottom = Math.min(window.innerHeight, r.bottom);
  var hit = null;
  if (right > left && bottom > top) {
    hit = document.elementFromPoint((left + right) / 2, (top + bottom) / 2);
  }
  var hitOk = !!(hit && (hit === e || e.contains(hit)));
  if (!hitOk) {
    return { valid: false, reason: 'root_obscured', mountToken: window._vtableMountToken || null };
  }
  return {
    valid: true,
    mountToken: window._vtableMountToken || null,
    width: Math.round(r.width * 10) / 10,
    height: Math.round(r.height * 10) / 10,
    left: Math.round(r.left * 10) / 10,
    top: Math.round(r.top * 10) / 10
  };
}

function invalidateMountedVTable() {
  window._vtable = null;
  window._vtableElement = null;
  window._vtableMountToken = null;
  return { ok: true };
}

// ============ 2. 图标功能映射 ============
function iconFunction(name) {
  var n = (name || '').toLowerCase();
  if (n.indexOf('sort') !== -1) return '排序';
  if (n.indexOf('filter') !== -1) return '筛选';
  if (n.indexOf('dropdown') !== -1 || n.indexOf('downward') !== -1) return '下拉菜单';
  if (n.indexOf('freeze') !== -1 || n.indexOf('frozen') !== -1) return '冻结列';
  if (n.indexOf('checkbox') !== -1) return '复选框';
  if (n.indexOf('radio') !== -1) return '单选';
  if (n.indexOf('switch') !== -1) return '开关';
  if (n.indexOf('expand') !== -1) return '展开';
  if (n.indexOf('collapse') !== -1) return '折叠';
  if (n.indexOf('content') !== -1) return '文本内容';
  if (n === 'group' || n === '') return '';
  return name;
}

// ============ 3. 获取表头图标坐标（场景图局部坐标）============
function getCellIconBounds(vtable, col, row) {
  var cellGroup = vtable.scenegraph && vtable.scenegraph.getCell ? vtable.scenegraph.getCell(col, row) : null;
  if (!cellGroup) return [];
  var icons = [];
  var MAX_COORD = 1e15;
  function collect(node, depth) {
    if (!node || depth > 3) return;
    if (depth > 0) {
      var bounds = node.globalAABBBounds;
      var name = node.name || '';
      if (bounds && typeof bounds.x1 === 'number' && name && name !== 'text') {
        var x = bounds.x1, y = bounds.y1;
        var w = bounds.x2 - bounds.x1, h = bounds.y2 - bounds.y1;
        if (Math.abs(x) < MAX_COORD && Math.abs(y) < MAX_COORD && w > 0 && w < 500 && h > 0 && h < 500) {
          icons.push({
            name: name, func: iconFunction(name),
            x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10,
            width: Math.round(w * 10) / 10, height: Math.round(h * 10) / 10,
            centerX: Math.round(((bounds.x1 + bounds.x2) / 2) * 10) / 10,
            centerY: Math.round(((bounds.y1 + bounds.y2) / 2) * 10) / 10
          });
        }
      }
    }
    if (node.children) {
      for (var i = 0; i < node.children.length; i++) collect(node.children[i], depth + 1);
    }
  }
  collect(cellGroup, 0);
  return icons;
}

// ============ 4. 列 body 行为分类 ============
function getFirstBodyRow(t, col) {
  var headerLevel = t.columnHeaderLevelCount || t.frozenRowCount || 1;
  for (var r = headerLevel; r < t.rowCount; r++) {
    try { if (t.isHeader && !t.isHeader(col, r)) return r; } catch (e) { return r; }
    if (!t.isHeader) return r;
  }
  return headerLevel;
}

function classifyColumnBody(t, col) {
  var sampleRow = getFirstBodyRow(t, col);
  if (sampleRow >= t.rowCount) return { behavior: 'none', detail: '', editable: false, bodyType: '' };

  var hasEditor = false;
  try { hasEditor = !!(t.isHasEditorDefine && t.isHasEditorDefine(col)); } catch (e) {}

  var editable = hasEditor;
  var type = '';
  try { type = t.getCellType(col, sampleRow) || ''; } catch (e) {}

  var behavior = 'none', detail = '';
  if (type === 'checkbox') { behavior = 'control:checkbox'; detail = '可勾选'; }
  else if (type === 'button') { behavior = 'control:button'; detail = '可点击'; }
  else if (type === 'radio') { behavior = 'control:radio'; detail = '可单选'; }
  else if (type === 'switch') { behavior = 'control:switch'; detail = '可开关'; }
  else if (type === 'link') { behavior = 'link'; detail = '可跳转'; }
  else {
    var hasCustom = false;
    try { hasCustom = !!(t.getCustomLayout && t.getCustomLayout(col, sampleRow)); } catch (e) {}
    if (!hasCustom) { try { hasCustom = !!(t.getCustomRender && t.getCustomRender(col, sampleRow)); } catch (e) {} }
    if (hasCustom) { behavior = 'popup-candidate'; detail = '自定义弹窗/跳转'; }
    if (behavior === 'none' && editable) { detail = '可编辑文本'; }
    else if (behavior === 'none') { detail = '纯文本'; }
  }
  return { behavior: behavior, detail: detail, editable: editable, bodyType: type };
}

// ============ 5. 扫描所有列 ============
function scanColumns(maxCol) {
  var t = window._vtable;
  if (!t) return null;
  var headerLevelCount = t.columnHeaderLevelCount || 1;
  var results = [];
  // 可见 VTable 根元素相对【iframe 自身视口】的偏移。
  var vtEl = window._vtableElement;
  if (!_duVisibleVTableElement(vtEl)) vtEl = findVisibleVTableElement();
  if (!vtEl) return null;
  var vtRect = vtEl.getBoundingClientRect();
  // iframe 在顶层视口的偏移（JS 一次算完，Python 不再叠加）
  var ifrRect = window.frameElement ? window.frameElement.getBoundingClientRect() : { left: 0, top: 0 };

  for (var col = 0; col < Math.min(maxCol, t.colCount || maxCol); col++) {
    var bodyInfo = classifyColumnBody(t, col);
    for (var row = 0; row < headerLevelCount; row++) {
      var isHeader = false;
      try { isHeader = !!(t.isHeader && t.isHeader(col, row)); } catch (e) {}

      var title = '';
      var field = '';
      var define = null;
      try { if (t.getCellValue) title = t.getCellValue(col, row) || ''; } catch (e) {}
      try { define = t.getHeaderDefine ? t.getHeaderDefine(col, row) : null; } catch (e) { define = null; }
      if (define) {
        if (!title) title = define.title || define.caption || '';
        // Column identity: prefer explicit data field over display title.
        field = define.field || define.key || define.dataIndex || define.fieldKey || '';
        if (field && typeof field === 'object') {
          field = field.field || field.key || field.dataIndex || '';
        }
      }
      if (!field) {
        try { field = t.getHeaderField ? (t.getHeaderField(col, row) || '') : ''; } catch (e) {}
      }
      if (!title && field) title = field;

      var icons = [];
      if (isHeader) { icons = getCellIconBounds(t, col, row); }

      var titleText = typeof title === 'string' ? title : String(title == null ? '' : title);
      var fieldText = typeof field === 'string' ? field : String(field == null ? '' : field);
      var entry = {
        col: col, row: row, isHeader: isHeader,
        title: titleText,
        field: fieldText,
        // Stable identity hint for agents/tools: col + field + title
        identity: {
          col: col,
          field: fieldText || null,
          title: titleText || null
        },
        titlePreview: titleText.length > 80 ? titleText.substring(0, 80) + '…' : titleText,
        bodyBehavior: bodyInfo.behavior, bodyDetail: bodyInfo.detail,
        bodyType: bodyInfo.bodyType, bodyEditable: bodyInfo.editable,
        icons: icons.map(function (ic) {
          // 顶层视口坐标 = iframe 偏移 + .vtable 偏移 + 图标中心坐标（一次算完）
          return {
            name: ic.name, func: ic.func,
            width: ic.width, height: ic.height,
            viewportX: Math.round((ifrRect.left + vtRect.left + ic.centerX) * 10) / 10,
            viewportY: Math.round((ifrRect.top + vtRect.top + ic.centerY) * 10) / 10
          };
        })
      };
      results.push(entry);
    }
  }
  return results;
}
