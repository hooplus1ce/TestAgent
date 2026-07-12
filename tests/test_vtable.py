"""vtable.py 测试：_js_args 参数序列化（纯逻辑，不依赖浏览器）。"""
import json
from unittest.mock import patch


class _FakeActions:
    def __init__(self):
        self.calls = []

    def move_to(self, point, duration=0):
        self.calls.append(("move_to", point, duration))
        return self

    def click(self):
        self.calls.append(("click",))
        return self

    def wait(self, seconds):
        self.calls.append(("wait", seconds))
        return self

    def hold(self):
        self.calls.append(("hold",))
        return self

    def release(self):
        self.calls.append(("release",))
        return self


class _FakeTab:
    def __init__(self):
        self.actions = _FakeActions()


def test_js_args_int():
    import vtable
    s = vtable._js_args(1, 2)
    assert s == "[1, 2]"


def test_js_args_string_and_bool():
    import vtable
    s = vtable._js_args("制令单号", True)
    assert json.loads(s) == ["制令单号", True]


def test_js_args_empty():
    import vtable
    assert vtable._js_args() == "[]"


def test_js_args_negative_and_float():
    import vtable
    s = vtable._js_args(-1, 3.14)
    assert json.loads(s) == [-1, 3.14]


def test_js_args_special_chars():
    import vtable
    s = vtable._js_args("含'引号\"和\\斜杠")
    # 确保序列化后是合法 JSON
    parsed = json.loads(s)
    assert parsed == ["含'引号\"和\\斜杠"]


def test_is_loading_complete_waits_for_visible_mask_and_visible_vtable_canvas():
    import vtable

    calls = []

    class LoadingWait:
        def hidden(self, **kwargs):
            calls.append(("hidden", kwargs))
            return True

    class CanvasWait:
        def stop_moving(self, **kwargs):
            calls.append(("stop_moving", kwargs))
            return True

    loading = type("Loading", (), {
        "states": type("States", (), {"is_displayed": True})(),
        "wait": LoadingWait(),
    })()
    hidden_canvas = type("Canvas", (), {
        "states": type("States", (), {"is_displayed": False})(),
        "wait": CanvasWait(),
    })()
    visible_canvas = type("Canvas", (), {
        "states": type("States", (), {"is_displayed": True})(),
        "wait": CanvasWait(),
    })()

    class Frame:
        def run_js(self, script):
            assert "window._vtableElement" in script
            return "1"

        def eles(self, locator, timeout=None):
            calls.append(("eles", locator, timeout))
            if "vtable-loading" in locator:
                return [loading]
            return [hidden_canvas, visible_canvas]

    assert vtable.is_loading_complete(Frame()) is True
    assert any(item[0] == "hidden" for item in calls)
    assert any(item[0] == "stop_moving" for item in calls)


def test_is_loading_complete_skips_mask_wait_when_already_absent():
    import vtable

    calls = []

    class CanvasWait:
        def stop_moving(self, **kwargs):
            calls.append(("stop_moving", kwargs))
            return True

    canvas = type("Canvas", (), {
        "states": type("States", (), {"is_displayed": True})(),
        "wait": CanvasWait(),
    })()

    class Frame:
        def run_js(self, script):
            return "0"

        def eles(self, locator, timeout=None):
            calls.append(("eles", locator, timeout))
            return [] if "vtable-loading" in locator else [canvas]

    assert vtable.is_loading_complete(Frame()) is True
    assert not any(item[0] == "hidden" for item in calls)
    assert any(item[0] == "stop_moving" for item in calls)


def test_is_loading_complete_rejects_canvas_that_never_stabilizes():
    import vtable

    canvas_wait = type("CanvasWait", (), {"stop_moving": lambda self, **kwargs: False})()
    canvas = type("Canvas", (), {
        "states": type("States", (), {"is_displayed": True})(),
        "wait": canvas_wait,
    })()

    class Frame:
        def run_js(self, script):
            return "0"

        def eles(self, locator, timeout=None):
            return [] if "vtable-loading" in locator else [canvas]

    assert vtable.is_loading_complete(Frame(), timeout=1) is False


def test_vtable_action_click_uses_visible_cell_coordinates():
    import vtable
    fake_tab = _FakeTab()

    with patch.object(vtable, "_ensure_vtable", return_value=True), \
         patch.object(vtable, "get_cell_rect", return_value={
             "ok": True,
             "viewportX": 111.24,
             "viewportY": 222.26,
             "col": 2,
             "row": 3,
             "scrolled": True,
         }) as get_cell_rect, \
         patch.object(vtable.browser_session, "get_tab", return_value=fake_tab):
        result = vtable.vtable_action(action="click", col=2, row=3, duration=0.25)

    assert result["ok"] is True
    assert result["action"] == "click"
    assert result["coordinate_space"] == "top_viewport"
    assert result["viewportX"] == 111.2
    assert result["viewportY"] == 222.3
    get_cell_rect.assert_called_once_with(2, 3, scroll=True)
    assert fake_tab.actions.calls == [
        ("move_to", (111.24, 222.26), 0.25),
        ("click",),
    ]


def test_vtable_action_header_icon_scrolls_before_resolving_icon_point():
    import vtable
    fake_tab = _FakeTab()

    with patch.object(vtable, "_ensure_vtable", return_value=True), \
         patch.object(vtable, "scroll_to_cell", return_value={"ok": True}) as scroll_to_cell, \
         patch.object(vtable, "_wait_cell_center_stable", return_value={"viewportX": 10, "viewportY": 20}) as wait_center, \
         patch.object(vtable, "_run", return_value=[{
             "col": 4,
             "isHeader": True,
             "icons": [{"name": "filter-icon", "viewportX": 88.8, "viewportY": 33.3}],
         }]), \
         patch.object(vtable.browser_session, "get_tab", return_value=fake_tab):
        result = vtable.vtable_action(action="click", col=4, row=8,
                                      target="header-icon", icon_name="filter")

    assert result["ok"] is True
    assert result["target"] == "header-icon"
    assert result["icon"] == "filter"
    assert result["viewportX"] == 88.8
    assert result["viewportY"] == 33.3
    scroll_to_cell.assert_called_once_with(4, 0)
    wait_center.assert_called_once_with(4, 0)
    assert fake_tab.actions.calls == [
        ("move_to", (88.8, 33.3), 0.3),
        ("click",),
    ]


def test_vtable_action_drag_supports_relative_destination():
    import vtable
    fake_tab = _FakeTab()

    with patch.object(vtable, "_ensure_vtable", return_value=True), \
         patch.object(vtable, "get_cell_rect", return_value={
             "ok": True,
             "viewportX": 10,
             "viewportY": 20,
             "col": 1,
             "row": 2,
             "scrolled": True,
         }), \
         patch.object(vtable.browser_session, "get_tab", return_value=fake_tab):
        result = vtable.vtable_action(action="drag", col=1, row=2,
                                      drag_to={"dx": 15, "dy": -5},
                                      hover_first=False, duration=0.2)

    assert result["ok"] is True
    assert result["destinationX"] == 25
    assert result["destinationY"] == 15
    assert fake_tab.actions.calls == [
        ("move_to", (10, 20), 0),
        ("hold",),
        ("move_to", (25, 15), 0.2),
        ("release",),
    ]


def test_get_cell_render_info_scrolls_and_returns_colors():
    import vtable

    payload = {
        "ok": True,
        "col": 24,
        "row": 2,
        "text": "生产中",
        "fontColor": "#000000",
        "tagBackgroundColor": "rgba(2, 168, 84, 0.2)",
        "cellBackgroundColor": "rgba(229, 233, 235, 0.7)",
    }
    with patch.object(vtable, "_ensure_vtable", return_value=True), \
         patch.object(vtable, "scroll_to_cell", return_value={"ok": True}) as scroll_to_cell, \
         patch.object(vtable, "_wait_cell_center_stable", return_value={"viewportX": 1, "viewportY": 2}) as wait_center, \
         patch.object(vtable, "_run", return_value=payload) as run:
        result = vtable.get_cell_render_info(24, 2, detail="full")

    assert result["ok"] is True
    assert result["text"] == "生产中"
    assert result["tagBackgroundColor"] == "rgba(2, 168, 84, 0.2)"
    assert result["scrolled"] is True
    scroll_to_cell.assert_called_once_with(24, 2)
    wait_center.assert_called_once_with(24, 2)
    assert "getCellRenderInfo" in run.call_args.args[1]
    assert '"full"' in run.call_args.args[1]


def test_get_cell_icons_returns_top_viewport_icons():
    import vtable

    payload = {
        "ok": True,
        "col": 8,
        "row": 4,
        "icons": [{"index": 0, "name": "edit", "viewportX": 10.5, "viewportY": 20.5}],
    }
    with patch.object(vtable, "_ensure_vtable", return_value=True), \
         patch.object(vtable, "scroll_to_cell", return_value={"ok": True}), \
         patch.object(vtable, "_wait_cell_center_stable", return_value={"viewportX": 1, "viewportY": 2}), \
         patch.object(vtable, "_run", return_value=payload) as run:
        result = vtable.get_cell_icons(8, 4, icon_name="edit")

    assert result["ok"] is True
    assert result["icons"][0]["viewportX"] == 10.5
    assert result["scrolled"] is True
    assert "getCellIconsViewport" in run.call_args.args[1]
    assert '"edit"' in run.call_args.args[1]


def test_vtable_action_cell_icon_clicks_icon_by_index():
    import vtable
    fake_tab = _FakeTab()

    with patch.object(vtable, "_ensure_vtable", return_value=True), \
         patch.object(vtable, "get_cell_icons", return_value={
             "ok": True,
             "icons": [
                 {"index": 0, "name": "view", "viewportX": 10, "viewportY": 20},
                 {"index": 1, "name": "edit", "viewportX": 30, "viewportY": 40},
             ],
             "scrolled": True,
         }) as get_icons, \
         patch.object(vtable.browser_session, "get_tab", return_value=fake_tab):
        result = vtable.vtable_action(action="click", col=8, row=4,
                                      target="cell-icon", icon_index=1)

    assert result["ok"] is True
    assert result["target"] == "cell-icon"
    assert result["icon"] == "edit"
    assert result["icon_index"] == 1
    get_icons.assert_called_once_with(8, 4, icon_name=None, scroll=True)
    assert fake_tab.actions.calls == [
        ("move_to", (30, 40), 0.3),
        ("click",),
    ]


def test_get_cell_rect_stops_when_scroll_fails():
    import vtable

    with patch.object(vtable, "_ensure_vtable", return_value=True), \
         patch.object(vtable, "scroll_to_cell", return_value={"ok": False, "reason": "out of range"}) as scroll, \
         patch.object(vtable, "_wait_cell_center_stable") as wait_center:
        result = vtable.get_cell_rect(2, 999, scroll=True)

    assert result == {"ok": False, "reason": "out of range"}
    scroll.assert_called_once_with(2, 999)
    wait_center.assert_not_called()


def test_get_columns_values_propagates_header_row_count():
    import vtable

    with patch.object(vtable, "_ensure_vtable", return_value=True), \
         patch.object(vtable, "_run", return_value={
             "values": {"订单号": ["SO-1"]}, "missing": [], "headerRows": 2,
         }):
        result = vtable.get_column_values("订单号")

    assert result["values"] == ["SO-1"]
    assert result["header_rows"] == 2


def test_match_icon_rejects_fractional_index():
    import vtable

    icon, reason = vtable._match_icon([{"name": "edit"}], icon_index=0.5)

    assert icon is None
    assert "非负整数" in reason
