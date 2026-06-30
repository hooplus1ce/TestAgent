// VTable 工具函数：根据中文列标题获取该列所有单元格值
// ============================================================
// 用途：
//   - 筛选验证：执行筛选后调用此函数，验证该列所有值是否符合筛选条件
//   - 数据校验：对比某列的实际值与期望值，发现数据映射不一致（如数字码 vs 中文标签）
//   - 样本采集：获取某列的全部数据用于后续分析
//
// 特性：
//   - 标题双重匹配：精确匹配 → 包含匹配，容忍 "制令单号 *" 或 "制令单号（必填）" 等带后缀的标题
//   - 三种取值模式（通过 raw 参数控制）：
//     raw=false  —— 场景图优先：先读渲染后的视觉文本（经过 customLayout/format 处理），
//                    若无 sceneGraph 则降级为 getCellValue。返回的文本与用户肉眼看到的一致。
//     raw=true   —— 原始数据：读未经格式化的原始字段值（如数字码 0/1/2/3）
//   - 返回值尊重 VTable 当前的排序、筛选、分页状态
//
// 用法：
//   var visualTexts = getColumnValuesByTitle(window._vtable, '制令单类型');
//   // → ['普通制令单', '普通制令单', '包装制令单', ...]（视觉文本，与界面一致）
//
//   var rawCodes = getColumnValuesByTitle(window._vtable, '制令单类型', true);
//   // → ['0', '0', '1', ...]（原始数字码）
//
//   var orderNos = getColumnValuesByTitle(window._vtable, '制令单号');
//   // → ['MO202606270041', 'MO202606260019', ...]
// ============================================================

function getColumnValuesByTitle(vtable, title, raw) {
  if (!vtable || !title) return null;

  var headerLevelCount = vtable.columnHeaderLevelCount || vtable.frozenRowCount || 1;
  var targetCol = -1;

  // ① 遍历所有列，在表头行中找匹配的中文标题
  for (var col = 0; col < vtable.colCount; col++) {
    for (var row = 0; row < headerLevelCount; row++) {
      var headerValue = '';
      try {
        headerValue = vtable.getCellValue(col, row) || '';
      } catch (e) {}

      // 精确匹配或包含匹配
      if (headerValue === title || headerValue.indexOf(title) !== -1) {
        targetCol = col;
        break;
      }
    }
    if (targetCol !== -1) break;
  }

  if (targetCol === -1) return null;

  // ② 循环所有 body 行取值
  var values = [];
  for (var row = headerLevelCount; row < vtable.rowCount; row++) {
    var val = null;
    try {
      if (raw) {
        // === 原始值模式：读未经 format 的数据 ===
        val = vtable.getCellRawValue
          ? vtable.getCellRawValue(targetCol, row)
          : vtable.getCellOriginValue(targetCol, row);
      } else {
        // === 视觉文本模式：场景图优先，降级到 getCellValue ===
        val = getVisualCellText(vtable, targetCol, row);
        if (val === null || val === undefined) {
          val = vtable.getCellValue(targetCol, row);
        }
      }
    } catch (e) {}
    values.push(val);
  }

  return values;
}

// 通过 VTable 场景图获取单元格的渲染后视觉文本
// 能正确读取 customLayout、cellType、formatter 处理后的文本
// 返回 null 表示场景图不可用或找不到 text 节点
function getVisualCellText(vtable, col, row) {
  try {
    // 场景图 API：需要 VTable 已渲染且该行在视口内
    var cellGroup = vtable.scenegraph && vtable.scenegraph.getCell
      ? vtable.scenegraph.getCell(col, row)
      : null;
    if (!cellGroup) return null;

    var textNode = null;
    // 遍历场景图，找到第一个非空的 text 节点
    (function walk(node, depth) {
      if (!node || depth > 5 || textNode) return;
      if (node.type === 'text' && node.attribute && node.attribute.text) {
        textNode = node;
        return;
      }
      if (node.children) {
        for (var i = 0; i < node.children.length; i++) {
          walk(node.children[i], depth + 1);
        }
      }
    })(cellGroup, 0);

    if (textNode && textNode.attribute && textNode.attribute.text !== undefined) {
      return textNode.attribute.text;
    }
    return null;
  } catch (e) {
    return null;
  }
}
