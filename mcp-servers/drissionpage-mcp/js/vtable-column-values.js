// VTable 工具：按中文列标题取列值 + 单元格坐标/滚动（DrissionPage 移植版）
// 在 *iframe(frame) 上下文* 通过 frame.run_js 执行，依赖 window._vtable（先 mountVTable()）。

// ---- 按中文列标题获取该列所有单元格值 ----
// raw=false：场景图视觉文本（与界面一致）；raw=true：原始字段值（如数字码）
function getColumnValuesByTitle(vtable, title, raw) {
  if (!vtable || !title) return null;
  var headerLevelCount = vtable.columnHeaderLevelCount || vtable.frozenRowCount || 1;
  var targetCol = -1;
  for (var col = 0; col < vtable.colCount; col++) {
    for (var row = 0; row < headerLevelCount; row++) {
      var headerValue = '';
      try { headerValue = vtable.getCellValue(col, row) || ''; } catch (e) {}
      if (headerValue === title || headerValue.indexOf(title) !== -1) { targetCol = col; break; }
    }
    if (targetCol !== -1) break;
  }
  if (targetCol === -1) return null;

  var values = [];
  for (var r = headerLevelCount; r < vtable.rowCount; r++) {
    var val = null;
    try {
      if (raw) {
        val = vtable.getCellRawValue ? vtable.getCellRawValue(targetCol, r) : vtable.getCellOriginValue(targetCol, r);
      } else {
        val = getVisualCellText(vtable, targetCol, r);
        if (val === null || val === undefined) { val = vtable.getCellValue(targetCol, r); }
      }
    } catch (e) {}
    values.push(val);
  }
  return values;
}

// ---- 场景图渲染后视觉文本 ----
function getVisualCellText(vtable, col, row) {
  try {
    var cellGroup = vtable.scenegraph && vtable.scenegraph.getCell ? vtable.scenegraph.getCell(col, row) : null;
    if (!cellGroup) return null;
    var textNode = null;
    (function walk(node, depth) {
      if (!node || depth > 5 || textNode) return;
      if (node.type === 'text' && node.attribute && node.attribute.text) { textNode = node; return; }
      if (node.children) { for (var i = 0; i < node.children.length; i++) walk(node.children[i], depth + 1); }
    })(cellGroup, 0);
    if (textNode && textNode.attribute && textNode.attribute.text !== undefined) return textNode.attribute.text;
    return null;
  } catch (e) { return null; }
}

// ---- 单元格中心【顶层视口坐标】（供 click_xy / actions.move_to 直接使用）----
// 内部通过 window.frameElement.getBoundingClientRect() 一次算到顶层视口，
// Python 侧不再叠加 iframe 偏移。
function getCellCenterViewport(col, row) {
  var t = window._vtable;
  if (!t) return null;
  var ifrRect = window.frameElement ? window.frameElement.getBoundingClientRect() : { left: 0, top: 0 };
  var vtEl = document.querySelector('.vtable') || document.querySelector('[class*="vtable"]');
  var vtRect = vtEl ? vtEl.getBoundingClientRect() : { left: 0, top: 0 };
  var cx = null, cy = null;
  // 优先用 scenegraph 的 globalAABBBounds
  try {
    var cell = t.scenegraph && t.scenegraph.getCell ? t.scenegraph.getCell(col, row) : null;
    if (cell && cell.globalAABBBounds) {
      var b = cell.globalAABBBounds;
      cx = (b.x1 + b.x2) / 2; cy = (b.y1 + b.y2) / 2;
    }
  } catch (e) {}
  // 降级用 getCellRect
  if (cx === null) {
    try {
      var rect = t.getCellRect ? t.getCellRect(col, row) : null;
      if (rect) {
        var r0 = rect.bounds || rect;
        cx = (r0.x1 + r0.x2) / 2; cy = (r0.y1 + r0.y2) / 2;
      }
    } catch (e) {}
  }
  if (cx === null) return null;
  return {
    viewportX: Math.round((ifrRect.left + vtRect.left + cx) * 10) / 10,
    viewportY: Math.round((ifrRect.top + vtRect.top + cy) * 10) / 10
  };
}

// ---- 滚动到目标单元格（确保在视口内再取坐标）----
function scrollToCell(col, row) {
  var t = window._vtable;
  if (!t || !t.scrollToCell) return { ok: false, reason: 'no scrollToCell' };
  try { t.scrollToCell({ col: col, row: row }); return { ok: true }; }
  catch (e) { return { ok: false, reason: String(e) }; }
}
