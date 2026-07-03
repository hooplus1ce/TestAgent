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
- 首次登录或会话过期：直接 `login_ocr()`（OCR + HTTP 登录 + 注入 → 导航 SCM Admin）
- `refresh_session()` 等同于 `login_ocr()`，每次重新获取新 cookie，不再缓存

### 工具集
所有浏览器原子操作由 `drission-ui` MCP 提供：点击/输入/截图/弹窗检测/VTable 操作/网络监听/筛选区操作等。AI 只需调用工具并编排顺序，无需关心内部实现。

系统配置与环境信息见 `references/scm-access.md`。

---

## 2. 迭代工作流

严格按 4 阶段推进，**不可跳步**。

### Phase 1 — 需求采集

1. 确认领域（显式询问用户）
2. `connect()` → 立即 `check_session()`
3. `enter_module("<模块名>", expand_filter=True)` 进入模块
4. 确保筛选区切换为**内联模式**（见“筛选区显示模式”）
5. `dom_overview()` DOM 俯瞰，分析页面结构
6. `mount_vtable()` → `scan_vtable_columns()` 穷尽列定义与表头图标
7. `scan_filter_fields()` 穷尽筛选字段矩阵
8. DFS 穷尽按钮 + 弹窗探索
9. 用实际页面数据替代用户描述

### Phase 2 — 用例生成

每个用例 **19 个字段**，字段定义见 `references/field-spec.md`。

**质量门**（详见 `references/quality-rubric.md`）：
- 预期结果(L) 必须可验证——优先用 `listen_wait` 拿接口 `response.body` 作为可断言预期
- 自动化建议(S) 必须说明可由 drission-ui MCP 执行的关键动作与断言方式
- 级别(C) 按决策树分配
- DFS 衍生用例导出前去重

**必须覆盖**：正常流程、异常流程、业务规则验证、数据状态流转。

### Phase 3 — 分区域迭代探索（对话驱动）

**核心原则**：不一次性生成全部用例，通过对话按用户指示逐步覆盖各区域。

```
用户 → 指令（如「测一下批量排产按钮」）
  Agent → 执行（click / detect_modal / scan / listen_wait）
  Agent → 汇报结果 + 询问下一步
用户 → 继续或调整方向
```

**每次 `click`/`click_cell`/`click_xy` 后 MUST 立即 `detect_modal()`**。

点击后三步排查（VTable 单元格交互后结果不一定是弹窗）：
```
① detect_modal()          → 弹窗类型
② dom_overview()          → 新 Tab 出现？
③ get_active_frame()      → iframe URL 变了？
// 都没变 → 同 iframe 内容变更
```

**断点续传**：每完成一个区域，调用 `scripts/load-exploration-state.py` 保存进度。

### Phase 4 — Excel 导出

1. 按 `scripts/excel-export-template.py` 模板组装数据
2. MUST 按视觉布局排序：筛选区(F) → 页签/按钮(I) → 表格交互(I) → 页面级(P)
3. 用 `bash` 执行 Python 文件脚本导出
4. 告知用户文件路径

---

## 3. 关键交互原则

### 坐标与 VTable
VTable 是 canvas 渲染，无真实 DOM 节点。所有点击走坐标，工具自动处理 iframe 偏移和坐标换算。

**列超出视口时先 scroll 再点击**：`scroll_to_cell(col, row)` → `click_cell(col, row)`。

### iframe
业务模块在 `[role=tabpanel][aria-hidden=false] iframe` 内。MCP 工具默认 `in_frame=True`，坐标换算自动完成。

### 筛选区显示模式
筛选区必须优先使用**内联模式**，不要让高级筛选以弹窗形式显示。

进入模块或开始筛选区探索后，若页面存在筛选区模式切换按钮（`.legions-pro-quick-filter-actions button:nth-last-child(2)`，图标通常为 `anticon-bars`），按以下流程切换并验证：

```
dom_tree(selector=".page-query")
  ↓
click(selector=".legions-pro-quick-filter-actions button:nth-last-child(2)")  // 打开“内联模式/弹窗模式”菜单
  ↓
dom_tree(selector=".ant-dropdown:not(.ant-dropdown-hidden)")  // 确认菜单结构
  ↓
click(text="内联模式")
  ↓
dom_tree(selector=".page-query")  // 验证出现 .legions-pro-quick-filter-remaining 且按钮变为“收起▲”
```

判定标准：
- `.ant-dropdown` 菜单关闭或隐藏；
- `.page-query` 内出现 `.legions-pro-quick-filter-remaining`；
- 原 `展开▼` 按钮变为 `收起▲`；
- 高级筛选字段直接显示在页面内，而不是通过 `.ant-modal` 的“高级搜索”弹窗显示。

筛选字段三段式约束：
- 每个筛选字段由“字段名下拉 / 操作符下拉 / 值控件”组成；
- 扫描字段时必须获取第二段操作符下拉的全部 `operatorOptions`，并与对应 `field` 绑定；
- 若第三段值控件是文本框，标记为 `valueMode=free-text`，可自由输入；
- 若第三段值控件是日期范围，标记为 `valueMode=date-range`，使用日期范围选择工具；
- 若第三段值控件是下拉框，标记为 `valueMode=must-select-option`，必须先获取 `options`，后续输入/筛选只能选择 `options` 中已有内容，不能任意填写，否则前端不会成功录入。

遍历下拉框强约束：
- 每次打开一个下拉框前，先确认页面不存在可见的 `.ant-select-dropdown` / `.ant-dropdown` 浮层；
- 打开下拉框后，优先使用 DrissionPage 智能等待：`wait.ele_displayed()` 等待浮层和菜单项出现；
- 读取完当前下拉框选项后，通过 body click / Escape 等方式关闭；
- 关闭后使用 `wait.ele_hidden()` 确认所有可见下拉浮层均已隐藏，再继续打开下一个下拉框；
- 不要使用固定 sleep 或 JS busy-wait 代替 DrissionPage 智能等待；
- 不能在前一个下拉框未确认收起时直接点击下一个下拉框，否则容易读取到旧浮层选项或造成 React 状态错乱。

如果当前已经是内联模式，直接继续扫描筛选字段；不要重复切换。

### 弹窗检测
详见 `references/modal-types.md`。每次点击后 `detect_modal()`，三级优先级：
iframe 交互弹窗 → top 层系统确认 → 无弹窗正常继续。

⚠️ VTable 筛选弹窗（`.vtable-filter-menu`）非 ant-design 组件，`detect_modal` 不检测。点击列头筛选图标后需用 `run_js` 补充探测。

### 弹窗交互策略
**与弹窗交互前 MUST 先用 `dom_tree` 获取弹窗完整 DOM 结构**，无论是要点击按钮、输入文本还是关闭弹窗。

原因：SCM 系统大量使用自定义封装的弹窗组件（如带 `modal-minimize-btn`/`modal-maximize-btn` 的高级搜索弹窗），React 合成事件与 DrissionPage 原生模拟点击之间存在兼容性差异。先通过 `dom_tree(selector=".ant-modal")` 拿到弹窗内部精确的按钮层级、class 和文本后，再用精确选择器定位交互，可避免盲目猜测导致点击无效。

流程：
```
detect_modal() → 确认弹窗存在
  ↓
dom_tree(selector=".ant-modal", max_depth=8)  → 获取弹窗完整结构
  ↓
分析 DOM → 确定精确的交互目标选择器（如 .ant-modal-footer button、css:.ant-modal-close-x）
  ↓
click(input/...) → 执行交互
  ↓
detect_modal() → 确认弹窗状态变化
```

### 会话维持
`check_session()` 检测过期 → 直接 `refresh_session()` 或 `login_ocr()`（每次重新获取新 cookie，不再缓存）。

---

## 4. 异常处理

| 异常 | 处理 |
|---|---|
| MCP 服务器未注册 | 检查 `.mcp.json`；`claude mcp list` 应见 `drission-ui` |
| 浏览器连接失败 | 检查 `http://localhost:9222/json`；确认 Chrome 以 9222 启动 |
| `mount_vtable` 失败 | `get_active_frame()` 确认 iframe；仍失败按截图降级生成低级用例 |
| `enter_module` iframe 未就绪 | 重试 `reset_to_initial`；检查菜单文本匹配 |
| SCM 会话过期 | `check_session` → 直接 `refresh_session` 或 `login_ocr`（每次重新 OCR 登录） |
| openpyxl 未安装 | `uv add openpyxl` |
| 需求信息不足 | 3 轮追问后生成骨架用例，备注 `[待确认]` |

## 5. 自检清单

- [ ] `drission-ui` MCP 可用
- [ ] 浏览器可连接（port 9222）
- [ ] `connect` 成功 + `check_session` 通过
- [ ] 用户已确认变量配置
- [ ] 输出目录有写入权限
- [ ] 每条用例通过质量门禁
- [ ] 每次点击后都调用了 `detect_modal`
