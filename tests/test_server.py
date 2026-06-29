"""server.py 测试：工具注册数量 + synchronized 串行化。"""
import asyncio
import threading
import time


def test_tool_count():
    """应注册 28 个工具。"""
    import server
    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) == 33, "工具数应为 33（28 + 5 个 4.2 新增: download_by_browser/listen_ws_start/listen_ws_wait/new_context/set_permission）"


def test_listen_stop_registered():
    """listen_stop 工具应存在。"""
    import server
    tools = asyncio.run(server.mcp.list_tools())
    names = [t.name for t in tools]
    assert "listen_stop" in names
    assert "listen_start" in names
    assert "listen_wait" in names
    assert "listen_ws_start" in names
    assert "listen_ws_wait" in names
    assert "download_by_browser" in names
    assert "new_context" in names
    assert "set_permission" in names


def test_synchronized_serializes():
    """synchronized 装饰器应串行化调用（通过 browser_session._lock）。"""
    import server
    import browser_session

    call_order = []

    @server.synchronized
    def slow_task(label):
        call_order.append(("start", label))
        time.sleep(0.05)
        call_order.append(("end", label))

    threads = [threading.Thread(target=slow_task, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 串行化：每个任务的 start/end 不交错
    labels_in_order = [label for _, label in call_order]
    assert len(labels_in_order) == 6
    # 第一个 start 的 label 应该和它的 end 紧邻（无其他 start 插入）
    first_label = labels_in_order[0]
    assert labels_in_order[1] == first_label, "任务应串行：start 后立即 end"


def test_synchronized_returns_value():
    """synchronized 不改变函数返回值。"""
    import server

    @server.synchronized
    def add(a, b):
        return a + b

    assert add(2, 3) == 5
