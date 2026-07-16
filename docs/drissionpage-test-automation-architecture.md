# DrissionPage 测试自动化架构

## 唯一实现

`mcp-service/` 是项目唯一 MCP 服务。它保留独立 `pyproject.toml` 和 Python `src/`
布局作为包边界，但由项目根 uv workspace 统一管理 `.venv` 和 `uv.lock`。所有 Agent
调用同一个包模块入口。

```text
Agent platform config
        |
        +-- Codex / Claude / Trae:
            uv run --package drissionpage-mcp -m drissionpage_mcp
        |
        v
drissionpage_mcp.server  (stdio FastMCP 3.x 装配入口)
        |
        +-- components/*     FileSystemProvider 工具注册（按域）
        +-- core/            config / tool_metadata / UI 契约 / recipe_context
        +-- services/        浏览器、交互、表格、网络、devtools、page_scan …
        +-- workflows/       flow_ops / recipe_execution / evidence / report
        +-- resources/       证据路径与 MCP resources
```

## 分层职责

| 层 | 职责 |
|---|---|
| `server.py` | FastMCP 装配、lifespan、middleware、resources、读写锁、薄 re-export |
| `components/` | 按 cap 域暴露的 `@tool` 适配层（session/vtable/workflow/…） |
| `core/` | 配置、`tool_metadata`（CAP/tags/profile）、UI 契约、`recipe_context` |
| `services/` | DrissionPage 实现：interaction、table_facade、network、devtools、page_scan、browser_context 等 |
| `workflows/` | 证据流、`recipe_execution`（run_test_cases）、报告与基线 |
| `resources/` | 证据路径约束、原子写入 |
| Skills | 探索策略、覆盖政策、业务用例语言；只通过 MCP 工具契约调用 |

Skills **不**导入内部 Python 模块或自行换算 VTable 坐标；常规回归优先通过稳定
MCP 工具编排，`run_js` 仅用于有边界的诊断取证。

## 运行时配置

- 服务配置：`mcp-service/configs/dp_configs.ini`
- 浏览器用户目录：配置中的 `../dp_profile`
- 证据目录：默认 `mcp-service/resources/`
- 模型工具面：默认 `DRISSIONPAGE_MCP_PROFILE=full`
- 能力裁剪：`DRISSIONPAGE_MCP_CAPS`
- 凭据：仅通过 MCP 进程环境变量注入
- 本地环境文件：根目录 `.env`（被 Git 忽略）

包的 `__main__` 会把 MCP 运行目录固定到 `mcp-service/`。

## 工具面与发现

- 默认 **full** profile 暴露 `core/tool_metadata.CAP_GROUPS` 中的全部公开工具。
- `ToolMetadataTransform` 注入 `cap:*` / `risk:*` / `level:*` 与 annotations。
- 可选 `DRISSIONPAGE_MCP_DISCOVERY=search` 启用 `RegexSearchTransform` 压缩目录。
- `activate_tool_groups` 可在会话内按 cap 裁剪可见工具。

## 证据闭环

```text
浏览器动作
  -> 页面/元素/网络/截图证据
  -> flow evidence（flow_ops）
  -> 覆盖矩阵与已验证用例
  -> automation_recipe 回放（recipe_execution.run_test_cases）
  -> 执行报告与基线差异
```

正式用例必须包含真实业务断言。删除、提交、审批等持久化动作需要显式授权和可验证
cleanup；敏感头、Cookie、Token、密码和超大正文不得进入证据。

## 多角色审批

角色账号分别运行在独立 BrowserContext 中。执行器按业务顺序调用
`role_session_activate` 切换身份；Cookie / localStorage / 登录态互不共享。

## 验收约束

1. 仓库中只有 `mcp-service` 一套 MCP 实现。
2. 所有 Agent 从根 workspace 运行包模块，默认 full profile 完整工具目录。
3. 根测试和服务测试都导入 `drissionpage_mcp`。
4. Skill 引用的工具必须存在于 MCP `tools/list`。
5. 标准 MCP 客户端能够完成 initialize、resources/list 和 tools/list。
