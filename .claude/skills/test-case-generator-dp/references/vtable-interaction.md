# VTable 交互探索规范

## 一、VTable 挂载与列扫描

```python
mount_vtable()                # → { ok: true } 或 { ok: false, reason }
scan_vtable_columns()         # → 每列: col, title, bodyBehavior, icons[]
```

`bodyBehavior` 值：`复选框` | `链接/按钮` | `文本`

## 二、VTable 单元格交互后的下拉/浮层搜索策略（MUST）

点击VTable单元格后,可能出现下拉选项、浮层等交互元素。**搜索顺序**:

1. **优先搜索VTable相关元素** (而非默认假设是Ant Design组件):
   ```javascript
   // 第一步: 搜 virtual-option (VTable自定义下拉选项)
   document.querySelectorAll('.virtual-option')
   
   // 第二步: 搜包含 'virtual' 的class (VTable生态常见)
   document.querySelectorAll('[class*="virtual"]')
   
   // 第三步: 才搜 Ant Design 组件作为后备
   document.querySelectorAll('.ant-select-dropdown-menu-item,.ant-select-item-option')
   ```

2. **典型场景**:
   - 商品编码选择下拉: `virtual-option` div元素
   - 搜索结果列表: 可能用virtual列表
   - 建议先从最通用的数字/连字符文本特征入手(如商品编码格式)

3. **关键教训**:
   - ❌ 错误: 假设所有下拉都是 `ant-select-dropdown-menu-item`
   - ✅ 正确: 优先搜索与VTable/virtual相关的class

## 二、挂载失败的降级路径（MUST）

当 `mount_vtable` 返回 `{ok: false}` 时：
1. 排查根因：`.vtable` 元素不存在则等待 3s 重试 1 次
2. `screenshot()` 存档
3. 降级生成**仅低级展示类**用例（基于截图+dom_overview 可见列标题）
4. 备注标注：「VTable 实例未挂载，断言基于截图，建议人工复核」
5. 跳过所有需点击的交互用例（复选框/链接/排序/筛选）

## 三、行选择（复选框列）

Canvas VTable 的复选框列可通过 `click_cell(col, row)` 直接点击。工具内部流程：

1. `get_cell_rect(col, row)` 通过 VTable 场景图 API（`scenegraph.getCell(col, row).globalAABBBounds`）获取单元格中心帧内坐标，JS 端叠加 `window.frameElement.getBoundingClientRect()` 偏移，一次算到顶层视口坐标 viewportX/viewportY（Python 不再二次叠加，避免双倍偏移）
2. `tab.actions.move_to((viewportX, viewportY)).click()` 执行坐标级点击

```python
# 点击第 N 行的复选框
click_cell(col=0, row=5)   # col=0 为复选框列
```

### 程序化选中（备选方案）

当需要批量勾选多行时，也可用 `run_js` 快速设置选中状态（更高效，不涉及 UI 点击动画）：

```python
# 设置选中状态
run_js("window._vtable.stateManager.checkedState = [rowIndex]; window._vtable.render()")
# 验证
run_js("return window._vtable.getCellInfo(0, rowIndex).value")  # → true
# 执行后续按钮操作
click(locator="text:批量完成")
```

## 三·二、VTable records 与业务侧 React state 同步（关键陷阱）

VTable records 分**视图层**和 **source 数据源**两层。业务侧 React 组件维护独立明细 state，通过 VTable onChange 回调同步。直接改 VTable records **不等于**改业务侧 state——保存时业务侧用自己的 state 校验，会报字段空（但 `inst.records[0].field` 明明有值）。

| API | 改哪层 | 触发业务侧 onChange | 适用 |
|---|---|---|---|
| `setCellCheckboxState(col,row,checked)` | 视图层 checkboxState | ❌ 不触发 onCheckboxChange | 仅 UI 选中态 |
| `changeCellValue(col,row,value)` | 视图层 records | ❌ | 仅 VTable 显示值 |
| `changeSourceCellValue(col,row,value)` | **source 数据源** | ✅ 触发业务侧同步 | **填明细字段（数量/单价等）** |
| `startEditCell` + 真实键盘 + `completeEditCell` | source（经编辑器） | ✅ | 标准编辑（需列有 editor） |

**判定**：保存报"XX不能为空"但 `records[0].field` 有值 → 业务侧未同步，改用 `changeSourceCellValue`，配合 `refreshAfterSourceChange()` 刷新。

**列是否有 editor 不能只看 `isHasEditorDefine`**：销售订单主表该方法全返回 false，但 `options.editCellTrigger:"click"` 存在；以列配置 `c.editor`/`c.bodyEditor` 是否为 null 为准。无 editor 的列只能用 `changeSourceCellValue`。

### 弹窗内 VTable 实例的挂载

`mount_vtable` 默认挂载主表到 `window._vtable`。弹窗内 VTable（如"选择商品"弹窗）是另一实例，需沿弹窗 canvas 祖先 fiber 单独挂载到 `window._modalVtable`：

```javascript
const mc = document.querySelector('.ant-modal canvas');
let p = mc.parentElement, fk = null;
for (let i=0; i<10 && p; i++) {
  fk = Object.keys(p).find(k => k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance'));
  if (fk) break;
  p = p.parentElement;
}
let fiber = p[fk];
for (let c=0; fiber && c<40; c++) {
  if (fiber.stateNode && fiber.stateNode.vtableInstance) { window._modalVtable = fiber.stateNode.vtableInstance; break; }
  fiber = fiber.return;
}
```

⚠️ **弹窗重开会复用旧实例**，`canvasElement` 变 null 且 4s 内不恢复——需在弹窗**首次打开**时操作 canvas 坐标；重开后只能用 API（`setCellCheckboxState` 不触发业务侧 onCheckboxChange，添加的商品进 VTable records 但业务侧 state 可能不完整，需 `changeSourceCellValue` 补 number/unitPrice 等字段）。

⚠️ **`window.frameElement` 在 run_js 中可能 undefined**：算顶层视口坐标改用 `Array.from(window.parent.document.querySelectorAll('iframe')).find(f => f.contentWindow === window)`；或直接用 `click_cell` 工具（内部处理换算）。

### 保存按钮两种形态（点击前必判）

SCM 保存按钮有两种形态，点击前 `run_js` 读 `button.className` 判断，**两种情况都要考虑**：

- **下拉触发器型**：`className` 含 `ant-dropdown-trigger`。点击只弹菜单（"保存"/"保存并新增"），需二次点 `li.ant-dropdown-menu-item` 才保存。
- **普通按钮型**：不含 `ant-dropdown-trigger`，点击直接保存。

详见 memory `scm-save-button-two-forms`。

## 四、单元格交互（按 bodyBehavior 分发）

| bodyBehavior | 操作 | 预期效果 |
|-------------|------|---------|
| 复选框 | `click_cell(col, row)` 直接点击 | 行选中/取消 |
| 链接/按钮 | `click_cell(col, row, double_click=True)` | 弹详情弹窗或跳转 |
| 文本 | 可能出现下拉(如商品选择),见第五节 | 见第五节 |

## 五、VTable 单元格下拉交互（新增）

当点击某些VTable单元格(如商品编码、选择型字段)会出现下拉选项:

### 5.1 下拉元素识别策略
```javascript
// ✅ 优先搜索顺序(按可能性排序):
// 1. virtual-option (VTable自定义下拉)
// 2. 包含'option'的元素
// 3. 包含'virtual'的元素
// 4. Ant Design组件(最后才搜)

// 快速验证方法: 搜文本特征(如商品编码格式)
[...document.querySelectorAll('*')].filter(el => 
  el.textContent.trim().match(/\d+\.\d+\.\d+/) ||  // 12.325.215.21 格式
  el.textContent.trim().match(/\d+-\d+-\d+/)      // 2001-0001-00 格式
)
```

### 5.2 点击后的三步检查
```python
# 1. click_cell 点击目标单元格
click_cell(col, row)

# 2. 先不假设弹窗类型,直接检查DOM变化
# 立即搜索 virtual-option!
run_js("""
  var opts = document.querySelectorAll('.virtual-option');
  return {
    count: opts.length,
    visible: [...opts].filter(o => o.offsetParent !== null).map(o => o.textContent.trim())
  }
""")

# 3. 然后才 detect_modal (防消息提醒)
detect_modal()

# 4. 如果找到 virtual-option,用文本定位器直接点击
click(locator="x://div[@class='virtual-option' and contains(text(), '7001-0687-01')]")
```

### 5.3 典型示例
- **商品编码选择**: 点击商品识别码列 → 出现 virtual-option 下拉
- **其他选择型字段**: 类似,先找 virtual-option
- 下拉选项通常有完整可见坐标(非0,0,0,0),可直接用 click 工具点击

## 五、列头图标（排序/筛选）

```python
click_cell(col, row=0, icon_name="sort")         # 排序
click_cell(col, row=0, icon_name="filter-icon")  # 筛选
```

工具内部自动从缓存查找图标坐标，无需手动操作 `viewportX/Y`。

## 六、坐标保障

- iframe 偏移、坐标换算由 MCP 工具自动处理
- `get_cell_rect(col, row, scroll=False)` 返回坐标，超出视口时需先 `scroll_to_cell(col, row)` 再 `click_cell`
- **禁止 AI 层面手动计算坐标**——会导致双倍偏移

## 七、DFS 子节点扩展

按钮点击后的产物作为 DFS 子节点递归遍历：

| 类型 | 判断条件 | 处理方式 |
|------|---------|---------|
| **交互弹窗** | `observe_post_click`/`detect_modal` 返回弹窗类型 | DFS 弹窗内部，关闭后继续 |
| **业务确认弹窗** | 确认/取消二选一 | 分别测「取消」「关闭×」「确定」，各自衍生用例 |
| **消息提醒** | 仅文字提示 | 提取文字 → 关闭（不残留） |
| **页面跳转** | Tab 变化 | 重新获取 iframe → 递归 DFS |
| **内容变更** | 无弹窗+无 Tab 变化+元素改变 | 重新分析当前 DOM 继续 DFS |
| **下载/打印** | 触发浏览器下载 | 记录类型，不阻塞 |
| **新 Tab** | Tab 数增加 | 切换到新 iframe → 递归 DFS → 关闭 Tab |

### 点击后观察（每次点击后 MUST）

优先用 `observe_post_click`——一次调用并发抓 弹窗/通知/消息/Tab/URL/网络，first-signal-wins，替代旧的三步串行排查：

```python
# 执行操作
click(...) 或 click_cell(...)

# 一次调用覆盖所有信号（MutationObserver 事件驱动，不漏短寿命 toast）
observe_post_click(timeout=8, signals=["modal","notification","message","tab","url"])
# → 命中：{type, scope?, payload?, elapsedMs}  type ∈ interactive/confirm/system_confirm/notification/message/tab_change/url_change
# → 未命中：{type:"none"} → 同 iframe 内容变更，重新分析 DOM
```

需要抓接口时：`signals=[...,"network"]` + `listen_targets="gateway"`（SCM 保存接口走 gateway，业务关键词不命中）。

```python
# 旧的三步串行排查（fallback，observe_post_click 不可用时用）
detect_modal()                        # ① 弹窗类型？
dom_overview()                        # ② Tab 变化？
run_js("return window.location.href") # ③ iframe URL 变化？
# 都没变 → ④ 内容变更（同 iframe 内元素刷新）
```

### 衍生用例规则

- **前置条件**：只写测试开始前的初始状态（如「1. 已登录系统 2. 在制造排产页面 3. 表格已加载 4. 未勾选行」），不含任何操作
- **测试步骤**：从初始状态到验证前的所有操作都要写明，**不允许跳步**
- **预期结果**：只验证当前操作的结果，不验证父操作的结果

> ⚠️ 「前置条件须含完整操作路径」的旧规则已废弃——它把操作伪装成前置条件，破坏链路完整性。
