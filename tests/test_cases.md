# 领退料管理模块 - 测试用例

## 前置条件
- 已登录 SCM 系统
- 位于"领退料明细表"模块
- 筛选区已展开（内联模式）

---

## TC-01：筛选区全量字段扫描

| 字段 | 操作符选项 | 值选项 | 输入类型 |
|------|-----------|--------|---------|
| 领退单号 | 包含/等于/不等于/为空/不为空 | — | text-input |
| 关联单号 | 同上 | — | text-input |
| 审批状态 | 等于/不等于 | 审批中/审批通过/审批驳回 | searchable-dropdown |
| 审批人 | 包含/等于/不等于/为空/不为空 | 70人列表 | searchable-dropdown |
| 客商/生产部门 | 同上 | — | text-input |
| 领/退料人 | 同上 | — | text-input |
| 领退类型 | 等于/不等于 | 13种(生产退料/领料/补料…) | searchable-dropdown |
| 制单时间 | 介于 | — | date-range |
| 操作时间 | 介于 | — | date-range |
| 领退料状态 | 包含/等于/不等于/为空/不为空 | — | text-input |
| 发货模式 | 同上 | — | text-input |
| 预计出货时间 | 介于 | — | date-range |
| 成品识别码 | 同上 | — | text-input |
| 制令单 | 包含/等于/不等于/为空/不为空 | — | text-input |
| 备注 | 同上 | — | text-input |
| 创建人 | 同上 | 70人列表 | searchable-dropdown |
| 打印次数 | 等于/不等于/大于/大于等于/小于/小于等于 | — | text-input |
| 优先级 | 同上 | — | text-input |
| 是否推送仓库 | 等于/不等于 | 否/是 | searchable-dropdown |

**验证点：**
- [ ] 19 个字段全部扫描完整
- [ ] 每个字段的 operatorOptions 与当前 operator 一致
- [ ] 下拉字段的 options 列表读取完整
- [ ] 日期字段只有"介于"操作符

---

## TC-02：VTable 行选择与列值读取

### TC-02.1 按序号选中行
```
1. get_column_values("序号") → 找到"7" → row_idx=6
2. click_cell(col=0, row=7)  ← VTable row = data_idx + 1(跳过表头)
3. 验证 bodySelectGroup > 0
```

**验证点：**
- [ ] VTable 行索引从 1 开始（row 0 是表头）
- [ ] 点击复选框列（col=0）可选中行
- [ ] 选中后 bodySelectGroup 计数增加

### TC-02.2 列值读取
```
遍历所有列标题，调用 get_column_values(title)
```

**验证点：**
- [ ] 所有列的值可读取
- [ ] 数值列返回正确的数字格式
- [ ] 空列返回空列表

---

## TC-03：工具栏操作

### TC-03.1 加急/取消加急
```
1. 选中行（序号=7）
2. click 加急按钮 @(700, 118)
3. → toast "操作成功"（message 类型，~100ms 延迟渲染）
4. click 取消加急按钮 @(778, 118)
5. → toast "操作成功"
```

**验证点：**
- [ ] 加急前置条件：行必须已选中，否则弹出 notification "请先勾选数据"
- [ ] 加急成功出现 message "操作成功"
- [ ] 取消加急同样出现 "操作成功"
- [ ] toast 约 2-3s 后自动消失

### TC-03.2 批量编辑优先级
```
1. 选中行
2. click 批量编辑优先级 @(605, 118)
3. → 弹窗"批量编辑优先级"（modal）
   字段: 优先级 (number, value=0)
   按钮: Increase Value / Decrease Value / 取 消 / 确 定
4a. click Increase Value → value: 0→1
4b. click 输入框聚焦 → type(5) → value: 5
5. click 确 定 → toast "操作成功", 弹窗关闭
```

**验证点：**
- [ ] 弹窗包含 number 类型输入框
- [ ] Increase/Decrease 按钮每次 ±1
- [ ] 直接输入可覆盖值
- [ ] 确 定后弹窗关闭 + toast 反馈
- [ ] 取 消关闭弹窗不保存

---

## TC-04：表格内可点击元素

### TC-04.1 展开行图标
```
1. scan_floats 检测到 ant-table-row-expand-icon
2. 点击展开图标 → tbody 增加 4 行（子表展开）
3. 展开行 class: ant-table-expanded-row
4. 子表包含制令单明细（MO编号/成品/数量/标准用量）
```

**验证点：**
- [ ] scan_floats 正确识别"收起子表"和"展开子表"图标
- [ ] 展开后 tbody 行数增加
- [ ] 展开行使用 colspan 合并列
- [ ] 再次点击可收起

### TC-04.2 表格内链接双击 → tabpanel 切换
```
1. scan_floats 检测到 a 标签（cursor:pointer）
2. 双击采购单号 → tabpanel 切换至对应单据页面
3. 双击制令单号 → tabpanel 切换至制令单新增（查看模式）
4. frame_url 变更, active_tab 变更
```

**验证点：**
- [ ] 双击可交互链接触发 tabpanel 切换
- [ ] scan_floats 自动跟随新活跃 iframe
- [ ] frame_url 和 active_tab 返回新页面信息
- [ ] 新页面可继续操作（完整用例路径）

---

## TC-05：浮窗全方位检测（scan_floats）

### TC-05.1 弹窗检测
```
scan_floats(only_visible=True, include_table_data=True)
```

**覆盖的浮窗类型：**
- [ ] modal（如"获取实际库存不足"、"商品信息"、"批量编辑优先级"）
- [ ] drawer
- [ ] popover
- [ ] tooltip
- [ ] dropdown
- [x] message（"操作成功"toast）
- [x] notification（"请先勾选数据"）

**每浮窗返回结构：**
- [ ] title / type / scope
- [ ] center(cx,cy) — top-viewport 几何中心
- [ ] rect(x,y,w,h)
- [ ] closeButton — selectorHint + center
- [ ] buttons — text + center + selectorHint
- [ ] fields — label + type + center + value
- [ ] tables — headers + rowCount + data
- [ ] frame_url / active_tab

### TC-05.2 短寿命 Toast 检测
```
scan_floats 在 JS 注入后自动调用 detect_message/detect_notification
```

**验证点：**
- [ ] message "操作成功" 被捕获
- [ ] notification "请先勾选数据" 被捕获
- [ ] 无重复（JS 注入与 toast 检测不重叠）

---

## TC-06：坐标系统验证

| 场景 | 坐标系 | 预期 |
|------|--------|------|
| `get_element_coords(xpath)` → `click_xy()` | top-viewport | ✅ 命中 |
| `fr.actions.move_to(x,y)` | iframe-relative | ✅ 命中 |
| `tab.actions.move_to(x,y)` | top-viewport | ✅ 配合 viewport_midpoint |
| 裸用 `getBoundingClientRect()` 不叠加偏移 | iframe-relative | ❌ 点偏 |

**统一工具函数：**
- `get_element_coords(xpath, index, timeout)` → 返回 `{cx, cy}`
- `get_element_center(el)` → 返回 `{cx, cy}`（基于 viewport_midpoint）

---

## TC-07：Tabpanel 切换跟踪

```
交互前记录 frame_url
交互后 scan_floats → 比较 frame_url 变化
```

| 触发操作 | 切换结果 |
|---------|---------|
| 双击采购单号 PO2026... | saleOrderCreate 页面 |
| 双击制令单号 MO2026... | 制令单新增页面（prodctionOrderCreate） |
| 返回/列表按钮 | 返回原模块 |

**验证点：**
- [ ] scan_floats 结果中的 `frame_url` 反映当前活跃 iframe
- [ ] `active_tab` 反映当前模块名称
- [ ] tabpanel 切换后旧 iframe 的 aria-hidden=true

---

## TC-08：制令单查看页详情

### 页面元素
| 区域 | 内容 |
|------|------|
| 基本信息 | 制令单号/来源单号/销售单号/客户/成品编码/数量/日期 |
| 进度面板 | 领料进度 100% / 发料进度 0% / 缴库进度 0% |
| 物料明细(VTable) | 10列 × 1行: 4001-0001-00 ST12-卷料 应发94 已领94 |
| 工具栏 | 返回/列表/新增/生产量变更/删除(disabled) |

### 验证点
- [ ] VTable 列值读取完整
- [ ] 进度百分比与表格数据一致（已领94/应发94=100%）
- [ ] 删除按钮为 disabled 状态
- [ ] 备注字段为空

---

## 覆盖率统计

| 类别 | 覆盖 |
|------|------|
| 浮窗类型 | 6/7（modal/message/notification/dropdown/popover/tooltip） |
| 表格类型 | VTable + HTML Table |
| 交互方式 | click / double-click / drag / input / type |
| 反馈类型 | toast / modal / notification / tabpanel switch |
| 坐标系统 | top-viewport / iframe-relative / viewport_midpoint |
| 检测工具 | scan_floats / scan_filter_fields / get_element_coords |
