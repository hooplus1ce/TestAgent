# TestAgent

基于 MCP（Model Context Protocol）+ DrissionPage 的企业系统（WMS/MOM/ERP）AI 驱动自动化测试工具集。

## 组成

- **`mcp-servers/drission-ui/`** — drission-ui MCP 服务器：把 DrissionPage 浏览器自动化封装成一组精简的结构化 MCP 工具，供 AI 驱动的 UI 测试技能调用。详见 [mcp-servers/drission-ui/README.md](mcp-servers/drission-ui/README.md)。
- **`.claude/skills/`** — 测试用例生成技能（`test-case-generator-dp`、`test-case-generator-optimized`）。
- **`tests/`** — 单元测试。

## 快速开始

完整项目使用流程见 [项目使用说明.md](项目使用说明.md)。

```powershell
# 1. 安装依赖
uv sync

# 2. 以远程调试端口启动 Chrome
chrome --remote-debugging-port=9222

# 3. 注册 MCP 服务器（项目根 .mcp.json 已配置）
# Claude Code 会自动加载

# 4. 跑单元测试
uv run pytest tests/ -v
```

## 配置

通过环境变量覆盖默认配置（URL/域名/端口等），详见 [mcp-servers/drission-ui/README.md](mcp-servers/drission-ui/README.md#配置)。
