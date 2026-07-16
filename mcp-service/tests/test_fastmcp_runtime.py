import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from fastmcp import Client, FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from fastmcp.server.middleware.timing import DetailedTimingMiddleware
from fastmcp.server.providers import FileSystemProvider


def test_public_tools_have_capability_risk_and_level_tags():
    from drissionpage_mcp import server

    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}

    assert {"cap:core", "risk:read", "level:facade"} <= tools[
        "check_session"
    ].tags
    assert {"cap:core", "risk:write", "level:facade"} <= tools["connect"].tags
    assert {"cap:core", "risk:read"} <= tools["detect_layer_msg"].tags
    assert {"cap:filter", "risk:write"} <= tools["select_option"].tags
    assert {"cap:network", "risk:write"} <= tools["network_trace_start"].tags
    assert {"cap:observe", "risk:write"} <= tools["observe_start"].tags
    assert {"cap:storage", "risk:read"} <= tools["list_contexts"].tags
    assert {"cap:devtools", "level:primitive"} <= tools["run_js"].tags
    assert {"cap:legacy", "domain:page-model"} <= tools[
        "detect_page_family"
    ].tags


def test_runtime_governance_uses_safe_middleware_and_wait_timeouts():
    from drissionpage_mcp import server

    middleware = server.mcp.middleware
    assert any(isinstance(item, ErrorHandlingMiddleware) for item in middleware)
    assert any(isinstance(item, ResponseLimitingMiddleware) for item in middleware)
    structured = next(
        item for item in middleware if isinstance(item, StructuredLoggingMiddleware)
    )
    assert structured.include_payloads is False
    assert any(isinstance(item, DetailedTimingMiddleware) for item in middleware)

    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    assert tools["listen_wait"].timeout == 45.0
    assert tools["listen_ws_wait"].timeout == 45.0
    assert tools["observe_wait"].timeout == 45.0
    assert tools["run_test_cases"].timeout is None


def test_run_test_cases_reports_progress_without_changing_sync_api(monkeypatch):
    from drissionpage_mcp import server
    from drissionpage_mcp.workflows import recipe_execution

    expected = {
        "ok": True,
        "counts": {"passed": 2, "failed": 0, "xfailed": 0, "skipped": 0},
    }
    # Public MCP tool dispatches into recipe_execution; patch the shipped implementation.
    monkeypatch.setattr(recipe_execution, "run_test_cases", lambda *_args: expected)
    monkeypatch.setattr(server, "run_test_cases", lambda *_args: expected)
    progress = []

    async def collect(current, total, message):
        progress.append((current, total, message))

    async def run():
        async with Client(server.mcp, progress_handler=collect) as client:
            return await client.call_tool(
                "run_test_cases",
                {"case_file": "cases.json"},
            )

    result = asyncio.run(run())
    assert result.structured_content == expected
    assert [(item[0], item[1]) for item in progress] == [(0.0, 100.0), (100.0, 100.0)]
    assert "通过 2" in progress[-1][2]


def test_search_discovery_reduces_catalog_and_supports_chinese_queries():
    script = r'''
import asyncio
import json
from fastmcp import Client
from drissionpage_mcp import server

async def main():
    async with Client(server.mcp) as client:
        tools = await client.list_tools()
        chinese = await client.call_tool("search_tools", {"pattern": "表格"})
        exact = await client.call_tool("search_tools", {"pattern": "query_table"})
        print(json.dumps({
            "names": [tool.name for tool in tools],
            "chinese_search": chinese.content[0].text,
            "exact_search": exact.content[0].text,
        }, ensure_ascii=False))

asyncio.run(main())
'''
    env = os.environ.copy()
    env.update({
        "DRISSIONPAGE_MCP_DISCOVERY": "search",
        "DRISSIONPAGE_MCP_COMPONENT_RELOAD": "false",
        "DRISSIONPAGE_MCP_WARMUP_OCR": "false",
    })
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert len(payload["names"]) == 11
    assert {"search_tools", "call_tool", "detect_page_family"} <= set(
        payload["names"]
    )
    assert '"name":"' in payload["chinese_search"]
    assert "query_table" in payload["exact_search"]


def test_session_visibility_activates_only_selected_capabilities():
    from drissionpage_mcp import server

    async def run():
        async with Client(server.mcp) as client:
            before = {tool.name for tool in await client.list_tools()}
            await client.call_tool(
                "activate_tool_groups",
                {"groups": ["legacy", "filter", "observe"]},
            )
            after = {tool.name for tool in await client.list_tools()}
            return before, after

    before, after = asyncio.run(run())
    assert len(after) < len(before)
    assert {"scan_layer_content", "query_table", "activate_tool_groups"} <= after
    assert {"network_trace_start", "role_session_start", "run_js"}.isdisjoint(after)


def test_filesystem_provider_discovers_new_tool_without_reconnecting(tmp_path):
    components = tmp_path / "components"
    components.mkdir()
    (components / "first.py").write_text(
        "from fastmcp.tools import tool\n"
        "@tool\n"
        "def first_tool(): return {'ok': True}\n",
        encoding="utf-8",
    )
    provider = FileSystemProvider(Path(components), reload=True)
    server = FastMCP("reload-test", providers=[provider])

    async def run():
        async with Client(server) as client:
            before = {tool.name for tool in await client.list_tools()}
            (components / "second.py").write_text(
                "from fastmcp.tools import tool\n"
                "@tool\n"
                "def second_tool(): return {'ok': True}\n",
                encoding="utf-8",
            )
            after = {tool.name for tool in await client.list_tools()}
            return before, after

    before, after = asyncio.run(run())
    assert before == {"first_tool"}
    assert after == {"first_tool", "second_tool"}
