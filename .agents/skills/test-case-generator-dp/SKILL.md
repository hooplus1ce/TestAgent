---
name: test-case-generator-dp
description: Generate WMS/MOM/ERP enterprise test cases from real browser exploration using the drissionpage-mcp server, VTable/table scans, modal observation, network evidence, and JSON-to-Excel export. Use when asked to create or improve functional test cases for SCM/MOM/ERP/WMS pages, or when the user asks to connect the browser, open the page under test, check login/session state, refresh cookies, or get the active iframe.
---

# Test Case Generator DP for Codex

This is the Codex adapter for the existing DrissionPage-based test case
generation workflow. Treat `.claude/skills/test-case-generator-dp/` as the
shared skill source for strategy, schemas, references, and export scripts.
Generated business test case data belongs under project-level `test_cases/`,
not inside a skill directory.

## Required MCP Servers

Use the project Codex MCP configuration in `.codex/config.toml`.

- `drissionpage-mcp` is the required browser automation server for SCM/MOM/ERP pages.
- Do not use Playwright MCP in this adapter. A future Playwright comparison
  version should be created as a separate adapter.

Before generating test cases, confirm the active Codex session has loaded
`drissionpage-mcp`. In the TUI use `/mcp`; from a shell use `codex mcp list`.

`mcp-service/` is the only MCP implementation in this repository. Codex starts
the package directly from `.codex/config.toml` with
`uv run --package drissionpage-mcp -m drissionpage_mcp`; the config does not use an
explicit MCP `cwd`. Do not construct another MCP command.
Browser settings come from
`mcp-service/configs/dp_configs.ini`. The service fills missing process variables
from the gitignored root `.env`; variables explicitly inherited from the
Agent process take precedence. Every Agent adapter in this project uses the complete
`full` profile. Capability filtering remains available through
`DRISSIONPAGE_MCP_CAPS` only when a smaller tool surface is explicitly needed.

Implementation layout (for debugging only — do not import these from the skill):
`components/` registers tools, `services/` holds browser logic, `workflows/` holds
evidence/recipe/report. Public tool **names and parameters stay stable**; see
`docs/drissionpage-test-automation-architecture.md`.

## Reference Files

Before running a full generation workflow, read the relevant upstream reference
files from `.claude/skills/test-case-generator-dp/`:

1. `SKILL.md`
2. `references/scm-access.md`
3. `references/field-spec.md`
4. `references/quality-rubric.md`
5. `references/coverage-model.md`
6. `references/filter-validation.md`
7. `references/modal-types.md`
8. `references/vtable-interaction.md`
9. `探索式增量生成工作流程.md`

Read only the references needed for the requested area when the task is narrow.

## Workflow

1. Run the Browser Ready Gate whenever the user says "connect browser" or before real page exploration:
   `connect(port=9222, target_hint=<TEST_PAGE_URL>)` -> open/select the page under test with `browser_tabs` -> `check_session` -> if expired, `refresh_session` -> return to the page/module -> final `check_session` -> `get_active_frame` -> `detect_page_family`. Stop if the final session check still reports expiration or if no active iframe is available.
2. Enter the requested module with `enter_module(..., expand_filter=True)` when the ready gate did not already land on the target module, then run `get_active_frame` and `detect_page_family` again. Use the returned `preferred_table_kind` / `adapters` when choosing table and modal strategies (`bootstrap` + `layer` for legacy jQuery pages; `vtable`/`html` for AntD modules).
3. Start an evidence flow with `flow_start(module=<MODULE_NAME>)`. While it is active,
   use `explore_action(...)` for each business interaction so the MCP records the
   element, observed feedback, network request/response summary, screenshot and
   duration as one traceable step. Call `flow_capture_page_state(label="initial")`
   before interaction to record DOM, form and table assets. Use
   `capture_before`/`capture_after` only when a page-level state comparison is needed.
4. Collect page structure with `capture_page_model`, `scan_filter_fields`, and
   `scan_table(kind="auto")`.
   Use `table_action(...)` for table interactions, `query_table(...)` for table
   assertions, and `inspect_table_cell(...)` before clicking data-row icons or
   asserting status text/colors. Prefer these facades for normal business tests;
   the complete tool catalog is available when a specialized interaction or
   service diagnosis requires it.
   For legacy jQuery/Bootstrap modules (`detect_page_family` → `legacy_jq_bootstrap`):
   use `scan_table(kind="bootstrap"|"auto")`, open dialogs via toolbar buttons, then
   `scan_form_fields(scope="layer")` / `scan_layer_content` for layer.js iframe forms,
   and `select_option(..., scope="layer")` for bootstrap-select fields.
5. Build a coverage model before generating cases: asset inventory, testable
   functions, scenario matrix, and coverage statuses (`已验证`, `待验证`,
   `需用户确认`, `工具缺口`).
6. For every DOM click/input/select action use `explore_action(...)`; it owns the
   observation lifecycle. For table actions use `table_action(...)`, which also
   returns the observed toast/modal/network signal.

   Use `observe_snapshot(detail="full")` for visible VTable overlays too:
   column filter menus, toolbar tooltips, and column-setting menus are exposed
   as structured overlays; hidden VTable overlay DOM should not be treated as
   visible behavior.

7. For a single interface assertion pass `listen_targets="gateway"` to
   `explore_action` or `table_action`. Use `network_trace_start` /
   `network_trace_stop` only for evidence spanning multiple actions.
8. Finish the evidence flow with `flow_stop()`, then use
   `generate_test_cases_from_flow(flow_file=...)` to obtain a coverage matrix and
   formal candidates. Only its `已验证` rows can enter the 19-column case JSON;
   review the generated business wording and executable assertions before export.
9. Generate only cases supported by observed page behavior, table data, modal
   content, toast/message text, URL/tab changes, or network evidence.
10. Store generated case JSON files in:

   ```text
   test_cases/<LEVEL1_PINYIN>_<MODULE_PINYIN>/
   ```

11. Export Excel using the existing upstream exporter:

   ```bash
   uv run python .claude/skills/test-case-generator-dp/scripts/generate_from_json.py test_cases/<LEVEL1_PINYIN>_<MODULE_PINYIN>/*.json
   ```

Use `uv run python`, not bare `python`, because this project environment does
not guarantee a `python` executable on PATH.

## Execution And Regression

Every formal case JSON MUST include an explicit `automation_recipe` with the
complete observed flow and at least one business assertion. A successful tool
return (`ok=true`) is not a business assertion. Destructive steps are permitted
only after explicit user authorization and MUST include deterministic cleanup.
Execute every formal case at least once through `run_test_cases(case_file=...)`
before delivery; a skipped or failed trial is not reproducible automation and
must stay outside the formal deliverable until fixed. The returned execution JSON
must use evidence and screenshots captured in that execution, not exploration-time
artifacts. Generate a Markdown report with
`generate_test_report(execution_file=..., coverage_file=..., baseline_file=...)`.
Use `compare_regression_report` for a direct current versus baseline comparison.
Do not overwrite a historical baseline automatically.

For role/department/permission and approval-flow regression, keep each account
in an isolated BrowserContext and execute actors sequentially:

```text
role_session_start -> role_session_activate
```

Use stable role IDs such as `requester`, `dept_manager`, and
`finance_approver`. Credentials must be supplied through the derived
`HL_ROLE_<ROLE_ID>_USERNAME` and `HL_ROLE_<ROLE_ID>_USERPWD`
environment variables. Never place passwords in a recipe, test case, skill, or
MCP JSON file. Activate the required role before every actor-specific business
step, and close all role sessions in `cleanup`.

Known-defect reproduction must never count as a normal pass. Mark an executable
known-defect case as `xfailed`, or keep it as evidence plus a defect sidecar when
the product fix path would make deterministic cleanup impossible. Such a case
must be excluded from the repeatable formal suite until setup and both cleanup
branches are safe.

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
- Report coverage by explicit requirement/risk/asset totals. Never derive a high
  percentage from only the generated cases while silently omitting page assets.

## Current Boundary

This adapter keeps browser operation, role isolation, evidence capture,
execution and reporting in the single `mcp-service` implementation exposed as
`drissionpage-mcp`; the skill owns coverage policy and user-facing Chinese
business wording. No legacy MCP command or alternate implementation is
supported.
