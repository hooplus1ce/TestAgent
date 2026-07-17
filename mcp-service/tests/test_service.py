import asyncio
import os


def _template_uri(template) -> str:
    """FastMCP 3 uses uri_template; older SDK used uriTemplate."""
    return (
        getattr(template, "uri_template", None)
        or getattr(template, "uriTemplate", None)
        or ""
    )


def test_registered_catalog_matches_the_source_service():
    from drissionpage_mcp import server
    from drissionpage_mcp.core import tool_metadata

    tools = asyncio.run(server.mcp.list_tools())
    resources = asyncio.run(server.mcp.list_resources())
    templates = asyncio.run(server.mcp.list_resource_templates())

    tool_names = {tool.name for tool in tools}
    grouped_tools = {
        tool for group in tool_metadata.CAP_GROUPS.values() for tool in group
    }
    assert len(tools) == len(grouped_tools)
    assert tool_names == grouped_tools
    assert {"role_session_open", "role_session_login", "run_js", "click_xy"} <= tool_names
    # Public surface is fully FileSystemProvider-backed; catalog must still match CAP_GROUPS.
    assert {
        "detect_page_family",
        "connect",
        "query_table",
        "table_action",
        "flow_start",
        "run_test_cases",
        "click",
        "explore_action",
        "run_js",
        "browser_tabs",
        "activate_tool_groups",
    } <= tool_names
    assert {str(resource.uri) for resource in resources} == {
        "drissionpage-mcp://caps",
        "drissionpage-mcp://context",
        "drissionpage-mcp://resources",
    }
    assert len(templates) == 1
    assert "drissionpage-mcp://resources/{resource_path}" in {
        _template_uri(t) for t in templates
    }


def test_server_uses_standalone_fastmcp():
    """Import path must be standalone fastmcp 3.x, with public version."""
    import importlib.metadata
    from drissionpage_mcp import server

    assert server.FastMCP.__module__.startswith("fastmcp")
    assert getattr(server.mcp, "version", None) == server.__version__
    assert importlib.metadata.version("fastmcp").split(".")[0] >= "3"


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


def test_remote_address_resolution_prefers_environment_over_ini(tmp_path):
    from drissionpage_mcp.core import config

    ini_path = tmp_path / "dp_configs.ini"
    ini_path.write_text(
        "[chromium_options]\naddress = browser.internal:9223\n",
        encoding="utf-8",
    )

    assert config.resolve_remote_address(ini_path, {}) == "browser.internal:9223"
    assert config.resolve_remote_address(
        ini_path, {"HL_REMOTE_PORT": "9333"}
    ) == "browser.internal:9333"
    assert config.resolve_remote_address(
        ini_path,
        {"HL_REMOTE_PORT": "9333", "HL_REMOTE_ADDRESS": "127.0.0.1:9444"},
    ) == "127.0.0.1:9444"


def test_remote_address_resolution_uses_dotenv_without_overriding_system_env(tmp_path, monkeypatch):
    from drissionpage_mcp.core import config

    env_path = tmp_path / ".env"
    env_path.write_text("HL_REMOTE_ADDRESS=dotenv-host:9224\n", encoding="utf-8")
    monkeypatch.delenv("HL_REMOTE_ADDRESS", raising=False)
    assert config._load_env_file(env_path)
    assert os.environ["HL_REMOTE_ADDRESS"] == "dotenv-host:9224"

    monkeypatch.setenv("HL_REMOTE_ADDRESS", "system-host:9555")
    assert config._load_env_file(env_path)
    assert os.environ["HL_REMOTE_ADDRESS"] == "system-host:9555"


def test_browser_session_applies_resolved_remote_address(monkeypatch):
    from drissionpage_mcp.core import config
    from drissionpage_mcp.services import browser_session

    created_options = []

    class FakeOptions:
        def __init__(self, **_kwargs):
            self.address = None
            created_options.append(self)

        def set_address(self, address):
            self.address = address

    class FakeTab:
        url = "https://example.test/"
        title = "example"

    class FakeBrowser:
        def __init__(self, _options):
            self.latest_tab = FakeTab()

    monkeypatch.setattr(config, "REMOTE_ADDRESS", "127.0.0.1:9223")
    monkeypatch.setattr(config, "DEFAULT_PORT", 9223)
    monkeypatch.setattr(config, "CHROME_PATH", "")
    monkeypatch.setattr(config, "EDGE_MODE", False)
    monkeypatch.setattr(config, "PROXY", "")
    monkeypatch.setattr(config, "DISABLE_PDF_PREVIEW", False)
    monkeypatch.setattr(config, "REMOVE_TEST_TYPE", False)
    monkeypatch.setattr(config, "HEADLESS", False)
    monkeypatch.setattr(browser_session, "ChromiumOptions", FakeOptions)
    monkeypatch.setattr(browser_session, "Chromium", FakeBrowser)
    monkeypatch.setattr(browser_session, "_browser", None)
    monkeypatch.setattr(browser_session, "_tab", None)
    monkeypatch.setattr(browser_session, "_address", "127.0.0.1:9223")
    monkeypatch.setattr(browser_session, "_port", 9223)

    browser_session.connect()

    assert created_options[0].address == "127.0.0.1:9223"


def test_module_entry_sets_service_working_directory(monkeypatch, tmp_path):
    from drissionpage_mcp import __main__, server
    from drissionpage_mcp.core import config

    started_from = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(server, "main", lambda: started_from.append(os.getcwd()))

    __main__.main()

    assert started_from == [str(config.SERVICE_ROOT)]
