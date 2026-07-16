"""Column identity: title + field + colIndex resolution (no browser)."""

from __future__ import annotations

from drissionpage_mcp.services import table_facade, vtable


def _columns():
    return [
        {"col": 0, "row": 0, "isHeader": True, "title": "单号", "field": "orderNo"},
        {"col": 1, "row": 0, "isHeader": True, "title": "状态", "field": "status"},
        {"col": 4, "row": 0, "isHeader": True, "title": "操作", "field": "actionLeft"},
        {"col": 8, "row": 0, "isHeader": True, "title": "操作", "field": "actionRight"},
        # Multi-level header noise for same col (should collapse to leaf/highest row)
        {"col": 1, "row": 1, "isHeader": True, "title": "状态", "field": "status"},
    ]


def test_resolve_by_unique_title():
    result = vtable.resolve_column(column_title="单号", columns=_columns())
    assert result["ok"] is True
    assert result["col"] == 0
    assert result["match"] == "title"
    assert result["column"]["field"] == "orderNo"


def test_resolve_by_field_when_title_ambiguous():
    ambiguous = vtable.resolve_column(column_title="操作", columns=_columns())
    assert ambiguous["ok"] is False
    assert "匹配不唯一" in ambiguous["reason"]
    assert {c["col"] for c in ambiguous["candidates"]} == {4, 8}
    assert {c["field"] for c in ambiguous["candidates"]} == {"actionLeft", "actionRight"}

    left = vtable.resolve_column(column_title="操作", field="actionLeft", columns=_columns())
    assert left["ok"] is True
    assert left["col"] == 4
    assert left["match"] == "title+field"

    right = vtable.resolve_column(field="actionRight", columns=_columns())
    assert right["ok"] is True
    assert right["col"] == 8
    assert right["match"] == "field"


def test_resolve_by_explicit_col():
    result = vtable.resolve_column(col=1, columns=_columns())
    assert result["ok"] is True
    assert result["col"] == 1
    assert result["match"] == "col"
    # Multi-level header collapses to highest row representative
    assert result["column"]["row"] == 1


def test_resolve_field_passed_via_column_title_legacy():
    result = vtable.resolve_column(column_title="orderNo", columns=_columns())
    assert result["ok"] is True
    assert result["col"] == 0
    assert result["match"] == "field-via-title"


def test_find_vtable_col_backward_compatible_api(monkeypatch):
    monkeypatch.setattr(
        vtable,
        "scan_vtable_columns",
        lambda max_col=100: {"ok": True, "columns": _columns()},
    )
    col, reason = table_facade._find_vtable_col("单号")
    assert col == 0 and reason is None

    col, reason = table_facade._find_vtable_col("操作")
    assert col is None
    assert "匹配不唯一" in reason

    col, reason = table_facade._find_vtable_col("操作", field="actionRight")
    assert col == 8 and reason is None


def test_resolve_action_col_enriches_ambiguous_reason(monkeypatch):
    monkeypatch.setattr(
        vtable,
        "scan_vtable_columns",
        lambda max_col=100: {"ok": True, "columns": _columns()},
    )
    col, reason = table_facade._resolve_vtable_action_col(column_title="操作")
    assert col is None
    assert "匹配不唯一" in reason
    assert "actionLeft" in reason and "actionRight" in reason
    assert "col=4" in reason


def test_resolve_rejects_missing_identity():
    result = vtable.resolve_column(columns=_columns())
    assert result["ok"] is False
    assert "需要提供" in result["reason"]
