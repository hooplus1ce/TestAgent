"""Network tools discovered by FileSystemProvider."""

from fastmcp.tools import tool

from drissionpage_mcp.components._sync import with_read, with_write
from drissionpage_mcp.services import network_record


@tool(name="listen_start")
def listen_start(targets, method: str = None) -> dict:
    """启动网络监听。targets 为 URL 特征：单个字符串、逗号分隔的多个特征、或列表。
    method 可选 'POST'/'GET'/'GET,POST'/'ALL' 等，采用 4.2 set_method 链式 API；
    不传则默认监听 GET+POST。每次启动都会重置 resource type，避免继承 WS-only 状态。
    """
    return with_write(network_record.listen_start, targets, method=method)


@tool(name="listen_wait")
def listen_wait(count: int = 1, timeout: float = 10, fit_count: bool = False) -> dict:
    """等待监听的数据包。返回 {url, method, api_target, post_data, status, body}。
    api_target 为请求头中的接口路由标识（同一 gateway URL 下区分不同接口）。
    post_data 为 POST 请求体（JSON 字符串），含查询参数如 conditions/isDelivery 等。
    count>1 返回 packets 列表。fit_count=False 时超时前抓到多少返回多少，适合探索式断言。
    """
    return with_write(
        network_record.listen_wait,
        count=count,
        timeout=timeout,
        fit_count=fit_count,
    )


@tool(name="listen_stop")
def listen_stop() -> dict:
    """停止网络监听（与 listen_start 配对，避免监听器泄漏）。"""
    return with_write(network_record.listen_stop)


@tool(name="network_record_start")
def network_record_start(targets=None, method: str = None) -> dict:
    """启动网络时间线记录。targets 为 URL 特征；method 默认 GET,POST，支持 POST/GET/ALL 等。

    与 listen_start 不同，本工具用于围绕一段业务操作收集多包时间线：
    network_record_start -> 执行业务动作 -> network_record_stop。
    """
    return with_write(network_record.start, targets=targets, method=method)


@tool(name="network_record_stop")
def network_record_stop(
    timeout: float = 3.0,
    max_packets: int = 50,
    fit_count: bool = False,
    max_body_chars: int = 12000,
) -> dict:
    """停止网络时间线记录并返回捕获到的数据包列表。fit_count=False 时超时前抓到多少返回多少。"""
    return with_write(
        network_record.stop,
        timeout=timeout,
        max_packets=max_packets,
        fit_count=fit_count,
        max_body_chars=max_body_chars,
    )


@tool(name="network_trace_start")
def network_trace_start(targets=None, method: str = None) -> dict:
    """开始多步骤业务网络证据采集。

    单个动作优先使用 explore_action(listen_targets=...)；只有需要覆盖多个连续动作时
    才使用 network_trace_start -> actions -> network_trace_stop。
    """
    return with_write(network_record.start, targets=targets, method=method)


@tool(name="network_trace_stop")
def network_trace_stop(
    timeout: float = 3.0,
    max_packets: int = 50,
    fit_count: bool = False,
    max_body_chars: int = 12000,
) -> dict:
    """结束多步骤网络证据采集并返回脱敏后的数据包时间线。"""
    return with_write(
        network_record.stop,
        timeout=timeout,
        max_packets=max_packets,
        fit_count=fit_count,
        max_body_chars=max_body_chars,
    )


@tool(name="network_record_export")
def network_record_export(filename: str = None) -> dict:
    """导出最近一次 network_record_stop 的数据包到 JSON 文件。"""
    return with_read(network_record.export, filename=filename)


@tool(name="listen_ws_start")
def listen_ws_start(targets: str = None) -> dict:
    """启动 4.2 WebSocket 专项监听，并重置此前 listener 状态。"""
    return with_write(network_record.listen_ws_start, targets)


@tool(name="listen_ws_wait")
def listen_ws_wait(count: int = 1, timeout: float = 10, fit_count: bool = False) -> dict:
    """等待 WebSocket 数据包，并限制每个 payload 的输出体积。"""
    return with_write(
        network_record.listen_ws_wait,
        count=count,
        timeout=timeout,
        fit_count=fit_count,
    )
