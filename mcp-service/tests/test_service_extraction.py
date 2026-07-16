"""Structural checks that shipped entrypoints dispatch into extracted modules."""

import inspect

from drissionpage_mcp import server
from drissionpage_mcp.services import devtools, interaction, page_scan, table_facade
from drissionpage_mcp.workflows import recipe_execution


def test_server_table_wrappers_call_table_facade():
    assert "table_facade" in inspect.getsource(server.query_table)
    assert "table_facade" in inspect.getsource(server.click_table_cell)
    assert table_facade.query_table.__module__.endswith("table_facade")


def test_server_interaction_wrappers_call_interaction_service():
    assert "interaction" in inspect.getsource(server.explore_action)
    assert "interaction" in inspect.getsource(server.enter_module)
    assert interaction.explore_action.__module__.endswith("interaction")


def test_server_workflow_wrappers_call_recipe_execution():
    assert "recipe_execution" in inspect.getsource(server.run_test_cases)
    assert "recipe_execution" in inspect.getsource(server.generate_test_report)
    assert recipe_execution.run_test_cases.__module__.endswith("recipe_execution")


def test_server_devtools_and_page_wrappers_call_extracted_modules():
    assert "devtools" in inspect.getsource(server.run_js)
    assert "devtools" in inspect.getsource(server.browser_tabs)
    assert "page_scan" in inspect.getsource(server.dom_tree)
    assert "page_scan" in inspect.getsource(server.dom_overview)
    assert devtools.browser_tabs.__module__.endswith("devtools")
    assert page_scan.dom_tree.__module__.endswith("page_scan")


def test_server_flow_and_storage_wrappers_call_extracted_modules():
    from drissionpage_mcp.services import browser_context
    from drissionpage_mcp.workflows import flow_ops

    assert "flow_ops" in inspect.getsource(server.flow_start)
    assert "flow_ops" in inspect.getsource(server.flow_capture_page_state)
    assert "browser_context" in inspect.getsource(server.set_permission)
    assert "browser_context" in inspect.getsource(server.new_context)
    assert flow_ops.flow_start.__module__.endswith("flow_ops")
    assert browser_context.set_permission.__module__.endswith("browser_context")
