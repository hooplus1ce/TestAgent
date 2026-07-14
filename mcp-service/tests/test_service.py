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
    assert len(tools) == 90
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


def test_default_runtime_paths_use_workspace_env_and_service_config():
    from drissionpage_mcp.core import config

    if "DRISSIONPAGE_MCP_CONFIG_DIR" not in os.environ:
        assert config.CONFIG_DIR == config.SERVICE_ROOT / "configs"
    if "DRISSIONPAGE_MCP_ENV_FILE" not in os.environ:
        assert config.ENV_FILE == config.DEFAULT_WORKSPACE_ROOT / ".env"
    assert config.DEFAULT_WORKSPACE_ROOT == config.SERVICE_ROOT.parent
    assert config.DP_CONFIG_PATH.is_file()


def test_module_entry_sets_service_working_directory(monkeypatch, tmp_path):
    from drissionpage_mcp import __main__, server
    from drissionpage_mcp.core import config

    started_from = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(server, "main", lambda: started_from.append(os.getcwd()))

    __main__.main()

    assert started_from == [str(config.SERVICE_ROOT)]
