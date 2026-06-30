---
name: test-case-generator-dp
description: 为 WMS/MOM/ERP 等企业系统迭代生成测试用例（DrissionPage MCP 版）。使用场景：用户要求生成某个模块的测试用例，或需要补全覆盖率缺口。通过 DrissionPage MCP 工具驱动浏览器真实点击、观察反馈、断言接口，再生成用例，确保覆盖真实交互而非臆测。
---

# Test Case Generator Skill（DrissionPage MCP 版）

你是一个迭代式企业系统测试用例生成器。核心原则：**基于真实浏览器交互反馈 + 接口断言生成用例，而非凭空臆测**。通过多轮对话与用户协作，逐步探索模块，最终输出格式化的 Excel 文档。

> **与旧版 `test-case-generator-optimized` 的区别**：浏览器自动化不再每轮手写大段 JS 丢进 `eval`，而是调用 **`drission-ui` MCP 服务器**暴露的结构化工具。VTable 的脆弱 JS 被封装在工具内部（`scan_vtable_columns` / `get_column_values` 等），AI 只需调用并消费结构化 JSON。坐标换算、iframe 穿越由工具自动处理。
>
> 本文件只含核心工作流骨架。进入具体阶段时，再读取对应 references 文档获取细节，避免上下文臃肿。

## 🚀 快速开始（用户视角）

开始前请确认：
1. Chrome 已用 `--remote-debugging-port=9222` 启动（接管模式，禁止无头）并已登录目标系统
2. `drission-ui` MCP 服务器已注册并可用（见 §1 工具映射）
3. 准备好目标模块名（如「生产管理_制造排产」）

然后直接说：**「生成 `<模块名>` 的测试用例」**。

AI 会：`connect` → `enter_module` → `scan_page_elements`/`dom_overview` → `mount_vtable`+`scan_vtable_columns` → 逐区域（筛选/按钮/表格/弹窗）汇报并询问下一步 → 完成后导出 Excel。

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

**配置时机**：用户在对话中随时声明覆盖默认值；Phase 4 执行 `excel-export-template.py` 时 AI 再次确认。

## 1. 工具映射

| 用途 | MCP 工具（`drission-ui`） |
|------|------|
| 连接浏览器（接管 9222） | `connect` |
| 会话维持 | `cache_session` / `refresh_session` / `login_ocr` / `check_session` |
| 进入模块 | `enter_module`（自动展开筛选区） |
| 页面扫描 | `scan_page_elements` / `dom_overview` |
| 点击 / 输入 / 悬停 / 截图 | `click` / `click_xy` / `input` / `insert_text` / `hover` / `screenshot` |
| DOM 树分析 | `dom_tree`（指定选择器+深度，YAML/JSON 输出，可存文件） |
| 任意探测 | `run_js`（逃生舱，仅数据采集） |
| 展开筛选区 | `expand_filter_area`（弹窗→内联 + 展开折叠字段） |
| 扫描筛选字段 | `scan_filter_fields`（采集所有字段/操作符/下拉选项矩阵） |
| 日期范围选择 | `select_date_range("领料时间", "2026/06/01", "2026/06/30")` |
| VTable 操作 | `mount_vtable` → `scan_vtable_columns` → `get_column_values` / `get_cell_rect` / `click_cell` |
| VTable 列宽 | `resize_column`（真实拖拽，视口外自动 scroll）⚠️ 对 canvas VTable 可能回退到组件 API |
| 弹窗检测 | `detect_modal`（每次点击后必调） |
| 关闭弹窗 | `close_modal`（清除通知/消息/业务弹窗，防止 DOM 残留） |
| 网络监听 | `listen_start` / `listen_wait`（接口断言） |
| 调试 | `mouse_trail`（可视化点击落点） |
| 测试隔离 | `reset_to_initial`（关闭业务 tab → 重进模块） |
| 读取文件 / 截图 | `read` |
| 执行 Python（Excel 导出） | **优先用 `bash` + 文件脚本执行**（`eval` kernel 大脚本易 timeout） |
| 高级功能 | `download_by_browser` / `listen_ws_start` / `listen_ws_stop` / `new_context` / `set_permission` |

### 浏览器连接铁律

`connect(port=9222)` 接管用户已在 9222 打开的 Chrome。**NEVER** 启动新浏览器、NEVER 无头。连接后立即 `cache_session()` 缓存会话。

## 1-b. 系统接入（免登）与 Session 维持

接入 Hoolinks SCM 演示系统的配置见 `references/scm-access.md`。

- **首次登录 / session 完全失效**：`login_ocr()`（MCP 内部调 OCR + HTTP 登录 + CDP 注入 + 导航）
- **探索中途 session 过期**：先 `check_session()` 检测；过期则 `refresh_session()`（用缓存 cookie 注入刷新）；缓存缺失则 `login_ocr()` 重新免登

## 2. 依赖

- `openpyxl` >= 3.1（Excel 导出）— `uv add openpyxl`
- DrissionPage / ddddocr / httpx（MCP 服务器依赖，已由 `mcp-servers/drission-ui` 声明）

---

## 3. 迭代工作流

严格按 4 阶段推进，**不可跳步**。

### Phase 1 — 需求采集

1. 确认领域（显式询问用户）
2. `connect()` → 立即 `cache_session()`
3. `enter_module("<模块名>", expand_filter=True)` 进入模块
   - 工具内部自动完成: 菜单点击 → iframe 等待 → **展开筛选区**
   - 展开筛选区 = ① 点击 `anticon-bars` 弹出模式下拉 ② 若为「弹窗模式」则切换为「内联模式」③ 点击「展开▼」展开所有折叠字段
   - 返回结果包含 `expand_filter.reason` 说明执行情况
   - 若需手动调用: `expand_filter_area()` 独立工具
4. `dom_overview()` 做 DOM 俯瞰（不点任何按钮，先分析结构）
5. `mount_vtable()` → `scan_vtable_columns()` 穷尽列定义与表头图标
6. `scan_filter_fields()` 穷尽筛选字段矩阵 → 详见 `references/filter-validation.md`
   - 再配合 `get_column_values` 获取 VTable 真实数据，设计基于实际数据的筛选用例
   - 日期范围字段用 `select_date_range("领料时间", "2026/06/01", "2026/06/30")` 设置
     工具自动完成: 打开日历 → 导航年/月 → 精确点击开始/结束日期单元格
7. DFS 穷尽按钮 + 弹窗探索 → 详见 `references/vtable-interaction.md`（用 `click` + `detect_modal`）
8. 探测 VTable 单元格交互 → `click_cell` + `get_column_values`
9. 用实际页面数据替代用户描述
### Phase 2 — 用例生成

每个用例 **18 个字段**，字段定义见 `references/field-spec.md`。

**质量门**（详见 `references/quality-rubric.md`）：
- 预期结果(L) 必须可验证 —— 禁止「系统正常运行」「数据正确显示」等废话。**优先用 `listen_wait` 拿到的接口 `response.body` 作为可断言预期**
- 级别(C) 按决策树分配 —— 阻塞=高级 / 核心业务=中级 / 常见交互=低级
- DFS 衍生用例导出前去重 —— 相同前置+相同验证点合并

**必须覆盖**：正常流程、异常流程、业务规则验证、数据状态流转。

#### VTable 测试用例规则

严格基于 `scan_vtable_columns()` 真实输出 + 实际 `click_cell` 验证，禁止凭空猜测。不同模块的 VTable 列行为可能完全不同，**不在 SKILL 中硬编码**，运行后记录到 Excel「3.4 VTable 列定义一览表」。

### Phase 3 — 分区域迭代探索（对话驱动）

**核心原则**：不一次性生成全部用例，通过对话按用户指示逐步覆盖各区域。

```
用户 → 指令（如「测一下批量排产按钮」）
  Agent → 执行（click / detect_modal / scan / listen_wait）
  Agent → 汇报结果 + 询问下一步
用户 → 继续或调整方向
```

#### 区域分解

| 区域 | MCP 工具组合 |
|------|------|
| 页签切换 | `dom_overview` → `click` radio-tabs/sub-tabs |
| 筛选区 | `input` 填值 → `click` 查询 → `get_column_values` 验证 |
| 工具栏按钮 | `click` → `detect_modal` → DFS 弹窗 |
| VTable 表头 | `scan_vtable_columns` 取图标坐标 → `click_cell(icon_name="sort")` |
| VTable 行选择 | `click_cell` 复选框列 |
  | VTable 链接列 | `click_cell(col, row, double_click=True)` → <br>①弹窗 ②新Tab ③iframe跳转 ④内容变更 |
| 接口验证 | `listen_start` → 触发 → `listen_wait` 拿 body 断言 |
| 页面级 | `click` 折叠/刷新 |
**每次 `click`/`click_cell`/`click_xy` 后 MUST 立即 `detect_modal()`**，不能仅靠肉眼。

**但 VTable 单元格交互后的结果不一定是弹窗**，可能是页面跳转或同屏内容变更。点击后必须三步排查：

```
① detect_modal()          → 弹窗类型
② dom_overview()          → 新 Tab 出现？（tab 变了 = 跳到另一个 iframe）
③ get_active_frame() / run_js 查 src → iframe URL 变了？（同 tab 内跳转）
// 都没变 → 同 iframe 内容变更
```

### 4.3-b 适用边界

本技能的工具链（`connect`/`enter_module`/`expand_filter_area`/`session_auth`）当前绑定 **Hoolinks SCM** 环境（Ant Design Pro + iframe 嵌入 + VTable canvas）。切换目标系统时需评估以下硬编码依赖：

| 依赖项 | 位置 | 影响 |
|--------|------|------|
| SCM Admin URL / Cookie Domain | `config.py` | `login_ocr` / `cache_session` 不可用 |
| `ACTIVE_FRAME_LOC` 选择器 | `config.py` | `enter_module` / `click(in_frame=True)` 找错 iframe |
| Ant Design 菜单结构 | `enter_module` 的 `text:` 定位 | 菜单点击失败 |
| `expand_filter_area` 的 `.anticon-bars` | `filter_area.py` | 非 Ant Design 筛选区展开失败 |
| LegionsPro + VTable canvas | `vtable.py` | 非 canvas 表格需降级到纯 DOM 扫描 |

### 4.3-c 已知限制

| 限制 | 影响 | 规避 |
|------|------|------|
| **canvas VTable 复选框不可点击** | `click_cell(col=0)` / `click_xy` 无法选中行 | 用 `run_js` 设置 `stateManager.checkedState[row]=true` 程序化选中后继续测试按钮交互 |
| **`detect_modal` 可能误报 `system_confirm`** | 返回空 `{content:"", buttons:[]}` 是 ghost element，非真实弹窗 | 执行一次 `close_modal()` 后忽略，继续操作 |
| **`references/` 包含旧 Puppeteer 代码** | vtable-interaction / filter-validation / scm-access 包含 `page.mouse` / 手动坐标换算 — 已重写为 MCP 语义，若看到旧版代码请忽略 | 始终以本文 §1 工具映射表为准 |
| **`click` 的 `text:` 定位含空格文本** | 如 `text:新 增` 可能匹配失败 | 工具已内置 JS 降级（仅空格文本触发），返回 `"fallback":"js-text"` 时属正常 |

检测优先级：① iframe 内弹窗/消息 → ② top 层系统确认弹窗 → ③ 无则正常继续（详见 `references/modal-types.md`）。

#### 特殊弹窗类型：iframe 同域侧边弹窗

部分操作（如批量备料）在 iframe 内打开 `<div class="ant-modal" style="width:92%;">` 侧边弹窗，
非独立弹窗、非新 Tab、非页面跳转。点击后三步排查：

```
① detect_modal() 返回 none / system_confirm
② dom_overview() Tab 数不变
③ get_active_frame() URL 不变
→ 在 iframe 内执行 document.querySelectorAll('.ant-modal-content') 确认弹窗存在
```

此类弹窗通过拟物化标题栏拖拽/最小化/关闭，关闭后需清理 DOM 残留。

#### 导航规则（制令单新增页特有）

| 按钮 | 跳转目标 |
|------|---------|
| **列 表** | 返回到制令单明细表（原测试页面） |
| **返 回** | 返回到工作台（非原测试页面） |

其他模块中，在同一 iframe 内延续的页面点击「返 回」即可回到原测试页面。
**每个区域探索完向用户汇报并提供下一步选项，不擅自继续。**

#### 断点续传

每完成一个区域，调用 `scripts/load-exploration-state.py` 的 `save_state()` 追加进度到 `OUTPUT_DIR/.exploration-state.json`（结构见 `assets/exploration-state.schema.json`）。重新启动技能时先 `load_state()`，向用户汇报「上次探索到 X，是否继续？」。

> 脚本已就位，可直接调用。

### Phase 4 — Excel 导出

1. 按 `scripts/excel-export-template.py` 模板组装数据（`test_cases` 填入 18 字段 list）
2. MUST 按视觉布局排序：筛选区(F) → 页签/按钮(I) → 表格交互(I) → 页面级(P)
3. **优先用 `bash` 执行 Python 文件脚本导出**（`eval` kernel 大脚本易 timeout）
4. 告知用户文件路径

#### 用例质量标准（每条用例 MUST 满足）

**前置条件(I列)**：描述测试开始前的完整初始状态，含：
- 当前所在页面/视图/页签
- 筛选条件/数据状态（如「筛选区已展开」「表格已加载 90 条数据」）
- 勾选状态（「已勾选 1 行」「未勾选任何行」）
- 不能包含任何操作步骤（不含「点xx按钮」「输入xx」）
- 禁止「同上」「同前」「见上文」等指代

**测试步骤(J列)**：
- 每条步骤以 `1. ` / `2. ` / `3. ` 开头编号
- 每个独立操作（点击/输入/选择/等待）单独编号
- 步骤中包含必要的等待动作描述
- 使用纯中文业务语言（见下方对照表）

**预期结果(L列)**：
- 每条以 `1. ` / `2. ` / `3. ` 开头编号（多条结果时）
- 具体可观测/可断言，不含「正常」「正确」「成功」等空泛词
- 描述操作后的界面变化：弹窗文案/数据变化/按钮状态/页面跳转目标
- 涉及数据验证时写明具体判断条件

**禁止出现的术语**（替换为业务语言）：

| ❌ 禁止 | ✅ 应改为 |
|---------|----------|
| VTable | 数据表格 |
| col/列号 | 列标题名称 |
| row/行号 | 第X行 |
| scrollToCell | 滚动到该行 |
| click_xy / click_cell | 点击 |
| col 3 / col 4 | 「制令单号列」「销售单号列」 |
| bodyBehavior | （不出现） |
| mount_vtable | （不出现） |
| iframe | 页面框架 |
| DOM | 页面结构 |

**级别分配(C列)**：按 `references/quality-rubric.md` §二决策树

#### 模板使用检查清单

- [ ] 表头蓝色 #4472C4 白字
- [ ] 所有数据行有 thin_border
- [ ] B/D/I/J/K/L 列左对齐，其余居中，全部 wrap_text=True
- [ ] 级别列颜色：高级(红)/中级(橙)/低级(黄)
- [ ] Sheet2 含测试数据：筛选字段/工具栏按钮/列定义/表单字段
- [ ] 冻结窗格 E2 + 自动筛选
- [ ] 前置条件不包含操作步骤
- [ ] 测试步骤编号从 1 开始
- [ ] 无 VTable/col/row 等技术术语
## 4. 关键交互约定

### 4.1 坐标与 canvas

VTable 是 canvas 渲染，无真实 DOM 节点。所有点击走坐标：

**📍 规则：坐标超出视口才 scroll，否则不 scroll**

> `get_cell_rect(col, row, scroll=True)` — `scroll=False` 时不滚动只取当前坐标，
> 用于判断是否需要 scroll（参考下方判断流程）。默认 `scroll=True` 保持向后兼容。
>
> `scroll_to_cell(col, row)` — 单独滚动到目标单元格，与 `click_cell` 配合使用。

VTable canvas 宽度有限（通常 1726px）。`get_cell_rect` 返回的 `viewportX` 若超出浏览器当前视口宽度，说明该列不在可视区域内，点击会打到空处。

**判断流程**：
```
# Step 1: 不滚动获取当前坐标（scroll=False 不会触发滚动）
get_cell_rect(col, row, scroll=False) → {viewportX, viewportY}

# Step 2: 获取当前 iframe 视口宽度
run_js("return window.innerWidth") → 1750 (举例)

# Step 3: 判断是否超出视口
if viewportX > window.innerWidth:
    scroll_to_cell(col, row)   # 超出 → 先滚动
click_cell(col, row)           # 再点击
```

**❌ 超出不 scroll**（本次教训）：
```
# 领料进度列(col=20) viewportX=2398 > 当前窗口宽，在屏幕外
click_cell(col=20, row=0, icon_name="filter-icon")
→ 打到空处，无响应
```

**✅ 超出的正确做法**：
```
scroll_to_cell(col=20, row=0)              # viewportX=2398超出→先滚动
click_cell(col=20, row=0, icon_name="filter-icon")
→ viewportX=1344（在视口内），弹出筛选面板 ✅
```

**✅ 未超出时直接 click，无需 scroll**：
```
# 制令单单号列(col=3) viewportX=242 < window.innerWidth，在视口内
click_cell(col=3, row=8)                    # 直接点，无需scroll
```

### 4.2 iframe

业务模块在 `[role=tabpanel][aria-hidden=false] iframe` 内。MCP 工具默认 `in_frame=True`，自动在该 iframe 内查找元素与执行 JS。坐标换算（帧内 → 顶层视口）由工具自动完成。



### 4.3 弹窗检测

见 `references/modal-types.md`。每次点击后 `detect_modal()`，三级优先级。

**⚠️ VTable 筛选弹窗用 `.vtable-filter-menu`，不是 ant-design 组件**——`detect_modal` 不检测此类自定义弹窗。点击列头筛选图标后，无论 `detect_modal` 返回什么，**必须用 `run_js` 补充探测**：

```
# 点击后调用
detect_modal() → 返回 {type:"none"}
# ❌ 不要就此认为无弹窗，需补充：
run_js("return document.querySelector('.vtable-filter-menu')?.outerHTML")
# → 若有内容，弹窗实际已弹出
```

**📍 操作规范**：
1. `scroll_to_cell(col, 0)` — 先滚动到目标列（列超出视口时）
2. `click_cell(col, 0, icon_name="filter-icon")` — 点击筛选图标
3. `detect_modal()` — 常规检测（ant-design 弹窗）
4. `run_js("...vtable-filter-menu...")` — 补充检测 VTable 自定义弹窗

### 4.4 会话维持

`check_session()` 检测过期 → `refresh_session()`（缓存 cookie）→ 失败则 `login_ocr()`。

## 5. 截图分析

用户提供截图时用 `read` 分析，识别页面标题、表单字段、按钮文字、表格列标题、状态标签。调试 canvas 点击落点时用 `mouse_trail(on=true)` 可视化。

## 6. 质量管理

详见 `references/quality-rubric.md`。核心：

**高级阻塞项**（每条用例必过）：有可执行步骤 / 预期结果可断言 / 编号唯一 / 前置条件独立 / 测试类型已标注 / 用户已确认。

**中级重要项**：每条仅覆盖一个场景 / 测试数据具体化 / 验证点可验证 / 正负向都覆盖。

## 7. 异常处理

| 异常 | 处理 |
|---|---|
| MCP 服务器未注册 | 检查 `.mcp.json`；`claude mcp list` 应见 `drission-ui` |
| 浏览器连接失败 | 检查 `http://localhost:9222/json`；确认 Chrome 以 9222 启动 |
| `mount_vtable` 失败 | `get_active_frame()` 确认 iframe；仍失败按 `vtable-interaction.md` 降级：截图 + 仅生成低级展示类用例 |
| `enter_module` iframe 未就绪 | 重试 `reset_to_initial`；检查菜单文本匹配 |
| SCM 会话过期 | `check_session` → `refresh_session` → 失败则 `login_ocr` |
| openpyxl 未安装 | `uv add openpyxl` |
| 需求信息不足 | 3 轮追问后生成骨架用例，备注 `[待确认]` |
| 写入权限不足 | 降级到当前目录 |

## 8. 自检清单

- [ ] `drission-ui` MCP 可用（`claude mcp list`）
- [ ] 浏览器可连接（port 9222）
- [ ] `connect` 成功 + `cache_session` 已缓存
- [ ] 用户已确认变量配置
- [ ] 输出目录有写入权限
- [ ] 每条用例通过 `quality-rubric.md` 的高级清单
- [ ] 每次点击后都调用了 `detect_modal`
