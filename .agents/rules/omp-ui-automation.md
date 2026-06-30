# OMP 入口规则 — UI 自动化测试与用例生成

## 强制约束

1. **禁用 `browser` 工具**（内置 puppeteer）。全部浏览器操作只能通过 `drission-ui` MCP 服务。
2. **技能**：使用 `.claude/skills/test-case-generator-dp/SKILL.md`（DrissionPage MCP 版）。
3. **MCP 服务器**：`drission-ui`（配置在 `.mcp.json`），连接 port 9222 的 Chrome。
4. **工作流**：严格按照技能文件的 Phase 1~4 迭代推进，不可跳步。

## 用法

在新会话中直接说：

> 「加载技能 `.claude/skills/test-case-generator-dp`，使用 MCP 服务对 XXX 模块进行测试用例生成。禁用 `browser` 工具，全部交互走 `drission-ui` MCP。」

## 已知问题（2026-06-30）

| # | 问题 | 影响 |
|---|------|------|
| 1 | `get_active_frame` 在 tab 式模块返回 false → 依赖活动帧的工具不可用 | ✅ **已修复**：两步策略（JS name → get_frame by name） |
| 2 | `click(in_frame=True)` 依赖活动帧；`click(in_frame=False)` 无法穿透 iframe | 用 `run_js(in_frame=false)` 穿透 iframe 操作 |
| 3 | `run_js` 不支持 async/await/Promise | 用 `while(Date.now()-start<N){}` 自旋等待 |
| 4 | VTable 列坐标超出视口时点击失效 | 先 `get_cell_rect(col,row,scroll=False)` 判断，超出则 `scroll_to_cell` |
| 5 | `detect_modal` 不检测 VTable 自定义弹窗 `.vtable-filter-menu` | 点击筛选图标后额外用 `run_js` 查 `.vtable-filter-menu` |
| 6 | VTable 单元格交互结果有三种可能（弹窗/新Tab/iframe跳转） | 点击后用 `detect_modal` + `dom_overview` + `get_active_frame` 三步排查 |

## 关键技能章节速查

| 需求 | 位置 |
|------|------|
| 工具映射 | SKILL.md §1 |
| 进入模块 + 展开筛选区 | SKILL.md §3 Phase1 step3 |
| 滚动规则（超出才滚） | SKILL.md §4.1 |
| 双击单元格 | SKILL.md §Phase3 区域分解 + `click_cell(double_click=True)` |
| 日期范围选择 | SKILL.md §3 Phase1 step6 + `select_date_range` 工具 |
| 弹窗/跳转排查 | SKILL.md §Phase3 区域分解后 |
| VTable 筛选举例 | `vtable-interaction.md` §七 DFS 子节点扩展 |
| 质量门 | `quality-rubric.md` |
| 18 字段规范 | `field-spec.md` |
