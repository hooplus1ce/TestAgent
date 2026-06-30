# 筛选字段穷尽与验证规范

> 承接 SKILL.md Phase 1-a / 1-b。本文件覆盖：筛选模式切换与展开、筛选字段扫描、运算符/选项穷尽、以及**筛选结果的浏览器内验证流程**（筛选用例质量的核心保障）。

## 零、筛选模式切换与展开（扫描前置 MUST）

每次进入一个二级菜单模块（查询/报表/列表类），**必须先执行以下操作链**，将筛选区域从「弹窗模式」切换到「内联模式」并展开所有折叠字段，使所有筛选字段、运算符选项、下拉待选项都暴露在 DOM 中，供 §一 扫描和分析。**若页面不包含模式切换按钮（旧版 scm-spo 直接内联），则跳过此步骤。**

```javascript
// ===== 筛选模式切换与展开 =====
// 必须在目标模块 iframe 的 contentWindow 中执行

// Step 1: 检测筛选模式切换按钮（anticon-bars 下拉触发按钮）
var modeToggleBtn = Array.from(document.querySelectorAll('button')).find(function(b){
  return b.querySelector('.anticon-bars') && b.classList.contains('ant-dropdown-trigger');
});

if (modeToggleBtn) {
  // Step 2: 点击模式切换按钮，弹出下拉菜单
  modeToggleBtn.click();
  await new Promise(function(r){ setTimeout(r, 1000); });

  // Step 3: 检测当前选中的模式
  var selectedMode = document.querySelector('.ant-dropdown-menu-item-selected');
  var currentMode = selectedMode ? selectedMode.textContent.trim() : '';

  // Step 4: 如果当前是「弹窗模式」，切换到「内联模式」
  if (currentMode.indexOf('弹窗') >= 0) {
    var inlineItem = Array.from(document.querySelectorAll('.ant-dropdown-menu-item')).find(function(item){
      return item.textContent.trim().indexOf('内联') >= 0;
    });
    if (inlineItem) {
      inlineItem.click();
      await new Promise(function(r){ setTimeout(r, 1500); });
    }
  } else {
    // 已在内联模式，关闭下拉
    modeToggleBtn.click();
    await new Promise(function(r){ setTimeout(r, 500); });
  }
}

// Step 5: 检测筛选区展开/折叠状态，仅在折叠时点击展开
var expandBtn = Array.from(document.querySelectorAll('button')).find(function(b){
  var text = b.textContent.trim().replace(/\s+/g, '');
  return text.indexOf('展开') >= 0 || text.indexOf('收起') >= 0;
});
if (expandBtn) {
  var btnText = expandBtn.textContent.trim().replace(/\s+/g, '');
  if (btnText.indexOf('展开') >= 0) {
    // 当前折叠 → 点击展开
    expandBtn.click();
    await new Promise(function(r){ setTimeout(r, 1500); });
    console.log('筛选区已从折叠展开');
  } else {
    // 当前已展开（按钮显示「收起」）→ 无需操作
    console.log('筛选区已展开，跳过展开操作');
  }
} else {
  console.log('未找到展开/收起按钮，可能无折叠筛选区');
}
```

**目的**：许多模块默认使用「弹窗模式」或折叠状态，导致 DOM 中不可见大量筛选字段和选项。只有在「内联模式」且展开后，§一 的扫描代码才能检测到全部字段及其输入类型。**若此步骤未执行，后端可能返回不完整的筛选字段列表，导致测试用例遗漏。**

## 一、筛选字段扫描（查询报表类强制）

定位 iframe 后扫描所有筛选行。两种模式：

```javascript
// 模式 A：内联筛选（scm-spo 旧版）— field/operator/value 三列
var selects = document.querySelectorAll('.ant-select');
// selects[0]=字段名, selects[1]=运算符, selects[2]=值输入

// 模式 B：高级搜索弹窗 — 点击「展开▼」弹出
// 弹窗内 .ant-row .ant-col-xs-12 每行一个筛选条件
```

穷尽每个字段的运算符（包含/等于/介于/为空…）和下拉选项后，产出**筛选字段矩阵**。**矩阵必须包含每列的实际输入类型**（text input / dropdown / searchable-dropdown / date range），因为字段的输入方式决定了用例步骤的写法。弹窗/下拉的检测代码模板见 `modal-types.md`。

### 输入类型检测代码

```javascript
// 检测每个筛选字段的值输入类型
var allAntRows = document.querySelector('.legions-pro-quick-filter .ant-row').children;
var fieldTypes = [];
for (var i = 0; i < allAntRows.length; i++) {
  var innerRow = allAntRows[i].querySelector('.ant-row');
  if (!innerRow) continue;
  var cols = innerRow.children;
  if (cols.length < 3) continue;
  var field = cols[0].textContent.trim();
  var hasSelect = !!cols[2].querySelector('.ant-select');
  var hasSearchInput = !!cols[2].querySelector('.ant-select-search__field');
  var hasTextInput = !!cols[2].querySelector('input[type="text"]:not(.ant-select-search__field)');
  var type = 'input';
  if (hasSelect && hasSearchInput) type = 'searchable-dropdown';
  else if (hasSelect) type = 'dropdown';
  fieldTypes.push({ field: field, inputType: type });
}
```

## 二、筛选验证方法（MUST，AI 内部使用，不出现在 Excel）

生成筛选用例时，AI **必须先用 `getColumnValuesByTitle()` 在浏览器中执行验证**，确认筛选逻辑正确后，再将验证结论转化为**纯中文描述**写入 Excel 预期结果列(L)。

> ⚠️ 这一步是筛选用例可信度的根基。不经验证直接写预期结果 = 凭空臆测，违反技能核心原则。

### 验证流程

```javascript
// Step 1: 执行筛选操作（输入值 → 点查询）
// Step 2: 用 getColumnValuesByTitle 取目标列所有值（scripts/vtable-column-values.js）
var values = getColumnValuesByTitle(window._vtable, '制令单号');
// Step 3: 内部断言
var allMatch = values.every(function(v){ return v && v.indexOf('MO202606') !== -1; });
// Step 4: 将结论转化为中文写入 Excel L 列
```

### 内部结论 → Excel 预期结果映射

| 内部验证结论 | Excel 预期结果(L) 写入内容 |
|-------------|--------------------------|
| `allMatch === true` | 「表格所有制令单号均包含『MO202606』，无不符合的记录」 |
| `allMatch === false` | 「存在不符合筛选条件的记录，需确认筛选逻辑」 |

### 组合查询（多列分别断言后合并）

```javascript
var ok1 = getColumnValuesByTitle(vt, '制令单号').every(v => v && v.indexOf('MO') !== -1);
var ok2 = getColumnValuesByTitle(vt, '生产部门').every(v => v === '冲压车间');
// → Excel L 列: 「所有制令单号包含 MO，且所有生产部门为『冲压车间』」
```

## 三、getColumnValuesByTitle 用法详解

```javascript
// 视觉文本（默认，经 customLayout 映射，与用户肉眼一致）
getColumnValuesByTitle(window._vtable, '制令单号')
// → ['MO202606270041', 'MO202606260019', ...]

// 原始值（未经格式化，如数字类型/数据码）
getColumnValuesByTitle(window._vtable, '生产数量', true)
// → [3, 2, 45, 36719, ...]
```

**标题匹配策略**：先精确匹配 `headerValue === title`，不中再包含匹配 `headerValue.indexOf(title) !== -1`。即使标题带后缀（「制令单号 *」「制令单号（必填）」）也能命中。

### raw 参数的关键区别

```
getColumnValuesByTitle(vt, '制令单类型', false)
  → ['普通制令单', '普通制令单', '包装制令单']   // 视觉文本（customLayout 映射后）

getColumnValuesByTitle(vt, '制令单类型', true)
  → ['0', '0', '1']                            // 原始数据码
```

**⚠️ 筛选验证 MUST 用 `raw=false`（视觉文本）**：筛选弹窗可能显示原始码而表格显示中文，必须用视觉文本断言才与用户体验一致。

### 视觉文本读取原理

`raw=false` 优先通过 VTable 场景图 API（`scenegraph.getCell`）读取 canvas 实际渲染文字，能正确获取 customLayout / cellType / formatter 处理后的视觉文本。视觉文本需单元格在视口内才能读到（场景图只渲染可见区域），返回 `null` 时自动降级为 `getCellValue`。

## 四、筛选用例生成要点

| 要点 | 要求 |
|------|------|
| 单字段筛选 | 每个字段 + 每个常用运算符至少 1 条用例 |
| 组合查询 | 至少 1 条多字段组合用例（验证 AND 逻辑） |
| 清空/重置 | 验证清空筛选后表格恢复全量 |
| 边界值 | 数值类字段测最小/最大/越界（如数量=0、负数） |
| 空结果 | 输入不存在的值，验证空状态展示 |
| 级别 | 主字段筛选 中级，组合/重置 低级，边界/空结果 低级 |

**预期结果必须基于实际 `getColumnValuesByTitle` 验证结论**，禁止写「筛选结果正确」这类不可验证表述。

### 测试数据取样规则（MUST 从页面实际数据出发，禁止凭空构造）

设计筛选用例时，测试数据必须取自页面实际数据，不能臆造：

1. **取真实值样本**：在 VTable 中取前 100 行数据（不足则取全部），从中随机挑选作为测试数据，避免只取前 5 行导致样本偏差（如前几行可能全是同一状态/同一部门）
2. **dropdown / searchable-dropdown 字段**：打开下拉列表记录**所有可选值**，测试数据从可选项中取
3. **text input 字段**：从实际数据中取一个**存在的值**作为测试数据
4. **运算符匹配**：「等于」用完全匹配的实际值；「包含」用实际值的子串
5. **验证**：用 `getColumnValuesByTitle()` 确认筛选后各列的值符合预期

**反例**（教训）：「成品名称包含『测试』」→ 实际该字段是 searchable-dropdown，且没有完全匹配「测试」的选项。
**正例**：「成品名称选择『LZY-测试品1』」→ 该选项在 51 个下拉选项中真实存在。
