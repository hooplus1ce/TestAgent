# Codex Adapter

This directory contains Codex-specific project configuration. It is parallel to
the existing `.claude` setup and does not modify Claude configuration.

## MCP

Codex reads `.codex/config.toml` in trusted projects. This project configures:

- `drissionpage-mcp`: the project-specific DrissionPage MCP server.

Codex, Claude, and Trae all call `mcp-service/launcher.py`. The launcher fixes the
runtime directory to `mcp-service/`, so its local virtual environment and
`mcp-service/configs/dp_configs.ini` remain portable with the repository.

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
