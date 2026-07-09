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
    assert "return !!(el && el.isConnected);" in js
    assert "var nodes = nodeList.filter(duKeepFloatNode);" in js
    assert "nodeList.filter(duVisible)" not in js


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
