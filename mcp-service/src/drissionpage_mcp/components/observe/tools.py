"""Observation and modal tools discovered by FileSystemProvider."""

from fastmcp.tools import tool

from drissionpage_mcp.components._sync import with_read, with_write
from drissionpage_mcp.services import modal, observe


@tool(name="close_modal")
def close_modal() -> dict:
    """关闭当前残留的弹窗/通知/消息，避免积累在 DOM 中干扰后续交互。
    每次 detect_modal() 返回非 none 后调用此函数清理。
    通知类 → 点×关闭；业务确认弹窗 → 点取消或×。
    返回 {ok, closed:[...], errors:[...]}，可判断清理是否成功。
    """
    return with_write(modal.close_modal)


@tool(name="observe_start")
def observe_start(signals: list[str] = None, listen_targets: str = None) -> dict:
    """两段式观察器·启动：**点击前**调用，安装 MutationObserver + 网络监听，立即返回。
    observer 在点击前就已监听，消除「点击→观察」调用间隙（agent 思考时间可能 > toast 寿命），
    可靠捕获短寿命 toast（如保存成功 ~3s）。必须配对调用 observe_wait() 读取信号并清理。

    Args:
        signals: 监听信号类型列表，默认 ['overlay','notification','message','tab','url']。
        listen_targets: 网络监听 URL 特征（逗号分隔）；仅 signals 含 'network' 时生效。
    """
    return with_write(
        observe.observe_start,
        signals=signals,
        listen_targets=listen_targets,
    )


@tool(name="observe_wait")
def observe_wait(
    timeout: float = 8,
    poll_interval: float = 0.12,
    include_snapshot: bool = True,
) -> dict:
    """两段式观察器·等待：轮询 observe_start 安装的 observer，任一信号命中立即返回，
    随后清理 observer + listener。须在 observe_start 之后、点击之后调用。
    """
    return with_write(
        observe.observe_wait,
        timeout=timeout,
        poll_interval=poll_interval,
        include_snapshot=include_snapshot,
    )


@tool(name="observe_snapshot")
def observe_snapshot(
    only_visible: bool = True,
    include_table_data: bool = False,
    detail: str = "summary",
) -> dict:
    """统一观察器快照：读取当前可见浮层/弹窗/抽屉/dropdown/calendar/toast。

    这是手工检查当前浮层状态的推荐入口；legacy scan_floats/scan_modal/scan_drawer 保留内部兼容，
    但不再作为默认公开工具暴露。
    """
    return with_read(
        observe.observe_snapshot,
        only_visible=only_visible,
        include_table_data=include_table_data,
        detail=detail,
    )
