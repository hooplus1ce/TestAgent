"""Pure unit tests for dual-stack page-family scoring and table-kind routing.

These exercise the shipped helpers in ``drissionpage_mcp.core.page_family``
without a browser and without reimplementing scoring logic in the test.
"""
from drissionpage_mcp.core import page_family


def test_score_legacy_bootstrap_probe_prefers_legacy_family():
    probe = {
        "signals": {
            "bootstrap_table": True,
            "bootstrap": True,
            "adminlte": True,
            "layer": True,
            "legacy_mpa": True,
            "ant_shell": False,
            "vtable": False,
            "ant_table": False,
            "quick_filter": False,
        },
        "counts": {"bootstrap_table": 1, "ant_table": 0, "vtable": 0},
        "globals": {
            "jQuery": True,
            "bootstrapTablePlugin": True,
            "layer": True,
            "VTable": False,
            "ReactStore": False,
        },
    }
    family, confidence, reasons = page_family._score_family(probe)
    assert family == page_family.FAMILY_LEGACY
    assert confidence >= 0.5
    assert any("bootstrap" in r for r in reasons)
    assert page_family._preferred_table_kind(family, probe) == "bootstrap"
    adapters = page_family._adapters_for(family, probe)
    assert "bootstrap_table" in adapters
    assert "layer_modal" in adapters


def test_score_vtable_probe_prefers_vtable_family():
    probe = {
        "signals": {
            "vtable": True,
            "quick_filter": True,
            "ant_table": False,
            "bootstrap_table": False,
            "ant_shell": True,
            "legacy_mpa": False,
        },
        "counts": {"vtable": 2, "page_query": 1},
        "globals": {"VTable": True, "jQuery": False, "ReactStore": True},
    }
    family, confidence, reasons = page_family._score_family(probe)
    assert family == page_family.FAMILY_VTABLE
    assert confidence >= 0.5
    assert page_family._preferred_table_kind(family, probe) == "vtable"
    assert "vtable" in page_family._adapters_for(family, probe)


def test_score_shell_markers_without_business_table():
    probe = {
        "signals": {
            "ant_shell": True,
            "react_container": True,
            "bootstrap_table": False,
            "vtable": False,
            "ant_table": False,
        },
        "counts": {"ant_layout": 3, "ant_select_v3": 1},
        "globals": {"ReactStore": True, "webpackChunk": ["webpackChunkscmAdminDemoName"]},
    }
    family, confidence, _ = page_family._score_family(probe)
    assert family == page_family.FAMILY_SHELL
    assert confidence >= 0.5
    assert page_family._preferred_table_kind(family, probe) == "auto"


def test_score_modern_component_library_uses_generic_dom_adapter():
    probe = {
        "signals": {
            "modern_components": True,
            "generic_table": True,
            "vtable": False,
            "bootstrap_table": False,
            "ant_table": False,
        },
        "counts": {"element_ui": 4, "mui": 0, "arco": 0, "semi": 0},
        "globals": {},
    }
    family, confidence, reasons = page_family._score_family(probe)
    assert family == page_family.FAMILY_MODERN
    assert confidence >= 0.65
    assert "modern component library" in reasons
    assert page_family._preferred_table_kind(family, probe) == "html"
    assert "generic_dom" in page_family._adapters_for(family, probe)


def test_weak_probe_returns_unknown():
    family, confidence, _ = page_family._score_family(
        {"signals": {}, "counts": {}, "globals": {}}
    )
    assert family == page_family.FAMILY_UNKNOWN
    assert confidence < 0.25


def test_normalize_table_kind_aliases_and_unknown():
    assert page_family.normalize_table_kind("auto") == "auto"
    assert page_family.normalize_table_kind("VTable") == "vtable"
    assert page_family.normalize_table_kind("html") == "html"
    assert page_family.normalize_table_kind("bootstrap") == "bootstrap"
    assert page_family.normalize_table_kind("bootstrap-table") == "bootstrap"
    assert page_family.normalize_table_kind("bootstrap_table") == "bootstrap"
    assert page_family.normalize_table_kind("bt") == "bootstrap"
    assert page_family.normalize_table_kind("nope") == "auto"
    assert page_family.normalize_table_kind(None) == "auto"


def test_auto_table_scan_order_puts_preferred_first():
    assert page_family.auto_table_scan_order("bootstrap")[0] == "bootstrap"
    assert page_family.auto_table_scan_order("vtable")[0] == "vtable"
    assert page_family.auto_table_scan_order("html")[0] == "html"
    assert page_family.auto_table_scan_order("auto") == ["vtable", "bootstrap", "html"]
    # full set retained
    for preferred in ("bootstrap", "vtable", "html", "auto"):
        order = page_family.auto_table_scan_order(preferred)
        assert set(order) == {"vtable", "bootstrap", "html"}
        assert len(order) == 3


def test_legacy_probe_scan_order_starts_with_bootstrap():
    probe = {
        "signals": {"bootstrap_table": True, "vtable": False, "ant_table": False},
        "counts": {},
        "globals": {"bootstrapTablePlugin": True},
    }
    family, _, _ = page_family._score_family(probe)
    preferred = page_family._preferred_table_kind(family, probe)
    assert preferred == "bootstrap"
    assert page_family.auto_table_scan_order(preferred)[0] == "bootstrap"


def test_ui_contract_includes_layer_roots():
    from drissionpage_mcp.core import ui_contract, ui_contract_legacy

    assert ui_contract.LAYER_ROOT == ui_contract_legacy.LAYER_ROOT
    assert ui_contract.LAYER_ROOT in ui_contract.FLOAT_ROOTS
    assert ui_contract.LAYER_ROOT in ui_contract.OBSERVABLE_OVERLAYS
    assert "layer" in ui_contract.OVERLAY_CLOSE or "layui-layer-close" in ui_contract.OVERLAY_CLOSE


def test_dual_stack_tools_registered_in_metadata():
    from drissionpage_mcp.core import tool_metadata

    exposed = set()
    for tools in tool_metadata.CAP_GROUPS.values():
        exposed.update(tools)
    for name in (
        "detect_page_family",
        "scan_layer_content",
        "scan_table",
        "select_option",
        "set_field_value",
        "scan_form_fields",
        "observe_snapshot",
        "close_modal",
        "click_table_cell",
        "hover_table_cell",
        "table_action",
        "query_table",
    ):
        assert name in exposed
    assert "detect_page_family" in tool_metadata.ENTERPRISE_TOOLS
    assert "scan_layer_content" in tool_metadata.ENTERPRISE_TOOLS
    assert "set_field_value" in tool_metadata.ENTERPRISE_TOOLS
    assert "legacy" in tool_metadata.CAP_GROUPS


def test_public_table_facades_use_dual_stack_helpers():
    """click/hover public tools must route through dual-stack raw helpers (incl. bootstrap)."""
    import inspect
    from drissionpage_mcp import server
    from drissionpage_mcp.services import bootstrap_table

    click_src = inspect.getsource(server.click_table_cell)
    hover_src = inspect.getsource(server.hover_table_cell)
    click_raw = inspect.getsource(server._click_table_cell_raw)
    hover_raw = inspect.getsource(server._hover_table_cell_raw)
    table_action_src = inspect.getsource(server.table_action)
    query_src = inspect.getsource(server.query_table)

    assert "_click_table_cell_raw" in click_src
    assert "_hover_table_cell_raw" in hover_src
    assert "bootstrap_table.click_bootstrap_table_cell" in click_raw
    assert "bootstrap_table.hover_bootstrap_table_cell" in hover_raw
    assert "_auto_table_scan_order" in click_raw
    assert "_auto_table_scan_order" in hover_raw
    assert "bootstrap" in table_action_src
    assert "bootstrap" in query_src
    assert callable(bootstrap_table.hover_bootstrap_table_cell)
    assert callable(bootstrap_table.click_bootstrap_table_cell)
