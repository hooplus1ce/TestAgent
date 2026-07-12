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

function mountVTable() {
  var el = findVisibleVTableElement();
  if (!el || !el.parentElement) return { ok: false, reason: 'visible .vtable not found' };

  var parent = el.parentElement;
  var fk = Object.keys(parent).find(
    function (k) { return k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance'); }
  );
  if (!fk) {
    var current = parent;
    for (var i = 0; i < 5; i++) {
      if (!current) break;
      fk = Object.keys(current).find(
        function (k) { return k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance'); }
      );
      if (fk) { parent = current; break; }
      current = current.parentElement;
    }
  }
  if (!fk) return { ok: false, reason: 'fiber key not found' };

  var fiber = parent[fk];
  for (var count = 0; fiber && count < 30; count++) {
    if (fiber.stateNode && fiber.stateNode.vtableInstance) {
      window._vtable = fiber.stateNode.vtableInstance;
      window._vtableElement = el;
      return { ok: true, levels: count };
    }
    fiber = fiber.return;
  }
  return { ok: false, reason: 'vtableInstance not found in ' + count + ' levels' };
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
      try { if (t.getCellValue) title = t.getCellValue(col, row) || ''; } catch (e) {}
      if (!title) { try { var define = t.getHeaderDefine ? t.getHeaderDefine(col, row) : null; if (define) title = define.title || define.caption || ''; } catch (e) {} }
      if (!title) { try { title = t.getHeaderField ? t.getHeaderField(col, row) || '' : ''; } catch (e) {} }

      var icons = [];
      if (isHeader) { icons = getCellIconBounds(t, col, row); }

      var titleText = typeof title === 'string' ? title : String(title);
      var entry = {
        col: col, row: row, isHeader: isHeader,
        title: titleText,
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
