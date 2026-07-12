"""HTML 表格可见性、业务行与统一值契约测试。"""
import json
from unittest.mock import patch


def test_get_html_table_values_returns_scalar_values_and_cell_metadata():
    import html_table

    payload = {
        "values": ["SO-1", "SO-2"],
        "cells": [
            {"row": 0, "text": "SO-1", "hasLink": True},
            {"row": 1, "text": "SO-2", "hasLink": False},
        ],
        "column": "订单号",
        "count": 2,
    }

    class Frame:
        def run_js(self, script):
            assert "visible table not found" in script
            return json.dumps(payload, ensure_ascii=False)

    with patch.object(html_table, "_get_frame", return_value=Frame()):
        result = html_table.get_html_table_values("订单号")

    assert result == {"ok": True, **payload}


def test_visible_table_wrappers_maps_only_uncovered_dom_indexes():
    import html_table

    hidden = object()
    covered = object()
    visible = object()

    class Frame:
        def run_js(self, script):
            assert "duVisible(wrapper)" in script
            return json.dumps([2])

        def eles(self, locator, timeout=None):
            assert locator == "c:.ant-table-wrapper"
            return [hidden, covered, visible]

    assert html_table._visible_table_wrappers(Frame()) == [visible]


def test_business_rows_excludes_placeholder_expanded_and_measure_rows():
    import html_table

    business_row = object()

    class Body:
        def __init__(self):
            self.calls = []

        def eles(self, locator, timeout=None):
            self.calls.append(locator)
            if locator == "css:tbody > tr.ant-table-row":
                return []
            return [business_row]

    body = Body()

    class Wrapper:
        def ele(self, locator, timeout=None):
            return body if locator == "c:.ant-table-body table" else None

    rows = html_table._business_rows(Wrapper())

    assert rows == [business_row]
    fallback = body.calls[-1]
    assert ".ant-table-placeholder" in fallback
    assert ".ant-table-expanded-row" in fallback
    assert ".ant-table-measure-row" in fallback
    assert "aria-hidden" in fallback


def test_row_selection_rejects_negative_index_before_browser_lookup():
    import html_table

    with patch.object(html_table, "_get_frame") as get_frame:
        result = html_table.click_html_row_selection(row=-1)

    assert result["ok"] is False
    assert "非负整数" in result["reason"]
    get_frame.assert_not_called()
