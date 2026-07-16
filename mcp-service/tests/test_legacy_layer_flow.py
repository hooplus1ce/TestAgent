"""Unit tests for shade → bootstrap row → layer iframe convergence (no browser)."""

from __future__ import annotations

from drissionpage_mcp.services import bootstrap_table, layer_modal, legacy_layer_flow


def test_clear_blocking_shade_delegates(monkeypatch):
    monkeypatch.setattr(
        layer_modal,
        "clear_layer_shade",
        lambda timeout=2.0: {"ok": True, "had_shade": True, "closed": True, "scope": "iframe"},
    )
    result = legacy_layer_flow.clear_blocking_shade(timeout=1.5)
    assert result["closed"] is True


def test_select_bootstrap_row_passes_close_shade(monkeypatch):
    calls = []

    def fake_select(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "kind": "bootstrap", "row": kwargs.get("row", 0)}

    monkeypatch.setattr(bootstrap_table, "click_bootstrap_row_selection", fake_select)
    result = legacy_layer_flow.select_bootstrap_row(row=2, close_shade=True)
    assert result["ok"] is True
    assert calls[0]["row"] == 2
    assert calls[0]["close_shade"] is True


def test_select_row_open_layer_happy_path(monkeypatch):
    steps = {"shade": 0, "select": 0, "toolbar": 0, "layer": 0}

    monkeypatch.setattr(
        legacy_layer_flow,
        "clear_blocking_shade",
        lambda timeout=2.0: steps.__setitem__("shade", 1)
        or {"ok": True, "had_shade": True, "closed": True},
    )
    monkeypatch.setattr(
        legacy_layer_flow,
        "select_bootstrap_row",
        lambda **kw: steps.__setitem__("select", 1)
        or {"ok": True, "kind": "bootstrap", "row": kw.get("row", 0), "close_shade": kw.get("close_shade")},
    )
    monkeypatch.setattr(
        legacy_layer_flow,
        "click_frame_toolbar",
        lambda text, timeout=5.0: steps.__setitem__("toolbar", 1)
        or {"ok": True, "text": text},
    )
    monkeypatch.setattr(
        legacy_layer_flow,
        "enter_layer_iframe",
        lambda **kw: steps.__setitem__("layer", 1)
        or {
            "ok": True,
            "entered": True,
            "content_kind": "nested_iframe",
            "meta": {"layerKind": "iframe", "title": "编辑"},
            "scan": {"ok": True, "fieldCount": 3, "buttonCount": 2},
        },
    )

    result = legacy_layer_flow.select_row_open_layer(row=1, toolbar_text="编辑")
    assert result["ok"] is True
    assert result["kind"] == "bootstrap+layer"
    assert result["fieldCount"] == 3
    assert steps == {"shade": 1, "select": 1, "toolbar": 1, "layer": 1}
    # After shade clear, row select should not re-fight shade.
    assert result["steps"]["selection"].get("close_shade") is False


def test_select_row_open_layer_stops_on_selection_failure(monkeypatch):
    monkeypatch.setattr(
        legacy_layer_flow,
        "clear_blocking_shade",
        lambda timeout=2.0: {"ok": True, "had_shade": False, "closed": False},
    )
    monkeypatch.setattr(
        legacy_layer_flow,
        "select_bootstrap_row",
        lambda **kw: {"ok": False, "reason": "row checkbox not found"},
    )
    called = {"toolbar": False}

    def boom(*_a, **_k):
        called["toolbar"] = True
        return {"ok": True}

    monkeypatch.setattr(legacy_layer_flow, "click_frame_toolbar", boom)
    result = legacy_layer_flow.select_row_open_layer(row=0)
    assert result["ok"] is False
    assert "row checkbox" in result["reason"]
    assert called["toolbar"] is False


def test_select_row_open_layer_skips_toolbar_when_empty(monkeypatch):
    monkeypatch.setattr(
        legacy_layer_flow,
        "clear_blocking_shade",
        lambda timeout=2.0: {"ok": True, "had_shade": False, "closed": False},
    )
    monkeypatch.setattr(
        legacy_layer_flow,
        "select_bootstrap_row",
        lambda **kw: {"ok": True, "row": 0},
    )
    monkeypatch.setattr(
        legacy_layer_flow,
        "click_frame_toolbar",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("toolbar must be skipped")),
    )
    monkeypatch.setattr(
        legacy_layer_flow,
        "enter_layer_iframe",
        lambda **kw: {
            "ok": True,
            "entered": True,
            "content_kind": "nested_iframe",
            "meta": {"layerKind": "iframe"},
            "scan": {"ok": True, "fieldCount": 1, "buttonCount": 1},
        },
    )
    result = legacy_layer_flow.select_row_open_layer(toolbar_text="")
    assert result["ok"] is True
    assert result["steps"]["toolbar"].get("skipped") is True


def test_bootstrap_row_selection_uses_layer_modal_shade(monkeypatch):
    """click_bootstrap_row_selection integrates shared shade helpers."""
    calls = []

    class Fr:
        pass

    monkeypatch.setattr(bootstrap_table, "_get_frame", lambda: Fr())
    monkeypatch.setattr(
        layer_modal,
        "detect_layer_shade",
        lambda parent=None, timeout=0.5: calls.append("detect")
        or {"ok": True, "has_shade": True, "scope": "iframe"},
    )
    monkeypatch.setattr(
        layer_modal,
        "clear_layer_shade",
        lambda parent=None, timeout=2.0: calls.append("clear")
        or {"ok": True, "had_shade": True, "closed": True, "scope": "iframe"},
    )
    monkeypatch.setattr(
        bootstrap_table,
        "_run_json",
        lambda fr, js: {
            "ok": True,
            "checked": False,
            "alreadySelected": False,
            "dataIndex": "0",
        },
    )

    class Cb:
        def wait(self):
            return self

        def clickable(self, timeout=1.0, raise_err=False):
            return True

        def click(self, by_js=False, timeout=2):
            return True

    fr = Fr()
    fr.ele = lambda *a, **k: Cb()
    monkeypatch.setattr(bootstrap_table, "_get_frame", lambda: fr)
    monkeypatch.setattr(bootstrap_table, "_click_marked_checkbox", lambda fr, cb: None)
    monkeypatch.setattr(bootstrap_table, "_clear_bt_select_mark", lambda fr: None)

    result = bootstrap_table.click_bootstrap_row_selection(row=0, close_shade=True)
    assert result["ok"] is True
    assert "detect" in calls and "clear" in calls
    assert result.get("shade_closed") is True
