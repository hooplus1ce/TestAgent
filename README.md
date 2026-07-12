# TestAgent

基于 MCP 和 DrissionPage 的 SCM/WMS/MOM/ERP Web 自动化测试项目。

## 项目结构

- `mcp-service/`：项目唯一的 DrissionPage MCP 实现，采用 Python `src/` 布局。
- `.claude/skills/test-case-generator-dp/`：测试策略、覆盖模型、字段规范和 Excel 导出脚本。
- `.agents/skills/test-case-generator-dp/`：Codex Skill 适配层。
- `tests/`：面向 `mcp-service/src/drissionpage_mcp` 的完整回归测试。
- `test_cases/`：项目级测试用例 JSON 和导出结果。

不存在其他 MCP 实现或兼容启动入口。Claude、Codex 和 Trae 都以
`mcp-service/launcher.py` 作为统一入口，对外服务名为 `drissionpage-mcp`。

## 快速开始

同步独立 MCP 环境：

```bash
uv sync --project mcp-service --all-groups
```

检查可迁移的浏览器基线配置：

```bash
sed -n '1,120p' mcp-service/configs/dp_configs.ini
```

`dp_configs.ini` 中的 `../dp_profile` 复用项目根目录的浏览器用户数据。
机器差异和账号密码通过 MCP 进程环境变量注入，不写入仓库配置。

项目已有以下 Agent 配置：

- Claude：`.mcp.json`
- Codex：`.codex/config.toml`
- Trae：`.trae/mcp.json`

修改配置后重启对应 Agent 会话，再检查 `drissionpage-mcp` 是否加载。

## 验证

```bash
uv run pytest -q
uv run --project mcp-service pytest -q mcp-service/tests
```

手动启动 stdio 服务：

```bash
uv run --project mcp-service python mcp-service/launcher.py
```

完整使用说明见 [docs/项目使用说明.md](docs/项目使用说明.md)，服务架构与工具说明见
[mcp-service/README.md](mcp-service/README.md)。
