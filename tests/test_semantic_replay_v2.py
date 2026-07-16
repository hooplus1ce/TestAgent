from types import SimpleNamespace
from unittest.mock import patch


class _States:
    is_displayed = True
    is_enabled = True


class _Input:
    def __init__(self, value=""):
        self.value = value
        self.states = _States()
        self.cleared = False
        self.inputs = []

    def clear(self):
        self.cleared = True
        self.value = ""

    def input(self, value):
        self.inputs.append(value)
        self.value = value

    def property(self, name):
        return self.value if name == "value" else None

    def attr(self, name):
        if name == "value":
            return self.value
        if name in {"readonly", "disabled"}:
            return None
        return ""


class _Container:
    states = _States()

    def __init__(self, label, control):
        self.label = label
        self.control = control

    def ele(self, locator, timeout=0):
        if "label" in locator:
            return SimpleNamespace(text=self.label)
        return None

    def eles(self, locator, timeout=0):
        if "input" in locator or "textarea" in locator or "contenteditable" in locator:
            return [self.control]
        return []


class _Context:
    def __init__(self, overlay_containers=None, page_containers=None):
        self.overlay_containers = overlay_containers or []
        self.page_containers = page_containers or []

    def eles(self, locator, timeout=0):
        if "ant-modal" in locator or "ant-drawer" in locator:
            return self.overlay_containers
        return self.page_containers

    def run_js(self, *_args, **_kwargs):
        raise AssertionError("semantic field input must not execute JavaScript")


def _without_observation(server):
    return (
        patch.multiple(
            server.interaction.table_facade,
            pre_click_cleanup=lambda *_args, **_kwargs: {"errors": []},
            attach_cleanup=lambda result, _cleanup: result,
        ),
        patch.multiple(
            server.observe,
            observe_start=lambda **_kwargs: {"ok": True},
            observe_wait=lambda **_kwargs: {"type": "none", "events": []},
        ),
        patch.object(server.flow_evidence, "wants_screenshot", return_value=False),
    )


def test_explore_action_inputs_semantic_field_target_without_locator():
    from drissionpage_mcp import server
    fake_tab = SimpleNamespace()
    cleanup, attach, screenshot = _without_observation(server)
    with cleanup, attach, screenshot, \
         patch.object(server.browser_session, "get_tab", return_value=fake_tab), \
         patch.object(server.interaction, "set_field_value", return_value={
             "ok": True, "field_name": "供应商编码", "value": "SUP-20260711",
         }) as setter:
        result = server.explore_action(
            action="input",
            target={"type": "field", "name": "供应商编码", "scope": "modal"},
            text="SUP-20260711",
        )

    setter.assert_called_once_with(
        "供应商编码", "SUP-20260711", in_frame=True, clear=True,
        timeout=8, scope="modal", select_index=0,
    )
    assert result["ok"] is True
    assert result["action"]["action"] == "input"


def test_set_field_value_prefers_latest_visible_overlay_field_in_iframe():
    from drissionpage_mcp import server
    older_input = _Input("OLD")
    latest_input = _Input()
    frame = _Context(
        overlay_containers=[
            _Container("供应商编码", older_input),
            _Container("供应商编码", latest_input),
        ],
    )
    tab = _Context()
    with patch.object(server.browser_session, "get_tab", return_value=tab), \
         patch.object(server.browser_session, "get_active_frame", return_value=frame):
        result = server.set_field_value(
            "供应商编码", "SUP-20260711", in_frame=True, scope="modal",
        )

    assert result["ok"] is True
    assert result["scope"] == "iframe"
    assert result["area"] == "overlay"
    assert older_input.inputs == []
    assert latest_input.cleared is True
    assert latest_input.inputs == ["SUP-20260711"]


def test_set_field_value_falls_back_to_framework_neutral_aria_control():
    from drissionpage_mcp import server

    control = _Input()

    class GenericContext:
        def eles(self, locator, timeout=0):
            if "@aria-label" in locator:
                return [control]
            return []

    tab = GenericContext()
    with patch.object(server.browser_session, "get_tab", return_value=tab), \
         patch.object(server.browser_session, "get_active_frame", return_value=None):
        result = server.set_field_value(
            "供应商编码", "SUP-20260711", in_frame=False, scope="page",
        )

    assert result["ok"] is True
    assert result["adapter"] == "generic-dom"
    assert control.inputs == ["SUP-20260711"]


def test_generic_aria_select_uses_native_trigger_and_option_clicks():
    from drissionpage_mcp.services import page_model

    calls = []

    class Element:
        states = _States()

        def __init__(self, text=""):
            self.text = text

        def ele(self, *_args, **_kwargs):
            return None

        def click(self, **kwargs):
            calls.append((self.text or "trigger", kwargs.get("by_js")))

    trigger = Element()
    option = Element("已审核")

    class Target:
        def eles(self, locator, timeout=0):
            if locator.startswith("css:[role='option']"):
                return [option]
            if "@role='combobox'" in locator:
                return [trigger]
            return []

    result = page_model._select_generic_combobox_option(
        [("top", Target())],
        field_name="审批状态",
        option_text="已审核",
        select_index=0,
        timeout=1.0,
    )

    assert result["ok"] is True
    assert result["adapter"] == "generic-aria-select"
    assert calls == [("trigger", False), ("已审核", False)]


def test_semantic_button_prefers_latest_visible_modal_stable_xpath():
    from drissionpage_mcp import server
    modal_data = {
        "ok": True,
        "overlays": [
            {"scope": "iframe", "type": "modal", "title": "旧弹窗", "buttons": [
                {"text": "确定", "semanticXPath": "//button[@data-test='old-confirm']"},
            ]},
            {"scope": "iframe", "type": "modal", "title": "新弹窗", "buttons": [
                {"text": "取消", "semanticXPath": "//button[normalize-space(.)='取消']"},
                {"text": "确定", "semanticXPath": "//button[@data-test='new-confirm']"},
            ]},
        ],
    }
    fake_tab = SimpleNamespace()
    cleanup, attach, screenshot = _without_observation(server)
    with cleanup, attach, screenshot, \
         patch.object(server.observe, "observe_snapshot", return_value=modal_data), \
         patch.object(server.page_model, "scan_toolbar_actions", side_effect=AssertionError("toolbar fallback not expected")), \
         patch.object(server.browser_session, "get_tab", return_value=fake_tab), \
         patch.object(server.interaction, "_resolve_and_click", return_value={"ok": True}) as click:
        result = server.explore_action(
            target={"type": "button", "text": "确定"},
        )

    click.assert_called_once_with(
        "xpath://button[@data-test='new-confirm']", in_frame=True, by_js=False, timeout=8,
    )
    assert result["ok"] is True
    assert result["target"]["area"] == "modal"
    assert result["target"]["overlay_title"] == "新弹窗"


def test_semantic_dropdown_option_uses_stable_xpath_and_explicit_modal_never_falls_back():
    from drissionpage_mcp import server
    dropdown = {
        "ok": True,
        "overlays": [{
            "scope": "iframe",
            "type": "dropdown",
            "title": "内联模式",
            "options": [{
                "text": "弹窗模式",
                "semanticXPath": "//li[normalize-space(.)='弹窗模式']",
            }],
        }],
    }
    with patch.object(server.observe, "observe_snapshot", return_value=dropdown), \
         patch.object(server.page_model, "scan_toolbar_actions", side_effect=AssertionError("no page fallback")):
        resolved = server.interaction._resolve_visible_action_target(
            {"text": "弹窗模式", "scope": "dropdown"}, in_frame=True,
        )

    assert resolved["ok"] is True
    assert resolved["locator"] == "xpath://li[normalize-space(.)='弹窗模式']"
    assert resolved["meta"]["area"] == "dropdown"

    with patch.object(server.observe, "observe_snapshot", return_value={"ok": True, "overlays": []}), \
         patch.object(server.page_model, "scan_toolbar_actions", side_effect=AssertionError("explicit modal must not scan page")):
        missing = server.interaction._resolve_visible_action_target(
            {"text": "确定", "scope": "modal"}, in_frame=True,
        )

    assert missing["ok"] is False
    assert "visible modal action not found" in missing["reason"]


def test_find_vtable_row_requires_unique_value_and_returns_canvas_row():
    from drissionpage_mcp import server
    with patch.object(server.table_facade, "get_table_values", return_value={
        "ok": True, "kind": "vtable", "values": ["SO-1", "SO-2", "SO-3"],
        "header_rows": 2,
    }):
        found = server.find_vtable_row("销售订单号", "SO-2")

    assert found == {
        "ok": True,
        "kind": "vtable",
        "column_title": "销售订单号",
        "value": "SO-2",
        "row": 3,
        "data_index": 1,
        "match": "equals",
        "header_rows": 2,
    }

    with patch.object(server.table_facade, "get_table_values", return_value={
        "ok": True, "kind": "vtable", "values": ["SO-2", "SO-2"],
    }):
        duplicate = server.find_vtable_row("销售订单号", "SO-2")

    assert duplicate["ok"] is False
    assert duplicate["match_count"] == 2
    assert "唯一" in duplicate["reason"]


def test_recipe_dispatcher_saves_dynamic_row_and_resolves_later_reference():
    from drissionpage_mcp import server
    server._reset_recipe_context()
    with patch.object(server.table_facade, "find_vtable_row", return_value={
        "ok": True, "row": 7, "column_title": "采购单号", "value": "PO-7", "token": "secret",
    }) as find_row, \
         patch.object(server.table_facade, "vtable_action", return_value={"ok": True, "row": 7}) as action:
        saved = server._run_recipe_action("find_vtable_row", {
            "column_title": "采购单号", "value": "PO-7", "save_as": "purchase_row",
        })
        replayed = server._run_recipe_action("vtable_action", {
            "action": "click", "row": {"$ref": "purchase_row.row"},
            "column_title": "操作", "target": "cell-icon", "icon_name": "edit",
        })

    find_row.assert_called_once_with(column_title="采购单号", value="PO-7")
    action.assert_called_once_with(
        action="click", row=7, column_title="操作", target="cell-icon", icon_name="edit",
    )
    assert saved["saved_as"] == "purchase_row"
    assert server._recipe_values()["purchase_row"]["token"] == "[REDACTED]"
    assert replayed["ok"] is True


def test_recipe_dispatcher_rejects_missing_dynamic_reference():
    from drissionpage_mcp import server
    server._reset_recipe_context()
    result = server._run_recipe_action("vtable_action", {
        "action": "click", "row": {"$ref": "missing.row"}, "col": 2,
    })

    assert result["ok"] is False
    assert "missing.row" in result["reason"]


def test_vtable_row_business_reads_are_dynamic_and_aligned():
    from drissionpage_mcp import server
    columns = {
        "备注": ["A", "E2E-SALARY-1", "B"],
        "生产数量": ["5", "2", "9"],
        "金额": ["10", "7", "18"],
    }

    def values(column_title, **_kwargs):
        return {"ok": True, "kind": "vtable", "values": columns[column_title]}

    with patch.object(server.table_facade, "get_table_values", side_effect=values), \
            patch.object(server.vtable, "get_columns_values", return_value={
                "ok": True, "values": columns,
            }):
        count = server.count_vtable_rows("备注", "E2E-SALARY-1")
        row = server.get_vtable_row_values(
            "备注", "E2E-SALARY-1", ["备注", "生产数量", "金额"],
        )

    assert count["match_count"] == 1
    assert row["data_index"] == 1
    assert row["values"] == {"备注": "E2E-SALARY-1", "生产数量": "2", "金额": "7"}


def test_vtable_row_business_read_rejects_table_change_after_lookup():
    from drissionpage_mcp import server
    with patch.object(server.table_facade, "get_table_values", return_value={
        "ok": True, "kind": "vtable", "values": ["A", "E2E-SALARY-1"],
        "header_rows": 1,
    }), patch.object(server.vtable, "get_columns_values", return_value={
        "ok": True,
        "values": {
            "备注": ["E2E-SALARY-1", "A"],
            "金额": ["7", "10"],
        },
    }):
        result = server.get_vtable_row_values(
            "备注", "E2E-SALARY-1", ["金额"],
        )

    assert result["ok"] is False
    assert result["previous_data_index"] == 1
    assert result["matching_indexes"] == [0]
    assert "发生变化" in result["reason"]
