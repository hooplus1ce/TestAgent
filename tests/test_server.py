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
from unittest.mock import patch


EXPECTED_PUBLIC_TOOLS = {
    "browser_console_messages",
    "browser_get_element_state",
    "browser_list_caps",
    "browser_press_key",
    "browser_save_pdf",
    "browser_scroll",
    "browser_tabs",
    "capture_page_model",
    "connect",
    "refresh_session",
    "check_session",
    "enter_module",
    "explore_action",
    "scan_filter_fields",
    "get_active_frame",
    "dom_tree",
    "scan_page_elements",
    "scan_toolbar_actions",
    "scan_form_fields",
    "scan_pagination",
    "observe_snapshot",
    "find_elements",
    "find_batch",
    "click",
    "click_xy",
    "select_date_range",
    "select_option",
    "input",
    "insert_text",
    "hover",
    "screenshot",
    "run_js",
    "scan_table",
    "get_table_values",
    "get_table_data",
    "get_all_table_data",
    "get_vtable_cell_render_info",
    "get_vtable_cell_icons",
    "vtable_action",
    "click_table_cell",
    "hover_table_cell",
    "resize_table_column",
    "scan_action_availability_by_selection",
    "close_modal",
    "observe_start",
    "observe_wait",
    "listen_start",
    "listen_wait",
    "listen_stop",
    "network_record_start",
    "network_record_stop",
    "network_record_export",
    "mouse_trail",
    "download_by_browser",
    "listen_ws_start",
    "listen_ws_wait",
    "new_context",
    "switch_context",
    "list_contexts",
    "set_permission",
    "get_element_coords",
}

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
    import server
    tools = asyncio.run(server.mcp.list_tools())
    return {t.name for t in tools}


def test_public_tool_surface():
    """公开 MCP 工具应收敛到业务常用工具 + 表格 facade + 高级调试工具。"""
    names = _tool_names()
    assert names == EXPECTED_PUBLIC_TOOLS


def test_public_tools_are_grouped_in_caps():
    """公开工具必须进入 capability 分组，避免 tools/list 与能力说明不一致。"""
    import caps

    grouped_tools = {
        tool
        for tools in caps.CAP_GROUPS.values()
        for tool in tools
    }
    assert EXPECTED_PUBLIC_TOOLS <= grouped_tools


def test_public_tools_include_mcp_annotations():
    """关键工具应暴露 MCP tool annotations，帮助客户端区分只读/写入风险。"""
    import server

    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}

    assert tools["find_elements"].annotations.readOnlyHint is True
    assert tools["find_elements"].annotations.destructiveHint is False
    assert tools["observe_snapshot"].annotations.readOnlyHint is True
    assert tools["observe_snapshot"].annotations.destructiveHint is False
    assert tools["click"].annotations.readOnlyHint is False
    assert tools["click"].annotations.destructiveHint is True
    assert tools["connect"].annotations.readOnlyHint is False
    assert tools["connect"].annotations.destructiveHint is False
    assert tools["connect"].annotations.idempotentHint is True
    assert tools["screenshot"].annotations.destructiveHint is False


def test_resources_and_templates_are_exposed():
    """MCP resources 应暴露 caps/context 和证据文件读取模板。"""
    import server

    resources = asyncio.run(server.mcp.list_resources())
    resource_uris = {str(r.uri) for r in resources}

    assert {
        "drission-ui://caps",
        "drission-ui://context",
        "drission-ui://resources",
    } <= resource_uris

    templates = asyncio.run(server.mcp.list_resource_templates())
    template_uris = {t.uriTemplate for t in templates}
    assert "drission-ui://resources/{resource_path}" in template_uris


def test_caps_resource_returns_json():
    import server

    contents = asyncio.run(server.mcp.read_resource("drission-ui://caps"))
    data = json.loads(contents[0].content)

    assert data["enabled"]
    assert "core" in data["available"]


def test_evidence_resource_template_reads_encoded_nested_file(monkeypatch, tmp_path):
    import resource_store
    import server

    monkeypatch.setattr(resource_store.config, "SHOT_DIR", str(tmp_path))
    nested = tmp_path / "生产动态表"
    nested.mkdir()
    (nested / "dom.yml").write_text("tag: body", encoding="utf-8")
    encoded = urllib.parse.quote("生产动态表/dom.yml", safe="")

    contents = asyncio.run(server.mcp.read_resource(f"drission-ui://resources/{encoded}"))

    assert contents[0].content == "tag: body"


def test_drission_ui_caps_filters_public_tools():
    """DRISSION_UI_CAPS 应实际影响 MCP tools/list，而不只是报告能力分组。"""
    env = os.environ.copy()
    env["DRISSION_UI_CAPS"] = "core"
    script = """
import asyncio, json, sys
sys.path.insert(0, 'mcp-servers/drission-ui')
import server

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
    assert {"connect", "observe_snapshot", "get_element_coords", "browser_list_caps"} <= names
    assert "scan_floats" not in names
    assert "scan_table" not in names
    assert "run_js" not in names


def test_list_parameters_have_typed_items_schema():
    """常用 list 入参应生成精确 items schema，帮助 Agent 正确构造参数。"""
    import server

    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    checks = [
        ("find_batch", "locators"),
        ("observe_start", "signals"),
        ("explore_action", "signals"),
        ("explore_action", "modifiers"),
        ("browser_press_key", "modifiers"),
    ]
    for tool_name, field in checks:
        prop = tools[tool_name].inputSchema["properties"][field]
        assert prop["items"]["type"] == "string"


def test_explore_action_uses_default_observe_signals_without_listen_targets():
    """explore_action 默认观察信号不应依赖未定义局部变量。"""
    import server

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
    import server

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
    import server

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
    import server

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
    import server

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


def test_explore_action_capture_after_disables_snapshot_by_default():
    """capture_after 已会返回 after 模型，默认不再重复附带 snapshot_after。"""
    import server

    class FakeActions:
        def move_to(self, point, duration=0):
            return self

        def click(self):
            return self

    calls = {}
    fake_tab = SimpleNamespace(actions=FakeActions())

    with patch.object(server.observe, "observe_start", return_value={"ok": True}), \
         patch.object(server.observe, "observe_wait", side_effect=lambda **kwargs: calls.setdefault("observe_wait", kwargs) or {"type": "modal"}), \
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


def test_drissionpage_observe_wait_compacts_and_dedupes_events():
    """drissionpage-mcp observe_wait 应保留主 payload，但 events 只返回去重摘要。"""
    script = r"""
import json
import sys
import time
sys.path.insert(0, 'mcp-servers/drissionpage-mcp')
import observe

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
    """drissionpage-mcp 应提供单日期字段紧凑设置工具。"""
    script = r"""
import asyncio
import json
import sys
sys.path.insert(0, 'mcp-servers/drissionpage-mcp')
import server

async def main():
    tools = await server.mcp.list_tools()
    data = {
        "has_set_date": "set_date" in {tool.name for tool in tools},
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

    assert data["has_set_date"] is True
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


def test_drissionpage_explore_action_click_reuses_click_fallbacks():
    """drissionpage-mcp explore_action(click) 应复用 click 的定位和降级逻辑。"""
    script = r"""
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, 'mcp-servers/drissionpage-mcp')
import server

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
        "get_table_values",
        "get_table_data",
        "get_vtable_cell_render_info",
        "get_vtable_cell_icons",
        "vtable_action",
        "click_table_cell",
        "hover_table_cell",
        "resize_table_column",
    } <= names


def test_scan_table_routes_to_vtable_backend():
    import server
    with patch.object(server.vtable, "scan_vtable_columns", return_value={"ok": True, "columns": []}) as scan:
        assert server.scan_table(kind="vtable") == {"ok": True, "columns": [], "kind": "vtable"}
        scan.assert_called_once_with(50)


def test_scan_table_routes_to_html_backend():
    import server
    with patch.object(server.html_table, "scan_html_table", return_value={"ok": True, "tables": []}) as scan:
        assert server.scan_table(kind="html") == {"ok": True, "tables": [], "kind": "html"}
        scan.assert_called_once_with()


def test_scan_table_auto_falls_back_to_html_backend():
    import server
    with patch.object(server.vtable, "scan_vtable_columns", return_value={"ok": False, "reason": "no vtable"}), \
         patch.object(server.html_table, "scan_html_table", return_value={"ok": True, "tables": [{"index": 0}]}) as scan_html:
        result = server.scan_table(kind="auto")
        assert result["ok"] is True
        assert result["kind"] == "html"
        assert result["fallback_from"] == "vtable"
        assert result["vtable_reason"] == "no vtable"
        scan_html.assert_called_once_with()


def test_get_table_values_routes_to_html_backend():
    import server
    with patch.object(server.html_table, "get_html_table_values", return_value={"ok": True, "values": []}) as get_values:
        assert server.get_table_values("订单号", kind="html") == {"ok": True, "values": [], "kind": "html"}
        get_values.assert_called_once_with("订单号", 0)


def test_get_all_table_data_auto_prefers_vtable_backend():
    import server

    with patch.object(server.page_model.vtable, "scan_vtable_columns", return_value={
        "ok": True,
        "columns": [{"title": "订单号", "col": 1, "bodyBehavior": "none"}],
    }), patch.object(server.page_model.vtable, "get_column_values", return_value={
        "ok": True,
        "values": ["SO001"],
    }) as get_values:
        result = server.get_all_table_data(kind="auto")

    assert result["ok"] is True
    assert result["kind"] == "vtable"
    assert result["rows"] == [{"订单号": "SO001"}]
    get_values.assert_called_once_with("订单号", raw=False)


def test_scan_floats_includes_ant_calendar_by_dom_presence():
    import page_model

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
         patch("observe.detect_message", return_value={}), \
         patch("observe.detect_notification", return_value={}):
        result = page_model.scan_floats()

    assert result["count"] == 1
    assert result["floats"][0]["type"] == "calendar"
    assert result["floats"][0]["scope"] == "top"
    assert captured_js
    js = captured_js[0]
    assert ".ant-calendar-picker-container, .ant-calendar" in js
    assert "function duCalendarActive" in js
    assert "title: title\n  };\n}\nfunction duScanCalendar" in js
    assert "return !!(el && el.isConnected);" in js
    assert "var nodes = nodeList.filter(duKeepFloatNode);" in js
    assert "nodeList.filter(duVisible)" not in js


def test_scan_floats_includes_visible_vtable_filter_menu_by_display_state():
    import page_model

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
         patch("observe.detect_message", return_value={}), \
         patch("observe.detect_notification", return_value={}):
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
    import page_model

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
         patch("observe.detect_message", return_value={}), \
         patch("observe.detect_notification", return_value={}):
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
    import observe

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
sys.path.insert(0, 'mcp-servers/drissionpage-mcp')
import observe
import page_model

source_consts = page_model._COMMON_JS + "\n" + "\n".join(str(c) for c in page_model.scan_floats.__code__.co_consts)
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
    import observe
    import page_model

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
    scan.assert_called_once_with(only_visible=True, include_table_data=True)


def test_observe_start_creates_active_session():
    import observe

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
        }
        with observe._session_lock:
            assert observe._session["active"] is True
            assert observe._session["sigset"] == {"modal", "message"}
    finally:
        with observe._session_lock:
            observe._session.clear()


def test_observe_wait_attaches_snapshot_after_signal():
    import observe

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
    import server
    with patch.object(server.vtable, "click_cell", return_value={"ok": True, "col": 2, "row": 1}) as click_cell:
        assert server.click_table_cell(row=1, col=2, kind="vtable") == {"ok": True, "col": 2, "row": 1, "kind": "vtable"}
        click_cell.assert_called_once_with(2, 1, None, True, 0.3, False)


def test_vtable_action_routes_to_backend_by_column_title():
    import server
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
    import server
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
    import server
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
    import server
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
    import server
    with patch.object(server.vtable, "vtable_action", return_value={"ok": True, "action": "hover", "col": 2, "row": 1}) as action:
        assert server.hover_table_cell(row=1, col=2, kind="vtable", duration=0.2) == {
            "ok": True,
            "action": "hover",
            "col": 2,
            "row": 1,
            "kind": "vtable",
        }
        action.assert_called_once_with(action="hover", col=2, row=1, target="cell", duration=0.2)


def test_drissionpage_vtable_action_tool_is_public_and_grouped():
    script = r"""
import asyncio
import json
import sys
sys.path.insert(0, 'mcp-servers/drissionpage-mcp')
import caps
import server

async def main():
    tools = await server.mcp.list_tools()
    names = {tool.name for tool in tools}
    print(json.dumps({
        "public": all(name in names for name in [
            "vtable_action",
            "get_vtable_cell_render_info",
            "get_vtable_cell_icons",
        ]),
        "grouped": all(name in caps.CAP_GROUPS["vtable"] for name in [
            "vtable_action",
            "get_vtable_cell_render_info",
            "get_vtable_cell_icons",
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
    import server

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
    import server

    @server.write_synchronized
    def add(a, b):
        return a + b

    assert add(2, 3) == 5


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
    import server

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
    import server

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
    import server

    fake_listen = _FakeListen()
    fake_tab = SimpleNamespace(listen=fake_listen)

    with patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        result = server.listen_start("gateway", method="BREW")

    assert result["method"] == "GET,POST"
    assert ("method", "GET", True) in fake_listen.calls
    assert ("method", "POST", False) in fake_listen.calls


def test_listen_wait_passes_fit_count_to_drissionpage():
    import server

    fake_listen = _FakeListen(wait_return=None)
    fake_tab = SimpleNamespace(listen=fake_listen)

    with patch.object(server.browser_session, "get_tab", return_value=fake_tab):
        result = server.listen_wait(count=3, timeout=1, fit_count=False)

    assert result["ok"] is False
    assert fake_listen.wait_kwargs == {"count": 3, "timeout": 1, "fit_count": False}


def test_listen_ws_start_sets_websocket_listener_state_for_drissionpage_42():
    import server

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
    import server

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
    import server

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
    assert fake_listen.wait_kwargs == {"count": 5, "timeout": 1, "fit_count": False}
    assert fake_listen.stopped is True


def test_browser_console_messages_collects_and_filters():
    import server

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


def test_click_xy_cleans_transient_overlays_before_click():
    import server

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
    import server

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
    assert ele.click_kwargs == {"by_js": False, "timeout": 2.0, "wait_stop": False}
    assert result["ok"] is True
    assert result["fallback"] == "js-text"
    assert "native_error" in result


def test_click_text_locator_prefers_clickable_xpath_before_inner_text():
    import server

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
