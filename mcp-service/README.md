# drissionpage-mcp

项目唯一的 DrissionPage MCP stdio 服务，使用 Python `src/` 布局。所有 Agent 从项目根
uv workspace 直接运行包的 `__main__` 入口。

## Layout

```text
mcp-service/
├── pyproject.toml
├── configs/                         # 服务配置模板
├── docs/                            # 实现参考文档
├── scripts/                         # 本地验证脚本
├── src/drissionpage_mcp/
│   ├── server.py                    # MCP tools/resources 注册与 stdio 启动
│   ├── core/                        # 配置、能力分组、UI 契约
│   ├── services/                    # 浏览器、会话、表格、观察、网络实现
│   ├── resources/                   # MCP evidence resources
│   ├── workflows/                   # 证据、用例生成、执行、报告
│   └── assets/js/                   # 随包发布的浏览器注入脚本
└── tests/                           # 不连接浏览器的包级冒烟测试
```

## Install And Run

从项目根目录执行：

```bash
uv sync --all-packages --all-groups
uv run --package drissionpage-mcp -m drissionpage_mcp
```

MCP client 配置示例：

```json
{
  "mcpServers": {
    "drissionpage-mcp": {
      "command": "uv",
      "args": ["run", "--package", "drissionpage-mcp", "drissionpage-mcp"],
      "env": {
        "DRISSIONPAGE_MCP_PROFILE": "full",
        "DRISSIONPAGE_MCP_CAPS": "all"
      }
    }
  }
}
```

Codex、Claude 和 Trae 使用同一个 workspace package 命令。包模块会把运行目录固定到
`mcp-service/`。服务默认读取
`mcp-service/configs/dp_configs.ini`；所有启动配置均使用仓库相对路径，迁移项目时不需要
修改机器绝对路径。设置相对的
`DRISSIONPAGE_MCP_CONFIG_DIR` 可覆盖配置目录，模板位于 `configs/dp_configs.example.ini`。

各平台只负责自己的配置格式适配：Claude 使用根目录 `.mcp.json`，Codex 使用
`.codex/config.toml`，Trae 使用 `.trae/mcp.json`。它们必须继续指向同一个
`drissionpage_mcp` workspace package。

新服务的证据默认保存到 `mcp-service/resources/`。服务启动时会可选加载被 gitignore 的
根目录 `.env`，仅补充进程中尚未设置的变量；Agent 或系统显式注入的变量优先。
`refresh_session` 与 `login_ocr` 从加载后的进程环境读取 `HL_USERNAME` 和
`HL_USERPWD`，参见 `.env.example`。

## Role-Based Approval Regression

`BrowserContext` 隔离 Cookie、localStorage 和登录态，适合把同一审批流中的业务参与者
分别登录为独立账号。服务新增以下 MCP 工具：

- `role_session_start(role_id)`：创建独立 Context、读取角色环境变量并登录；失败自动清理。
- `role_session_activate(role_id)`：将现有通用工具切换到该角色的 Context。
- `role_session_list()` / `role_session_close(role_id)`：查看或释放角色会话。

角色 ID 使用英文逻辑名，例如 `requester`、`dept_manager`、`finance_approver`。变量名由
角色 ID 自动派生：

```text
role_id=requester       -> HL_ROLE_REQUESTER_USERNAME / HL_ROLE_REQUESTER_USERPWD
role_id=dept_manager    -> HL_ROLE_DEPT_MANAGER_USERNAME / HL_ROLE_DEPT_MANAGER_USERPWD
```

将这些变量放到被 Git 忽略的根 `.env` 或 MCP 客户端的 secret/environment 配置中，
不得提交到代码仓库。
工具返回变量名、Context 状态和 Cookie 名称，但不会返回密码、Cookie 值或令牌。

账号密码只负责把测试执行者登录为正确的业务身份。每次回归还应固定并维护：测试账号所属
部门和权限矩阵、审批模板/审批人路由、可重复创建与清理的业务数据，以及各审批节点的
预期可见性和操作结果。角色会话隔离的是登录态，不会替系统配置这些业务前置条件。

审批回归按角色顺序执行即可，无需并行：

```text
role_session_start(requester)
role_session_start(dept_manager)
role_session_activate(requester) -> 创建并提交单据
role_session_activate(dept_manager) -> 查询待办并审批
role_session_activate(requester) -> 验证审批结果和可见权限
```

`run_test_cases` 也可以直接回放这类用例。将角色动作写入 `automation_recipe`，执行器会在
角色用例启动时只连接浏览器，不会对默认 tab 进行会话刷新；每个角色用例在首个业务动作
前必须显式执行 `role_session_activate`。

```json
{
  "case_id": "APPROVAL-001",
  "case_title": "申请人提交后由部门主管审批",
  "automation_recipe": {
    "setup": [
      {"action": "role_session_start", "args": {"role_id": "requester"}},
      {"action": "role_session_start", "args": {"role_id": "dept_manager"}}
    ],
    "steps": [
      {"action": "role_session_activate", "args": {"role_id": "requester"}},
      {"action": "enter_module", "args": {"menu_text": "申请单"}},
      {"action": "role_session_activate", "args": {"role_id": "dept_manager"}},
      {"action": "enter_module", "args": {"menu_text": "待办审批"}},
      {
        "action": "query_table",
        "args": {"operation": "values", "column_title": "单据状态"},
        "assertions": [
          {"path": "values", "operator": "all_each_contains", "value": "已审批"}
        ]
      }
    ],
    "cleanup": [
      {"action": "role_session_close", "args": {"role_id": "dept_manager"}},
      {"action": "role_session_close", "args": {"role_id": "requester"}}
    ]
  }
}
```

在上述两个角色切换之间插入创建、提交、审批等业务动作即可。涉及提交、审批、驳回等
持久化操作时，仍须设置 `destructive: true`，并在 `cleanup` 中提供可验证的业务清理步骤。
执行结果会标记 `role_mode: true`，逐步记录也会保留实际执行的角色动作。
角色 facade 不接受代理凭据；代理请通过服务配置提供。

默认 `DRISSIONPAGE_MCP_PROFILE=full` 向模型暴露完整工具目录；需要主动减少模型上下文时
可显式改为 `enterprise`，此时只保留稳定的企业测试主路径。
在配置了 capability 裁剪时，审批回归需包含 `roles`，例如
`DRISSIONPAGE_MCP_CAPS=core,roles,vtable,workflow`。
同一 MCP 进程中的通用浏览器工具仍按调用顺序执行；这正符合多角色审批回归的状态传递模型。

## Verify

```bash
uv run pytest -q
uv run --package drissionpage-mcp python mcp-service/scripts/verify_live.py
```

第二条命令连接本机 Chrome 调试端口，运行浏览器只读验证。
