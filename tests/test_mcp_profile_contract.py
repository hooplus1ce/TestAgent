import json
from pathlib import Path
import re
import tomllib

from drissionpage_mcp.core import caps


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SUGGESTION = (
    "历史用例尚无已验证的 automation_recipe；须使用 enterprise facade 重新探索并试运行后补充"
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


def test_agent_configs_pin_enterprise_profile():
    for relative in (".mcp.json", ".mcp.drissionpage-mcp.json", ".trae/mcp.json"):
        payload = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        server = payload["mcpServers"]["drissionpage-mcp"]
        assert server["env"]["DRISSIONPAGE_MCP_PROFILE"] == "enterprise"

    with (ROOT / ".codex/config.toml").open("rb") as config_file:
        config = tomllib.load(config_file)
    assert config["mcp_servers"]["drissionpage-mcp"]["env"]["DRISSIONPAGE_MCP_PROFILE"] == "enterprise"


def test_skills_do_not_name_hidden_mcp_tools_as_calls():
    hidden = _all_profile_tools() - caps.ENTERPRISE_TOOLS
    violations = {}
    for root in (
        ROOT / ".agents/skills/test-case-generator-dp",
        ROOT / ".claude/skills/test-case-generator-dp",
    ):
        for path in root.rglob("*.md"):
            tokens = set(re.findall(
                r"`([a-z][a-z0-9_]+)(?:\([^`]*\))?`",
                path.read_text(encoding="utf-8"),
            ))
            found = sorted(tokens & hidden)
            if found:
                violations[str(path.relative_to(ROOT))] = found
    assert violations == {}


def test_legacy_cases_cannot_recommend_hidden_or_unverified_automation():
    hidden = _all_profile_tools() - caps.ENTERPRISE_TOOLS
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
            named_tools = set(re.findall(r"\b[a-z][a-z0-9_]+\b", suggestion))
            forbidden = sorted(named_tools & hidden)
            if forbidden:
                violations.append((str(path.relative_to(ROOT)), "hidden", forbidden))
    assert violations == []


def test_unknown_profile_falls_back_to_enterprise(monkeypatch):
    monkeypatch.setenv("DRISSIONPAGE_MCP_PROFILE", "typo")
    assert caps.get_enabled_profile() == "enterprise"
