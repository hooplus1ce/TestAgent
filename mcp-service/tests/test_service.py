import asyncio
import os


def test_registered_catalog_matches_the_source_service():
    from drissionpage_mcp import server
    from drissionpage_mcp.core import caps

    tools = asyncio.run(server.mcp.list_tools())
    resources = asyncio.run(server.mcp.list_resources())
    templates = asyncio.run(server.mcp.list_resource_templates())

    tool_names = {tool.name for tool in tools}
    grouped_tools = {tool for group in caps.CAP_GROUPS.values() for tool in group}
    assert len(tools) == 87
    assert tool_names == grouped_tools
    assert {"role_session_open", "role_session_login", "run_js", "click_xy"} <= tool_names
    assert {str(resource.uri) for resource in resources} == {
        "drissionpage-mcp://caps",
        "drissionpage-mcp://context",
        "drissionpage-mcp://resources",
    }
    assert len(templates) == 1


def test_bundled_browser_assets_are_available():
    from drissionpage_mcp.services import browser_session

    assert "scanInteractiveControls" in browser_session.load_js("element-scan.js")


def test_default_browser_config_and_launcher_are_service_local():
    from drissionpage_mcp.core import config

    if "DRISSIONPAGE_MCP_CONFIG_DIR" not in os.environ:
        assert config.CONFIG_DIR == config.SERVICE_ROOT / "configs"
    assert config.DP_CONFIG_PATH.is_file()
    assert (config.SERVICE_ROOT / "launcher.py").is_file()
