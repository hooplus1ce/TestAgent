# Codex Adapter

This directory contains Codex-specific project configuration. It is parallel to
the existing `.claude` setup and does not modify Claude configuration.

## MCP

Codex reads `.codex/config.toml` in trusted projects. This project configures:

- `drission-ui`: the project-specific DrissionPage MCP server.

The test-case generation route uses only `drission-ui`. This keeps the agent
tool surface focused on the Python/DrissionPage implementation and avoids
duplicating browser-control tools. A future Playwright comparison version should
be added as a separate adapter, not mixed into this default path.

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
