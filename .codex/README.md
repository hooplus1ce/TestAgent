# Codex Adapter

This directory contains Codex-specific project configuration. It is parallel to
the existing `.claude` setup and does not modify Claude configuration.

## MCP

Codex reads `.codex/config.toml` in trusted projects. This project configures:

- `drissionpage-mcp`: the project-specific DrissionPage MCP server.

Codex, Claude, and Trae all call `mcp-service/launcher.py`. The launcher fixes the
runtime directory to `mcp-service/`, so the local virtual environment and
`configs/dp_configs.ini` remain portable with the repository.

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

The existing `.claude/skills/test-case-generator-dp` workflow remains a
read-only upstream reference until the shared workflow is extracted.
