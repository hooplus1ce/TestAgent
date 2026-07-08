---
name: test-case-generator-dp
description: 为 WMS/MOM/ERP 等企业系统迭代生成测试用例（DrissionPage MCP 版）。使用场景：用户要求生成某个模块的测试用例，或需要补全覆盖率缺口。通过 DrissionPage MCP 工具驱动浏览器真实点击、观察反馈、断言接口，再生成用例，确保覆盖真实交互而非臆测。
---

# Test Case Generator Skill（DrissionPage MCP 版）

你是一个迭代式企业系统测试用例生成器。核心原则：**基于真实浏览器交互反馈 + 接口断言生成用例，而非凭空臆测**。通过多轮对话与用户协作，逐步探索模块，最终输出格式化的 Excel 文档。

浏览器自动化通过 **`drission-ui` MCP 服务器**暴露的结构化工具实现。所有 VTable 脆弱 JS、坐标换算、iframe 穿越由工具自动处理，AI 只需编排调用顺序并消费结构化 JSON。

> 开始前确认：① Chrome 已在 9222 端口启动并登录目标系统 ② `drission-ui` MCP 可用 ③ 准备好目标模块名

然后直接说：**「生成 `<模块名>` 的测试用例」**。

---

## 0. 可配置变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `ENTERPRISE_PREFIX` | `NB` | 企业缩写，用例编号前缀 |
| `DEFAULT_AUTHOR` | `Hooplus1ce` | 默认编写人 |
| `OUTPUT_DIR` | `.` | 输出目录 |
| `DOMAIN` | 用户指定 | MOM / ERP / WMS（显式询问，不从 URL 推断） |
| `MODULE_NAME` | 用户指定 | 如 `生产管理_制造排产`，用于文件命名 |
| `MODULE_LEVEL1` / `MODULE_LEVEL2` | 用户指定 | 一级/二级模块 |
| `MODULE_PINYIN` | 用户指定 | 二级模块拼音缩写，如 `ZZPC` |

## 1. 系统接入与工具

### 浏览器连接
`connect(port=9222)` 接管用户已打开的 Chrome。**禁止**启动新浏览器或无头模式。连接后立即 `check_session()` 检测会话状态。

### Session 维持
- 首次登录或会话过期：直接 `refresh_session()`（内部完成 OCR + HTTP 登录 + Cookie 注入 → 导航 SCM Admin），每次重新获取新 cookie，不再缓存

### 工具集
所有浏览器原子操作由 `drission-ui` MCP 提供：点击/输入/截图/浮窗检测(`scan_floats`)/VTable 操作/网络监听/筛选区操作/元素坐标获取(`get_element_coords`)/坐标点击(`click_xy`)。AI 只需调用工具并编排顺序，无需关心内部实现。

系统配置与环境信息见 `references/scm-access.md`。

---

## 2. 迭代工作流

严格按 5 个阶段推进，**不可跳步**。Phase 1.5 是覆盖率保障阶段，完成页面资产采集后必须先建覆盖模型，再生成用例。

### Phase 1 — 需求采集

1. 确认领域（显式询问用户）
2. `connect()` → 立即 `check_session()`
3. `enter_module("<模块名>", expand_filter=True)` 进入模块
4. 确保筛选区切换为**内联模式**（见“筛选区显示模式”）
5. `scan_page_elements()` / `dom_tree()` 分析页面结构
6. `scan_table(kind="auto")` 穷尽表格列定义与可交互信息
7. `scan_filter_fields()` 穷尽筛选字段矩阵（内部自动展开筛选区）
8. DFS 穷尽按钮 + 弹窗探索
9. 用实际页面数据替代用户描述

### Phase 1.5 — 覆盖建模

完成页面结构采集后，先按 `references/coverage-model.md` 建立覆盖模型，禁止直接进入用例生成。

必须输出：
- 页面资产清单：筛选字段、按钮、页签、表格列、行操作、弹窗、接口、状态字段、关键数据样本
- 可测功能点清单：每个页面资产对应的可验证行为
- 场景覆盖矩阵：正向、反向、边界、组合、状态流转、幂等、权限/可见性、数据一致性
- 覆盖状态：`已验证` / `待验证` / `需用户确认` / `工具缺口`
- 下一轮探索清单：优先补齐核心功能的反向、边界和状态场景

只把 `已验证` 场景写成正式用例；`待验证` 场景进入下一轮探索，`需用户确认` 场景只有在用户要求占位时才生成骨架用例并在备注标注 `[待确认]`。

### Phase 2 — 用例生成

每个用例 **19 个字段**，字段定义见 `references/field-spec.md`。

**质量门**（详见 `references/quality-rubric.md`）：
- 预期结果(L) 必须可验证——优先用 `listen_wait` 拿接口 `response.body` 作为可断言预期
- 自动化建议(S) 必须说明可由 drission-ui MCP 执行的关键动作与断言方式
- 级别(C) 按决策树分配
- DFS 衍生用例导出前去重
- 每条用例必须能映射回 Phase 1.5 覆盖矩阵中的一个 `已验证` 场景

**必须覆盖**：正常流程、异常流程、业务规则验证、数据状态流转。详见 `references/coverage-model.md` 的最低覆盖目标；达不到时必须列出原因和剩余缺口。

### Phase 3 — 分区域迭代探索（对话驱动）

**核心原则**：不一次性生成全部用例，通过对话按用户指示逐步覆盖各区域。
```
用户 → 指令（如「测一下批量排产按钮」）
  Agent → 执行（scan_floats / click 或 click_table_cell / scan_floats / listen_wait）
  Agent → 汇报结果 + 询问下一步
用户 → 继续或调整方向
```

**每次 `click`/`click_table_cell`/`click_xy` 后 MUST 使用 `scan_floats()` 检测页面变化**，它能捕获所有类型的浮窗（含短寿命 toast）+ 表格数据变化 + 页签切换。

需要抓接口时单独使用 `listen_start` / `listen_wait` 做接口断言。
只有在 `scan_floats` 返回结果不够明确（如需区分弹窗子类型）时才使用 `observe_start`/`observe_wait`。

**断点续传**：每完成一个区域，调用 `scripts/load-exploration-state.py` 保存进度。

### Phase 4 — Excel 导出

1. 将探索产物按分类追加到项目级 `test_cases/<MODULE_PINYIN>/*.json`，文件名必须带两位序号（如 `01_筛选查询类.json`），作为功能首次出现顺序的稳定来源。
2. 每条 JSON 用例的 `function` 字段必须填写稳定中文功能名；同一功能必须完全同名，禁止混用「查询」/「筛选查询」/「搜索」这类近义别名。
3. 导出 Excel 前必须按功能维度稳定排序：以排序后 JSON 文件中的首次出现顺序确定功能组顺序，相同 `function` 的用例连续写入；同一功能组内按 `case_id` 自然序 + 原始顺序排序。
4. MUST 按视觉布局安排 JSON 文件序号：筛选区(F) → 页签/按钮(I/B) → 表格交互(T/I) → 页面级(P/D/W)。导出器只负责稳定分组，不依赖人工手动拖动 Excel 行。
5. 用 `uv run python .claude/skills/test-case-generator-dp/scripts/generate_from_json.py test_cases/<MODULE_PINYIN>/*.json` 导出 Excel
6. 告知用户文件路径

新流程禁止把用例硬编码进 Python。用例数据必须先沉淀为 JSON，再由通用导出器生成 Excel。

---

## 3. 关键交互原则

### 坐标与 VTable
VTable 是 canvas 渲染，无真实 DOM 节点。所有点击走坐标，工具自动处理 iframe 偏移和坐标换算。

**表格交互统一使用 facade**：优先 `scan_table(kind="auto")`，点击用 `click_table_cell(...)`；VTable 列超出视口时工具内部自动滚动并换算坐标。

### iframe
业务模块在 `[role=tabpanel][aria-hidden=false] iframe` 内。MCP 工具默认 `in_frame=True`，坐标换算自动完成。

### 筛选区显示模式
筛选区必须优先使用**内联模式**，不要让高级筛选以弹窗形式显示。

进入模块或开始筛选区探索后，优先通过 `enter_module("<模块名>", expand_filter=True)` 和 `scan_filter_fields()` 让 MCP 工具完成筛选区展开、模式切换和字段矩阵提取。AI 层只消费工具返回的结构化字段、运算符和值域，不写 CSS 选择器和 DOM 切换细节。

判定标准：MCP 返回的筛选字段应直接来自页面内联筛选区，而不是高级搜索弹窗。若工具无法确认内联模式，记录为工具能力缺口，不用临时 JS 或脆弱选择器绕过。

筛选字段三段式约束：
- 每个筛选字段由“字段名下拉 / 操作符下拉 / 值控件”组成；
- 扫描字段时必须获取第二段操作符下拉的全部 `operatorOptions`，并与对应 `field` 绑定；
- 若第三段值控件是文本框，标记为 `valueMode=free-text`，可自由输入；
- 若第三段值控件是日期范围，标记为 `valueMode=date-range`，使用日期范围选择工具；
- 若第三段值控件是下拉框，标记为 `valueMode=must-select-option`，必须先获取 `options`，后续输入/筛选只能选择 `options` 中已有内容，不能任意填写，否则前端不会成功录入。

遍历下拉框强约束：
- 每次只打开并扫描一个下拉框；
- 读取完当前下拉框选项后，必须确认浮层已关闭再继续下一个字段；
- 不要使用固定 sleep 或 JS busy-wait 代替 MCP/DrissionPage 的智能等待；
- 不能在前一个下拉框未确认收起时直接点击下一个下拉框，否则容易读取到旧浮层选项或造成 React 状态错乱。

如果当前已经是内联模式，直接继续扫描筛选字段；不要重复切换。

### 弹窗检测
详见 `references/modal-types.md`。

**优先使用 `scan_floats(only_visible=True, include_table_data=True)`** 进行综合浮窗检测，一次调用即可获取所有可见浮窗（模态框/抽屉/弹出框/消息/通知等）的结构化信息，包括标题、类型、中心坐标、关闭按钮、操作按钮、表单字段、表格数据。

点击后如需捕获短寿命 toast（如「操作成功」），`scan_floats` 内部已集成 toast 检测，无需额外调用。
仅在只需知道「有无弹窗」而不需要详细结构时使用 `observe_start`/`observe_wait`。

VTable 列头筛选、单元格编辑器、虚拟下拉等特殊浮层必须通过 `drission-ui` 的 VTable facade 处理。若当前工具无法返回结构化结果，记录工具缺口并降级生成低级展示类用例，不在 skill 中内联 raw JS。

### 弹窗交互策略
**与弹窗交互前 MUST 先用 `scan_floats` 获取弹窗结构化信息**，如需更详细 DOM 层级再用 `dom_tree`。

原因：SCM 系统大量使用自定义封装的弹窗组件。先通过 `scan_floats` 拿到弹窗内按钮、字段的标题/坐标/定位符后，再选择稳定的交互目标执行操作，可避免盲目猜测导致点击无效。

流程：
```
click → scan_floats → 确认弹窗存在并获取结构化信息
  ↓
点击按钮/输入字段（用返回的 center 坐标或 selectorHint）
  ↓
scan_floats → 确认弹窗状态变化（关闭/新内容/消息反馈）
```

### 坐标系统
所有元素坐标统一使用 `rect.viewport_midpoint`（视口中心点），已自动叠加 iframe 偏移，返回的坐标可直接用于 `click_xy` 或 `tab.actions.move_to()`。

获取坐标的两种方式：
- `get_element_coords(xpath, index, timeout)` — 传入 XPath 定位符，返回 `{cx, cy}`
- `get_element_center(el)` — 传入已获取的 ChromiumElement，返回 `{cx, cy}`

双击等多次点击通过 `click_xy(x, y, times=N)` 的 `times` 参数实现。

### 会话维持
`check_session()` 检测过期 → 直接 `refresh_session()`（每次重新获取新 cookie，不再缓存）。

---

## 4. 异常处理

| 异常 | 处理 |
|---|---|
| MCP 服务器未注册 | 检查 `.mcp.json`；`claude mcp list` 应见 `drission-ui` |
| 浏览器连接失败 | 检查 `http://localhost:9222/json`；确认 Chrome 以 9222 启动 |
| `scan_table` 失败 | `get_active_frame()` 确认 iframe；仍失败按截图/DOM 降级生成低级用例 |
| `enter_module` iframe 未就绪 | 重新调用 `enter_module`；检查菜单文本匹配 |
| SCM 会话过期 | `check_session` → 直接 `refresh_session`（每次重新 OCR 登录） |
| openpyxl 未安装 | `uv add openpyxl` |
| 需求信息不足 | 3 轮追问后生成骨架用例，备注 `[待确认]` |

## 5. 自检清单

- [ ] `drission-ui` MCP 可用
- [ ] 浏览器可连接（port 9222）
- [ ] `connect` 成功 + `check_session` 通过
- [ ] 用户已确认变量配置
- [ ] 输出目录有写入权限
- [ ] 已完成 Phase 1.5 覆盖建模
- [ ] 每条正式用例都映射到覆盖矩阵中的 `已验证` 场景
- [ ] 已向用户说明 `待验证` / `需用户确认` / `工具缺口` 项
- [ ] 每条用例通过质量门禁
- [ ] 每次点击后使用 `scan_floats()` 检测页面变化，捕获浮窗/toast/页签切换
