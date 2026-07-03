"""server.py 测试：工具注册数量 + synchronized 串行化。"""
import asyncio
import threading
import time


def test_tool_count():
    """应注册 49 个工具（cache_session 已废弃移除）。"""
    import server
    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) == 49, f"工具数应为 49，当前为 {len(tools)}"


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

    # 验证串行化：enter/exit 对不交错
    assert len(results) == 6
    # 每对 enter/exit 的 label 应相同
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


def test_new_tools_registered():
    """新增的 find_elements/find_static/find_batch/get_frame 应存在。"""
    import server
    tools = asyncio.run(server.mcp.list_tools())
    names = [t.name for t in tools]
    assert "find_elements" in names, "find_elements 工具缺失"
    assert "find_static" in names, "find_static 工具缺失"
    assert "find_batch" in names, "find_batch 工具缺失"
    assert "get_frame" in names, "get_frame 工具缺失"

