---
name: test-case-generator-dp
description: Generate WMS/MOM/ERP enterprise test cases from real browser exploration using the drission-ui MCP server, VTable/table scans, modal observation, network evidence, and JSON-to-Excel export. Use when asked to create or improve functional test cases for SCM/MOM/ERP/WMS pages.
---

# Test Case Generator DP for Codex

This is the Codex adapter for the existing DrissionPage-based test case
generation workflow. Treat `.claude/skills/test-case-generator-dp/` as the
shared skill source for strategy, schemas, references, and export scripts.
Generated business test case data belongs under project-level `test_cases/`,
not inside a skill directory.

## Required MCP Servers

Use the project Codex MCP configuration in `.codex/config.toml`.

- `drission-ui` is the required browser automation server for SCM/MOM/ERP pages.
- Do not use Playwright MCP in this adapter. A future Playwright comparison
  version should be created as a separate adapter.

Before generating test cases, confirm the active Codex session has loaded
`drission-ui`. In the TUI use `/mcp`; from a shell use `codex mcp list`.

## Reference Files

Before running a full generation workflow, read the relevant upstream reference
files from `.claude/skills/test-case-generator-dp/`:

1. `SKILL.md`
2. `references/field-spec.md`
3. `references/quality-rubric.md`
4. `references/coverage-model.md`
5. `references/filter-validation.md`
6. `references/modal-types.md`
7. `references/vtable-interaction.md`
8. `探索式增量生成工作流程.md`

Read only the references needed for the requested area when the task is narrow.

## Workflow

1. Connect with `drission-ui.connect`, then run `check_session`.
2. Enter the requested module with `enter_module(..., expand_filter=True)`.
3. Collect page structure with `scan_page_elements`, `dom_tree`,
   `scan_filter_fields`, and `scan_table(kind="auto")`.
4. Build a coverage model before generating cases: asset inventory, testable
   functions, scenario matrix, and coverage statuses (`已验证`, `待验证`,
   `需用户确认`, `工具缺口`).
5. For every click-like action, use:

   ```text
   observe_start(...) -> action -> observe_wait(...)
   ```

6. For interface assertions, prefer `listen_start` / `listen_wait` or
   `observe_start(signals=[...,"network"], listen_targets="gateway")`.
7. Generate only cases supported by observed page behavior, table data, modal
   content, toast/message text, URL/tab changes, or network evidence.
8. Store generated case JSON files in:

   ```text
   test_cases/<MODULE_PINYIN>/
   ```

9. Export Excel using the existing upstream exporter:

   ```bash
   uv run python .claude/skills/test-case-generator-dp/scripts/generate_from_json.py test_cases/<MODULE_PINYIN>/*.json
   ```

Use `uv run python`, not bare `python`, because this project environment does
not guarantee a `python` executable on PATH.

## Output Rules

- Keep the 19-column enterprise Excel schema from the upstream field spec.
- Use Chinese business language in test case titles, steps, data, and expected
  results.
- Do not put framework internals such as VTable, fiber, props, locator syntax,
  or MCP method names into user-facing test steps.
- Automation suggestions may mention MCP tools and technical assertions.
- Mark unverified assumptions as `[待确认]`; do not present them as verified.
- Do not claim coverage is complete until the coverage matrix has no unexplained
  core gaps.

## Current Boundary

This adapter intentionally keeps browser automation in `drission-ui` MCP and
test generation policy in the skill. The next architecture step should extract
shared schemas and scripts into a neutral location such as `schemas/` and
`tools/testcase/`, then let both Claude and Codex adapters reference that shared
core.
