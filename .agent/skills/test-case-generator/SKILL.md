---
name: test-case-generator
description: 为 WMS/MOM/ERP 等企业系统迭代生成测试用例。使用场景：用户要求生成某个模块的测试用例，或需要补全覆盖率缺口。
---

# Test Case Generator Skill

你是一个迭代式企业系统测试用例生成器，通过多轮对话与用户协作，逐步完善测试用例，最终输出格式化的 Excel 文档。

## 0. 可配置变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `ENTERPRISE_PREFIX` | `NB` | 企业缩写，用于用例编号前缀 |
| `DEFAULT_AUTHOR` | `Hooplus1ce` | 默认编写人 |
| `OUTPUT_DIR` | `.` | 输出目录 |
| `DOMAIN` | 用户指定 | MOM / ERP / WMS |
| `MODULE_NAME` | 用户指定 | 如 `生产管理_制造排产`，用于文件命名 |
| `MODULE_LEVEL1` | 用户指定 | 一级模块，如「生产管理」 |
| `MODULE_LEVEL2` | 用户指定 | 二级模块，如「制造排产」 |
| `MODULE_PINYIN` | 用户指定 | 二级模块拼音缩写，如 `ZZPC` |

## 0-b. 系统接入

接入 Hoolinks SCM 演示系统时：

**浏览器连接规则（MUST）**：
- 默认接管端口号 9222 下已打开的 Chrome 浏览器实例
- 使用 `browser open app.cdp_url http://localhost:9222` 连接，**禁止启动新浏览器**
- 连接后检查当前页面是否已有 SCM 会话；若有则直接复用，无需重新登录

Session 过期处理：
- 检测 top 层 `.ant-confirm` 弹窗（提示「您还未登录或登录信息过期，请重新登录」）
- 按 `scripts/scm-login.js` 中的 `refreshSession()` 流程：注入 Cookie 后刷新页面

### 0-c. 验证前初始状态重置（MUST）

每次验证用例前，必须重置页面到初始状态，确保用例间不互相影响：

```javascript
// Step 1: 关闭当前 tab（点击 tab 上的关闭×按钮）
var tabCloseBtn = document.querySelector('.ant-tabs-tab-active.outSide .anticon-close');
if (tabCloseBtn) tabCloseBtn.click();
await new Promise(function(r){ setTimeout(r, 2000); });

// Step 2: 从左侧菜单栏重新进入目标模块
var menuItem = [...document.querySelectorAll('.ant-menu-item, li[class*="ant-menu"]')].find(function(el){
  return el.textContent.trim().indexOf('制造排产') >= 0;
});
if (menuItem) menuItem.click();

// Step 3: 等待 iframe 加载完成（轮询最多 15 秒，每 500ms 检查一次）
var maxWait = 30; // 30 iterations × 500ms = 15s
for (var i = 0; i < maxWait; i++) {
  await new Promise(function(r){ setTimeout(r, 500); });
  var iframe = document.querySelector('iframe[src*="makerTable"]');
  if (iframe) {
    // iframe 存在后，再等待 VTable 渲染
    try {
      var doc = iframe.contentDocument || iframe.contentWindow.document;
      if (doc.querySelector('.vtable')) break;
    } catch(e) {}
  }
}
```

| 用途 | 工具 |
| 浏览器自动化 | `browser`（必须用 `app.cdp_url: "http://localhost:9222"` 连接已有 Chrome，禁止用无头浏览器） |
| 文件/路径读取 | `read` |

| 执行 Python | `eval`（持久 kernel） |
| Shell 命令 | `bash` |

## 2. Python 依赖

- `openpyxl` >= 3.1 — 通过 `uv add openpyxl` 安装

## 3. 迭代工作流

严格按以下 4 个阶段推进，不可跳步。

### Phase 1 — 需求采集

### 0-e. 视口坐标转换通用规则（MUST）

所有 `page.mouse.click()` / `page.mouse.move()` 的坐标**必须是主页面视口坐标**（绝对坐标），不可使用 iframe 内或 VTable canvas 内的相对坐标。

**来自 iframe 内元素**（如工具栏按钮、弹窗按钮）：
```javascript
var iframeRect = document.querySelector('iframe').getBoundingClientRect();
var elRect = iframe.contentDocument.querySelector('button').getBoundingClientRect();
// elRect 是 iframe 相对坐标 → 需加 iframe 偏移
var viewportX = iframeRect.left + elRect.left + elRect.width / 2;
var viewportY = iframeRect.top  + elRect.top  + elRect.height / 2;
await page.mouse.click(viewportX, viewportY);
```

**来自 VTable 场景图**（canvas 内元素）：
```javascript
var viewportX = iframeRect.left + vtableRect.left + scenegraphBounds.x1;
var viewportY = iframeRect.top  + vtableRect.top  + scenegraphBounds.y1;
```

**来自 VTable `getCellRect`**（需 scroll 修正）：
```javascript
var viewportX = iframeRect.left + vtableRect.left + cellCenterX - vt.scrollLeft;
var viewportY = iframeRect.top  + vtableRect.top  + cellCenterY - vt.scrollTop;
```

**Scanner 产出**（已算好视口坐标，直接使用）：
```javascript
await page.mouse.click(scanResult[col].icons[0].viewportX, scanResult[col].icons[0].viewportY);
```

**常见错误**：
- ❌ `page.mouse.click(elRect.left, elRect.top)` — 这是在 iframe 内取得的坐标，点击位置偏移整个 iframe 的距离
- ❌ `page.mouse.click(vt.getCellRect(col,row).bounds.x1, ...)` — 未加 scroll 修正，滚动后偏移

- 确认领域（显式询问用户，不要从 URL 推断）
- 按 **输入 → 处理 → 输出** 引导用户描述业务流程
- SCM 系统：若用户只提供模块名，执行以下流程：
【Step 0 — DOM 结构俯瞰】进入模块页面后，第一件事不是点任何按钮，而是站在最高视角分析页面结构。在 iframe 中执行：

```javascript
var layout = await f.evaluate(function(){
  return {
    tabs: [...document.querySelectorAll('.ant-radio-button-wrapper, .ant-tabs-tab')].map(function(t){
      return { text: t.textContent.trim(), selected: t.classList.contains('ant-radio-button-wrapper-checked') || t.classList.contains('ant-tabs-tab-active') };
    }),
    buttons: [...document.querySelectorAll('button')].filter(function(b){ return b.offsetParent !== null && b.textContent.trim(); }).map(function(b){
      return { text: b.textContent.trim().replace(/\s+/g, ''), disabled: b.disabled };
    }),
    filters: [...document.querySelectorAll('.ant-row')].filter(function(r){ return r.querySelectorAll('.ant-select').length >= 2; }).map(function(r){
      var s = r.querySelectorAll('.ant-select');
      return { field: s[0]?.textContent?.trim(), operator: s[1]?.textContent?.trim() };
    })
  };
});
// layout 产出：所有页签、按钮、筛选字段的全局清单
```

**产出物**：页面布局全景图。这张图决定了后续 DFS 遍历的边界和优先级——哪些区域有交互、哪些是静态展示，一目了然。

  Step 1. 侧边栏点击目标模块 → 切换到 iframe
  **Step 1.5 执行筛选模式切换与展开（MUST）→ 将所有筛选字段暴露在 DOM 中（详见 Phase 1-a）**
  Step 2. 运行 `scripts/vtable-scanner.js` 挂载 VTable → `scanColumns()` 获取列行为和图标坐标
  Step 3. 执行 Phase 1-b 穷尽筛选字段
  Step 4. 执行 Phase 1-c DFS 穷尽所有按钮 + Phase 1-c-2 弹窗探索
  Step 5. 执行 Phase 1-d 探测 VTable 单元格交互
  Step 6. 用实际页面数据替代用户描述
- 记录业务规则、状态流转节点

**推进条件**: 领域和模块确认 + 主链路描述清楚 + ≥2 条业务规则 + 测试类型确认


### Phase 1-a — 筛选模式切换与展开（所有查询报表类 MUST）

每次进入一个二级菜单模块（查询/报表/列表类），必须先执行以下操作链，
将筛选区域从「弹窗模式」切换到「内联模式」并展开所有折叠字段，
使所有筛选字段、运算符选项、下拉待选项都暴露在 DOM 中，供后续 Phase 1-b 扫描和分析。
若页面不包含该模式切换按钮（旧版 scm-spo 直接内联），则跳过此步骤。

**操作链（在目标模块的 iframe 中 MUST 完整执行）：**

```javascript
// ===== 筛选模式切换与展开 =====
// 必须在 iframe 的 contentWindow 中执行

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


**目的**：许多模块默认使用「弹窗模式」或折叠状态，导致 DOM 中不可见大量筛选字段和选项。
只有在「内联模式」且展开后，Phase 1-b 的扫描代码才能检测到全部字段及其输入类型。
若此步骤未执行，后端可能返回不完整的筛选字段列表，导致测试用例遗漏。

### Phase 1-b — 筛选字段穷尽（查询报表类强制）

定位 iframe 后，扫描所有筛选行，每行 = 字段选择 + 运算符 + 值输入：

```javascript
// 内联筛选模式（scm-spo 旧版）：field/operator/value 三列
var selects = document.querySelectorAll('.ant-select');
// selects[0] = 字段名, selects[1] = 运算符, selects[2] = 值输入

// 高级搜索弹窗模式：点击「展开▼」弹出弹窗
// 弹窗内 .ant-row .ant-col-xs-12 每行一个筛选条件
```

穷尽运算符和下拉选项后，产出**筛选字段矩阵**，矩阵必须包含每列的**实际输入类型**（text input / dropdown / searchable-dropdown / date range），因为字段的输入方式决定了用例步骤的写法。输入类型检测代码：

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

**重要**：设计筛选用例时，必须从页面**实际数据**出发，不能凭空构造。具体流程：

1. 在 VTable 中获取各列的**真实值样本**：取前 100 行数据（若不足则取全部），从中随机挑选作为测试数据，避免只取前 5 行导致样本偏差（如前几行可能全是同一状态/同一部门）
2. 对于 dropdown/searchable-dropdown 字段，打开下拉列表记录**所有可选值**
3. 对于 text input 字段，从实际数据中取一个**存在的值**作为测试数据
4. 对于「等于」操作符，使用完全匹配的实际值；对于「包含」操作符，使用实际值的子串
5. 验证筛选结果时，用 `getColumnValuesByTitle()` 确认筛选后各列的值符合预期

**反例**（本次教训）：「成品名称包含「测试」」→ 实际该字段是 searchable-dropdown，且没有完全匹配「测试」的选项
**正例**：「成品名称选择「LZY-测试品1」」→ 该选项在 51 个下拉选项中真实存在

详见 `references/modal-types.md` 中的检测代码模板。

生成筛选用例时，AI **必须先用 `getColumnValuesByTitle()` 在浏览器中执行验证**，确认筛选逻辑正确后，再将验证结论转化为**纯中文描述**写入 Excel 的预期结果列。

具体流程：

```javascript
// AI 内部执行（不出现在 Excel 中）：
// Step 1: 执行筛选操作
// Step 2: 用 getColumnValuesByTitle 取列值
var values = getColumnValuesByTitle(window._vtable, '制令单号');
// Step 3: 内部断言
var allMatch = values.every(function(v){ return v.indexOf('MO202606') !== -1; });
// Step 4: 将结论转化为中文写入 Excel 预期结果
```

| 内部验证结论 | Excel 预期结果列写入的内容 |
|-------------|--------------------------|
| `allMatch === true` | 「表格所有制令单号均包含「MO202606」，无不符合的记录」 |
| `allMatch === false` | 「存在不符合筛选条件的记录，需确认筛选逻辑」 |

对组合查询，每列分别断言后，合并为一条中文描述：

```javascript
// AI 内部分别断言
var ok1 = getColumnValuesByTitle(vt, '制令单号').every(function(v){ return v && v.indexOf('MO') !== -1; });
var ok2 = getColumnValuesByTitle(vt, '生产部门').every(function(v){ return v === '冲压车间'; });
// → Excel 写入: 「所有制令单号包含 MO，且所有生产部门为「冲压车间」」
```

### Phase 1-c — 按钮 DFS 穷尽（所有模块强制）

**核心原则：深度优先遍历（DFS）**。点一个按钮 → 弹窗出来 → **弹窗作为一个独立的交互节点**，对其 DOM 进行完整分析后生成派生 DFS 子节点（详见 `Phase 1-c-2` →「弹窗内部 DFS」）→ 关闭 → 点下一个按钮。

```
for 每个按钮 in 当前页面:
    【前置】关闭所有残留消息弹窗
    点击按钮
    if 交互弹窗 → 记录标题/字段/子按钮 → DFS 弹窗 → 关闭
    if 业务确认弹窗（iframe 内 .ant-confirm） → 记录标题/内容/按钮 → 测试「取消」→ 关闭× → 测试「确定」
    if 消息提醒 → 提取文字 → 关闭（不残留）
    if 页面跳转 → 重新获取 iframe → 递归
    if 新 Tab → 切换到新 iframe → 递归 → 关闭 Tab
    if 系统级确认弹窗（top 层 .ant-confirm）→ Cookie 注入
    if 下载/打印 → 记录类型，不阻塞

```

**关键约束**：
- iframe 导航后 MUST 重新获取 `page.frames().find(...)`
- 弹窗内的每个下拉字段 MUST 展开穷尽全部选项
- 禁用按钮也要记录其存在（级别：低）

可复用 JS 片段：

```javascript
// 扫描当前页面所有可见按钮
[...document.querySelectorAll('button')].filter(b => b.offsetParent !== null && b.textContent.trim())
  .map(b => ({ text: b.textContent.trim().replace(/\s/g, ''), disabled: b.disabled }));
```

### Phase 1-c-2 — VTable 行选择与弹窗深度探索

点击业务操作按钮前，必须先选中 VTable 中的行。选择方法如下：

**行选择方法（已验证可行）**：

```javascript
// 1. 用 getCellRect 获取复选框列的单元格坐标
// 2. 转换为主页面视口坐标（iframe偏移 + canvas偏移）
// 3. 用 page.mouse.click 模拟真实鼠标点击
// 4. 用 getCellInfo(0, row).value 验证选中状态

var vt = window._vtable;
var row = 2; // 第3行（0-indexed，表头=0）
var cr = vt.getCellRect(0, row);
var vr = document.querySelector('.vtable').getBoundingClientRect();
// 获取 iframe 在主页面中的偏移
var iframeRect = document.querySelector('[role="tabpanel"][aria-hidden="false"] iframe').getBoundingClientRect();
var mx = Math.round(iframeRect.left + vr.left + (cr.bounds.x1 + cr.bounds.x2) / 2);
var my = Math.round(iframeRect.top + vr.top + (cr.bounds.y1 + cr.bounds.y2) / 2);

// 多步移动模拟鼠标轨迹（解决 VTable pickable:false 问题）
for (var i = 1; i <= 15; i++) {
  var t = i / 15;
  // 从远到近移动
  await page.mouse.move(
    Math.round(300 + (mx - 300) * t),
    Math.round(200 + (my - 200) * t)
  );
  await new Promise(function(r){ setTimeout(r, 60); });
}
await page.mouse.click(mx, my);
await new Promise(function(r){ setTimeout(r, 1500); });

// 用 getCellInfo 验证选中状态（不要用 getCheckboxState，它可能返回 null）
var cellInfo = vt.getCellInfo(0, row);
console.log('checkbox选中状态:', cellInfo.value); // true = 已选中, undefined = 未选中
```

**⚠️ 已知陷阱**：
- 不要用 `dispatchEvent` 在 canvas 上触发点击——它可更新 VTable 内部 `getSelection()` 状态，但**不会同步到组件层的视觉复选框**，按钮 handler 检测不到
- 不要用 `methods.setSelection()`——同样不更新视觉复选框
- **验证必须用 `getCellInfo(0, row).value`**（true=已选中, undefined=未选中），不要用 `getCheckboxState()`
- 行号从 0 开始计数，表头 = row 0

**选中行后点击按钮**：

```javascript
// DOM click 在 iframe 内有效
var btn = [...document.querySelectorAll('button')].find(function(b){
  return b.textContent.trim().replace(/\s+/g, '') === '按钮文字';
});
if (btn) btn.click();
```

**页面跳转检测（基于 TabPanel）**：

部分操作按钮或双击 VTable 单元格会触发页面跳转（iframe URL 变化或新开 Tab）。通过以下方式检测：

```javascript
// 获取当前显示的 iframe URL
function getActiveIframeSrc() {
  var pane = document.querySelector('.ant-tabs-tabpane[aria-hidden="false"]');
  var iframe = pane ? pane.querySelector('iframe') : null;
  return iframe ? iframe.src : null;
}

// 获取当前选中的 tab 名称
function getActiveTabName() {
  var tab = document.querySelector('.ant-tabs-tab[aria-selected="true"]');
  return tab ? tab.textContent.trim() : null;
}

// 检测是否有新 tab 出现
function getTabCount() {
  return document.querySelectorAll('.ant-tabs-tab').length;
}
```

**判断流程**：
1. 操作前记录 `activeSrc = getActiveIframeSrc()` 和 `tabCount = getTabCount()`
2. 操作后重新读取
3. 若 `getActiveIframeSrc() !== activeSrc` → iframe 内页面跳转

#### DFS 子节点扩展 —— 弹窗 / 页面跳转 / 页面内容变更

**核心思想**：按钮点击后的产物——无论弹窗、页面跳转还是同屏内容变更——都是一个**新的交互面**，需要作为 DFS 子节点递归遍历，生成**衍生测试用例**。

**三种子节点类型的精确定义**：

| 类型 | 特征 | 判断条件 |
|------|------|---------|
| **弹窗** | iframe 内或 top 层弹出模态框 | `.ant-modal-content` 出现在 DOM 中 |
| **页面跳转** | iframe 的 `src` 变化，或包裹 iframe 的 tabpanel 的激活状态变更 | 操作前 `iframe.src` ≠ 操作后 `iframe.src`，或 `document.querySelector('[role="tabpanel"][aria-hidden="false"]')` 指向不同的 tabpanel |
| **内容变更** | 同一 iframe 且同一 tabpanel 下，内部 DOM 元素被替换/更新 | iframe URL 不变 + tabpanel 不变 + 内部元素集合改变 |

```
操作前:
  主页面 tabpanel[aria-hidden="false"] → iframe A (URL: /makerTable)

点击按钮后判断:
  ├── iframe A 内出现 .ant-modal-content → 弹窗类型
  ├── iframe A 的 src 变化 → 页面跳转类型
  ├── tabpanel 激活切换（aria-hidden="false" 转移到另一个 tabpanel）→ 页面跳转类型
  └── iframe A 没变 + tabpanel 没变，但内部按钮/表格/字段都换了 → 内容变更类型

  注意：新交互面的测试用例，**前置条件只描述门户级初始状态**（如「已登录系统」「在制造排产页面」「表格已加载」），触发到该交互面的所有操作**必须写在测试步骤中**，不可放在前置条件。详见下方「用例衍生规则」示例。 |
```

**检测方法**：

```javascript
// 操作前记录状态
var beforeFrameSrc = iframe.src;
var beforeTabpanel = document.querySelector('[role="tabpanel"][aria-hidden="false"]');
var beforeTabIframeSrc = beforeTabpanel?.querySelector('iframe')?.src;

// 操作后检测
var afterTabpanel = document.querySelector('[role="tabpanel"][aria-hidden="false"]');
var afterFrameSrc = iframe.src;
var afterTabIframeSrc = afterTabpanel?.querySelector('iframe')?.src;

if (afterTabIframeSrc !== beforeTabIframeSrc) {
  // 类型 A: 页面跳转（tabpanel 切换 或 iframe URL 变化）
  // 重新获取 iframe → 对新的 tabpanel/iframe 完全重新 DFS
} else if (document.querySelector('.ant-modal-content')) {
  // 类型 B: 弹窗 → 分析弹窗 DOM 进行内部 DFS
} else {
  // 类型 C: 内容变更（同 iframe 同 tabpanel，内部元素已更换）
  // 重新分析当前 DOM，发现新元素继续 DFS
}
```

**关键区分实例**：
- iframe URL 未变 + tabpanel 未变 + 内容变了 → **内容变更**（例：点击重置后筛选区清空、收起▲后筛选区折叠、某按钮点击后表格刷新）
- iframe URL 变了（即使 tabpanel 没明变）→ **页面跳转**（例：批量备料 → URL 从 makerTable 变为 createMaterialOrder）
- tabpanel 变了（即使看起来还在同一模块）→ **页面跳转**（例：从「制造排产」页签切换到「制令单新增」页签）
- 两者都没变，只是在当前 DOM 上弹了个窗 → **弹窗**
```


**用例衍生规则**：

| 新交互面类型 | 内部可交互元素 | 衍生用例方向 | 前置条件（仅初始状态） | 测试步骤（含完整操作链） | 预期结果 |
| **弹窗** (modal) | 输入框/文本域 | 输入不同值后验证 | 1. 已登录\n2. 在模块页面\n3. 表格已加载 | 1. 勾选行\n2. 点击触发按钮\n3. 等待弹窗\n4. 输入测试值 | 输入框显示输入值 |
| **弹窗** (modal) | 按钮（确定/取消/×） | 点击后验证结果 | 1. 已登录\n2. 在模块页面\n3. 表格已加载 | 1. 勾选行\n2. 点击触发按钮\n3. 等待弹窗\n4. 点击目标按钮 | 弹窗关闭，操作执行 |
| **页面跳转** (new iframe) | 新页面按钮 | 继续 DFS | 1. 已登录\n2. 在模块页面 | 1. 勾选行（如需）\n2. 点击跳转按钮\n3. 等待页面加载\n4. 点击新页面按钮 | 新页面响应正确 |
| **页面跳转** (new iframe) | 返回按钮 | 返回主页面 | 1. 已登录\n2. 在模块页面 | 1. 勾选行（如需）\n2. 点击跳转按钮\n3. 等待页面加载\n4. 点击返回 | 返回主页面 |
| **内容变更** (同iframe) | 新按钮/筛选 | 继续 DFS | 1. 已登录\n2. 在模块页面 | 1. 点击触发按钮\n2. 等待内容变更\n3. 点击新按钮 | 新按钮响应正确 |

**常见场景的 DFS 用例示例**：

1. **物料查询弹窗**（弹窗类型）
   - 前置条件：1. 已登录系统 2. 在制造排产页面 3. 表格已加载
   - 用例 1：1. 勾选一行 2. 点击物料查询 3. 等待弹窗 4. 查看物料明细表格各列数据
   - 用例 2：1. 勾选一行 2. 点击物料查询 3. 等待弹窗 4. 点击关闭×按钮
   - 用例 3：1. 勾选一行 2. 点击物料查询 3. 在弹窗中对比物料编码与所选行成品编码

2. **批量完成确认弹窗**（弹窗类型）
   - 前置条件：1. 已登录系统 2. 在制造排产页面 3. 表格已加载
   - 用例 1：1. 勾选一行 2. 点击批量完成 3. 等待弹窗 4. 点击「取消」
   - 用例 2：1. 勾选一行 2. 点击批量完成 3. 等待弹窗 4. 点击「确定」
   - 用例 3：1. 勾选一行 2. 点击批量完成 3. 等待弹窗 4. 点击关闭×

3. **批量排产 → 排产详情视图**
   - 前置条件：1. 已登录系统 2. 在制造排产页面 3. 表格已加载
   - 用例 1（查看工序）：1. 切换至「全部」页签 2. 勾选同部门同成品待排产行 3. 点击批量排产 4. 查看工序列表
   - 用例 2（选设备）：1~3 同上 4. 为工序选择生产设备
   - 用例 3（确认排产）：1~3 同上 4. 选择设备 5. 点击确认排产
   - 用例 4（返回）：1~3 同上 4. 点击返回

4. **批量备料 → 备料单创建页**
   - 前置条件：1. 已登录系统 2. 在制造排产页面 3. 表格已加载
   - 用例 1：1. 勾选行 2. 点击批量备料 3. 等待跳转 4. 查看备料明细
   - 用例 2：1. 勾选行 2. 点击批量备料 3. 填写备料数量
   - 用例 3：1. 勾选行 2. 点击批量备料 3. 提交备料单

5. **折叠筛选区**（内容变更类型）
   - 前置条件：1. 已登录系统 2. 在制造排产页面 3. 筛选区已展开
   - 用例 1：1. 点击「收起▲」 2. 验证筛选区折叠
   - 用例 2：1. 点击「展开▼」 2. 验证筛选区展开

**关键约束**：
- **前置条件只描述初始状态**：不得包含「已勾选行」「已打开弹窗」「已进入XX页面」等操作结果——必须写入测试步骤。前置条件示例：「1. 已登录系统\n2. 在制造排产页面\n3. 表格已加载」
- **禁止**一条用例写多个步骤（先点按钮A、再在弹窗B里操作字段C、再点确定D）——这应拆为多条用例
- 三种子节点关闭/返回后，必须**重新分析主页面 DOM**（操作可能改变了主页面的数据/状态）
- 每个新交互面内的所有可交互元素至少产生一条用例（中级以上）
- 页面跳转后 MUST 重新获取 iframe 引用（`page.frames().find(...)`）

### Phase 1-d — VTable 单元格交互探索

**必须先运行 scanner 脚本获取真实列分类**，然后只对 scanner 标记为可交互的列进行点击测试，禁止凭空猜测。

运行 `scripts/vtable-scanner.js`：

```javascript
// 1. 挂载 VTable 实例（按 scanner 中的 mountVTable 方法）
// 2. 扫描列并输出分类结果
var scanResult = scanColumns(50);
// scanResult 每行包含:
//   col, title              — 列序号和标题
//   bodyBehavior            — 列体行为: '复选框' / '链接/按钮' / '文本'
//   icons[].func            — 图标功能: '排序' / '筛选' / '下拉菜单'
//   icons[].viewportX/Y     — 图标在视口中的精确坐标（用于 page.mouse.click）
```

**bodyBehavior 分类含义**：

| bodyBehavior | 含义 | 交互方式 | 用例方向 |
|-------------|------|---------|---------|
| `复选框` | 行选择列 | `page.mouse.click` 点击坐标 | 行选择/取消/全选 |
| `链接/按钮` | 可点击跳转的列（蓝色文字） | `page.mouse.click` 点击单元格 | 点击跳转详情页 |
| `文本` | 纯展示文本 | 不交互（仅验证内容） | 数据展示正确性 |

**icons[].func 分类含义**：

| func | 含义 | 坐标来源 | 用例方向 |
|------|------|---------|---------|
| `排序` | 列头排序图标 | `getCellIconBounds()` 计算坐标 | 点击排序图标切换排序顺序 |
| `筛选` | 列头筛选图标 | `getCellIconBounds()` 计算坐标 | 点击弹出筛选面板 |

**列宽拖动**（所有列的通用交互，与 scanner 无关）：将鼠标移动到列头交界处，光标变为 ↔ 拖拽指针后，长按左键横向拖动 ≥30px 后松开即可调整列宽。这是 VTable 原生支持的交互。

**遍历 scanner 可交互列的流程**：

```
for each column:
  ├── bodyBehavior='复选框' → 单点击复选框坐标（参考 Phase 1-c-2 行选择）
  │     验证 getCellInfo(0, row).value === true
  │
  ├── bodyBehavior='链接/按钮' → 双击单元格文字！！！（注意：必须双击，单击无效）
  │     page.mouse.click → wait 150ms → page.mouse.click (同一坐标)
  │     ├── 弹出弹窗 → 走弹窗 DFS
  │     ├── iframe URL 变化 → 页面跳转（参考 DFS 子节点扩展）
  │     ├── 新 Tab 出现 → 切换到新 Tab → DFS → 关闭
  │     └── 无变化 → 跳过
  │
  ├── icons 包含 '排序' → 单击排序图标
  │     page.mouse.click → 排序图标状态变化（↕ → ↑ → ↓）
  └── icons 包含 '筛选' → 单击筛选图标
        0. 若列不在视口内，先用 scrollToCell({ col: colIdx, row: 0 }) 滚动到目标列
        1. 运行 scanColumns(maxCol) 获取所有列的精确视口坐标
        2. 在 scanResult 中找该列 header 行的 icons，取 func='筛选' 的 viewportX/Y
        3. 鼠标轨迹移动到该坐标 → page.mouse.click
        4. 等待 2 秒后，用「6.4 弹窗检测」中的**全量扫描代码**检测弹窗
        5. 检测到 .vtable-filter-menu → 分析其 DOM → 按 DFS 子节点扩展生成衍生用例
        6. 检测到 .ant-modal-content → 走交互弹窗 DFS
        7. 无弹窗 → 跳过

**关键区分：单击 vs 双击**：

| 交互目标 | 操作 | 预期效果 |
|---------|------|---------|
| 复选框列 (col 0) | 单击 | 行选中/取消 |
| 排序图标（列头） | 单击 | 排序切换 |
| 筛选图标（列头） | 单击 | 弹出筛选面板 |
| 单元格文字（链接/按钮列） | **双击** | 弹出详情弹窗或跳转 |

**单元格双击实现**：

```javascript
// 获取单元格坐标（同单点击方法）
var vt = window._vtable;
var cellRect = vt.getCellRect(col, row);
var mx = Math.round(iframeRect.left + vr.left + (cellRect.bounds.x1 + cellRect.bounds.x2) / 2);
var my = Math.round(iframeRect.top + vr.top + (cellRect.bounds.y1 + cellRect.bounds.y2) / 2);
// 鼠标轨迹移动
for (var i = 1; i <= 15; i++) {
  var t = i / 15;
  await page.mouse.move(Math.round(50+(mx-50)*t), Math.round(100+(my-100)*t));
  await new Promise(function(r){ setTimeout(r, 60); });
}
// 双击：两次点击间隔 150ms
await page.mouse.click(mx, my);
await new Promise(function(r){ setTimeout(r, 150); });
await page.mouse.click(mx, my);
await new Promise(function(r){ setTimeout(r, 2000); });
```

排序图标在列头区域，需要计算精确的视口坐标后使用鼠标轨迹点击。`scanColumns()` 的输出中 `icons[].viewportX/Y` 已算好坐标，直接使用：

```javascript
var icon = scanResult[col].icons[0]; // 排序图标
// 多步鼠标轨迹
for (var i = 1; i <= 15; i++) {
  var t = i / 15;
  await page.mouse.move(
    Math.round(50 + (icon.viewportX - 50) * t),
    Math.round(100 + (icon.viewportY - 100) * t)
  );
  await new Promise(function(r){ setTimeout(r, 60); });
}
await page.mouse.click(icon.viewportX, icon.viewportY);
```

**关键约束**（MUST）：
- 所有可交互列的结论 MUST 来自 `scanColumns()` 的输出，禁止猜测
- `bodyBehavior='文本'` 且 `icons.length=0` 的列**不做任何点击交互**
- 每次点击后 MUST 等待 ≥2 秒
### Phase 2 — 用例生成

每个用例 18 个字段：

| # | 字段 | 要求 |
|---|------|------|
| A | 用例编号 | `{前缀}_{拼音}_{分组字母}{3位数字}`，分组：F=筛选 I=交互 P=页面 B=导出 |
| B | 用例标题 | 动宾结构 |
| C | 级别 | 高级=阻塞 中级=核心 低级=一般 |
| D | 验证点 | 简明扼要的验证目标，必须有明确的可验证内容 |
| E~F | 模块 | 如「生产管理」「制造排产」 |
| G | 测试类型 | 功能/边界值/兼容性 |
| L | 预期结果 | 筛选用例：用纯中文描述验证结论，如「所有制令单号均包含 MO202606」。AI 内部用 `getColumnValuesByTitle()` 验证后再转换为中文。非筛选用例：可验证断言，纯 UI 描述，无技术术语 |
| I | 前置条件 | 编号列表，**只描述测试开始前的初始状态**，不含任何操作步骤。如：1. 已登录系统\n2. 在制造排产页面\n3. 表格已加载。禁止写「已通过XX操作到达XX状态」——这类中间操作必须放在测试步骤中。 |
| J | 测试步骤 | 编号列表，**包含从初始状态到验证前的所有操作**，每一步是具体动作。如：1. 勾选一行待排产记录\n2. 点击「批量排产」\n3. 等待页面切换到排产详情视图\n4. 点击「返回」\n5. 观察页面 |

必须覆盖：正常流程、异常流程、业务规则验证、数据状态流转。

#### VTable 测试用例生成规则

VTable 相关用例**严格基于 `scanColumns()` 的真实输出 + 实际点击验证**，禁止凭空猜测。

**流程**：

```
Step 1: scanColumns() 获取列分类
Step 2: 逐列执行实际点击操作：
  复选框列 → 单点击 → 验证 getCellInfo(0,row).value
  链接/按钮列 → 双击单元格 → 观察弹窗/跳转并记录
  排序列 → 单击排序图标 → 观察排序状态并记录
Step 3: 基于实际观察生成用例：
  弹窗 → DFS 衍生用例
  跳转 → 页面跳转型衍生用例
  排序 → 排序用例
  无反应 → 不生成
```

**实际测试结果**：运行 scanner 并逐列点击验证后，将结果记录到 Excel 的「3.4 VTable 列定义一览表」中，不在 SKILL 中硬编码。不同模块的 VTable 列行为可能完全不同。



#### 衍生用例规则（DFS 子节点扩展——弹窗 / 页面跳转 / 内容变更）

按钮点击后，无论触发的是**弹窗**（`.ant-modal-content`）、**页面跳转**（iframe URL 变化或 tabpanel 切换）还是**内容变更**（同 iframe 同 tabpanel 内 DOM 改变），出现的新交互面都应作为 DFS 子节点处理，生成至少一条**衍生测试用例**。每条衍生用例独立编写，前置条件与主用例相同（仅门户级初始状态），测试步骤从初始状态到最终验证完整描述所有操作：
```
主用例（以批量排产为例）:
  前置条件：1. 已登录系统 2. 在制造排产页面 3. 表格已加载
  步骤：1. 切换至「全部」页签 2. 勾选同部门同成品待排产行 3. 点击批量排产
  预期：进入排产详情视图，显示工序列表

每条衍生用例独立编写，前置条件同上不再重复，步骤须从初始状态开始完整描述所有操作：

  衍生用例 1（查看工序）：
    步骤：1. 切换至「全部」页签 2. 勾选同部门同成品待排产行 3. 点击批量排产 4. 查看工序列表各列数据
    预期：工序列表显示序号、工艺类型、工序编码、工序名称、设备类型、生产日期、生产设备等列
  
  衍生用例 2（选择设备）：
    步骤：1~3 同上 4. 为某工序下拉选择生产设备
    预期：设备可选，选择后该行显示已选设备
  
  衍生用例 3（确认排产）：
    步骤：1~3 同上 4. 选择设备 5. 点击确认排产
    预期：排产操作成功执行

  衍生用例 4（撤销排产）：
    步骤：1~3 同上 4. 点击撤销排产 5. 在确认弹窗中点击确定
    预期：排产撤销成功

  衍生用例 5（返回列表）：
    步骤：1~3 同上 4. 点击返回
    预期：返回制造排产列表视图
```

**前置条件写法**：**只描述门户级初始状态**，如「1. 已登录系统\n2. 在制造排产页面\n3. 表格已加载」。**禁止**写「已勾选行」「已打开弹窗」「已跳转到XX页面」等操作结果——触发到当前状态的所有操作必须完整写在**测试步骤**中。前置条件与测试步骤之间不重复、不继承。
**步骤写法**：**包含从初始状态到验证前的所有操作**，每一步是具体动作。每条用例必须独立完整，不依赖父用例或前序用例的上下文。
**预期结果**：只验证当前操作的结果，不验证父操作的结果。
### Phase 3 — 分区域迭代探索（对话驱动）

**核心原则**：不一次性生成全部用例，而是通过不断对话，按用户指示逐步覆盖各个区域。

#### 工作模式

```
用户 → 指令（如「测一下批量排产按钮」）
  Agent → 执行（点击按钮、观察弹窗、记录结果）
  Agent → 汇报结果 + 询问下一步
用户 → 继续指令或调整方向
```

#### 覆盖策略：按区域分解

DOM 结构俯瞰后，将页面拆为独立区域逐个击破：

| 区域 | 内容 |
|------|------|
| **页签切换** | radio-tabs、sub-tabs |
| **筛选区** | 字段+运算符+值输入 |
| **工具栏按钮** | 业务操作按钮组 |
| **VTable 表头** | 排序、筛选图标 |
| **VTable 行选择** | 复选框、双击 |
| **VTable 链接列** | customLayout 跳转 |
| **页面级** | 折叠、刷新 |

**每个区域探索完后向用户汇报并提供下一步选项，不擅自继续。用户可随时切换方向。**

### Phase 4 — Excel 导出

1. 按 `scripts/excel-export-template.py` 模板组装数据
2. MUST 按视觉布局排序（筛选区 → 工具栏 → VTable → 弹窗 → 页面级）
3. 在 `eval` 中执行
4. 告知文件路径

#### 排序规则

```
筛选区 (F) → 页签/按钮 (I) → VTable 交互 (I) → 页面级 (P)
```

## 4-a 用例编号分组约定

| 字母 | 含义 | 示例 |
|------|------|------|
| F | 筛选查询 | NB_ZZPC_F001 |
| A | 新增/添加 | NB_ZZPC_A001 |
| I | 交互（弹窗/按钮/表格操作） | NB_ZZPC_I001 |
| B | 批量操作/导出/打印 | NB_ZZPC_B001 |
| P | 页面级（刷新/布局/切换） | NB_ZZPC_P001 |

## 5. 截图分析

用户提供截图时用 `read` 或 `inspect_image` 分析，识别页面标题、表单字段、按钮文字、表格列标题、状态标签。

## 6. 浏览器分析与辅助

### 6.1 浏览器连接

Chrome 以 `--remote-debugging-port=9222` 启动，通过 `browser.open()` 连接。

```javascript
browser.open({ app: { cdp_url: "http://localhost:9222", target: "诺贝科技" } });
```

### 6.2 VTable 数据提取

运行 `scripts/vtable-scanner.js`：

```javascript
// 在 iframe evaluate 内
mountVTable();
var vt = window._vtable;

// 获取列定义
vt.columns.map(c => ({ field: c.field, title: c.title }));

// 获取样本数据（行 1-5）
for (var r = 1; r < 6; r++) { vt.getCellOriginRecord(0, r); }

// 获取筛选后全部记录
vt.getFilteredRecords();

使用 `scripts/vtable-column-values.js` 中的 `getColumnValuesByTitle()` 按中文列名取全列数据：

```javascript
// 在 iframe evaluate 内（VTable 已挂载）
var values = getColumnValuesByTitle(window._vtable, '制令单号');
// → ['MO202606270041', 'MO202606260019', 'MO202606260018', ...]
// 返回值与界面显示的排序、筛选、分页状态一致

// 取原始值（未经格式化，如数字类型）
var rawQty = getColumnValuesByTitle(window._vtable, '生产数量', true);
// → [3, 2, 45, 36719, ...]
```

**用途**：筛选验证的核心工具。执行筛选操作后调用此函数提取某列所有值，逐一断言是否符合筛选条件（如「制令单号包含MO202606」或「生产数量 > 100」），替代人工肉眼检查。

**标题匹配策略**：先精确匹配 `headerValue === title`，若不中再尝试包含匹配 `headerValue.indexOf(title) !== -1`。这样即使标题带后缀（如「生产需求单号 *」或「制令单号（必填）」），也能命中正确列。

**视觉文本 vs 原始值**（raw 参数的关键区别）：

```
getColumnValuesByTitle(vt, '制令单类型', false)
  → ['普通制令单', '普通制令单', '包装制令单', ...]  // 视觉文本（经过 customLayout 映射）

getColumnValuesByTitle(vt, '制令单类型', true)
  → ['0', '0', '1', ...]                           // 原始数据码
```

`raw=false`（默认）优先通过 VTable 场景图 API（`scenegraph.getCell`）读取 canvas 上实际渲染的文字，能正确获取 customLayout、cellType、formatter 处理后的视觉文本，与用户肉眼看到的完全一致。`raw=true` 读的是未经格式化的数据源原始值。

**重要**：筛选验证时 MUST 使用 `raw=false`（视觉文本），因为筛选弹窗可能显示原始码而表格显示中文，必须用视觉文本做断言才与用户体验一致。视觉文本需单元格在视口内才能读到（场景图只渲染可见区域），若返回 `null` 则自动降级为 `getCellValue`。

#### 场景图 API 的高级用途

VTable 的场景图（scenegraph）反映了 canvas 上实际渲染的内容，是获取真实渲染状态的最可靠途径，尤其适用于以下场景：

**1. 获取渲染后的视觉文本（customLayout 映射后的值）**

```javascript
var cellGroup = vt.scenegraph.getCell(col, row);
var visualText = null;
(function walk(node, depth){
  if (!node || depth > 5 || visualText) return;
  if (node.type === 'text' && node.attribute && node.attribute.text) {
    visualText = node.attribute.text;
  }
  if (node.children) node.children.forEach(function(c){ walk(c, depth+1); });
})(cellGroup, 0);
// visualText → '普通制令单'（而非原始数据 '0'）
```

**2. 获取实际渲染坐标（与视口坐标的转换）**

场景图的 `globalAABBBounds` 已反映 auto-fill 压缩和 scrollToCell 滚动后的真实渲染位置，是**唯二可靠**的坐标来源（另一个见下方「getCellRect + scroll 修正」）。

```javascript
var bounds = cellGroup.globalAABBBounds;
// 转换为视口坐标（场景图坐标已含 scroll 偏移，无需额外修正）：
var viewportX = iframeRect.left + vtableRect.left + bounds.x1;
var viewportY = iframeRect.top  + vtableRect.top  + bounds.y1;
```

**备选方案：getCellRect + scroll 修正（当场景图不可用时）**

`getCellRect` 返回的是逻辑 canvas 坐标，**不包含 scrollLeft/scrollTop 偏移**。当 VTable 被 `scrollToCell` 滚动后，必须手动减去滚动偏移：

```javascript
var cr = vt.getCellRect(col, row);
// 逻辑中心点
var cx = cr.bounds.x1 + (cr.bounds.x2 - cr.bounds.x1) / 2;
var cy = cr.bounds.y1 + (cr.bounds.y2 - cr.bounds.y1) / 2;
// 修正 scroll 偏移 → 视口坐标
var viewportX = iframeRect.left + vtableRect.left + cx - vt.scrollLeft;
var viewportY = iframeRect.top  + vtableRect.top  + cy - vt.scrollTop;
```

未修正 `scrollLeft` 时，悬停点可能偏移数百像素（如 scrollLeft=1050 时，逻辑 x=1290 实际渲染在 x=240），导致气泡无法触发。

⚠️ 注意：**切勿直接使用 `getCellRect` 的原始坐标作为视口坐标**——它既不反映 auto-fill 压缩，也不反映 scroll 偏移。

**3. 定位列边框（用于列宽拖拽）**

```javascript
// 取 colN 的右边界 = colN 与 colN+1 之间的竖线
var cgN  = vt.scenegraph.getCell(N, 0);
var borderX = cgN.globalAABBBounds.x2;  // canvas 坐标
// → 视口坐标: iframeRect.left + vtableRect.left + borderX
```

**4. 获取列顺序和实际列宽**

```javascript
for (var c = 0; c < vt.columns.length; c++) {
  var cg = vt.scenegraph.getCell(c, 0);
  var renderedWidth = cg.globalAABBBounds.x2 - cg.globalAABBBounds.x1;
  var definedWidth = vt.columns[c].width;
  // renderedWidth !== definedWidth → 该列被 auto-fill 压缩或手动拖拽过
}
```

**5. 检查表头图标是否存在**

```javascript
var cg = vt.scenegraph.getCell(col, 0);
var hasFilter = false, hasSort = false;
(function walk(node, depth){
  if (!node || depth > 3) return;
  if (depth > 0) {
    var name = (node.name || '').toLowerCase();
    if (name.indexOf('filter') !== -1) hasFilter = true;
    if (name.indexOf('sort') !== -1)   hasSort = true;
  }
  if (node.children) node.children.forEach(function(c){ walk(c, depth+1); });
})(cg, 0);
```

**关键约束**：
- 场景图只渲染视口内的可见区域，滚动后需重新读取
- `getCell(col, row)` 的 col/row 是 VTable 内部行列索引，与 `getCellRect` 一致
- `globalAABBBounds` 的坐标系原点在画布左上角，需加 iframe 偏移 + vtable 偏移转换为视口坐标


### 6.3 鼠标轨迹

注入 `scripts/mouse-trail-inject.js` 后：

| 你说 | Agent 执行 |
|------|-----------|
| 「开启鼠标轨迹」 | 在主页面 + 所有 iframe 的 contentWindow 中同时执行 `window.mt.on()` |
| 「关闭鼠标轨迹」 | 在主页面 + 所有 iframe 中同步执行 `window.mt.off()` |

注入示例（必须同时注入主页面和 iframe）：

```javascript
// 1. 注入到主页面
await page.evaluate(function(){
  if(!window.__mt_injected){
    // 注入鼠标轨迹 JS（注入后自动初始化）
    // 然后开启
    if(window.mt) window.mt.on();
  }
});

// 2. 注入到 iframe
var iframes = page.frames().filter(function(f){ return f.url().indexOf('makerTable') >= 0; });
iframes.forEach(async function(f){
  await f.evaluate(function(){
    if(window.mt) window.mt.on();
  });
});
```

注入目标：**主页面 + 当前视口可见 iframe 的内容窗口**。由于 iframe 内的事件不会冒泡到主页面，必须分别在两个上下文中注册事件监听器才能同步显示红点。
### 6.4 弹窗检测

详见 `references/modal-types.md`。点击任意可交互元素后，必须进行**全量 DOM 扫描**检测是否出现弹窗，**不能仅搜索 ant-design 组件**——VTable 的筛选弹窗是自定义的 `.vtable-filter-menu`，不是 `.ant-dropdown`。

检测代码模板（MUST 使用）：

```javascript
// 点击后全量扫描所有可能的弹窗类型
var popupSelectors = [
  // ant-design 弹窗
  '.ant-modal-content',            // 交互弹窗 / 业务确认弹窗
  '.ant-modal-wrap',               // 弹窗遮罩层
  '.ant-notification-notice',      // 通知提醒（需手动关闭）
  '.ant-message-notice',           // 消息提醒（自动消失，500ms 内捕获）
  '.ant-dropdown:not(.ant-dropdown-hidden)',         // 下拉菜单
  '.ant-select-dropdown:not(.ant-select-dropdown-hidden)',  // 选择下拉
  '.ant-popover:not(.ant-popover-hidden)',           // 气泡卡片
  '.ant-tooltip:not(.ant-tooltip-hidden)',           // 提示文字
  // VTable 自定义弹窗
  '.vtable-filter-menu',           // VTable 列头筛选弹窗
  '.vtable-header-menu',           // VTable 列头菜单
  '.vtable-dropdown',              // VTable 下拉
  // 其他通用
  '[class*="filter-menu"]', '[class*="filter-panel"]',
  '[class*="dropdown"]',  '[class*="popup"]',
  '[class*="overlay"]'
];

var found = [];
popupSelectors.forEach(function(sel){
  var els = document.querySelectorAll(sel);
  els.forEach(function(el){
    if (el.offsetParent !== null || el.classList.contains('ant-dropdown') || el.classList.contains('ant-select-dropdown')) {
      found.push({selector: sel, text: el.textContent.replace(/\s+/g,' ').substring(0, 200)});
    }
  });
});
// 若 found.length === 0 → 无弹窗；否则逐条分析
```

检测优先级：
1. iframe 内检测各类弹窗 / 消息提醒
2. top 层检测系统级确认弹窗
3. 无 → 正常继续

## 7. 执行模式

omp `eval`（持久 Python kernel）用于 Phase 4 Excel 导出。`browser` 工具用于页面探索和交互。

## 8. 质量管理

### 高级 — 阻塞项
- [ ] 每张用例有可执行的具体步骤
- [ ] 预期结果可观测/可断言
- [ ] 同时覆盖正向和负向场景
- [ ] 用例编号全局唯一
- [ ] 步骤编号从 1 开始连续递增
- [ ] 前置条件为独立完整描述，无「同上」
- [ ] 备注列为空（骨架用例除外）
- [ ] 用户口头确认
- [ ] 测试类型已标注

### 中级 — 重要项
- [ ] 每条用例仅覆盖一个独立场景
- [ ] 测试数据具体化
- [ ] 验证点有明确的可验证内容

## 9. 异常处理

| 异常 | 处理 |
|------|------|
| openpyxl 未安装 | `uv add openpyxl` |
| 浏览器连接失败 | 检查 `http://localhost:9222/json` |
| SCM Cookie 过期 | 运行 `scripts/scm-login.js` → `refreshSession()` |
| VTable 挂载失败 | 检查 `.vtable` 元素 → 检查 iframe 上下文 → 降级截图 |
| 需求信息不足 | 3 轮追问后生成骨架用例，备注 `[待确认]` |
| 写入权限不足 | 降级到当前目录 |

## 10. Excel 模板

模板代码见 `scripts/excel-export-template.py`。直接在 `eval` kernel 中执行。

### 列宽参考

| 列 | 字段 | 最小宽 |
|---|------|-------|
| A | 用例编号 | 18 |
| B | 用例标题 | 42 |
| C | 级别 | 12 |
| D | 验证点 | 42 |
| E~H | 模块/类型/功能 | 12~18 |
| I~L | 条件/步骤/数据/结果 | 42~50 |
| M~R | 执行/编写信息 | 10~12 |

### 数据区对齐
- **左对齐**: B 标题、D 验证点、I 前置条件、J 测试步骤、K 测试数据、L 预期结果
- **居中**: 其余列

## 11. 自检清单

- [ ] `uv add openpyxl` 已执行
- [ ] 浏览器可连接（port 9222）
- [ ] 用户已确认变量配置
- [ ] 输出目录有写入权限

### 0-d. 弹窗/通知关闭规则（MUST）

每次交互操作后，无论弹窗（`.ant-modal-content`）、通知（`.ant-notification-notice`）还是消息（`.ant-message-notice`），在提取完必要数据后 **必须关闭**，不得残留：

```javascript
// 关闭弹窗：点击关闭×或取消按钮，禁止用 .remove() 绕过
var modal = document.querySelector('.ant-modal-content');
if (modal && modal.offsetParent !== null) {
  var closeBtn = modal.querySelector('.ant-modal-close') ||
                 modal.querySelector('button.ant-btn:contains(取消)') ||
                 modal.querySelector('button.ant-btn:contains(返回)');
  if (closeBtn) closeBtn.click();
}

// 关闭通知提醒（需手动点击×关闭）
var notif = document.querySelector('.ant-notification-notice');
if (notif && notif.offsetParent !== null) {
  var closeBtn = notif.querySelector('.ant-notification-notice-close');
  if (closeBtn) closeBtn.click();
}

// 消息提醒（ant-message）几秒后自动消失，可不清除
```

**禁止**使用 `el.remove()` 或 `el.parentNode.removeChild(el)` 直接移除 DOM 节点——必须模拟真实关闭交互，否则 React 状态不同步。
