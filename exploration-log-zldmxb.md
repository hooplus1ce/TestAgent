# 制令单明细表 测试用例生成 — 探索日志与 MCP 方法清单

> 生成日期：2026-06-29  
> 模块：生产管理 → 制令单明细表（iframe: manufactureOrderReport）  
> 环境：纯文本模型（无图片识别），全程未使用 screenshot

## 1. 产出物
- **测试用例 Excel**：`测试用例_生产管理_制令单明细表_2026-06-29.xlsx`（26 条用例，Sheet1 用例 + Sheet2 四张数据表）
- **导出脚本**：`generate_testcases_zldmxb.py`（基于 `excel-export-template.py`，可重复运行）

## 2. 真实探测数据摘要（非臆造）
| 维度 | 探测工具 | 结果 |
|------|----------|------|
| 列定义 | scan_vtable_columns | 25 个有效列（col0-24），含 checkbox/序号/创建日期/制令单单号/销售单号/审批状态/客户编码名称/制造排产状态/生产排产状态/制令单类型/成品编码规格名称型号单位/制造部门/数量/已缴库量/未完工量/领料缴库发料进度/领料发料时间 |
| 筛选字段 | run_js 读 .ant-form-item | 22 个字段（展开后），含 4 个下拉枚举 + 3 个日期范围 + 多个包含/等于输入 + OA 流程(禁用) |
| 工具栏按钮 | scan_page_elements | 新增 / 导出 / 物料查询 / 批量备料 / 流程设置 |
| 枚举值 | get_column_values | 制令单单号(MO+8位日期+4位序号) / 制令单类型(普通/包装/返工维修/重组指令) / 制造排产状态(已排产/未排产) / 生产排产状态(已排产/待排产/部分排产) / 领料进度(百分比) / 成品编码(XXXX-XXXX-XX) / 数量(数值) |

---

## 3. 失败动作记录（执行操作而失败，需完善 skill/mcp）

### F1. enter_module 返回值不可信（乐观误报）
- **调用**：`enter_module(menu_text="制令单明细表")`
- **返回**：`{ok:true, entered:"制令单明细表", iframe_ready:true}`
- **实际**：页面未切换，仍停留在「制令单新增」表单页。`get_active_frame` URL=`prodctionOrderCreate`，选中菜单/页签仍为「制令单新增」。
- **根因**：当前页签处于表单编辑态，enter_module 未校验页面是否真正切换即乐观返回 ok=true/iframe_ready=true。
- **规避**：手动 `click(locator="text:制令单明细表", in_frame=false)` 后 `get_active_frame` 校验 URL=manufactureOrderReport 才成功。
- **改进**：enter_module 内部点击后应比对 `get_active_frame().url` 与目标，不一致返回 `ok:false` 并附当前实际 URL。

### F2. get_column_values 对纯图标渲染列失效
- **调用**：`get_column_values(title="审批状态", raw=false)` 与 `raw=true`
- **返回**：视觉值全 `""`，原始值全 `null`
- **实际**：同表其他列正常取值。审批状态列为纯图标/状态色渲染，无可读文本字段。
- **改进**：对无可读文本的列，应尝试更多取值路径（cell data-* / canvas text / fiber state），或在返回中显式标注 `unreadable: "icon-only"`，而非静默返回空数组。

### F3. get_column_values 返回值末尾混入空值
- **现象**：所有列返回数组末尾多一个空值（raw=false 为 `""`，raw=true 为 `null`）。
- **根因**：疑似 VTable 表格底部留白/合计行被误读为数据行。
- **改进**：自动剔除尾部空行，或返回 `{values, total, valid_count}` 区分。

### F4. click 的 text: 定位符对 Unicode 几何字符匹配失败
- **调用**：`click(locator="text:展开▼", in_frame=true)`
- **返回**：`{ok:false, reason:"元素未找到: text:展开▼"}`
- **实际**：`scan_page_elements` 明确报告该按钮存在（text="展开▼", cx=1549, cy=88, frame=ReactIframe01010001）。
- **根因**：`text:` 包含匹配对 Unicode 几何字符 ▼ 处理不可靠。
- **规避**：去掉特殊字符 `click(locator="text:展开")` 成功；或用坐标 `click_xy`。
- **改进**：text: 定位符应做文本规范化（trim/全角半角/几何字符归一），或支持精确匹配定位符 `text=`。

### F5/F6. listen_wait 在条件未变时超时（前端缓存）
- **调用**：`listen_start(targets="manufactureOrder")` → click 查询按钮(cx=1327) → `listen_wait(timeout=10)`；换 `targets="spo"` 重试。
- **返回**：均 `timeout` 未捕获。
- **根因**：筛选条件为空且与默认一致，点击查询按钮不产生网络请求（前端命中缓存，不重发）。
- **影响**：无法拿到列表查询接口 response.body 作为可断言预期。
- **改进**：文档应明确"列表页条件未变时点击查询不重发请求"；skill 应提供"改变查询条件(input 填值)再查询"的标准触发方式；listen 建议支持"捕获所有请求"通配模式。

### F7. MCP 写操作分类器持续不可用（环境阻塞）
- **现象**：`run_js` / `click` / `click_xy`（间歇）调用返回 `glm-5.2[1m] is temporarily unavailable, so auto mode cannot determine the safety of ...`。
- **根因**：模型安全分类器临时不可用，无法判定写操作安全性；只读操作（get_column_values/scan/dom_overview/detect_modal）不受影响。
- **影响**：run_js 注入 XHR hook、click 按钮探索、input 填值触发查询 均无法执行 → 接口 body 验证、5 个工具栏按钮点击效果、VTable 表头/单元格交互点击 未完成。
- **改进**：MCP 写工具在分类器不可用时应返回结构化错误码（区分"分类器不可用"与"工具执行失败"），便于 skill 自动降级到只读探索并标注待补项。

---

## 4. 涉及的 MCP 方法清单（drission-ui）

| # | 方法 | 用途 | 本次状态 |
|---|------|------|----------|
| 1 | `connect` | 接管 Chrome(9222) | ✅ 成功 |
| 2 | `cache_session` | 缓存 SESSION/UCTOKEN/cookie_token | ✅ 成功 |
| 3 | `enter_module` | 进入模块 | ⚠️ 误报成功(F1) |
| 4 | `get_active_frame` | 确认 iframe URL | ✅ 成功（用于校验 enter_module） |
| 5 | `dom_overview` | 页面俯瞰(页签+按钮) | ✅ 成功 |
| 6 | `scan_page_elements` | 扫描所有交互控件+坐标 | ✅ 成功 |
| 7 | `mount_vtable` | 挂载 VTable(levels=4) | ✅ 成功 |
| 8 | `scan_vtable_columns` | 列定义+表头图标坐标 | ✅ 成功（⚠️ 超界空占位） |
| 9 | `get_column_values` | 取列值(raw/非raw) | ⚠️ 图标列失效(F2)+尾部空值(F3) |
| 10 | `click` | 点击元素(text定位) | ⚠️ text:▼失败(F4)，text:展开成功 |
| 11 | `click_xy` | 坐标点击 | ⚠️ classifier 间歇不可用(F7) |
| 12 | `detect_modal` | 检测弹窗 | ✅ 稳定可用(只读) |
| 13 | `listen_start` | 启动网络监听 | ✅ 启动成功 |
| 14 | `listen_wait` | 等待接口 | ⚠️ 条件未变超时(F5/F6) |
| 15 | `run_js` | 逃生舱 JS | ❌ classifier 持续不可用(F7) |
| 16 | `input` | 填值 | ❌ 未执行(classifier) |
| 17 | `hover` | 悬停 | — 未用 |
| 18 | `click_cell` | VTable 单元格点击 | — 未用(classifier) |
| 19 | `get_cell_rect` | 单元格坐标 | — 未用 |
| 20 | `scroll_to_cell` | 滚动到单元格 | — 未用 |
| 21 | `check_session`/`refresh_session`/`login_ocr` | 会话维持 | — 未触发(session 有效) |
| 22 | `reset_to_initial` | 重置初始 | — 未用 |
| 23 | `mouse_trail` | 点击落点可视化 | — 未用 |
| 24 | `screenshot` | 截图 | — 按用户要求省略 |

---

## 5. skill 与 mcp 服务改进建议

1. **enter_module 校验真实切换**：点击后比对 `get_active_frame().url` 与目标模块，不一致返回 `ok:false` + 实际 URL（修复 F1）。
2. **get_column_values 健壮性**：图标列返回 `unreadable` 标注而非空数组；自动剔除尾部空行并返回 valid_count（修复 F2/F3）。
3. **scan_vtable_columns 截断**：返回真实列数 count，对超界索引不填充空占位（本次 max_col=60 返回 60 列，实际仅 25 列有效，浪费且易误判）。
4. **click text: 定位符**：文本规范化（trim/全角半角/几何字符归一），或支持精确匹配 `text=`（修复 F4）。
5. **listen 文档与通配**：明确"条件未变不重发请求"；支持 targets 通配捕获所有请求；提供"改变条件触发请求"标准流程（修复 F5/F6）。
6. **classifier 不可用降级**：MCP 写工具返回结构化错误码，skill 据此自动降级到只读探索 + 标注待补（修复 F7）。
7. **纯文本模型工作流**：SKILL.md 增加"无图片识别"分支——用 `dom_overview` + `scan_page_elements` + `run_js` 替代 screenshot 视觉识别（本次已实践可行）。
8. **接口断言预期**：skill 应在 classifier/网络受限时，允许用 `get_column_values` 列值断言 + `get_active_frame` URL 断言 + `detect_modal` 弹窗断言替代接口 body 断言，并标注"接口验证待补"。

---

## 6. 待补项（classifier 恢复后执行）
- [ ] 工具栏 5 按钮（新增/导出/物料查询/批量备料/流程设置）实际点击 + detect_modal
- [ ] VTable 表头复选框全选、排序(sort_normal)、筛选(filter-icon)点击验证
- [ ] popup-candidate 列单元格点击跳转/弹窗验证（制令单单号/制造排产状态/领料进度等）
- [ ] 接口 body 断言：input 填筛选值 → 查询 → listen_wait 拿 response.body
