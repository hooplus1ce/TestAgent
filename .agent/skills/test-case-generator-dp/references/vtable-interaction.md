# VTable 交互探索规范

> 承接 SKILL.md Phase 1-c-2 / 1-d。本文件覆盖：VTable 挂载、行选择、单元格交互、DFS 子节点扩展、场景图 API、以及**挂载失败时的降级路径**。

## 一、VTable 挂载与列扫描

```javascript
// 1. 挂载（运行 scripts/vtable-scanner.js 的 mountVTable）
mountVTable();              // → { ok: true } 或 { ok:false, reason }
var vt = window._vtable;

// 2. 列分类（注意函数名是 scanColumns，无下划线前缀）
var scanResult = scanColumns(50);
// 每行: { col, title, bodyBehavior, icons[] }
//   bodyBehavior: '复选框' | '链接/按钮' | '文本'
//   icons[].func:  '排序' | '筛选'
//   icons[].viewportX/Y: 图标视口坐标（直接用于 page.mouse.click）
```

## 一·补充、VTable 数据提取样本（只读，不点击）

挂载 VTable 后，可读取列定义和样本数据，用于用例的测试数据列(K)填充：

```javascript
var vt = window._vtable;

// 获取列定义
vt.columns.map(c => ({ field: c.field, title: c.title }));

// 获取样本数据（行 1-5，0=表头）
for (var r = 1; r < 6; r++) { vt.getCellOriginRecord(0, r); }

// 获取筛选后全部记录
vt.getFilteredRecords();
```

**用途**：为测试数据列(K)提供真实字段值，替代用户描述或臆造数据。详见 `filter-validation.md` 的 `getColumnValuesByTitle()` —— 那是按列名取全列数据的推荐方式。

## 二、VTable 挂载失败的降级路径（MUST）

当 `mountVTable()` 返回 `{ok:false}` 时，**不要硬冲**，按以下流程降级：

```
mountVTable() → {ok:false}
  │
  ├─ Step 1: 排查根因
  │    检查 .vtable 元素是否存在 → 不存在：页面未加载完，等待 3s 重试 1 次
  │    检查 fiber key 是否找到 → 未找到：React 版本异常，记 reason
  │    检查 vtableInstance 30 层内是否存在 → 不存在：记 reason
  │
  ├─ Step 2: 截图存档
  │    page.screenshot → 保存到 OUTPUT_DIR/.vtable-fallback-<timestamp>.png
  │
  ├─ Step 3: 降级生成用例（仅低级展示类）
  │    基于截图 + querySelector('.vtable') 的可见列标题，生成：
  │      - 「VTable 数据展示正确性」用例（低级，预期基于截图比对）
  │      - 「VTable 列标题完整显示」用例（低级）
  │    在用例 R 备注列标注：「VTable 实例未挂载，断言基于截图，建议人工复核」
  │
  └─ Step 4: 跳过所有需点击的交互用例（复选框/链接/排序/筛选）
       不生成无法验证的交互用例
```

## 三、行选择（复选框列点击）

**已验证可行的方法**：用 `getCellRect` 取坐标 → 转视口坐标（iframe偏移 + canvas偏移）→ 鼠标轨迹移动 → `page.mouse.click` → 用 `getCellInfo(0,row).value` 验证。

```javascript
var vt = window._vtable;
var row = 2;                       // 0-indexed，表头=0
var cr = vt.getCellRect(0, row);
var vr = document.querySelector('.vtable').getBoundingClientRect();
var iframeRect = document.querySelector('[role="tabpanel"][aria-hidden="false"] iframe').getBoundingClientRect();
var mx = Math.round(iframeRect.left + vr.left + (cr.bounds.x1 + cr.bounds.x2) / 2);
var my = Math.round(iframeRect.top  + vr.top  + (cr.bounds.y1 + cr.bounds.y2) / 2);

// 多步鼠标轨迹（解决 VTable pickable:false）
for (var i = 1; i <= 15; i++) {
  var t = i / 15;
  await page.mouse.move(Math.round(300 + (mx-300)*t), Math.round(200 + (my-200)*t));
  await new Promise(r => setTimeout(r, 60));
}
await page.mouse.click(mx, my);
await new Promise(r => setTimeout(r, 1500));

// 验证：用 getCellInfo，不要用 getCheckboxState（可能返回 null）
var info = vt.getCellInfo(0, row);
console.log('选中:', info.value);  // true=已选 / undefined=未选
```

**⚠️ 已知陷阱**：
- `dispatchEvent` 在 canvas 上触发点击 → 可更新 `getSelection()` 但**不同步视觉复选框**，按钮 handler 检测不到。禁用。
- `methods.setSelection()` → 同样不更新视觉复选框。禁用。
- 验证必须用 `getCellInfo(0,row).value`，不要用 `getCheckboxState()`。

## 四、单元格交互（按 bodyBehavior 分发）

| bodyBehavior | 操作 | 预期效果 |
|-------------|------|---------|
| 复选框 | 单击坐标 | 行选中/取消 |
| 链接/按钮 | **双击**单元格文字（间隔 150ms） | 弹详情弹窗或跳转 |
| 文本 | 不交互 | 仅验证展示内容 |

**双击实现**：
```javascript
var cellRect = vt.getCellRect(col, row);
var mx = Math.round(iframeRect.left + vr.left + (cellRect.bounds.x1 + cellRect.bounds.x2) / 2);
var my = Math.round(iframeRect.top  + vr.top  + (cellRect.bounds.y1 + cellRect.bounds.y2) / 2);
// 鼠标轨迹（同上）
await page.mouse.click(mx, my);
await new Promise(r => setTimeout(r, 150));   // 双击间隔
await page.mouse.click(mx, my);
await new Promise(r => setTimeout(r, 2000));  // 等弹窗
```

## 五、列头图标（排序/筛选）

坐标直接取自 `scanColumns()` 输出的 `icons[].viewportX/Y`，鼠标轨迹移动后单击：

```javascript
var icon = scanResult[col].icons[0];
for (var i = 1; i <= 15; i++) {
  var t = i / 15;
  await page.mouse.move(Math.round(50+(icon.viewportX-50)*t), Math.round(100+(icon.viewportY-100)*t));
  await new Promise(r => setTimeout(r, 60));
}
await page.mouse.click(icon.viewportX, icon.viewportY);
```

**筛选图标点击后**：等待 2s，用 `modal-types.md` / SKILL.md 6.4 的全量弹窗扫描代码检测 `.vtable-filter-menu`，命中则对筛选面板 DFS 生成衍生用例。

## 六、坐标可靠性保障（v2 增强）

为避免滚动/缩放导致坐标偏移，点击前增加保障：

- **目标行不在视口内**：先用 `vt.scrollToCell({col, row})` 滚动到目标，再取坐标
- **点击后验证**：若 `getCellInfo` 未反映预期变化，自动重试 1 次（重新取坐标再点）
- **场景图优先**：坐标优先用 `vt.scenegraph.getCell(col,row).globalAABBBounds`（含 auto-fill 压缩 + scroll 偏移），场景图不可用时降级为 `getCellRect` + 手动减 `scrollLeft/scrollTop`

### 场景图坐标转换

```javascript
var bounds = vt.scenegraph.getCell(col, row).globalAABBBounds;
var viewportX = iframeRect.left + vtableRect.left + bounds.x1;
var viewportY = iframeRect.top  + vtableRect.top  + bounds.y1;
```

### getCellRect + scroll 修正（降级方案）

```javascript
var cr = vt.getCellRect(col, row);
var cx = cr.bounds.x1 + (cr.bounds.x2 - cr.bounds.x1) / 2;
var cy = cr.bounds.y1 + (cr.bounds.y2 - cr.bounds.y1) / 2;
var viewportX = iframeRect.left + vtableRect.left + cx - vt.scrollLeft;   // 必须减 scroll
var viewportY = iframeRect.top  + vtableRect.top  + cy - vt.scrollTop;
```

⚠️ 切勿直接用 `getCellRect` 原始坐标做视口坐标——它既不含 auto-fill 压缩也不含 scroll 偏移。未修正时悬停点可能偏移数百像素。

## 七、DFS 子节点扩展（弹窗 / 页面跳转 / 内容变更）

按钮点击后的产物——弹窗、页面跳转、同屏内容变更——都是新的交互面，作为 DFS 子节点递归遍历，生成衍生用例。

### 五种子节点类型（含业务确认弹窗 / 下载打印）

DFS 遍历按钮时，点击产物分五类。前两类（业务确认弹窗、下载/打印）容易被忽略但必须处理：

| 类型 | 判断条件 | 处理方式 |
|------|---------|---------|
| **交互弹窗** | iframe 内 `.ant-modal-content` 出现 | DFS 弹窗内部，关闭后继续 |
| **业务确认弹窗** | iframe 内 `.ant-confirm`（确认/取消二选一） | 记录标题/内容/按钮 → **测「取消」→ 关闭× → 测「确定」**，分别衍生用例 |
| **消息提醒** | `.ant-message-notice` / `.ant-notification-notice` | 提取文字 → 关闭（不残留） |
| **页面跳转** | 操作前后 `iframe.src` 变化，或 `tabpanel[aria-hidden="false"]` 切换 | 重新获取 iframe → 递归 DFS |
| **内容变更** | iframe 不变 + tabpanel 不变 + 内部元素集合改变 | 重新分析当前 DOM 继续 DFS |
| **下载/打印** | 触发浏览器下载/打印对话框 | **记录类型，不阻塞**（不实际下载），继续下一按钮 |
| **新 Tab** | `.ant-tabs-tab` 数量增加 | 切换到新 iframe → 递归 DFS → 关闭 Tab |
| **系统级确认弹窗** | top 层 `.ant-confirm`（「未登录/过期」） | Cookie 注入（见 `scm-access.md`） |

**业务确认弹窗的「取消 + 确定」双测**是常见遗漏点。注意前置条件只写初始状态，触发操作（勾选+点击批量完成）放进步骤：

```
前置条件（三条用例共用）: 1. 已登录系统 2. 在制造排产页面 3. 表格已加载 4. 已勾选一行工单

点击「批量完成」→ 弹出确认弹窗「确认批量完成选中的工单？」
  ├─ 衍生1: 步骤=「1. 点击「批量完成」 2. 在确认弹窗中点击「取消」 3. 观察表格」→ 预期=「弹窗关闭，操作不执行，表格无变化」
  ├─ 衍生2: 步骤=「1. 点击「批量完成」 2. 在确认弹窗中点击「×」 3. 观察表格」→ 预期=「弹窗关闭，操作不执行」
  └─ 衍生3: 步骤=「1. 点击「批量完成」 2. 在确认弹窗中点击「确定」 3. 观察表格」→ 预期=「批量完成执行，选中行状态变更为已完成」
```

> 「点击批量完成」是触发操作，必须写在步骤里，不能偷渡到前置条件（如「已点击批量完成」是错误的）。

> ⚠️ v1 SKILL.md 的 Phase 1-c 流程伪代码明确包含这些分支（业务确认弹窗、消息提醒、下载/打印、新 Tab、系统级确认弹窗），本表是对其的完整保留。

```javascript
// 操作前
var beforeTabIframeSrc = document.querySelector('[role="tabpanel"][aria-hidden="false"]')?.querySelector('iframe')?.src;
// 操作后
var afterTabIframeSrc  = document.querySelector('[role="tabpanel"][aria-hidden="false"]')?.querySelector('iframe')?.src;

if (afterTabIframeSrc !== beforeTabIframeSrc)      → 页面跳转（重新获取 iframe，完全重新 DFS）
else if (document.querySelector('.ant-modal-content')) → 弹窗（分析弹窗 DOM 内部 DFS）
else                                                   → 内容变更（重新分析当前 DOM 继续 DFS）
```

### 衍生用例规则

| 新交互面 | 内部可交互元素 | 衍生方向 |
|---------|--------------|---------|
| 弹窗 | 输入框/下拉/按钮/表格/页签 | 每个元素至少 1 条用例（低级+） |
| 页面跳转 | 新页面所有按钮/表格 | 继续 Phase 1-c DFS |
| 内容变更 | 新出现的按钮/筛选/表格 | 继续 DFS，对比变更前后数据 |

**前置条件 / 步骤写法（遵循 `quality-rubric.md` §三 边界规则）**：

- **前置条件**：只写测试开始前的初始状态，不含任何操作。衍生用例的前置条件是"摄像机开机时已有的状态"（如「1. 已登录系统 2. 在制造排产页面 3. 表格已加载 4. 未勾选行」），**不是**「已进入排产详情」「已选中行并点击批量完成」这种操作后的状态。
- **测试步骤**：从初始状态到验证前的所有操作都要写明。衍生用例的步骤**必须包含触发该交互面的父操作**（如「1. 勾选一行 2. 点击批量排产 3. 等待切换到排产详情视图 4. 点击返回 5. 观察页面」），不允许跳过父操作直接写「进入排产详情」。
- **预期结果**：只验证当前操作的结果，不验证父操作的结果。

> ⚠️ v1 旧写法「前置条件必须包含完整操作路径」「步骤禁止回溯父步骤」**已废弃**——它把父操作伪装成前置条件，破坏了链路完整性。完整链路：`前置条件（初始状态）→ 步骤1 → 步骤2 → ... → 步骤N → 预期结果（验证）`。

**正例对照**（批量排产→返回，衍生用例）：

| 列 | ❌ v1 旧写法（错误） | ✅ v2 新写法（正确） |
|----|---------------------|---------------------|
| I 前置条件 | 「1. 已勾选待排产行 2. 已点击批量排产 3. 已进入排产详情视图」 | 「1. 已登录系统 2. 在制造排产页面 3. 表格已加载 4. 未勾选行」 |
| J 测试步骤 | 「1. 点击返回」 | 「1. 勾选一行待排产记录 2. 点击「批量排产」 3. 等待切换到排产详情视图 4. 点击「返回」 5. 观察页面」 |
| L 预期结果 | 「返回主页面」 | 「页面返回到制造排产主页面，表格保持原筛选状态」 |

### 关键约束

- **禁止**一条用例写多个步骤（点按钮A → 弹窗B操作字段C → 点确定D），必须拆为多条
- 三种子节点关闭/返回后，**必须重新分析主页面 DOM**（操作可能改变了主页面的数据/状态）
- 页面跳转后 MUST 重新获取 iframe 引用 `page.frames().find(...)`
