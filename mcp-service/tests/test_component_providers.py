"""Smoke tests for FileSystemProvider-backed component groups."""

import asyncio


def test_component_provider_groups_are_mounted():
    from drissionpage_mcp import server

    # Full profile should mount all domain providers used by public tools.
    assert len(server.component_providers) >= 10

    tools = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert {
        "scan_filter_fields",
        "select_option",
        "new_context",
        "switch_context",
        "close_context",
        "list_contexts",
        "set_permission",
        "listen_start",
        "listen_wait",
        "listen_stop",
        "network_record_start",
        "network_record_stop",
        "network_trace_start",
        "network_trace_stop",
        "network_record_export",
        "listen_ws_start",
        "listen_ws_wait",
        "observe_start",
        "observe_wait",
        "observe_snapshot",
        "close_modal",
        "query_table",
        "scan_table",
        "table_action",
        "flow_start",
        "run_test_cases",
        "generate_test_report",
        "capture_page_model",
        "click",
        "enter_module",
        "explore_action",
        "run_js",
        "browser_tabs",
    } <= tools


def test_network_listen_helpers_live_in_service_layer():
    from drissionpage_mcp.services import network_record

    for name in (
        "listen_start",
        "listen_wait",
        "listen_stop",
        "listen_ws_start",
        "listen_ws_wait",
        "start",
        "stop",
        "export",
    ):
        assert callable(getattr(network_record, name))
