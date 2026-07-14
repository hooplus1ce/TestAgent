"""server.py 测试：公开工具面 + synchronized 串行化。"""
import asyncio
import json
import os
import subprocess
import sys
import urllib.parse
from types import SimpleNamespace
import threading
import time
from unittest.mock import MagicMock, patch


FULL_TOOL_COUNT = 88

REMOVED_DUPLICATE_TOOLS = {
    "login_ocr",
    "expand_filter_area",
    "reset_to_initial",
    "dom_overview",
    "find_static",
    "get_frame",
    "mount_vtable",
    "scan_vtable_columns",
    "get_column_values",
    "get_cell_rect",
    "scroll_to_cell",
    "click_cell",
    "resize_column",
    "detect_modal",
    "observe_post_click",
    "detect_notification",
    "detect_message",
    "detect_url_change",
    "detect_tab_change",
    "scan_modal",
    "scan_drawer",
    "scan_floats",
    "scan_html_table",
    "get_html_table_values",
    "click_html_table_cell",
    "hover_html_table_cell",
    "get_html_table_data",
}


def _tool_names():
    from drissionpage_mcp import server
    tools = asyncio.run(server.mcp.list_tools())
    return {t.name for t in tools}


def test_public_tool_surface():
    """默认 full profile 必须公开所有 capability 分组中的工具。"""
    from drissionpage_mcp.core import caps

    names = _tool_names()
    grouped_tools = {tool for tools in caps.CAP_GROUPS.values() for tool in tools}
    assert len(names) == FULL_TOOL_COUNT
    assert names == grouped_tools
    assert {"run_js", "click_xy", "role_session_open", "role_session_login"} <= names


def test_public_tools_are_grouped_in_caps():
    """公开工具必须进入 capability 分组，避免 tools/list 与能力说明不一致。"""
    from drissionpage_mcp.core import caps
    grouped_tools = {
        tool
        for tools in caps.CAP_GROUPS.values()
        for tool in tools
    }
    assert _tool_names() == grouped_tools


def test_public_tools_include_mcp_annotations():
    """关键工具应暴露 MCP tool annotations，帮助客户端区分只读/写入风险。"""
    from drissionpage_mcp import server
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}

    assert tools["find_elements"].annotations.readOnlyHint is True
    assert tools["find_elements"].annotations.destructiveHint is False
    assert tools["observe_snapshot"].annotations.readOnlyHint is True
    assert tools["observe_snapshot"].annotations.destructiveHint is False
    assert tools["query_table"].annotations.readOnlyHint is True
    assert tools["inspect_table_cell"].annotations.readOnlyHint is True
    assert tools["explore_action"].annotations.readOnlyHint is False
    assert tools["explore_action"].annotations.destructiveHint is True
    assert tools["connect"].annotations.readOnlyHint is False
    assert tools["connect"].annotations.destructiveHint is False
    assert tools["connect"].annotations.idempotentHint is True
    assert tools["screenshot"].annotations.destructiveHint is False
    for name in ("network_trace_start", "network_trace_stop", "role_session_start"):
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.destructiveHint is False
    assert server.listen_wait._du_access == "write"
    assert server.listen_ws_wait._du_access == "write"


def test_resources_and_templates_are_exposed():
    """MCP resources 应暴露 caps/context 和证据文件读取模板。"""
    from drissionpage_mcp import server
    resources = asyncio.run(server.mcp.list_resources())
    resource_uris = {str(r.uri) for r in resources}

    assert {
        "drissionpage-mcp://caps",
        "drissionpage-mcp://context",
        "drissionpage-mcp://resources",
    } <= resource_uris

    templates = asyncio.run(server.mcp.list_resource_templates())
    template_uris = {t.uriTemplate for t in templates}
    assert "drissionpage-mcp://resources/{resource_path}" in template_uris


def test_caps_resource_returns_json():
    from drissionpage_mcp import server
    contents = asyncio.run(server.mcp.read_resource("drissionpage-mcp://caps"))
    data = json.loads(contents[0].content)

    from drissionpage_mcp.core import caps

    assert data["profile"] == "full"
    assert set(data["exposed_tools"]) == {
        tool for tools in caps.CAP_GROUPS.values() for tool in tools
    }
    assert data["enabled"]
    assert "core" in data["available"]


def test_evidence_resource_template_reads_encoded_nested_file(monkeypatch, tmp_path):
    from drissionpage_mcp.resources import resource_store
    from drissionpage_mcp import server
    monkeypatch.setattr(resource_store.config, "SHOT_DIR", str(tmp_path))
    nested = tmp_path / "生产动态表"
    nested.mkdir()
    (nested / "dom.yml").write_text("tag: body", encoding="utf-8")
    encoded = urllib.parse.quote("生产动态表/dom.yml", safe="")

    contents = asyncio.run(server.mcp.read_resource(f"drissionpage-mcp://resources/{encoded}"))

    assert contents[0].content == "tag: body"


def test_drissionpage_caps_filters_public_tools():
    """DRISSIONPAGE_MCP_CAPS 应实际影响 MCP tools/list，而不只是报告能力分组。"""
    env = os.environ.copy()
    env["DRISSIONPAGE_MCP_PROFILE"] = "enterprise"
    env["DRISSIONPAGE_MCP_CAPS"] = "core"
    script = """
import asyncio, json, sys
from drissionpage_mcp import server
async def main():
    tools = await server.mcp.list_tools()
    print(json.dumps(sorted(t.name for t in tools)))

asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    names = set(json.loads(result.stdout))
    assert {"connect", "observe_snapshot", "find_elements"} <= names
    assert "get_element_coords" not in names
    assert "browser_list_caps" not in names
    assert "scan_floats" not in names
    assert "scan_table" not in names
    assert "run_js" not in names


def test_full_profile_exposes_every_capability_group_exactly():
    """full profile 保留完整调试工具面，并继续受 capability 裁剪。"""
    from drissionpage_mcp.core import caps
    script = """
import asyncio, json, sys
from drissionpage_mcp import server
async def main():
    tools = await server.mcp.list_tools()
    print(json.dumps(sorted(t.name for t in tools)))

asyncio.run(main())
"""
    root = os.path.dirname(os.path.dirname(__file__))
    for capability, grouped_tools in caps.CAP_GROUPS.items():
        env = os.environ.copy()
        env["DRISSIONPAGE_MCP_PROFILE"] = "full"
        env["DRISSIONPAGE_MCP_CAPS"] = capability
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        exposed = set(json.loads(result.stdout))
        expected = set(grouped_tools)
        assert exposed == expected, capability


def test_list_parameters_have_typed_items_schema():
    """常用 list 入参应生成精确 items schema，帮助 Agent 正确构造参数。"""
    from drissionpage_mcp import server
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    checks = [
        ("explore_action", "signals"),
        ("explore_action", "modifiers"),
        ("query_table", "column_titles"),
    ]
    for tool_name, field in checks:
        prop = tools[tool_name].inputSchema["properties"][field]
        assert prop["items"]["type"] == "string"


def test_enterprise_facade_schemas_constrain_choice_parameters():
    from drissionpage_mcp import server

    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    query_properties = tools["query_table"].inputSchema["properties"]
    action_properties = tools["table_action"].inputSchema["properties"]
    explore_properties = tools["explore_action"].inputSchema["properties"]

    assert set(query_properties["operation"]["enum"]) == {"values", "data", "find", "count", "row"}
    assert set(query_properties["kind"]["enum"]) == {"auto", "html", "vtable"}
    assert set(query_properties["match"]["enum"]) == {"equals", "contains"}
    assert set(action_properties["action"]["enum"]) == {
        "click", "double_click", "hover", "drag", "resize",
    }
    assert set(action_properties["target"]["enum"]) == {
        "cell", "header", "header-icon", "cell-icon",
    }
    assert set(explore_properties["action"]["enum"]) == {
        "click", "input", "set_date", "table_cell", "select_option", "press_key",
    }


def test_explore_action_uses_default_observe_signals_without_listen_targets():
    """explore_action 默认观察信号不应依赖未定义局部变量。"""
    from drissionpage_mcp import server
    calls = {}

    with patch.object(server.observe, "observe_start", side_effect=lambda **kwargs: calls.setdefault("observe_start", kwargs) or {"ok": True}), \
         patch.object(server.observe, "observe_wait", return_value={"type": "none"}), \
         patch.object(server, "_pre_click_cleanup", return_value=None), \
         patch.object(server, "_attach_cleanup", side_effect=lambda result, cleanup: result), \
         patch.object(server.browser_session, "get_tab", return_value=SimpleNamespace()):
        result = server.explore_action(action="noop", timeout=0.01)

    assert result["ok"] is False
    assert calls["observe_start"]["signals"] == ["overlay", "notification", "message", "tab", "url"]
    assert calls["observe_start"]["listen_targets"] is None


def test_explore_action_adds_network_signal_when_listen_targets_present():
    """传 listen_targets 时默认观察信号应包含 network。"""
    from drissionpage_mcp import server
    calls = {}

    with patch.object(server.observe, "observe_start", side_effect=lambda **kwargs: calls.setdefault("observe_start", kwargs) or {"ok": True}), \
         patch.object(server.observe, "observe_wait", return_value={"type": "none"}), \
         patch.object(server, "_pre_click_cleanup", return_value=None), \
         patch.object(server, "_attach_cleanup", side_effect=lambda result, cleanup: result), \
         patch.object(server.browser_session, "get_tab", return_value=SimpleNamespace()):
        result = server.explore_action(action="noop", listen_targets="gateway", timeout=0.01)

    assert result["ok"] is False
    assert calls["observe_start"]["signals"] == ["overlay", "notification", "message", "tab", "url", "network"]
    assert calls["observe_start"]["listen_targets"] == "gateway"


def test_explore_action_button_target_resolves_visible_toolbar_action():
    """语义按钮目标应先扫描可见工具栏动作，再用坐标点击，避免命中文本隐藏节点。"""
    from drissionpage_mcp import server
    class FakeActions:
        def __init__(self):
            self.moves = []
            self.clicked = False

        def move_to(self, point, duration=0):
            self.moves.append((point, duration))
            return self

        def click(self):
            self.clicked = True
            return self

    actions = FakeActions()
    fake_tab = SimpleNamespace(actions=actions)
    calls = {}

    with patch.object(server.page_model, "scan_toolbar_actions", return_value={
            "ok": True,
            "actions": [{"text": "添 加", "cx": 356.6, "cy": 117.0}],
         }) as scan, \
         patch.object(server.observe, "observe_start", side_effect=lambda **kwargs: calls.setdefault("observe_start", kwargs) or {"ok": True}), \
         patch.object(server.observe, "observe_wait", side_effect=lambda **kwargs: calls.setdefault("observe_wait", kwargs) or {"type": "modal"}), \
         patch.object(server, "_pre_click_cleanup", return_value=None), \
         patch.object(server, "_attach_cleanup", side_effect=lambda result, cleanup: result), \
         patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        result = server.explore_action(target={"type": "button", "text": "添加"}, timeout=8)

    scan.assert_called_once_with(scope="toolbar", in_frame=True, max_items=160)
    assert actions.moves == [((356.6, 117.0), 0.3)]
    assert actions.clicked is True
    assert result["ok"] is True
    assert result["action"]["action"] == "click_xy"
    assert result["observe_policy"]["expect"] == "modal"
    assert calls["observe_start"]["signals"] == ["modal"]
    assert calls["observe_wait"]["include_snapshot"] is True


def test_explore_action_field_target_infers_calendar_fast_mode():
    """字段目标包含“日期/时间”时，fast 模式应只等待 calendar 且不附加快照。"""
    from drissionpage_mcp import server
    calls = {}

    with patch.object(server.observe, "observe_start", side_effect=lambda **kwargs: calls.setdefault("observe_start", kwargs) or {"ok": True}), \
         patch.object(server.observe, "observe_wait", side_effect=lambda **kwargs: calls.setdefault("observe_wait", kwargs) or {"type": "calendar"}), \
         patch.object(server, "_pre_click_cleanup", return_value=None), \
         patch.object(server, "_attach_cleanup", side_effect=lambda result, cleanup: result), \
         patch.object(server.browser_session, "get_tab", return_value=SimpleNamespace()), \
         patch.object(server, "_click_field_raw", return_value={"ok": True, "action": "field_click", "control_type": "date-picker"}) as click_field:
        result = server.explore_action(
            target={"type": "field", "name": "工作日期"},
            observe_mode="fast",
            timeout=8,
        )

    click_field.assert_called_once()
    assert result["ok"] is True
    assert result["observe_policy"]["expect"] == "calendar"
    assert calls["observe_start"]["signals"] == ["calendar"]
    assert calls["observe_wait"]["timeout"] == 2.0
    assert calls["observe_wait"]["include_snapshot"] is False


def test_explore_action_observe_mode_none_skips_observer():
    """纯操作场景可跳过 observe_start/observe_wait，保留动作执行结果。"""
    from drissionpage_mcp import server
    class FakeActions:
        def __init__(self):
            self.clicked = False

        def move_to(self, point, duration=0):
            return self

        def click(self):
            self.clicked = True
            return self

    actions = FakeActions()
    fake_tab = SimpleNamespace(actions=actions)

    with patch.object(server.observe, "observe_start", side_effect=AssertionError("observe_start should be skipped")), \
         patch.object(server.observe, "observe_wait", side_effect=AssertionError("observe_wait should be skipped")), \
         patch.object(server.caps, "ENABLED_PROFILE", "full"), \
         patch.object(server, "_pre_click_cleanup", return_value=None), \
         patch.object(server, "_attach_cleanup", side_effect=lambda result, cleanup: result), \
         patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        result = server.explore_action(
            action="click_xy",
            x=10,
            y=20,
            observe_mode="none",
        )

    assert actions.clicked is True
    assert result["ok"] is True
    assert result["observe_start"]["session"] == "skipped"
    assert result["signal"]["type"] == "skipped"


def test_enterprise_explore_action_rejects_low_level_or_unobserved_requests():
    from drissionpage_mcp import server

    with patch.object(server.caps, "ENABLED_PROFILE", "enterprise"):
        coordinate = server.explore_action(action="click_xy", x=10, y=20)
        javascript = server.explore_action(action="click", locator="text:保存", by_js=True)
        unobserved = server.explore_action(
            action="input", field_name="单号", text="PO-1", observe_mode="none",
        )

    for result in (coordinate, javascript, unobserved):
        assert result["ok"] is False
        assert result["profile"] == "enterprise"
    assert "显式坐标动作" in coordinate["reason"]
    assert "JS 点击" in javascript["reason"]
    assert "跳过动作观察" in unobserved["reason"]


def test_explore_action_capture_after_disables_snapshot_by_default():
    """capture_after 已会返回 after 模型，默认不再重复附带 snapshot_after。"""
    from drissionpage_mcp import server
    class FakeActions:
        def move_to(self, point, duration=0):
            return self

        def click(self):
            return self

    calls = {}
    fake_tab = SimpleNamespace(actions=FakeActions())

    with patch.object(server.observe, "observe_start", return_value={"ok": True}), \
         patch.object(server.observe, "observe_wait", side_effect=lambda **kwargs: calls.setdefault("observe_wait", kwargs) or {"type": "modal"}), \
         patch.object(server.caps, "ENABLED_PROFILE", "full"), \
         patch.object(server.page_model, "capture_page_model", return_value={"ok": True, "modals": {"count": 1}}), \
         patch.object(server, "_pre_click_cleanup", return_value=None), \
         patch.object(server, "_attach_cleanup", side_effect=lambda result, cleanup: result), \
         patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        result = server.explore_action(
            action="click_xy",
            x=10,
            y=20,
            capture_after=True,
        )

    assert result["ok"] is True
    assert calls["observe_wait"]["include_snapshot"] is False
    assert result["observe_policy"]["include_snapshot"] is False
    assert result["after"] == {"ok": True, "modals": {"count": 1}}


def test_explore_action_routes_text_input_to_standard_input_tool():
    from drissionpage_mcp import server
    fake_tab = SimpleNamespace()
    with patch.object(server.caps, "ENABLED_PROFILE", "full"), \
         patch.object(server.modal, "clear_transient_overlays", return_value={"ok": True, "closed": [], "errors": []}), \
         patch.object(server.browser_session, "get_tab", return_value=fake_tab), \
         patch.object(server, "input", return_value={"ok": True, "locator": "#order"}) as input_tool:
        result = server.explore_action(
            action="input", locator="#order", text="PO20260711", observe_mode="none",
        )

    input_tool.assert_called_once_with("#order", "PO20260711", in_frame=True, timeout=8)
    assert result["ok"] is True
    assert result["action"]["action"] == "input"


def test_drissionpage_observe_wait_compacts_and_dedupes_events():
    """drissionpage-mcp observe_wait 应保留主 payload，但 events 只返回去重摘要。"""
    script = r"""
import json
import sys
import time
from drissionpage_mcp.services import observe
event = {
    "type": "interactive",
    "scope": "iframe",
    "elapsedMs": 12,
    "payload": {
        "type": "interactive",
        "scope": "iframe",
        "title": "添加工资明细",
        "content": "工作日期 生产部门 薪资计算类型",
        "buttons": ["取 消", "确 定"],
        "rect": {"x": 100, "y": 120, "width": 680, "height": 530},
    },
}
events = [dict(event), dict(event)]
def fake_poll(sess, now):
    return events.pop(0) if events else None

with observe._session_lock:
    observe._session.clear()
    observe._session.update({
        "active": True,
        "sigset": {"modal"},
        "start": time.time(),
        "tab": object(),
        "fr": None,
        "watch_network": False,
    })

observe._poll_once = fake_poll
observe._teardown_session = lambda sess: None
observe._COLLECT_WINDOW = 0.01
result = observe.observe_wait(timeout=0.05, poll_interval=0.001, include_snapshot=False)
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)

    assert data["type"] == "interactive"
    assert data["payload"]["title"] == "添加工资明细"
    assert "primary_event" not in data
    assert data["event_count"] == 1
    assert data["events"] == [{
        "type": "interactive",
        "elapsedMs": 12,
        "scope": "iframe",
        "title": "添加工资明细",
        "content": "工作日期 生产部门 薪资计算类型",
    }]


def test_drissionpage_set_date_tool_and_date_normalization():
    """drissionpage-mcp 应只提供统一日期字段设置工具。"""
    script = r"""
import asyncio
import json
import sys
from drissionpage_mcp import server
async def main():
    tools = await server.mcp.list_tools()
    data = {
        "has_explore_action": "explore_action" in {tool.name for tool in tools},
        "has_set_date": "set_date" in {tool.name for tool in tools},
        "has_select_date_range": "select_date_range" in {tool.name for tool in tools},
        "set_date_schema": next(tool.inputSchema for tool in tools if tool.name == "set_date"),
        "dash": server._normalize_date_value("2026-06-01"),
        "slash": server._normalize_date_value("2026/06/01"),
        "invalid": server._normalize_date_value("2026.06.01"),
    }
    print(json.dumps(data, ensure_ascii=False, sort_keys=True))

asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)

    assert data["has_explore_action"] is True
    assert data["has_set_date"] is True
    assert data["has_select_date_range"] is False
    assert {"date", "start_date", "end_date"} <= set(data["set_date_schema"]["properties"])
    assert data["dash"] == {
        "ok": True,
        "dash": "2026-06-01",
        "slash": "2026/06/01",
        "year": 2026,
        "month": 6,
        "day": 1,
    }
    assert data["slash"]["dash"] == "2026-06-01"
    assert data["invalid"]["ok"] is False


def test_resolve_date_picker_targets_quick_filter_value_control():
    """Quick Filter 日期字段必须跳过字段名/操作符下拉，定位第三段日期值控件。"""
    from drissionpage_mcp import server

    visible = SimpleNamespace(is_displayed=True)
    input_element = SimpleNamespace(states=visible)

    class Picker:
        states = visible

        def eles(self, locator, timeout=None):
            if locator == "css:input.ant-calendar-range-picker-input":
                return []
            return [input_element]

    picker = Picker()

    class Column:
        def eles(self, locator, timeout=None):
            assert locator == "css:.ant-calendar-picker,.ant-picker"
            return [picker]

    frame = object()
    tab = object()
    with patch.object(server, "_date_field_contexts", return_value=(tab, [("iframe", frame)], ["filter"])), \
         patch.object(server.filter_area, "_quick_filter_field_column", return_value=(Column(), [object(), object()])):
        result = server._resolve_date_picker("创建时间", scope="filter")

    assert result["ok"] is True
    assert result["picker"] is picker
    assert result["target"] is frame
    assert result["picker_mode"] == "single"
    assert result["component"] == "legions-pro-quick-filter"


def test_set_date_handles_single_and_range_through_one_entry():
    from drissionpage_mcp import server

    target = SimpleNamespace(wait=MagicMock())
    calendar = object()
    picker = object()

    def resolved(mode):
        return {
            "ok": True,
            "picker": picker,
            "picker_mode": mode,
            "component": "ant-design",
            "scope": "iframe",
            "area": "page",
        }

    with patch.object(server, "_resolve_date_picker", side_effect=[resolved("single"), resolved("single")]), \
         patch.object(server, "_date_picker_values", side_effect=[[""], ["2026-07-13"]]), \
         patch.object(server, "_open_date_calendar", return_value=(target, calendar)), \
         patch.object(server, "_calendar_snapshot", return_value={"ok": True, "title": "2026年7月"}), \
         patch.object(server, "_select_calendar_date", return_value={"ok": True, "navigations": []}) as select:
        single = server.set_date("工作日期", date="2026-07-13")

    assert single["ok"] is True
    assert single["picker_mode"] == "single"
    assert single["date"] == "2026-07-13"
    assert select.call_count == 1

    with patch.object(server, "_resolve_date_picker", side_effect=[resolved("range"), resolved("range")]), \
         patch.object(server, "_date_picker_values", side_effect=[["", ""], ["2026-07-01", "2026-07-13"]]), \
         patch.object(server, "_open_date_calendar", return_value=(target, calendar)), \
         patch.object(server, "_find_calendar_root", return_value=calendar), \
         patch.object(server, "_calendar_snapshot", return_value={"ok": True, "title": "2026年7月"}), \
         patch.object(server, "_select_calendar_date", return_value={"ok": True, "navigations": []}) as select:
        date_range = server.set_date(
            "创建时间", start_date="2026/07/01", end_date="2026/07/13",
        )

    assert date_range["ok"] is True
    assert date_range["picker_mode"] == "range"
    assert date_range["startDate"] == "2026-07-01"
    assert date_range["endDate"] == "2026-07-13"
    assert select.call_count == 2


def test_set_date_rejects_distinct_range_for_quick_filter_single_boundary():
    from drissionpage_mcp import server

    resolved = {
        "ok": True,
        "picker": object(),
        "picker_mode": "single",
        "component": "legions-pro-quick-filter",
        "scope": "iframe",
        "area": "filter",
    }
    with patch.object(server, "_resolve_date_picker", return_value=resolved), \
         patch.object(server, "_open_date_calendar") as open_calendar:
        result = server.set_date(
            "创建时间", start_date="2026-07-01", end_date="2026-07-13",
            scope="filter",
        )

    assert result["ok"] is False
    assert "单边界日期控件" in result["reason"]
    open_calendar.assert_not_called()


def test_drissionpage_explore_action_click_reuses_click_fallbacks():
    """drissionpage-mcp explore_action(click) 应复用 click 的定位和降级逻辑。"""
    script = r"""
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch
from drissionpage_mcp import server
server.caps.ENABLED_PROFILE = "full"
class FakeElement:
    def __init__(self):
        self.click_calls = []

    def click(self, **kwargs):
        self.click_calls.append(kwargs)
        if kwargs.get("by_js"):
            raise RuntimeError("direct js failed")
        return False

class FakeTarget:
    def run_js(self, js):
        assert "var needle" in js
        return json.dumps({"ok": True, "tag": "BUTTON", "text": "搜索"})

ele = FakeElement()
with patch.object(server.browser_session, "get_tab", return_value=SimpleNamespace()), \
     patch.object(server.browser_session, "find", return_value=ele) as find, \
     patch.object(server.browser_session, "get_active_frame", return_value=FakeTarget()), \
     patch.object(server, "_pre_click_cleanup", return_value=None):
    result = server.explore_action(
        action="click",
        locator="text:搜索",
        observe_mode="none",
        timeout=5,
    )

print(json.dumps({
    "result": result,
    "find_args": find.call_args.args,
    "find_kwargs": find.call_args.kwargs,
    "click_calls": ele.click_calls,
}, ensure_ascii=False, sort_keys=True))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)

    assert data["result"]["ok"] is True
    assert data["result"]["action"]["action"] == "click"
    assert data["result"]["action"]["fallback"] == "js-text"
    assert data["find_kwargs"] == {"in_frame": True, "timeout": 1.0, "wait_clickable": False}
    assert "normalize-space(.)='搜索'" in data["find_args"][0]
    assert data["click_calls"][0] == {"by_js": False, "timeout": 2.0, "wait_stop": False}


def test_duplicate_tools_removed_from_public_surface():
    """重复/内部 helper 不应再作为 public MCP 工具暴露。"""
    names = _tool_names()
    assert REMOVED_DUPLICATE_TOOLS.isdisjoint(names)


def test_table_facade_registered():
    names = _tool_names()
    assert {
        "scan_table",
        "query_table",
        "inspect_table_cell",
        "table_action",
    } <= names


def test_query_table_routes_by_operation():
    from drissionpage_mcp import server

    with patch.object(server, "get_table_values", return_value={"ok": True, "values": ["A"]}) as values:
        result = server.query_table(operation="values", column_title="单号", kind="auto")

    assert result == {"ok": True, "values": ["A"], "operation": "values"}
    values.assert_called_once_with(
        column_title="单号", kind="auto", raw=False, table_index=0, filename=None,
    )

    invalid = server.query_table(operation="delete")
    assert invalid["ok"] is False
    assert "values/data/find/count/row" in invalid["reason"]


def test_inspect_table_cell_combines_render_and_icons():
    from drissionpage_mcp import server

    with patch.object(server, "get_vtable_cell_render_info", return_value={"ok": True, "text": "已审核"}), \
         patch.object(server, "get_vtable_cell_icons", return_value={"ok": True, "icons": []}):
        result = server.inspect_table_cell(row=2, column_title="状态")

    assert result["ok"] is True
    assert result["render"]["text"] == "已审核"
    assert result["icons"]["icons"] == []


def test_table_action_routes_common_and_advanced_actions():
    from drissionpage_mcp import server

    observation = {
        "observe_start": patch.object(server.observe, "observe_start", return_value={"ok": True}),
        "observe_wait": patch.object(server.observe, "observe_wait", return_value={"type": "none"}),
        "cleanup": patch.object(server, "_pre_click_cleanup", return_value=None),
    }
    with observation["observe_start"], observation["observe_wait"], observation["cleanup"], \
         patch.object(server, "click_table_cell", return_value={"ok": True, "kind": "html"}) as click_cell:
        clicked = server.table_action(
            action="double_click", row=1, column_title="单号", kind="html",
        )
    assert clicked["ok"] is True
    assert clicked["action"] == "double_click"
    assert clicked["result"]["kind"] == "html"
    click_cell.assert_called_once_with(
        row=1, col=None, column_title="单号", kind="html", table_index=0,
        icon_name=None, hover_first=True, duration=0.3, double_click=True,
        clean_overlays=False,
    )

    with patch.object(server.observe, "observe_start", return_value={"ok": True}), \
         patch.object(server.observe, "observe_wait", return_value={"type": "none"}), \
         patch.object(server, "_pre_click_cleanup", return_value=None), \
         patch.object(server, "vtable_action", return_value={"ok": True, "kind": "vtable"}) as action:
        result = server.table_action(
            action="click", row=0, column_title="操作", target="header-icon",
            icon_name="filter",
        )
    assert result["ok"] is True
    action.assert_called_once()


def test_table_action_adds_network_signal_for_interface_assertion():
    from drissionpage_mcp import server

    with patch.object(server, "_pre_click_cleanup", return_value=None), \
         patch.object(server.observe, "observe_start", return_value={"ok": True}) as start, \
         patch.object(server.observe, "observe_wait", return_value={"type": "network"}), \
         patch.object(server, "click_table_cell", return_value={"ok": True}):
        result = server.table_action(
            action="click", row=0, column_title="单号", listen_targets="gateway",
        )

    assert result["ok"] is True
    assert "network" in start.call_args.kwargs["signals"]
    assert start.call_args.kwargs["listen_targets"] == "gateway"


def test_network_trace_facade_reuses_timeline_recorder():
    from drissionpage_mcp import server

    with patch.object(server, "network_record_start", return_value={"ok": True}) as start:
        assert server.network_trace_start(targets="gateway", method="POST")["ok"] is True
    start.assert_called_once_with(targets="gateway", method="POST")

    with patch.object(server, "network_record_stop", return_value={"ok": True, "packets": []}) as stop:
        assert server.network_trace_stop(timeout=1, max_packets=5)["ok"] is True
    stop.assert_called_once_with(
        timeout=1, max_packets=5, fit_count=False, max_body_chars=12000,
    )


def test_scan_table_routes_to_vtable_backend():
    from drissionpage_mcp import server
    with patch.object(server.vtable, "scan_vtable_columns", return_value={"ok": True, "columns": []}) as scan:
        assert server.scan_table(kind="vtable") == {"ok": True, "columns": [], "kind": "vtable"}
        scan.assert_called_once_with(50)


def test_scan_table_routes_to_selected_visible_html_backend():
    from drissionpage_mcp import server
    tables = [{"index": 0}, {"index": 1}]
    with patch.object(server.html_table, "scan_html_table", return_value={"ok": True, "tables": tables}) as scan:
        result = server.scan_table(kind="html", table_index=1)

    assert result == {
        "ok": True, "tables": [{"index": 1}], "kind": "html",
        "table_index": 1, "table_count": 2,
    }
    scan.assert_called_once_with()


def test_scan_table_auto_falls_back_to_html_backend():
    from drissionpage_mcp import server
    with patch.object(server.vtable, "scan_vtable_columns", return_value={"ok": False, "reason": "no vtable"}), \
         patch.object(server.html_table, "scan_html_table", return_value={"ok": True, "tables": [{"index": 0}]}) as scan_html:
        result = server.scan_table(kind="auto")
        assert result["ok"] is True
        assert result["kind"] == "html"
        assert result["fallback_from"] == "vtable"
        assert result["vtable_reason"] == "no vtable"
        scan_html.assert_called_once_with()


def test_get_table_values_routes_to_html_backend_with_scalar_values():
    from drissionpage_mcp import server
    backend = {"ok": True, "values": ["SO-1"], "cells": [{"row": 0, "text": "SO-1"}]}
    with patch.object(server.html_table, "get_html_table_values", return_value=backend) as get_values:
        result = server.get_table_values("订单号", kind="html")

    assert result == {**backend, "kind": "html", "raw": False}
    get_values.assert_called_once_with("订单号", 0)


def test_html_table_facade_rejects_missing_index_and_raw_mode():
    from drissionpage_mcp import server
    with patch.object(server.html_table, "scan_html_table", return_value={"ok": True, "tables": []}):
        missing = server.scan_table(kind="html", table_index=0)
    raw = server.get_table_values("订单号", kind="html", raw=True)
    fractional = server.scan_table(kind="html", table_index=0.5)

    assert missing["ok"] is False
    assert missing["table_count"] == 0
    assert raw["ok"] is False
    assert "raw=true" in raw["reason"]
    assert fractional["ok"] is False
    assert "非负整数" in fractional["reason"]


def test_get_all_table_data_auto_prefers_vtable_backend():
    from drissionpage_mcp import server
    with patch.object(server.page_model.vtable, "scan_vtable_columns", return_value={
        "ok": True,
        "columns": [{"title": "订单号", "col": 1, "bodyBehavior": "none"}],
    }), patch.object(server.page_model.vtable, "get_columns_values", return_value={
        "ok": True,
        "values": {"订单号": ["SO001"]},
    }) as get_values:
        result = server.get_all_table_data(kind="auto")

    assert result["ok"] is True
    assert result["kind"] == "vtable"
    assert result["rows"] == [{"订单号": "SO001"}]
    get_values.assert_called_once_with(["订单号"], raw=False)


def test_vtable_full_read_uses_unique_leaf_headers_and_skips_controls():
    from drissionpage_mcp.services import page_model
    scan = {
        "ok": True,
        "columns": [
            {"col": 0, "row": 0, "title": "_vtable_checkbox", "bodyBehavior": "control:checkbox"},
            {"col": 1, "row": 0, "title": "订单信息", "bodyBehavior": "none"},
            {"col": 1, "row": 1, "title": "订单号", "bodyBehavior": "none"},
            {"col": 2, "row": 0, "title": "订单信息", "bodyBehavior": "none"},
            {"col": 2, "row": 1, "title": "客户", "bodyBehavior": "none"},
        ],
    }
    bulk = {
        "ok": True,
        "values": {"订单号": ["SO-1"], "客户": ["诺贝"]},
        "header_rows": 2,
    }

    with patch.object(page_model.vtable, "scan_vtable_columns", return_value=scan), \
         patch.object(page_model.vtable, "get_columns_values", return_value=bulk) as read_columns:
        result = page_model._read_vtable_rows(max_columns=10, max_rows=10)

    assert result["ok"] is True
    assert [column["title"] for column in result["columns"]] == ["订单号", "客户"]
    assert result["rows"] == [{"订单号": "SO-1", "客户": "诺贝"}]
    read_columns.assert_called_once_with(["订单号", "客户"], raw=False)


def test_vtable_column_resolution_rejects_duplicate_titles():
    from drissionpage_mcp import server
    with patch.object(server.vtable, "scan_vtable_columns", return_value={
        "ok": True,
        "columns": [
            {"col": 4, "title": "操作"},
            {"col": 8, "title": "操作"},
        ],
    }):
        col, reason = server._find_vtable_col("操作")

    assert col is None
    assert "匹配不唯一" in reason


def test_get_all_table_data_rejects_html_raw_mode():
    from drissionpage_mcp import server
    result = server.get_all_table_data(kind="html", raw=True)

    assert result["ok"] is False
    assert result["kind"] == "html"
    assert "raw=true" in result["reason"]


def test_selection_scan_rejects_negative_row_without_mutating_page():
    from drissionpage_mcp import server
    with patch.object(server.page_model, "scan_toolbar_actions") as scan_actions, \
         patch.object(server.vtable, "click_cell") as click_cell:
        result = server.scan_action_availability_by_selection(row=-1)

    assert result["ok"] is False
    assert "非负整数" in result["reason"]
    scan_actions.assert_not_called()
    click_cell.assert_not_called()


def test_selection_scan_does_not_click_when_before_snapshot_fails():
    from drissionpage_mcp import server
    with patch.object(server.page_model, "scan_toolbar_actions", return_value={
        "ok": False, "reason": "scan failed",
    }), patch.object(server.vtable, "click_cell") as click_cell:
        result = server.scan_action_availability_by_selection()

    assert result["ok"] is False
    assert result["mutated_page"] is False
    click_cell.assert_not_called()


def test_html_pagination_rejects_click_without_page_transition():
    from drissionpage_mcp.services import page_model
    class ButtonWait:
        def clickable(self, **kwargs):
            return True

    class Button:
        wait = ButtonWait()

        def click(self, **kwargs):
            return True

    button = Button()

    class Next:
        attrs = {}

        def ele(self, locator, timeout=None):
            return button

    class RootWait:
        def stop_moving(self, **kwargs):
            return True

    class Root:
        wait = RootWait()

        def ele(self, locator, timeout=None):
            if "item-active" in locator:
                return SimpleNamespace(text="1")
            if "pagination-next" in locator:
                return Next()
            return None

    with patch.object(page_model.html_table, "_visible_table_wrappers", return_value=[Root()]):
        result = page_model._click_next_page(object(), table_index=0)

    assert result["ok"] is False
    assert result["page_before"] == "1"
    assert result["page_after"] == "1"
    assert "did not change" in result["reason"]


def test_scan_floats_includes_ant_calendar_by_dom_presence():
    from drissionpage_mcp.services import page_model
    captured_js = []

    def fake_run_json(target, js, default):
        captured_js.append(js)
        return {
            "ok": True,
            "floats": [{
                "type": "calendar",
                "title": "日期选择器 2026年7月",
                "calendar": {"mode": "single", "panels": [], "selectedDates": [], "cells": []},
            }],
        }

    with patch.object(page_model, "_targets", return_value=(object(), None, [("top", object())])), \
         patch.object(page_model, "_run_json", side_effect=fake_run_json), \
         patch.object(page_model.browser_session, "get_active_tab_name", return_value="active"), \
         patch("drissionpage_mcp.services.observe.detect_message", return_value={}), \
         patch("drissionpage_mcp.services.observe.detect_notification", return_value={}):
        result = page_model.scan_floats()

    assert result["count"] == 1
    assert result["floats"][0]["type"] == "calendar"
    assert result["floats"][0]["scope"] == "top"
    assert captured_js
    js = captured_js[0]
    assert ".ant-calendar-picker-container" in js
    assert ".ant-calendar" in js
    assert ".ant-select-dropdown" in js
    assert "function duCalendarActive" in js
    assert "title: title\n  };\n}\nfunction duScanCalendar" in js
    assert "return !!(el && el.isConnected);" in js
    assert "var nodes = nodeList.filter(duKeepFloatNode).slice(0, 100);" in js
    assert "nodeList.filter(duVisible)" not in js
    assert "\x08" not in js
    assert "classList.contains(name)" in js
    assert "duHasClass(n, 'ant-calendar')" in js


def test_scan_form_fields_does_not_fall_back_to_page_for_missing_overlay_scope():
    from drissionpage_mcp.services import page_model
    scripts = []

    def capture(target, js, default):
        scripts.append(js)
        return {"ok": True, "scope": "modal", "rootCount": 0, "total": 0, "fields": []}

    with patch.object(page_model, "_target", return_value=(object(), None, object())), \
         patch.object(page_model, "_run_json", side_effect=capture):
        result = page_model.scan_form_fields(scope="modal")

    assert result["fields"] == []
    assert "if (!roots.length && false) roots = [document.body];" in scripts[0]
    assert "rootCount:roots.length" in scripts[0]


def test_scan_floats_classifies_top_confirm_without_losing_flag():
    from drissionpage_mcp.services import page_model
    def fake_run_json(target, js, default):
        return {"ok": True, "floats": [{"type": "modal", "isConfirm": True}]}

    with patch.object(page_model, "_targets", return_value=(object(), None, [("top", object())])), \
         patch.object(page_model, "_run_json", side_effect=fake_run_json), \
         patch.object(page_model.browser_session, "get_active_tab_name", return_value="active"):
        result = page_model.scan_floats()

    assert result["floats"] == [{
        "type": "modal", "scope": "top", "modalType": "system_confirm",
    }]


def test_scan_floats_includes_visible_vtable_filter_menu_by_display_state():
    from drissionpage_mcp.services import page_model
    captured_js = []

    def fake_run_json(target, js, default):
        captured_js.append(js)
        return {
            "ok": True,
            "floats": [{
                "type": "vtable-filter-menu",
                "title": "VTable列头筛选",
                "vtableFilter": {
                    "display": "block",
                    "activeTab": "按值筛选",
                    "valueCount": 8,
                    "values": [{"value": "MO202607080010", "text": "MO202607080010", "count": "1"}],
                },
            }],
        }

    with patch.object(page_model, "_targets", return_value=(object(), None, [("top", object())])), \
         patch.object(page_model, "_run_json", side_effect=fake_run_json), \
         patch.object(page_model.browser_session, "get_active_tab_name", return_value="active"), \
         patch("drissionpage_mcp.services.observe.detect_message", return_value={}), \
         patch("drissionpage_mcp.services.observe.detect_notification", return_value={}):
        result = page_model.scan_floats()

    assert result["count"] == 1
    assert result["floats"][0]["type"] == "vtable-filter-menu"
    assert result["floats"][0]["vtableFilter"]["display"] == "block"
    assert result["floats"][0]["vtableFilter"]["valueCount"] == 8
    js = captured_js[0]
    assert ".vtable-filter-menu" in js
    assert "function duScanVTableFilterMenu" in js
    assert "function duVTableFilterActive" in js
    assert "display:block/none" in js


def test_scan_floats_includes_visible_vtable_tooltip_and_menu_by_shown_class():
    from drissionpage_mcp.services import page_model
    captured_js = []

    def fake_run_json(target, js, default):
        captured_js.append(js)
        return {
            "ok": True,
            "floats": [
                {
                    "type": "vtable-tooltip",
                    "title": "工具栏",
                    "vtableOverlay": {
                        "kind": "vtable-tooltip",
                        "state": "shown",
                        "display": "block",
                        "text": "工具栏",
                        "options": [],
                    },
                },
                {
                    "type": "vtable-menu",
                    "title": "列设置",
                    "vtableOverlay": {
                        "kind": "vtable-menu",
                        "state": "shown",
                        "display": "block",
                        "text": "列设置",
                        "options": ["列设置"],
                    },
                },
            ],
        }

    with patch.object(page_model, "_targets", return_value=(object(), None, [("top", object())])), \
         patch.object(page_model, "_run_json", side_effect=fake_run_json), \
         patch.object(page_model.browser_session, "get_active_tab_name", return_value="active"), \
         patch("drissionpage_mcp.services.observe.detect_message", return_value={}), \
         patch("drissionpage_mcp.services.observe.detect_notification", return_value={}):
        result = page_model.scan_floats()

    assert result["count"] == 2
    assert [item["type"] for item in result["floats"]] == ["vtable-tooltip", "vtable-menu"]
    assert result["floats"][1]["vtableOverlay"]["options"] == ["列设置"]
    js = captured_js[0]
    assert ".vtable__bubble-tooltip-element" in js
    assert ".vtable__menu-element" in js
    assert "function duScanVTableOverlay" in js
    assert "vtable__bubble-tooltip-element--hidden" in js
    assert "vtable__menu-element--hidden" in js
    assert "--shown" in js


def test_observe_start_watches_vtable_filter_menu_as_overlay():
    from drissionpage_mcp.services import observe
    assert ".vtable-filter-menu" in observe._INSTALL_OBSERVER_JS
    assert ".vtable__bubble-tooltip-element" in observe._INSTALL_OBSERVER_JS
    assert ".vtable__menu-element" in observe._INSTALL_OBSERVER_JS
    assert "vtableFilterPayload" in observe._INSTALL_OBSERVER_JS
    assert "vtableOverlayPayload" in observe._INSTALL_OBSERVER_JS
    assert "vtable__bubble-tooltip-element--hidden" in observe._INSTALL_OBSERVER_JS
    assert "vtable__menu-element--hidden" in observe._INSTALL_OBSERVER_JS
    assert "display" in observe._INSTALL_OBSERVER_JS

    fake_tab = SimpleNamespace(url="https://example.test/top")
    fake_frame = SimpleNamespace(url="https://example.test/frame")

    with observe._session_lock:
        observe._session.clear()

    try:
        with patch.object(observe.browser_session, "get_tab", return_value=fake_tab), \
             patch.object(observe.browser_session, "get_active_frame", return_value=fake_frame), \
             patch.object(observe.browser_session, "tab_count", return_value=1), \
             patch.object(observe, "_run_js_safe", return_value={"ok": True}):
            observe.observe_start(signals=["overlay"])

        with observe._session_lock:
            assert "vtable-filter-menu" in observe._session["dom_types"]
            assert "vtable-tooltip" in observe._session["dom_types"]
            assert "vtable-menu" in observe._session["dom_types"]
    finally:
        with observe._session_lock:
            observe._session.clear()


def test_drissionpage_observer_and_snapshot_include_vtable_filter_menu():
    script = r"""
import json
import sys
from drissionpage_mcp.services import observe
from drissionpage_mcp.services import page_model
from drissionpage_mcp.core import ui_contract
source_consts = (page_model._COMMON_JS + "\n" + "\n".join(str(c) for c in page_model.scan_floats.__code__.co_consts) + "\n" + "\n".join(ui_contract.FLOAT_ROOTS))
data = {
    "observer_selector": ".vtable-filter-menu" in observe._INSTALL_OBSERVER_JS,
    "observer_tooltip_selector": ".vtable__bubble-tooltip-element" in observe._INSTALL_OBSERVER_JS,
    "observer_menu_selector": ".vtable__menu-element" in observe._INSTALL_OBSERVER_JS,
    "observer_payload": "vtableFilterPayload" in observe._INSTALL_OBSERVER_JS,
    "observer_overlay_payload": "vtableOverlayPayload" in observe._INSTALL_OBSERVER_JS,
    "observer_hidden_filter": "vtable__menu-element--hidden" in observe._INSTALL_OBSERVER_JS,
    "display_payload": "display" in observe._INSTALL_OBSERVER_JS,
    "scan_selector": ".vtable-filter-menu" in source_consts,
    "scan_tooltip_selector": ".vtable__bubble-tooltip-element" in source_consts,
    "scan_menu_selector": ".vtable__menu-element" in source_consts,
}
data["scan_payload"] = "duScanVTableFilterMenu" in source_consts
data["scan_overlay_payload"] = "duScanVTableOverlay" in source_consts
data["scan_display"] = "display:block/none" in source_consts
data["scan_hidden_filter"] = "vtable__bubble-tooltip-element--hidden" in source_consts
print(json.dumps(data, ensure_ascii=False, sort_keys=True))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)

    assert data == {
        "display_payload": True,
        "observer_hidden_filter": True,
        "observer_menu_selector": True,
        "observer_overlay_payload": True,
        "observer_payload": True,
        "observer_selector": True,
        "observer_tooltip_selector": True,
        "scan_display": True,
        "scan_hidden_filter": True,
        "scan_menu_selector": True,
        "scan_overlay_payload": True,
        "scan_payload": True,
        "scan_selector": True,
        "scan_tooltip_selector": True,
    }


def test_observe_snapshot_wraps_scan_floats_as_unified_overlays():
    from drissionpage_mcp.services import observe
    from drissionpage_mcp.services import page_model
    with patch.object(page_model, "scan_floats", return_value={
        "ok": True,
        "count": 1,
        "floats": [{"type": "calendar", "title": "日期选择器"}],
        "active_tab": "active",
        "has_active_frame": True,
        "frame_url": "https://example.test/frame",
    }) as scan:
        result = observe.observe_snapshot(include_table_data=True)

    assert result["ok"] is True
    assert result["type"] == "snapshot"
    assert result["count"] == 1
    assert result["overlays"] == [{"type": "calendar", "title": "日期选择器"}]
    assert result["page"]["active_tab"] == "active"
    assert result["source"] == "scan_floats"
    scan.assert_called_once_with(only_visible=True, include_table_data=True, detail="summary")


def test_observe_start_creates_active_session():
    from drissionpage_mcp.services import observe
    fake_tab = SimpleNamespace(url="https://example.test/top")
    fake_frame = SimpleNamespace(url="https://example.test/frame")

    with observe._session_lock:
        observe._session.clear()

    try:
        with patch.object(observe.browser_session, "get_tab", return_value=fake_tab), \
             patch.object(observe.browser_session, "get_active_frame", return_value=fake_frame), \
             patch.object(observe.browser_session, "tab_count", return_value=3), \
             patch.object(observe, "_run_js_safe", return_value={"ok": True}):
            result = observe.observe_start(signals=["modal", "message"])

        assert result == {
            "ok": True,
            "session": "active",
            "watched": ["message", "modal"],
            "base_url": "https://example.test/frame",
            "base_tab_count": 3,
            "observer_scopes": [],
            "network_active": False,
        }
        with observe._session_lock:
            assert observe._session["active"] is True
            assert observe._session["sigset"] == {"modal", "message"}
    finally:
        with observe._session_lock:
            observe._session.clear()


def test_observer_baseline_and_ui_event_priority_are_stable():
    from drissionpage_mcp.services import observe
    before_click = observe._observer_script(False)
    after_click = observe._observer_script(True)
    assert "var CAPTURE_EXISTING = false;" in before_click
    assert "var CAPTURE_EXISTING = true;" in after_click
    assert "__DU_CAPTURE_EXISTING__" not in before_click + after_click

    duplicated = [
        {"type": "interactive", "scope": "iframe", "payload": {
            "title": "添加工资明细", "content": "字段", "rect": {"x": 1},
        }},
        {"type": "interactive", "scope": "iframe", "payload": {
            "title": "添加工资明细", "content": "字段", "rect": {"x": 9},
        }},
    ]
    unique, _ = observe._compact_events(duplicated)
    assert len(unique) == 1
    assert observe._pick_primary([
        {"type": "network", "url": "/query"},
        {"type": "calendar", "payload": {"title": "日期范围选择器"}},
    ])["type"] == "calendar"


def test_observe_wait_attaches_snapshot_after_signal():
    from drissionpage_mcp.services import observe
    sess = {
        "active": True,
        "sigset": {"overlay"},
        "start": time.time(),
        "tab": object(),
        "fr": None,
        "watch_network": False,
    }
    with observe._session_lock:
        observe._session.clear()
        observe._session.update(sess)

    try:
        with patch.object(observe, "_poll_once", return_value={"type": "calendar", "elapsedMs": 1}), \
             patch.object(observe, "_teardown_session"), \
             patch.object(observe, "observe_snapshot", return_value={
                 "ok": True,
                 "type": "snapshot",
                 "count": 1,
                 "overlays": [{"type": "calendar"}],
             }):
            result = observe.observe_wait(timeout=0.2, poll_interval=0.01)
    finally:
        with observe._session_lock:
            observe._session.clear()

    assert result["type"] == "calendar"
    assert result["snapshot_after"]["overlays"] == [{"type": "calendar"}]


def test_click_table_cell_routes_to_vtable_backend_by_col():
    from drissionpage_mcp import server
    with patch.object(server.modal, "clear_transient_overlays", return_value={"ok": True, "closed": [], "errors": []}), \
         patch.object(server.vtable, "click_cell", return_value={"ok": True, "col": 2, "row": 1}) as click_cell:
        assert server.click_table_cell(row=1, col=2, kind="vtable") == {"ok": True, "col": 2, "row": 1, "kind": "vtable"}
        click_cell.assert_called_once_with(2, 1, None, True, 0.3, False)


def test_vtable_action_routes_to_backend_by_column_title():
    from drissionpage_mcp import server
    with patch.object(server, "_find_vtable_col", return_value=(5, None)) as find_col, \
         patch.object(server.vtable, "vtable_action", return_value={"ok": True, "action": "drag", "col": 5, "row": 2}) as action:
        result = server.vtable_action(action="drag", row=2, column_title="计划开始",
                                      drag_by_x=16, clean_overlays=False)

    assert result == {"ok": True, "action": "drag", "col": 5, "row": 2, "kind": "vtable"}
    find_col.assert_called_once_with("计划开始")
    action.assert_called_once_with(
        action="drag",
        col=5,
        row=2,
        target="cell",
        icon_name=None,
        icon_index=None,
        hover_first=True,
        duration=0.3,
        drag_to={"dx": 16},
    )


def test_get_vtable_cell_render_info_routes_to_backend_by_column_title():
    from drissionpage_mcp import server
    with patch.object(server, "_find_vtable_col", return_value=(24, None)) as find_col, \
         patch.object(server.vtable, "get_cell_render_info", return_value={
             "ok": True,
             "text": "生产中",
             "fontColor": "#000000",
             "tagBackgroundColor": "rgba(2, 168, 84, 0.2)",
         }) as get_info:
        result = server.get_vtable_cell_render_info(row=2, column_title="生产状态", detail="full")

    assert result["ok"] is True
    assert result["kind"] == "vtable"
    assert result["text"] == "生产中"
    find_col.assert_called_once_with("生产状态")
    get_info.assert_called_once_with(24, 2, detail="full")


def test_get_vtable_cell_icons_routes_to_backend_by_col():
    from drissionpage_mcp import server
    with patch.object(server.vtable, "get_cell_icons", return_value={
        "ok": True,
        "icons": [{"index": 0, "name": "edit", "viewportX": 10, "viewportY": 20}],
    }) as get_icons:
        result = server.get_vtable_cell_icons(row=3, col=9, icon_name="edit")

    assert result["ok"] is True
    assert result["kind"] == "vtable"
    assert result["icons"][0]["name"] == "edit"
    get_icons.assert_called_once_with(9, 3, icon_name="edit", detail="summary")


def test_vtable_action_passes_cell_icon_index_to_backend():
    from drissionpage_mcp import server
    with patch.object(server.vtable, "vtable_action", return_value={
        "ok": True,
        "target": "cell-icon",
        "icon_index": 1,
    }) as action:
        result = server.vtable_action(action="click", row=4, col=8, target="cell-icon",
                                      icon_index=1, clean_overlays=False)

    assert result == {"ok": True, "target": "cell-icon", "icon_index": 1, "kind": "vtable"}
    action.assert_called_once_with(
        action="click",
        col=8,
        row=4,
        target="cell-icon",
        icon_name=None,
        icon_index=1,
        hover_first=True,
        duration=0.3,
        drag_to=None,
    )


def test_hover_table_cell_routes_to_vtable_action():
    from drissionpage_mcp import server
    with patch.object(server.vtable, "vtable_action", return_value={"ok": True, "action": "hover", "col": 2, "row": 1}) as action:
        assert server.hover_table_cell(row=1, col=2, kind="vtable", duration=0.2) == {
            "ok": True,
            "action": "hover",
            "col": 2,
            "row": 1,
            "kind": "vtable",
        }
        action.assert_called_once_with(action="hover", col=2, row=1, target="cell", duration=0.2)


def test_enterprise_table_facades_are_public_and_grouped():
    script = r"""
import asyncio
import json
import sys
from drissionpage_mcp.core import caps
from drissionpage_mcp import server
async def main():
    tools = await server.mcp.list_tools()
    names = {tool.name for tool in tools}
    print(json.dumps({
        "public": all(name in names for name in [
            "query_table",
            "inspect_table_cell",
            "table_action",
        ]),
        "grouped": all(name in caps.CAP_GROUPS["vtable"] for name in [
            "query_table",
            "inspect_table_cell",
            "table_action",
        ]),
    }, ensure_ascii=False, sort_keys=True))

asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)

    assert data == {"grouped": True, "public": True}


def test_write_synchronized_serializes():
    """write_synchronized 装饰器应串行化写操作。"""
    from drissionpage_mcp import server
    results = []

    @server.write_synchronized
    def slow_task(label):
        results.append(("enter", label))
        time.sleep(0.05)
        results.append(("exit", label))

    threads = [threading.Thread(target=slow_task, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 6
    for i in range(0, 6, 2):
        assert results[i][0] == "enter"
        assert results[i+1][0] == "exit"
        assert results[i][1] == results[i+1][1], f"第 {i//2} 个任务 enter/exit label 不一致"


def test_synchronized_returns_value():
    """write_synchronized 不改变函数返回值。"""
    from drissionpage_mcp import server
    @server.write_synchronized
    def add(a, b):
        return a + b

    assert add(2, 3) == 5


def test_write_owner_can_reenter_through_read_tool():
    """执行套件持有写锁时必须能调用只读断言工具，不能自锁。"""
    from drissionpage_mcp import server
    result = []

    @server.read_synchronized
    def read_assertion():
        return "observed"

    @server.write_synchronized
    def execute_suite():
        result.append(read_assertion())

    worker = threading.Thread(target=execute_suite)
    worker.start()
    worker.join(timeout=1)

    assert worker.is_alive() is False
    assert result == ["observed"]


class _FakeSetter:
    def __init__(self, kind, calls, allowed):
        self.kind = kind
        self.calls = calls
        self.allowed = allowed

    def all(self):
        self.calls.append((self.kind, "all"))
        return self

    def ws(self, only=False):
        self.calls.append((self.kind, "WebSocket", only))
        return self

    def __getattr__(self, name):
        if name not in self.allowed:
            raise ValueError(name)

        def _call(only=False):
            self.calls.append((self.kind, name, only))
            return self

        return _call


class _FakeListen:
    def __init__(self, wait_return=None):
        self.calls = []
        self.set_method = _FakeSetter("method", self.calls, {"GET", "POST", "PUT", "DELETE"})
        self.set_res_type = _FakeSetter("res_type", self.calls, {"XHR", "Fetch", "WebSocket"})
        self.started = None
        self.stopped = False
        self.wait_kwargs = None
        self.wait_return = wait_return

    def start(self, **kwargs):
        self.started = kwargs

    def wait(self, **kwargs):
        self.wait_kwargs = kwargs
        return self.wait_return

    def stop(self):
        self.stopped = True


def test_scan_controls_uses_drission_viewport_click_point():
    from drissionpage_mcp import server
    class FakeElement:
        tag = "button"
        text = "查 询"
        rect = SimpleNamespace(size=(80, 32), viewport_midpoint=(1152.2, 211.7))

        def attr(self, name):
            return {"class": "el-button", "type": "button"}.get(name)

    class FakeTarget:
        def eles(self, locator, timeout=2):
            assert locator.startswith("c:")
            return [FakeElement()]

    items, seq = server._scan_controls_in_context(FakeTarget(), "active_iframe", 0, 10)
    assert seq == 1
    assert items[0]["cx"] == 1152.2
    assert items[0]["cy"] == 211.7
    assert items[0]["viewportX"] == 1152.2
    assert items[0]["viewportY"] == 211.7
    assert items[0]["coordinate_space"] == "top-viewport"
    assert items[0]["coord_source"] == "DrissionPage.Element.rect.viewport_midpoint"


def test_listen_start_sets_http_listener_state_for_drissionpage_42():
    from drissionpage_mcp import server
    fake_listen = _FakeListen()
    fake_tab = SimpleNamespace(listen=fake_listen)

    with patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        result = server.listen_start("gateway, scmpsm", method="POST")

    assert result == {
        "ok": True,
        "targets": ["gateway", "scmpsm"],
        "method": "POST",
        "resource_type": "ALL",
    }
    assert ("res_type", "all") in fake_listen.calls
    assert ("method", "POST", True) in fake_listen.calls
    assert fake_listen.started == {"urls": ["gateway", "scmpsm"]}


def test_listen_start_falls_back_to_get_post_for_unknown_method():
    from drissionpage_mcp import server
    fake_listen = _FakeListen()
    fake_tab = SimpleNamespace(listen=fake_listen)

    with patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        result = server.listen_start("gateway", method="BREW")

    assert result["method"] == "GET,POST"
    assert ("method", "GET", True) in fake_listen.calls
    assert ("method", "POST", False) in fake_listen.calls


def test_listen_wait_passes_fit_count_to_drissionpage():
    from drissionpage_mcp import server
    fake_listen = _FakeListen(wait_return=None)
    fake_tab = SimpleNamespace(listen=fake_listen)

    with patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        result = server.listen_wait(count=3, timeout=1, fit_count=False)

    assert result["ok"] is False
    assert fake_listen.wait_kwargs == {"count": 3, "timeout": 1, "fit_count": False, "raise_err": False}


def test_listen_ws_start_sets_websocket_listener_state_for_drissionpage_42():
    from drissionpage_mcp import server
    fake_listen = _FakeListen()
    fake_tab = SimpleNamespace(listen=fake_listen)

    with patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        result = server.listen_ws_start("socket")

    assert result == {
        "ok": True,
        "targets": ["socket"],
        "method": "ALL",
        "resource_type": "WebSocket",
    }
    assert ("method", "all") in fake_listen.calls
    assert ("res_type", "WebSocket", True) in fake_listen.calls
    assert fake_listen.started == {"urls": ["socket"]}


def test_network_record_start_sets_http_listener_state():
    from drissionpage_mcp import server
    fake_listen = _FakeListen()
    fake_tab = SimpleNamespace(listen=fake_listen)

    with patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        result = server.network_record_start("gateway", method="POST")

    assert result == {
        "ok": True,
        "session": "active",
        "targets": ["gateway"],
        "method": "POST",
        "resource_type": "ALL",
    }
    assert ("res_type", "all") in fake_listen.calls
    assert ("method", "POST", True) in fake_listen.calls
    assert fake_listen.started == {"urls": ["gateway"]}


def test_network_record_stop_returns_packet_timeline():
    from drissionpage_mcp import server
    packet = SimpleNamespace(
        url="https://example.test/gateway",
        method="POST",
        timestamp=123,
        request=SimpleNamespace(headers={"api-target": "scm.order.list"}, postData='{"page":1}'),
        response=SimpleNamespace(status=200, body={"success": True}),
    )
    fake_listen = _FakeListen(wait_return=[packet])
    fake_tab = SimpleNamespace(listen=fake_listen)

    with patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        server.network_record_start("gateway", method="POST")
        result = server.network_record_stop(timeout=1, max_packets=5)

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["packets"][0]["api_target"] == "scm.order.list"
    assert result["packets"][0]["status"] == 200
    assert fake_listen.wait_kwargs == {"count": 5, "timeout": 1.0, "fit_count": False, "raise_err": False}
    assert fake_listen.stopped is True


def test_network_capture_ignores_account_json_before_business_packet():
    from drissionpage_mcp import server

    noise = SimpleNamespace(
        url="https://scm.example.com//main/api/v1/account.json",
        method="GET",
        request=SimpleNamespace(headers={
            "api-target": "https://scm.example.com//main/api/v1/account.json",
        }),
        response=SimpleNamespace(status=200, body={"ok": True}),
    )
    business = SimpleNamespace(
        url="https://example.test/gateway",
        method="POST",
        request=SimpleNamespace(headers={"api-target": "scm.order.list"}, postData='{"page":1}'),
        response=SimpleNamespace(status=200, body={"success": True}),
    )
    fake_listen = _FakeListen(wait_return=[noise, business])
    fake_tab = SimpleNamespace(listen=fake_listen)

    with patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        server.network_record_start("gateway", method="POST")
        timeline = server.network_record_stop(timeout=1, max_packets=5)

    assert timeline["count"] == 1
    assert timeline["packets"][0]["api_target"] == "scm.order.list"


def test_listen_wait_skips_account_json_and_returns_next_business_packet():
    from drissionpage_mcp import server

    noise = SimpleNamespace(
        url="https://scm.example.com//main/api/v1/account.json",
        request=SimpleNamespace(headers={"api-target": "https://scm.example.com//main/api/v1/account.json"}),
    )
    business = SimpleNamespace(
        url="https://example.test/gateway",
        method="POST",
        request=SimpleNamespace(headers={"api-target": "scm.order.save"}, postData="{}"),
        response=SimpleNamespace(status=200, body={"success": True}),
    )

    class SequenceListen(_FakeListen):
        def __init__(self):
            super().__init__()
            self.responses = [noise, business]

        def wait(self, **kwargs):
            self.wait_kwargs = kwargs
            return self.responses.pop(0) if self.responses else None

    fake_tab = SimpleNamespace(listen=SequenceListen())
    with patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        result = server.listen_wait(count=1, timeout=1)

    assert result["ok"] is True
    assert result["api_target"] == "scm.order.save"


def test_observe_queue_skips_account_json_before_business_signal():
    from queue import Queue

    from drissionpage_mcp.services import observe

    noise = SimpleNamespace(
        url="https://scm.example.com//main/api/v1/account.json",
        request=SimpleNamespace(headers={"api-target": "https://scm.example.com//main/api/v1/account.json"}),
    )
    business = SimpleNamespace(
        url="https://example.test/gateway",
        method="POST",
        request=SimpleNamespace(headers={"api-target": "scm.order.save"}, postData="{}"),
        response=SimpleNamespace(status=200, body={"success": True}),
    )
    packets = Queue()
    packets.put(noise)
    packets.put(business)

    signal = observe._poll_once({
        "dom_types": set(),
        "watch_tab": False,
        "watch_url": False,
        "watch_network": True,
        "net_queue": packets,
        "start": time.monotonic(),
    }, time.monotonic())

    assert signal["type"] == "network"
    assert signal["api_target"] == "scm.order.save"


def test_browser_console_messages_collects_and_filters():
    from drissionpage_mcp import server
    class FakeConsole:
        def __init__(self):
            self.listening = False
            self.started = False
            self.cleared = False
            self._messages = [
                SimpleNamespace(data={"type": "log", "args": [{"value": "ignored"}]}),
                SimpleNamespace(data={"type": "error", "args": [{"value": "boom"}], "timestamp": 1}),
            ]

        def start(self):
            self.listening = True
            self.started = True

        def clear(self):
            self.cleared = True

        def wait(self, timeout=None):
            return False

        @property
        def messages(self):
            msgs = list(self._messages)
            self._messages.clear()
            return msgs

    fake_console = FakeConsole()
    fake_tab = SimpleNamespace(console=fake_console)

    with patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        result = server.browser_console_messages(level="error")

    assert result["ok"] is True
    assert fake_console.started is True
    assert result["count"] == 1
    assert result["messages"][0]["text"] == "boom"


def test_browser_get_element_state_reads_only_requested_drission_property():
    from drissionpage_mcp import server
    element = SimpleNamespace(states=SimpleNamespace(is_displayed=True))
    with patch.object(server.browser_session, "find", return_value=element):
        result = server.browser_get_element_state("tag:body", state="displayed")

    assert result == {
        "ok": True, "locator": "tag:body", "state": "displayed", "value": True,
    }


def test_browser_get_element_state_derives_and_normalizes_all_states():
    from drissionpage_mcp import server
    states = SimpleNamespace(
        is_displayed=False,
        is_enabled=False,
        is_selected=None,
        is_checked=1,
        is_clickable=0,
        is_covered=987,
        is_alive=True,
        is_in_viewport=False,
        is_whole_in_viewport=False,
        has_rect=((0, 0), (1, 1)),
    )
    with patch.object(server.browser_session, "find", return_value=SimpleNamespace(states=states)):
        result = server.browser_get_element_state("#target")

    assert result["ok"] is True
    assert result["states"]["hidden"] is True
    assert result["states"]["disabled"] is True
    assert result["states"]["checked"] is True
    assert result["states"]["covered"] is True
    assert result["states"]["has_rect"] is True
    assert all(type(value) is bool for value in result["states"].values())


def test_click_xy_cleans_transient_overlays_before_click():
    from drissionpage_mcp import server
    class FakeActions:
        def __init__(self):
            self.moves = []
            self.clicked = False

        def move_to(self, point, duration=0):
            self.moves.append((point, duration))
            return self

        def click(self):
            self.clicked = True
            return self

    actions = FakeActions()
    fake_tab = SimpleNamespace(actions=actions)
    cleanup = {"ok": True, "closed": [{"scope": "iframe", "type": "notification"}], "errors": []}

    with patch.object(server.modal, "clear_transient_overlays", return_value=cleanup) as clear, \
         patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        result = server.click_xy(12.5, 20.5)

    clear.assert_called_once_with()
    assert actions.moves == [((12.5, 20.5), 0.3)]
    assert actions.clicked is True
    assert result["pre_cleaned"] == cleanup["closed"]


def test_click_text_locator_falls_back_to_js_after_fast_native_failure():
    from drissionpage_mcp import server
    class FakeElement:
        def __init__(self):
            self.click_kwargs = None

        def click(self, **kwargs):
            self.click_kwargs = kwargs
            return False

    class FakeTarget:
        def run_js(self, js):
            assert "var needle" in js
            return json.dumps({"ok": True, "tag": "BUTTON", "text": "搜索"})

    ele = FakeElement()

    with patch.object(server.modal, "clear_transient_overlays", return_value={"ok": True, "closed": [], "errors": []}), \
         patch.object(server.browser_session, "find", return_value=ele) as find, \
         patch.object(server.browser_session, "get_active_frame", return_value=FakeTarget()):
        result = server.click("text:搜索", timeout=5)

    find.assert_called_once()
    assert find.call_args.kwargs == {"in_frame": True, "timeout": 1.0, "wait_clickable": False}
    assert "normalize-space(.)='搜索'" in find.call_args.args[0]
    assert ele.click_kwargs == {"by_js": True}
    assert result["ok"] is True
    assert result["fallback"] == "direct-js"
    assert "native_error" in result


def test_click_text_locator_prefers_clickable_xpath_before_inner_text():
    from drissionpage_mcp import server
    class FakeElement:
        def click(self, **kwargs):
            return self

    ele = FakeElement()

    with patch.object(server.modal, "clear_transient_overlays", return_value={"ok": True, "closed": [], "errors": []}), \
         patch.object(server.browser_session, "find", return_value=ele) as find:
        result = server.click("text:重置", timeout=5)

    find.assert_called_once()
    assert "self::button" in find.call_args.args[0]
    assert "normalize-space(.)='重置'" in find.call_args.args[0]
    assert result == {"ok": True, "locator": "text:重置"}


def test_click_xy_rejects_invalid_repeat_without_browser_side_effects():
    from drissionpage_mcp import server
    with patch.object(server.browser_session, "get_tab") as get_tab:
        result = server.click_xy(10, 20, times=0)

    assert result["ok"] is False and "times" in result["reason"]
    get_tab.assert_not_called()


def test_click_xy_returns_structured_action_failure():
    from drissionpage_mcp import server
    actions = MagicMock()
    actions.move_to.side_effect = RuntimeError("detached")
    with patch.object(server.modal, "clear_transient_overlays", return_value={"ok": True, "closed": [], "errors": []}), \
         patch.object(server.browser_session, "get_tab", return_value=SimpleNamespace(actions=actions)):
        result = server.click_xy(10, 20)

    assert result["ok"] is False and "detached" in result["reason"]


def test_browser_scroll_falls_back_to_top_with_matching_scroll_scope():
    from drissionpage_mcp import server
    element = SimpleNamespace()
    top = SimpleNamespace(scroll=MagicMock(), ele=MagicMock(return_value=element))
    frame = SimpleNamespace(scroll=MagicMock(), ele=MagicMock(return_value=None))
    with patch.object(server.browser_session, "get_tab", return_value=top), \
         patch.object(server.browser_session, "get_active_frame", return_value=frame):
        result = server.browser_scroll(direction="see", locator="#top-target")

    frame.ele.assert_called_once()
    top.ele.assert_called_once()
    top.scroll.to_see.assert_called_once_with(element)
    frame.scroll.to_see.assert_not_called()
    assert result["scope"] == "top"


def test_browser_save_pdf_handles_drission_bytes_return_without_leaking_payload(tmp_path):
    from drissionpage_mcp import server
    payload = b"%PDF-current-api"

    def save(path, name, as_pdf):
        with open(os.path.join(path, name), "wb") as stream:
            stream.write(payload)
        return payload

    with patch.object(server.browser_session, "get_tab", return_value=SimpleNamespace(save=save)):
        result = server.browser_save_pdf(path=str(tmp_path), filename="bytes.pdf")

    assert result["ok"] is True
    assert result["size"] == len(payload)
    assert result["path"] == str(tmp_path / "bytes.pdf")
    assert "result" not in result


def test_browser_scroll_rejects_invalid_arguments_before_connecting():
    from drissionpage_mcp import server
    with patch.object(server.browser_session, "get_tab") as get_tab:
        invalid_pixel = server.browser_scroll(direction="down", pixel=-1)
        missing_location = server.browser_scroll(direction="location", x=None, y=0)

    assert invalid_pixel["ok"] is False
    assert missing_location["ok"] is False
    get_tab.assert_not_called()


def test_set_permission_passes_explicit_allow_value_and_rejects_attributes():
    from drissionpage_mcp import server
    permission = MagicMock()
    browser = SimpleNamespace(set=SimpleNamespace(perm=SimpleNamespace(notifications=permission)))
    with patch.object(server.browser_session, "get_browser", return_value=browser) as get_browser:
        denied = server.set_permission("notifications", allow=False)
        invalid = server.set_permission("__class__", allow=True)

    assert denied == {"ok": True, "perm": "notifications", "allow": False}
    permission.assert_called_once_with(allow=False)
    assert invalid["ok"] is False
    assert get_browser.call_count == 1


def test_browser_save_pdf_verifies_created_file_and_sanitizes_name(tmp_path):
    from drissionpage_mcp import server
    calls = []

    def save(path, name, as_pdf):
        calls.append((path, name, as_pdf))
        output = os.path.join(path, name)
        with open(output, "wb") as stream:
            stream.write(b"%PDF-1.7")
        return output

    with patch.object(server.browser_session, "get_tab", return_value=SimpleNamespace(save=save)):
        result = server.browser_save_pdf(path=str(tmp_path), filename="../proof")

    assert result["ok"] is True and result["size"] == 8
    assert calls == [(str(tmp_path), "proof.pdf", True)]
    assert result["filename"] == "proof.pdf"


def test_browser_save_pdf_rejects_missing_output_file(tmp_path):
    from drissionpage_mcp import server
    fake = SimpleNamespace(save=lambda **_: str(tmp_path / "missing.pdf"))
    with patch.object(server.browser_session, "get_tab", return_value=fake):
        result = server.browser_save_pdf(path=str(tmp_path), filename="missing.pdf")

    assert result["ok"] is False and "未生成" in result["reason"]


def test_browser_press_key_validates_before_accessing_browser():
    from drissionpage_mcp import server
    with patch.object(server.browser_session, "get_tab") as get_tab:
        bad_modifier = server.browser_press_key("a", modifiers=["Enter"])
        bad_interval = server.browser_press_key("a", interval=-0.1)

    assert bad_modifier["ok"] is False and "modifiers" in bad_modifier["reason"]
    assert bad_interval["ok"] is False and "interval" in bad_interval["reason"]
    get_tab.assert_not_called()


def test_press_key_releases_modifiers_when_main_key_release_fails():
    from drissionpage_mcp import server
    class Actions:
        def __init__(self):
            self.events = []

        def key_down(self, key):
            self.events.append(("down", key))

        def key_up(self, key):
            self.events.append(("up", key))
            if key == server.Keys.ENTER:
                raise RuntimeError("main release failed")

    actions = Actions()
    target = SimpleNamespace(actions=actions)
    try:
        server._press_key_raw(target, "Enter", modifiers=["Ctrl", "Shift"])
    except RuntimeError as exc:
        assert "main release failed" in str(exc)
    else:
        raise AssertionError("main key release failure must propagate")

    assert actions.events[-2:] == [
        ("up", server.Keys.SHIFT),
        ("up", server.Keys.CTRL),
    ]


def test_browser_press_key_types_plain_character_in_active_frame():
    from drissionpage_mcp import server
    actions = MagicMock()
    tab = SimpleNamespace(actions=MagicMock())
    frame = SimpleNamespace(actions=actions)
    with patch.object(server.browser_session, "get_tab", return_value=tab), \
         patch.object(server.browser_session, "get_active_frame", return_value=frame):
        result = server.browser_press_key("a", interval=0.02)

    actions.type.assert_called_once_with("a", interval=0.02)
    assert result == {"ok": True, "key": "a", "modifiers": [], "scope": "iframe"}


def test_browser_tabs_rejects_invalid_action_before_connecting():
    from drissionpage_mcp import server
    with patch.object(server.browser_session, "get_browser") as get_browser:
        result = server.browser_tabs(action="destroy")

    assert result["ok"] is False
    get_browser.assert_not_called()


def test_browser_tabs_closing_current_prefers_business_tab():
    from drissionpage_mcp import server
    current = SimpleNamespace(tab_id="temporary")
    business = SimpleNamespace(
        tab_id="business", url="https://scm.example.com/", title="诺贝科技",
    )
    browser = MagicMock()
    browser.tab_ids = ["temporary", "business"]
    with patch.object(server.browser_session, "get_browser", return_value=browser), \
         patch.object(server.browser_session, "get_tab", return_value=current), \
         patch.object(server.browser_session, "_pick_tab", return_value=business) as pick, \
         patch.object(server.browser_session, "set_tab") as set_tab:
        result = server.browser_tabs(action="close", index=0)

    browser.close_tabs.assert_called_once_with("temporary")
    pick.assert_called_once_with(browser, server.browser_session._target_hint)
    set_tab.assert_called_once_with(business)
    assert result == {
        "ok": True, "closed_tab_id": "temporary", "active_tab_id": "business",
    }


def test_browser_tabs_lists_stable_zero_based_indexes():
    from drissionpage_mcp import server
    tabs = [{"tab_id": "a"}, {"tab_id": "b"}]
    with patch.object(server.browser_session, "get_browser", return_value=object()), \
         patch.object(server.browser_session, "list_tabs", return_value=tabs):
        result = server.browser_tabs(action="list")

    assert [item["index"] for item in result["tabs"]] == [0, 1]
    assert tabs == [{"tab_id": "a"}, {"tab_id": "b"}]


def test_download_by_browser_returns_json_safe_absolute_path(tmp_path):
    from drissionpage_mcp import server
    downloaded = tmp_path / "proof.txt"
    downloaded.write_text("proof", encoding="utf-8")
    mission = SimpleNamespace(
        wait=MagicMock(return_value=downloaded),
        final_path=downloaded,
        total_bytes=5,
        state="completed",
        name="proof.txt",
    )
    by_browser = MagicMock(return_value=mission)
    tab = SimpleNamespace(download=SimpleNamespace(by_browser=by_browser))
    with patch.object(server.browser_session, "get_tab", return_value=tab):
        result = server.download_by_browser(
            "data:text/plain,proof", save_path=tmp_path,
            rename="proof", suffix="txt", timeout=2,
        )

    assert result["ok"] is True
    assert type(result["path"]) is str
    assert result["path"] == str(downloaded)
    mission.wait.assert_called_once_with(show=False)
    by_browser.assert_called_once_with(
        url="data:text/plain,proof", timeout=2.0, file_exists="rename",
        save_path=str(tmp_path), rename="proof", suffix="txt",
    )


def test_download_by_browser_rejects_invalid_input_before_browser_access():
    from drissionpage_mcp import server
    with patch.object(server.browser_session, "get_tab") as get_tab:
        empty_url = server.download_by_browser("")
        invalid_policy = server.download_by_browser("https://example.test/file", file_exists="replace")
        invalid_timeout = server.download_by_browser("https://example.test/file", timeout=-1)

    assert empty_url["ok"] is False
    assert invalid_policy["ok"] is False
    assert invalid_timeout["ok"] is False
    get_tab.assert_not_called()
