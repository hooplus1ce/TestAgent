import json
from pathlib import Path
import tomllib

from drissionpage_mcp.core import tool_metadata


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SUGGESTION = (
    "历史用例尚无已验证的 automation_recipe；须使用当前完整 MCP 工具集重新探索并试运行后补充"
)


def _all_profile_tools() -> set[str]:
    return {
        tool for tools in tool_metadata.CAP_GROUPS.values() for tool in tools
    }


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def test_enterprise_profile_is_small_and_every_tool_is_grouped():
    assert len(tool_metadata.ENTERPRISE_TOOLS) < len(_all_profile_tools()) / 2
    assert tool_metadata.ENTERPRISE_TOOLS <= _all_profile_tools()


def test_all_agent_configs_share_workspace_entry_and_full_profile():
    expected_args = [
        "run",
        "--package",
        "drissionpage-mcp",
        "-m",
        "drissionpage_mcp",
    ]
    for relative in (".mcp.json", ".trae/mcp.json"):
        payload = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        server = payload["mcpServers"]["drissionpage-mcp"]
        assert server["env"]["DRISSIONPAGE_MCP_PROFILE"] == "full"
        assert server["env"]["DRISSIONPAGE_MCP_WARMUP_OCR"] in {"true", "false"}
        assert server["env"]["DRISSIONPAGE_MCP_COMPONENT_RELOAD"] == "true"
        assert server["env"]["DRISSIONPAGE_MCP_DISCOVERY"] == "search"
        assert server["env"]["DRISSIONPAGE_MCP_OBSERVABILITY"] == "true"
        assert server["args"] == expected_args
        assert "HL_USERNAME" not in server["env"]
        assert "HL_USERPWD" not in server["env"]

    with (ROOT / ".codex/config.toml").open("rb") as config_file:
        server = tomllib.load(config_file)["mcp_servers"]["drissionpage-mcp"]
    assert server["env"]["DRISSIONPAGE_MCP_PROFILE"] == "full"
    assert server["env"]["DRISSIONPAGE_MCP_WARMUP_OCR"] in {"true", "false"}
    assert server["env"]["DRISSIONPAGE_MCP_COMPONENT_RELOAD"] == "true"
    assert server["env"]["DRISSIONPAGE_MCP_DISCOVERY"] == "search"
    assert server["env"]["DRISSIONPAGE_MCP_OBSERVABILITY"] == "true"
    assert server["tool_timeout_sec"] == 600
    assert server["args"] == expected_args


def test_codex_mcp_entry_runs_stable_server_with_component_reload():
    config_path = ROOT / ".codex/config.toml"
    with config_path.open("rb") as config_file:
        server = tomllib.load(config_file)["mcp_servers"]["drissionpage-mcp"]

    assert "cwd" not in server
    assert server["args"] == [
        "run", "--package", "drissionpage-mcp", "-m", "drissionpage_mcp",
    ]
    assert server["env"]["DRISSIONPAGE_MCP_COMPONENT_RELOAD"] == "true"
    assert (ROOT / "mcp-service/pyproject.toml").is_file()
    assert (ROOT / "mcp-service/src/drissionpage_mcp/__main__.py").is_file()
    assert not (ROOT / "mcp-service/launcher.py").exists()


def test_uv_workspace_owns_the_only_lockfile():
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)

    assert project["tool"]["uv"]["workspace"]["members"] == ["mcp-service"]
    assert project["tool"]["uv"]["sources"]["drissionpage-mcp"] == {
        "workspace": True,
    }
    assert (ROOT / "uv.lock").is_file()
    assert not (ROOT / "mcp-service/uv.lock").exists()


def test_legacy_cases_cannot_recommend_unverified_automation():
    violations = []
    for path in (ROOT / "test_cases").rglob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in _walk(payload):
            suggestion = item.get("automation_suggestion")
            if not isinstance(suggestion, str):
                continue
            if "automation_recipe" not in item:
                if suggestion != LEGACY_SUGGESTION:
                    violations.append((str(path.relative_to(ROOT)), "unverified", suggestion))
                continue
    assert violations == []


def test_default_and_unknown_profile_use_full(monkeypatch):
    monkeypatch.delenv("DRISSIONPAGE_MCP_PROFILE", raising=False)
    assert tool_metadata.get_enabled_profile() == "full"
    monkeypatch.setenv("DRISSIONPAGE_MCP_PROFILE", "typo")
    assert tool_metadata.get_enabled_profile() == "full"
