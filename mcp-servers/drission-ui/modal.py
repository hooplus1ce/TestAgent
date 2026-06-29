"""弹窗三级检测：iframe 内业务弹窗/消息 → top 层系统确认弹窗 → 无。

移植自 references/modal-types.md 的检测代码模板。

智能等待：timeout > 0 时会轮询直到弹窗出现或超时，找到就立即返回。
"""
import json
import logging
import time

import browser_session

logger = logging.getLogger("drission-ui")


def _detect_in_doc(run_js_target):
    """在给定对象(frame 或 tab)的 document 内运行检测脚本。"""
    script = browser_session.load_js("modal-detect.js") + "\nreturn JSON.stringify(detectModalInDoc());"
    res = run_js_target.run_js(script)
    if isinstance(res, str):
        return json.loads(res) if res else {"type": "none"}
    return res or {"type": "none"}


def detect_modal(timeout: float = 0):
    """按优先级检测弹窗：①活动 iframe 内业务弹窗/消息 ②top 层系统确认弹窗 ③none。

    Args:
        timeout: 等待弹窗出现的秒数。
          - 0 (默认): 只检测一次，不等待，立即返回结果
          - > 0: 轮询直到弹窗出现或超时，找到就立即返回，不等满超时

    Returns:
        {type, title?, content?, buttons?, hasClose?, message?, scope?} 或 {type: "none"}
        type 取值: interactive / confirm / system_confirm / notification / message / none
    """
    deadline = time.time() + timeout if timeout > 0 else None
    while True:
        tab = browser_session.get_tab()
        fr = browser_session.get_active_frame(tab)
        # ① 优先在 iframe 内检测
        if fr is not None:
            info = _detect_in_doc(fr)
            if info.get("type") != "none":
                info["scope"] = "iframe"
                if timeout > 0:
                    info["waited"] = round(time.time() - (time.time() - timeout), 2)
                return info
        # ② top 层系统级确认弹窗
        top = _detect_in_doc(tab)
        # top 层只把含 .ant-confirm-body-wrapper 的视为系统级确认弹窗
        if top.get("type") in ("confirm", "interactive"):
            if top.get("type") == "confirm":
                top["type"] = "system_confirm"
                top["scope"] = "top"
                if timeout > 0:
                    top["waited"] = round(time.time() - (time.time() - timeout), 2)
                return top
        if deadline is None or time.time() >= deadline:
            result = {"type": "none"}
            if timeout > 0:
                result["waited"] = timeout
            return result
        # 没找到还在 deadline 内，等一小会儿再试
        time.sleep(0.15)


def mouse_trail(on: bool = True):
    """开启/关闭鼠标轨迹可视化。首次注入 mouse-trail-inject.js（主页面 + 可见 iframe）。"""
    tab = browser_session.get_tab()
    code = browser_session.load_js("mouse-trail-inject.js")
    # 注入到主页面
    tab.run_js(code)
    # 注入到活动 iframe
    fr = browser_session.get_active_frame(tab)
    if fr is not None:
        try:
            fr.run_js(code)
        except Exception as e:
            logger.warning("iframe 内注入 mouse-trail 失败: %s", e)
    cmd = "window.mt.on();" if on else "window.mt.off();"
    tab.run_js(cmd)
    if fr is not None:
        try:
            fr.run_js(cmd)
        except Exception as e:
            logger.warning("iframe 内切换 mouse-trail 状态失败: %s", e)
    return {"ok": True, "on": on}
