# Codex Adapter

This directory contains Codex-specific project configuration. It is parallel to
the existing `.claude` setup and does not modify Claude configuration.

## MCP

Codex reads `.codex/config.toml` in trusted projects. This project configures:

- `drissionpage-mcp`: the project-specific DrissionPage MCP server.

Codex uses a project-specific direct package entry instead of the shared
`launcher.py`. The MCP config intentionally omits `cwd`, so Codex uses the
session workspace root and runs
`uv run --project mcp-service python -m drissionpage_mcp`. The package module
then fixes its runtime directory to `mcp-service/`, preserving
`configs/dp_configs.ini` relative-path behavior.
All Agent sessions use the complete `full` tool catalog. `enterprise` remains
available only as an explicit context-reduction option.

Verify from the repository root:

```bash
codex mcp list
```

In the Codex TUI, use:

```text
/mcp
```

If project MCP servers do not appear, confirm this repository is trusted in
`~/.codex/config.toml`, then restart Codex from the repository root.

## Skills

Codex repo-scoped skills live under `.agents/skills`, not `.codex/skills`.
The Codex adapter skill is:

```text
.agents/skills/test-case-generator-dp/SKILL.md
```

The `.claude/skills/test-case-generator-dp` directory is the shared workflow
source; the Codex adapter reads its references and scripts without duplicating
the MCP implementation.
