# test-case-generator-omp（v2 优化版）

为 WMS / MOM / ERP 等企业系统**迭代生成测试用例**的 Skill。通过驱动浏览器真实点击、观察反馈，再生成用例，确保覆盖真实交互而非臆测，最终输出格式化的 Excel 文档。

> 本目录是 `.agent/skills/test-case-generator`（v1）的**优化重构版**，独立存放，不破坏原技能。优化依据见下方「与 v1 的差异」。

## ✨ 核心能力

| 能力 | 说明 |
|------|------|
| 迭代协作 | 严格 4 阶段工作流，分区域逐步探索，每步向用户汇报并等待指令 |
| 浏览器驱动 | 接管已运行 Chrome（端口 9222），不启动无头浏览器 |
| VTable 深度分析 | 专用扫描器识别列类型、排序/筛选图标坐标、场景图 API |
| DFS 弹窗探索 | 按钮→弹窗/跳转/内容变更 三类子节点递归遍历 |
| 筛选自动验证 | 浏览器内 `getColumnValuesByTitle()` 断言后转中文写入预期结果 |
| 结构化 Excel | 18 字段标准模板，按视觉布局排序，带级别配色 |

## 🚀 快速开始

1. 启动 Chrome（接管模式）：`chrome --remote-debugging-port=9222`
2. 登录目标系统
3. 告诉 AI：**「生成 `<模块名>` 的测试用例」**（如「生产管理_制造排产」）
4. AI 逐区域探索并询问你，配合指令推进
5. 完成后输出 `测试用例_<模块名>_<日期>.xlsx`

## 📁 目录结构（符合 Anthropic Claude Skills 最佳实践）

```
test-case-generator-optimized/
├── SKILL.md                          # 核心指令（精简，渐进式披露）
├── README.md                         # 本文件
├── references/                       # 按需加载的细节文档
│   ├── scm-access.md                 # SCM 接入配置 + OCR 免登 3 步 ★回补
│   ├── field-spec.md                 # 18 字段权威定义（字段↔列↔模板对照）★修复 L 列重号
│   ├── quality-rubric.md             # 质量门：预期结果正反例 + 级别决策树 + 高级/中级清单 ★新增+回补
│   ├── vtable-interaction.md         # VTable 交互 + 数据提取 + DFS 五类分支 + 挂载失败降级 + 场景图 API ★重组+回补
│   ├── filter-validation.md          # 筛选字段穷尽 + 验证流程 ★拆分自 SKILL
│   └── modal-types.md                # 三类弹窗检测与处理（沿用 v1）
├── scripts/                          # 可执行脚本
│   ├── scm-login-ocr.py              # OCR 免登脚本（ddddocr + httpx）★回补为独立脚本
│   ├── vtable-scanner.js             # VTable 列分类 + 图标坐标 ★修复命名
│   ├── vtable-column-values.js       # 按列名取全列数据（筛选验证核心）
│   ├── excel-export-template.py      # Excel 导出模板（双 Sheet）
│   ├── scm-login.js                  # 登录 + 会话维持 ★增强：引用 cookie 缓存
│   ├── cookie-cache.js               # 会话 cookie 缓存 ★新增（解决中途过期）
│   ├── load-exploration-state.py     # 断点续传进度读写 ★新增
│   └── mouse-trail-inject.js         # 鼠标轨迹可视化
└── assets/
    └── exploration-state.schema.json # 断点续传进度文件 Schema ★新增
```

### 设计原则

- **渐进式披露**：SKILL.md 只含骨架（~200 行），细节按需从 references 加载，降低冷启动 token 成本
- **单一权威源**：字段定义只在 `field-spec.md`，避免 SKILL.md 与 Python 模板漂移
- **参考文档与脚本解耦**：references 是 AI 读的规范，scripts 是执行的代码

## 🔧 与 v1 的差异（本次优化）

### 高级修复（正确性）
- **修复 Phase 2 字段表 L 列重号 bug**：v1 的 SKILL.md 字段表「L 预期结果」出现两次、I/J/K 编号错乱。v2 将字段定义抽离到 `field-spec.md`，与 `excel-export-template.py` 的 `HEADERS_18` 三方对齐
- **修复 `vtable-scanner.js` 命名**：头部注释 `__scanColumns` → `scanColumns`（与实际函数名一致）
- **剔除历史快照**：不迁移 `SKILL.md.backup`（53KB），依赖 git 历史回溯
- **回补免登流程**：将 v1 SKILL.md §0-b 的 SCM 接入 + OCR 免登 3 步代码抽离到 `scm-access.md` + 独立脚本 `scm-login-ocr.py`（v1 时期这段代码内联在 SKILL 中，易丢失）
- **回补 DFS 五类分支**：`vtable-interaction.md` 补全 v1 Phase 1-c 的业务确认弹窗（取消/确定双测）、消息提醒、下载/打印、新 Tab、系统级确认弹窗分支
- **回补 VTable 数据提取**：`vtable-interaction.md` 补全 `getCellOriginRecord` / `getFilteredRecords` 用法样本
- **回补高级强约束**：`quality-rubric.md` 补全 v1 §8 的「纯中文业务语言」「前置条件详细描述」「预期结果必须经浏览器验证」等强约束 + 对照表

### 中级增强（健壮性）
- **会话持久化**：新增 `cookie-cache.js`，Phase 1 即缓存 cookie，解决「探索到一半 session 过期却无 cookie 可注入」
- **质量门量化**：`quality-rubric.md` 给出预期结果正反例 + 级别决策树，杜绝「系统正常运行」类废话

### 低级重组（可维护性）
- **SKILL.md 拆分**：从 715 行精简到 ~200 行，细节下沉到 4 个 references 文档
- **VTable 挂载失败降级**：`vtable-interaction.md` 明确降级路径（截图 + 低级展示类用例）
- **用例去重规则**：`quality-rubric.md` 定义合并维度

### 低级易用性
- **快速开始**：SKILL.md 顶部 onboarding，明确前提条件和触发方式
- **断点续传**：`load-exploration-state.py` + schema，长任务可中断恢复
- **变量配置说明**：明确配置时机和覆盖方式

## 📋 字段速查（详见 `references/field-spec.md`）

18 字段：用例编号(A) 标题(B) 级别(C) 验证点(D) 一级模块(E) 二级模块(F) 测试类型(G) 功能(H) 前置条件(I) 测试步骤(J) 测试数据(K) 预期结果(L) 测试结果(M) 执行人(N) 执行时间(O) 编写人(P) 编写时间(Q) 备注(R)

## 🔗 技术栈

- 浏览器自动化：CDP（Chrome DevTools Protocol，端口 9222）
- VTable：场景图 API + getCellRect + scroll 修正
- 导出：openpyxl（Excel）
- OCR 登录（可选）：ddddocr + httpx
