import json
from pathlib import Path
import tomllib

from drissionpage_mcp.core import caps


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SUGGESTION = (
    "历史用例尚无已验证的 automation_recipe；须使用当前完整 MCP 工具集重新探索并试运行后补充"
)


def _all_profile_tools() -> set[str]:
    return {tool for tools in caps.CAP_GROUPS.values() for tool in tools}


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def test_enterprise_profile_is_small_and_every_tool_is_grouped():
    assert len(caps.ENTERPRISE_TOOLS) == 31
    assert len(caps.ENTERPRISE_TOOLS) <= 32
    assert caps.ENTERPRISE_TOOLS <= _all_profile_tools()


def test_all_agent_configs_pin_full_profile():
    for relative in (".mcp.json", ".mcp.drissionpage-mcp.json", ".trae/mcp.json"):
        payload = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        server = payload["mcpServers"]["drissionpage-mcp"]
        assert server["env"]["DRISSIONPAGE_MCP_PROFILE"] == "full"

    with (ROOT / ".codex/config.toml").open("rb") as config_file:
        config = tomllib.load(config_file)
    assert config["mcp_servers"]["drissionpage-mcp"]["env"]["DRISSIONPAGE_MCP_PROFILE"] == "full"


def test_codex_mcp_entry_runs_package_directly_from_service_project():
    config_path = ROOT / ".codex/config.toml"
    with config_path.open("rb") as config_file:
        server = tomllib.load(config_file)["mcp_servers"]["drissionpage-mcp"]

    assert "cwd" not in server
    assert server["args"] == [
        "run", "--project", "mcp-service", "python", "-m", "drissionpage_mcp",
    ]
    assert "launcher.py" not in server["args"]
    assert (ROOT / "mcp-service/pyproject.toml").is_file()
    assert (ROOT / "mcp-service/src/drissionpage_mcp/__main__.py").is_file()


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
    assert caps.get_enabled_profile() == "full"
    monkeypatch.setenv("DRISSIONPAGE_MCP_PROFILE", "typo")
    assert caps.get_enabled_profile() == "full"
