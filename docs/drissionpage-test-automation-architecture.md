# DrissionPage 测试自动化架构

## 唯一实现

`mcp-service/` 是项目唯一 MCP 服务。它保留独立 `pyproject.toml` 和 Python `src/`
布局作为包边界，但由项目根 uv workspace 统一管理 `.venv` 和 `uv.lock`。所有 Agent
调用同一个包模块入口。

```text
Agent platform config
        |
        +-- Codex / Claude / Trae:
            uv run --package drissionpage-mcp drissionpage-mcp
        |
        v
drissionpage_mcp.server (stdio FastMCP)
        |
        +-- core: config / capabilities / UI contract
        +-- services: browser / auth / roles / tables / overlays / network
        +-- workflows: evidence / testcase generation / execution / reporting
        +-- resources: evidence storage and MCP resources
```

## 运行时配置

- 服务配置：`mcp-service/configs/dp_configs.ini`
- 浏览器用户目录：配置中的 `../dp_profile`
- 证据目录：默认 `mcp-service/resources/`
- 模型工具面：默认 `DRISSIONPAGE_MCP_PROFILE=full`
- 能力裁剪：仅 `DRISSIONPAGE_MCP_CAPS`
- 凭据：仅通过 MCP 进程环境变量注入
- 本地环境文件：根目录 `.env`（被 Git 忽略）

包的 `__main__` 会把 MCP 运行目录固定到 `mcp-service/`，因此 ini 中所有相对路径在
不同机器上保持一致。

## 分层职责

| 层 | 职责 |
|---|---|
| `core` | 配置解析、capability 分组、UI 数据契约 |
| `services` | DrissionPage 浏览器原语、Session、BrowserContext、角色隔离、表格和网络 |
| `workflows` | 业务流证据、覆盖矩阵、用例配方执行、报告和基线比较 |
| `resources` | 证据路径约束、原子写入和 MCP resource 暴露 |
| Skills | 探索策略、覆盖政策、企业测试用例业务语言和 Excel 输出 |

Skills 不导入内部 Python 模块或自行换算 VTable 坐标；常规回归优先通过稳定 MCP 工具契约编排，`run_js` 只用于有边界的诊断取证。

## 证据闭环

```text
浏览器动作
  -> 页面/元素/网络/截图证据
  -> flow evidence
  -> 覆盖矩阵与已验证用例
  -> automation_recipe 回放
  -> 执行报告与基线差异
```

正式用例必须包含真实业务断言。删除、提交、审批等持久化动作需要显式授权和可验证 cleanup；敏感头、Cookie、Token、密码和超大正文不得进入证据。

## 多角色审批

角色账号分别运行在独立 BrowserContext 中。执行器按业务顺序调用
`role_session_activate` 切换身份，无需并行；Cookie、localStorage 和登录态互不共享。
账号凭据之外，还必须固定部门、角色权限、审批路由、业务夹具和节点断言。

## 验收约束

1. 仓库中只有 `mcp-service` 一套 MCP 实现。
2. 所有 Agent 从根 workspace 直接运行包模块，并统一以 full profile 暴露完整工具目录。
3. 根测试和服务测试都导入 `drissionpage_mcp` 新包。
4. Skill 引用的工具必须存在于 MCP `tools/list`。
5. 标准 MCP 客户端能够完成 initialize、resources/list 和 tools/list。
