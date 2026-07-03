# VTable 交互探索规范

## 一、VTable 挂载与列扫描

```python
mount_vtable()                # → { ok: true } 或 { ok: false, reason }
scan_vtable_columns()         # → 每列: col, title, bodyBehavior, icons[]
```

`bodyBehavior` 值：`复选框` | `链接/按钮` | `文本`

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

## 四、单元格交互（按 bodyBehavior 分发）

| bodyBehavior | 操作 | 预期效果 |
|-------------|------|---------|
| 复选框 | `click_cell(col, row)` 直接点击 | 行选中/取消 |
| 链接/按钮 | `click_cell(col, row, double_click=True)` | 弹详情弹窗或跳转 |
| 文本 | 不交互 | 仅验证展示内容 |

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
| **交互弹窗** | `detect_modal()` 返回弹窗类型 | DFS 弹窗内部，关闭后继续 |
| **业务确认弹窗** | 确认/取消二选一 | 分别测「取消」「关闭×」「确定」，各自衍生用例 |
| **消息提醒** | 仅文字提示 | 提取文字 → 关闭（不残留） |
| **页面跳转** | Tab 变化 | 重新获取 iframe → 递归 DFS |
| **内容变更** | 无弹窗+无 Tab 变化+元素改变 | 重新分析当前 DOM 继续 DFS |
| **下载/打印** | 触发浏览器下载 | 记录类型，不阻塞 |
| **新 Tab** | Tab 数增加 | 切换到新 iframe → 递归 DFS → 关闭 Tab |

### 三步排查法（每次点击后 MUST）

```python
# 操作前记录基线
dom_overview()
run_js("return window.location.href")

# 执行操作
click(...) 或 click_cell(...)

# 操作后三步排查
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
