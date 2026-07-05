"""server.py 测试：公开工具面 + synchronized 串行化。"""
import asyncio
import threading
import time
from unittest.mock import patch


EXPECTED_PUBLIC_TOOLS = {
    "connect",
    "refresh_session",
    "check_session",
    "enter_module",
    "scan_filter_fields",
    "get_active_frame",
    "dom_tree",
    "scan_page_elements",
    "find_elements",
    "find_batch",
    "click",
    "click_xy",
    "select_date_range",
    "input",
    "insert_text",
    "hover",
    "screenshot",
    "run_js",
    "scan_table",
    "get_table_values",
    "get_table_data",
    "click_table_cell",
    "hover_table_cell",
    "resize_table_column",
    "close_modal",
    "observe_start",
    "observe_wait",
    "listen_start",
    "listen_wait",
    "listen_stop",
    "mouse_trail",
    "download_by_browser",
    "listen_ws_start",
    "listen_ws_wait",
    "new_context",
    "switch_context",
    "list_contexts",
    "set_permission",
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
