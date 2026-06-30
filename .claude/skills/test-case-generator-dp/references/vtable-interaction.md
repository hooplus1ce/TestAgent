# VTable 交互探索规范

> 承接 SKILL.md Phase 1-c-2 / 1-d。本文件覆盖：VTable 挂载、行选择、单元格交互、DFS 子节点扩展、场景图 API、以及**挂载失败时的降级路径**。

## 一、VTable 挂载与列扫描

VTable 操作通过 `drission-ui` MCP 工具封装，AI 只需调用工具并消费结构化 JSON。坐标换算、iframe 穿越由工具自动处理。

```
# 1. 挂载 VTable
mount_vtable()
# → { ok: true } 或 { ok: false, reason: "…" }

# 2. 列扫描（表头信息 + 图标坐标）
scan_vtable_columns(max_col=50)
# 返回每列:
#   col       — 列索引
#   title     — 列标题
#   bodyBehavior — '复选框' | '链接/按钮' | '文本'
#   icons[]   — 列头图标列表
#     func    — 'sort' | 'filter'
#     viewportX / viewportY — 图标视口坐标（工具已自动处理 iframe 偏移）
```

> **注意**：`scan_vtable_columns` 内部已封装了原 `scanColumns(50)` 函数，返回结构化 JSON。AI 不应再手工调用 `window._vtable` 上的 JS 函数。

## 一·补充、VTable 数据提取样本（只读，不点击）

挂载 VTable 后，可读取列定义和样本数据，用于用例的测试数据列(K)填充。通过 `run_js` 工具在页面上下文中执行只读 JS：

```
# 获取列定义
run_js("return window._vtable.columns.map(c => ({ field: c.field, title: c.title }))")

# 获取样本数据（行 1-5，0=表头）
run_js(`
  var vt = window._vtable;
  return [1,2,3,4,5].map(r => vt.getCellOriginRecord(0, r))
`)

# 获取筛选后全部记录
run_js("return window._vtable.getFilteredRecords()")
```

**推荐方式**：按列名取全列数据，使用 `get_column_values(title, raw=False)`：

```
# 获取「制令单号」列全部值（raw=False 返回去重+清洗后的值）
get_column_values("制令单号")

# 获取原始值（raw=True 返回逐行值，含空值/重复）
get_column_values("制令单号", raw=True)
```

**用途**：为测试数据列(K)提供真实字段值，替代用户描述或臆造数据。详见 `filter-validation.md`。

## 二、VTable 挂载失败的降级路径（MUST）

当 `mount_vtable` 返回 `{ok: false}` 时，**不要硬冲**，按以下流程降级：

```
mount_vtable() → {ok: false}
  │
  ├─ Step 1: 排查根因
  │    检查 .vtable 元素是否存在 → 不存在：页面未加载完，等待 3s 重试 1 次
  │    检查 fiber key 是否找到 → 未找到：React 版本异常，记 reason
  │    检查 vtableInstance 30 层内是否存在 → 不存在：记 reason
  │
  ├─ Step 2: 截图存档
  │    screenshot → 保存到 OUTPUT_DIR/.vtable-fallback-<timestamp>.png
  │
  ├─ Step 3: 降级生成用例（仅低级展示类）
  │    基于截图 + dom_overview 的可见列标题，生成：
  │      - 「VTable 数据展示正确性」用例（低级，预期基于截图比对）
  │      - 「VTable 列标题完整显示」用例（低级）
  │    在用例 R 备注列标注：「VTable 实例未挂载，断言基于截图，建议人工复核」
  │
  └─ Step 4: 跳过所有需点击的交互用例（复选框/链接/排序/筛选）
       不生成无法验证的交互用例
```

## 三、行选择（复选框列点击）

### ⚠️ 已知限制：Canvas VTable 复选框无法通过坐标点击

VTable 的复选框列由 **canvas 渲染**，无真实 DOM 节点。经多次验证：

- `click_cell(col=0, row=N)` → **无效**，复选框视觉状态不变
- `click_xy` 在复选框坐标 → **无效**，canvas 不响应坐标点击
- `dispatchEvent` 在 canvas 上触发点击 → 可更新 `getSelection()` 但**不同步视觉复选框**，按钮 handler 检测不到
- `methods.setSelection()` → 同样不更新视觉复选框

**结论**：MCP 工具 `click_cell` / `click_xy` 均无法可靠点击 canvas 渲染的复选框。

### 推荐工作流：程序化选中 + 按钮交互

当用例需要"勾选行后点击操作按钮"时，采用以下流程：

```
# Step 1: 通过 run_js 程序化设置选中状态
run_js("
  var vt = window._vtable;
  vt.stateManager.checkedState = [rowIndex];  // rowIndex=目标行号
  vt.render();
")

# Step 2: 验证选中生效
run_js("return window._vtable.getCellInfo(0, rowIndex).value")
# → true（已选中）

# Step 3: 执行后续按钮操作（此时按钮 handler 可检测到选中状态）
click(locator="text:批量完成")
```

> **验证方式**：用 `run_js` 调用 `getCellInfo(0, row).value`，不要用 `getCheckboxState()`（可能返回 null）。

### 纯粹的行选择验证用例

若仅需验证「勾选/取消勾选行」的 UI 行为（不涉及按钮交互），可降级为「低级」用例，预期基于截图比对复选框视觉状态。

## 四、单元格交互（按 bodyBehavior 分发）

| bodyBehavior | 操作 | 预期效果 |
|-------------|------|---------|
| 复选框 | 见 §三（程序化选中） | 行选中/取消 |
| 链接/按钮 | 双击单元格文字 | 弹详情弹窗或跳转 |
| 文本 | 不交互 | 仅验证展示内容 |

**双击实现**：

```
# 链接/按钮列：双击单元格
click_cell(col, row, double_click=True)
# 工具内部自动处理：坐标定位 → 双击（间隔≈150ms） → 等待响应
```

**双击后必须立即检测产物**（见 §七）：

```
click_cell(col, row, double_click=True)
detect_modal()                        # ① 弹窗？
dom_overview()                        # ② Tab 变化？
run_js("return window.location.href") # ③ iframe URL 变化？
# 都没变 → ④ 内容变更
```

### ⚠️ 必须先 scroll 到目标列再点击

VTable 画布宽度有限（默认 ~1726px），第 15 列以后的列其坐标可能超过画布宽度。虽然在逻辑坐标上正确，但**点击时目标不在视口内，会打到空处**。

**每次点击列头图标或目标列单元格前，必须显式 scroll：**

```
# ❌ 错误：列不在视口内时直接 click_cell
click_cell(col=20, row=0, icon_name="filter-icon")
# → viewportX=2398，超出屏幕，点不到

# ✅ 正确：先 scroll 再 click
scroll_to_cell(col=20, row=0)
click_cell(col=20, row=0, icon_name="filter-icon")
# → viewportX=1344，在视口内，成功弹出筛选面板
```

## 五、列头图标（排序/筛选）

列头图标坐标由 `scan_vtable_columns` 返回的 `icons[]` 提供，但**无需手动操作坐标**——直接使用 `click_cell` 的 `icon_name` 参数：

```
# 点击排序图标
click_cell(col, row=0, icon_name="sort")

# 点击筛选图标
click_cell(col, row=0, icon_name="filter-icon")
```

> 工具内部自动从 `scan_vtable_columns` 缓存中查找对应列图标的视口坐标，**自动处理 iframe 偏移与鼠标轨迹**，AI 无需关心 `viewportX/viewportY` 数值。

**筛选图标点击后**：等待 2s，用 `detect_modal` 检测弹出的筛选面板，命中则对筛选面板 DFS 生成衍生用例。详见 `modal-types.md`。

## 六、坐标保障

### 工具自动处理的保障

`drission-ui` MCP 工具的 VTable 相关操作（`get_cell_rect`、`click_cell`、`scan_vtable_columns`）已**内部封装**以下保障：

- **iframe 偏移自动叠加**：工具内部获取 iframe 的 `getBoundingClientRect()`，将 canvas 坐标转换为视口坐标。AI 调用时传入的 `col`/`row` 参数是逻辑坐标，工具完成转换。
- **滚动自动检测**：`get_cell_rect(col, row, scroll=False)` 返回 `viewportX` 若超出当前视口宽度，说明目标列不在可见区域，需先 `scroll_to_cell` 再点击。
- **点击后验证**：若 `get_column_values` 未反映预期变化，自动重试 1 次（重新取坐标再点）。

### ⚠️ 禁止手动坐标计算

**切勿在 AI 层面手动计算视口坐标**。以下模式已废弃且会导致**双倍偏移**（工具内部已加过一次 iframe 偏移，AI 侧再加一次会错位）：

```
# ❌ 严禁：手动计算 iframeRect + vtableRect + bounds 偏移
# 工具内部已自动处理，重复计算会导致坐标错误

# ✅ 正确：直接使用工具参数
get_cell_rect(col, row)          # 工具返回的 viewportX/Y 已是最终视口坐标
click_cell(col, row)             # 直接传逻辑 col/row
click_cell(col, row=0, icon_name="sort")  # 传 icon_name，工具内部定位
```

### 场景图坐标（内部实现说明）

工具内部优先使用 VTable 场景图 API（`scenegraph.getCell(col, row).globalAABBBounds`，含 auto-fill 压缩 + scroll 偏移），场景图不可用时降级为 `getCellRect` + scroll 修正。这些都是工具内部实现细节，**AI 无需关心**。

## 七、DFS 子节点扩展（弹窗 / 页面跳转 / 内容变更）

按钮点击后的产物——弹窗、页面跳转、同屏内容变更——都是新的交互面，作为 DFS 子节点递归遍历，生成衍生用例。

### 五种子节点类型（含业务确认弹窗 / 下载打印）

DFS 遍历按钮时，点击产物分五类。前两类（业务确认弹窗、下载/打印）容易被忽略但必须处理：

| 类型 | 判断条件 | 处理方式 |
|------|---------|---------|
| **交互弹窗** | `detect_modal()` 返回弹窗类型 | DFS 弹窗内部，关闭后继续 |
| **业务确认弹窗** | `detect_modal()` 返回确认/取消二选一 | 记录标题/内容/按钮 → **测「取消」→ 关闭× → 测「确定」**，分别衍生用例 |
| **消息提醒** | `detect_modal()` 返回消息类型 | 提取文字 → 关闭（不残留） |
| **页面跳转** | 操作前后 `dom_overview()` Tab 变化 | 重新获取 iframe → 递归 DFS |
| **内容变更** | `detect_modal()` 无弹窗 + `dom_overview()` 无变化 + 元素集合改变 | 重新分析当前 DOM 继续 DFS |
| **下载/打印** | 触发浏览器下载/打印对话框 | **记录类型，不阻塞**（不实际下载），继续下一按钮 |
| **新 Tab** | `dom_overview()` 显示 Tab 数增加 | 切换到新 iframe → 递归 DFS → 关闭 Tab |
| **系统级确认弹窗** | `detect_modal()` 返回「未登录/过期」 | Cookie 注入（见 `scm-access.md`） |

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

### 三步排查法（每次点击后 MUST 执行）

```
# 操作前记录基线
dom_overview()     # 记录当前 Tab 结构
run_js("return window.location.href")   # 记录当前 iframe URL

# 执行操作
click(...) 或 click_cell(...)

# 操作后三步排查（顺序不可变）
detect_modal()                        # ① 弹窗类型？（返回 type:"none" 表示无弹窗）
dom_overview()                        # ② Tab 变化？（对比操作前后 Tab 数/名称）
run_js("return window.location.href") # ③ iframe URL 变化？（同 tab 内跳转）
# 都没变 → ④ 内容变更（同 iframe 内元素刷新）
```

### 实战案例：双击制令单单号单元格的三种可能

双击 VTable 单元格后，结果可能是以下三种之一，**必须逐一排查**：

```
# 双击后做检测
click_cell(col=3, row=8, double_click=True)
detect_modal()                        # → ① 弹窗类型
dom_overview()                        # → ② tab 变化（如制令单明细表 → 制令单新增）
run_js("return window.location.href") # → ③ iframe URL 变化（同 tab 内跳转）
# 都没变 → ④ 内容变更（同 iframe 内元素刷新）
```

**本次验证的真实案例**（双击第 8 行制令单单号 `MO202606100004`）：

| 检查项 | 结果 | 结论 |
|--------|------|------|
| `detect_modal()` | `type:"none"` | ❌ 不是弹窗 |
| `dom_overview()` | 新增 tab「制令单新增」| ✅ **Tab 跳转** |
| `run_js("return window.location.href")` | URL 含 `prodctionOrderCreate?mode=view&id=2340` | ✅ 确认跳转到详情页 |

→ 分类：**「新 Tab」类型**，需切换到新 iframe 继续 DFS。

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
- 页面跳转后 MUST 重新获取 iframe 引用：`get_active_frame()` 获取当前活跃 iframe，或通过 `dom_overview` 确认新的 Tab 结构后再操作
