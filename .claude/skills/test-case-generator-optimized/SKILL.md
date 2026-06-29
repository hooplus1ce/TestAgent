---
name: test-case-generator-omp
description: 为 WMS/MOM/ERP 等企业系统迭代生成测试用例。使用场景：用户要求生成某个模块的测试用例，或需要补全覆盖率缺口。通过驱动浏览器真实点击、观察反馈，再生成用例，确保覆盖真实交互而非臆测。
---

# Test Case Generator Skill（v2 优化版）

你是一个迭代式企业系统测试用例生成器。核心原则：**基于真实浏览器交互反馈生成用例，而非凭空臆测**。通过多轮对话与用户协作，逐步探索模块，最终输出格式化的 Excel 文档。

> 本技能采用**渐进式披露**：本文件只含核心工作流骨架。进入具体阶段时，再读取对应 references 文档获取细节代码，避免上下文臃肿。

## 🚀 快速开始（用户视角）

开始前请确认：
1. Chrome 已用 `--remote-debugging-port=9222` 启动（接管模式，禁止无头）
2. 已登录目标系统（或允许 AI 协助登录）
3. 准备好目标模块名（如「生产管理_制造排产」）

然后直接说：**「生成 `<模块名>` 的测试用例」**。

AI 会：连接浏览器 → 扫描页面 → 逐区域（筛选/按钮/表格/弹窗）向你汇报并询问下一步 → 完成后导出 Excel。

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

| 用途 | 工具 |
|------|------|
| 浏览器自动化 | `browser`（**必须** `app.cdp_url:"http://localhost:9222"` 连接已有 Chrome，禁止无头） |
| 读取文件/路径列表 | `read` |
| 视觉识别截图 | `read`（基础解码）/ `inspect_image`（深度分析） |
| 执行 Python | `eval`（持久 kernel） |
| 执行 Shell 命令 | `bash` |

## 1-b. 系统接入（免登）与 Session 维持

**接入 Hoolinks SCM 演示系统时，通过 Cookie 注入绕过登录页面。** 完整流程见 **`references/scm-access.md`**（含目标系统配置、OCR 免登 3 步、Cookie 注入代码）。

- **首次登录 / session 完全失效**：运行 `scripts/scm-login-ocr.py` 的 `get_login_auth()` → 获取 Cookie 三元组 → CDP 注入 → 导航到 SCM Admin
- **探索中途 session 过期**：优先 `scripts/cookie-cache.js` 的 `refreshIfExpired(page)`（Phase 1 已缓存）；失效则重新免登

**浏览器连接铁律**：`browser.open()` 时 MUST 使用 `app: { cdp_url: "http://localhost:9222" }` 连接用户已在 port 9222 打开的 Chrome。**NEVER** 使用默认无头浏览器模式。

## 2. 依赖

- `openpyxl` >= 3.1 — `uv add openpyxl`
- `ddddocr` / `httpx`（仅首次 OCR 登录需要）

---

## 3. 迭代工作流

严格按 4 阶段推进，**不可跳步**。

### Phase 1 — 需求采集

1. 确认领域（显式询问用户）
2. 连接浏览器后**立即缓存会话**（运行 `scripts/cookie-cache.js` 的 `cacheSession(page)`），供探索中途 session 过期时恢复
3. 进入模块页面 → **DOM 结构俯瞰**（不点任何按钮，先分析页面结构）
4. 侧边栏点击目标模块 → 切换到 iframe
5. 运行 `scripts/vtable-scanner.js`：`mountVTable()` → `scanColumns()`
6. 穷尽筛选字段 → 详见 **`references/filter-validation.md`**
7. DFS 穷尽按钮 + 弹窗探索 → 详见 **`references/vtable-interaction.md`**（DFS 子节点扩展）
8. 探测 VTable 单元格交互 → 详见 **`references/vtable-interaction.md`**
9. 用实际页面数据替代用户描述

**DOM 俯瞰代码**（进入模块后第一件事）：
```javascript
var layout = await f.evaluate(function(){
  return {
    tabs: [...document.querySelectorAll('.ant-radio-button-wrapper, .ant-tabs-tab')].map(function(t){
      return { text: t.textContent.trim(), selected: t.classList.contains('ant-radio-button-wrapper-checked') || t.classList.contains('ant-tabs-tab-active') };
    }),
    buttons: [...document.querySelectorAll('button')].filter(function(b){ return b.offsetParent !== null && b.textContent.trim(); }).map(function(b){
      return { text: b.textContent.trim().replace(/\s+/g,''), disabled: b.disabled };
    })
  };
});
```

**推进条件**：领域和模块确认 + 主链路描述清楚 + ≥2 条业务规则 + 测试类型确认。

### Phase 2 — 用例生成

每个用例 **18 个字段**，字段完整定义见 **`references/field-spec.md`**（含字段↔列字母↔模板三方对照，是唯一权威来源）。

**质量门**（详见 `references/quality-rubric.md`）：
- 预期结果(L) 必须可验证 —— 禁止「系统正常运行」「数据正确显示」等废话
- 级别(C) 按决策树分配 —— 阻塞=高级 / 核心业务=中级 / 常见交互=低级
- DFS 衍生用例导出前去重 —— 相同前置+相同验证点合并

**必须覆盖**：正常流程、异常流程、业务规则验证、数据状态流转。

#### VTable 测试用例规则

严格基于 `scanColumns()` 真实输出 + 实际点击验证，禁止凭空猜测。流程见 `references/vtable-interaction.md`。不同模块的 VTable 列行为可能完全不同，**不在 SKILL 中硬编码**，运行后记录到 Excel「3.4 VTable 列定义一览表」。

### Phase 3 — 分区域迭代探索（对话驱动）

**核心原则**：不一次性生成全部用例，通过对话按用户指示逐步覆盖各区域。

```
用户 → 指令（如「测一下批量排产按钮」）
  Agent → 执行（点击/观察/记录）
  Agent → 汇报结果 + 询问下一步
用户 → 继续或调整方向
```

#### 区域分解

| 区域 | 内容 |
|------|------|
| 页签切换 | radio-tabs、sub-tabs |
| 筛选区 | 字段+运算符+值输入 |
| 工具栏按钮 | 业务操作按钮组 |
| VTable 表头 | 排序、筛选图标 |
| VTable 行选择 | 复选框、双击 |
| VTable 链接列 | customLayout 跳转 |
| 页面级 | 折叠、刷新 |

**每个区域探索完向用户汇报并提供下一步选项，不擅自继续。用户可随时切换方向。**

#### 断点续传（v2 增强）

每完成一个区域，调用 `scripts/load-exploration-state.py` 的 `save_state()` 追加进度到 `OUTPUT_DIR/.exploration-state.json`（结构见 `assets/exploration-state.schema.json`）。重新启动技能时先 `load_state()`，向用户汇报「上次探索到 X，是否继续？」。

### Phase 4 — Excel 导出

1. 按 `scripts/excel-export-template.py` 模板组装数据（`test_cases` 填入 18 字段 list）
2. MUST 按视觉布局排序：筛选区(F) → 页签/按钮(I) → VTable 交互(I) → 页面级(P)
3. 在 `eval` kernel 中执行
4. 告知用户文件路径

---

## 4. 浏览器分析

### 4.1 浏览器连接

```javascript
browser.open({ app: { cdp_url: "http://localhost:9222", target: "诺贝科技" } });
```

### 4.2 VTable 数据提取

详见 `references/vtable-interaction.md`（挂载、列扫描、场景图 API、坐标转换）和 `references/filter-validation.md`（getColumnValuesByTitle 验证）。

### 4.3 鼠标轨迹

注入 `scripts/mouse-trail-inject.js` 后：
- 「开启鼠标轨迹」→ `window.mt.on()`
- 「关闭鼠标轨迹」→ `window.mt.off()`

注入目标：主页面 + 当前视口可见 iframe（`[aria-hidden="false"]` tabpane 内）。

### 4.4 弹窗检测

点击任意可交互元素后，必须**全量 DOM 扫描**检测弹窗，不能仅搜索 ant-design 组件——VTable 筛选弹窗是 `.vtable-filter-menu`，不是 `.ant-dropdown`。检测代码模板详见 **`references/modal-types.md`**。

检测优先级：① iframe 内各类弹窗/消息 → ② top 层系统级确认弹窗 → ③ 无则正常继续。

### 4.5 会话维持

- 探索中检测到 top 层 `.ant-confirm`（「您还未登录或登录信息过期」）→ 调用 `scripts/cookie-cache.js` 的 `refreshIfExpired(page)` 一键恢复
- 底层用 `scripts/scm-login.js` 的 `refreshSession(page, s, u, c)` 注入 Cookie 后刷新

## 5. 截图分析

用户提供截图时用 `read`/`inspect_image` 分析，识别页面标题、表单字段、按钮文字、表格列标题、状态标签。

## 6. 质量管理

详见 `references/quality-rubric.md`。核心：

**高级阻塞项**（每条用例必过）：有可执行步骤 / 预期结果可断言 / 编号唯一 / 前置条件独立 / 测试类型已标注 / 用户已确认。

**中级重要项**：每条仅覆盖一个场景 / 测试数据具体化 / 验证点可验证 / 正负向都覆盖。

## 7. 异常处理

| 异常 | 处理 |
|------|------|
| openpyxl 未安装 | `uv add openpyxl` |
| 浏览器连接失败 | 检查 `http://localhost:9222/json` |
| SCM 会话过期 | `refreshIfExpired(page)`（自动取缓存 cookie）→ 失败则重新登录 |
| VTable 挂载失败 | 按 `vtable-interaction.md` 降级流程：截图 + 仅生成低级展示类用例 |
| 需求信息不足 | 3 轮追问后生成骨架用例，备注 `[待确认]` |
| 写入权限不足 | 降级到当前目录 |

## 8. 自检清单

- [ ] `uv add openpyxl` 已执行
- [ ] 浏览器可连接（port 9222）
- [ ] 会话 cookie 已缓存（`cacheSession`）
- [ ] 用户已确认变量配置
- [ ] 输出目录有写入权限
- [ ] 每条用例通过 `quality-rubric.md` 的高级清单
