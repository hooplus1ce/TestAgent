"""弹窗三级检测：iframe 内业务弹窗/消息 → top 层系统确认弹窗 → 无。

移植自 references/modal-types.md 的检测代码模板。

智能等待：timeout > 0 时会轮询直到弹窗出现或超时，找到就立即返回。
"""
import json
import logging
import time

import browser_session

logger = logging.getLogger("drission-ui")


def _detect_in_target(target):
    """用 DrissionPage 原生方法检测 target（tab/frame）内的弹窗。返回 {type, ...} 或 {type: "none"}。"""
    try:
        modal = target.ele('css:.ant-modal-content', timeout=0.3)
        if modal:
            # 优先检查：ant-modal-wrap 为 display:none 则弹窗已关闭
            # （React 组件卸载不彻底时 ant-modal 残留但 wrap 已隐藏，这是最可靠的关闭判定）
            try:
                wrap_hidden = target.run_js(
                    "var w=document.querySelector('.ant-modal-wrap');"
                    "if(!w)return true;"
                    "return window.getComputedStyle(w).display==='none';"
                )
                if wrap_hidden:
                    return {"type": "none"}
            except Exception:
                pass
            # 二次确认：ghost element（已销毁但 DP 缓存未 GC）返回 false
            try:
                if not modal.states.is_displayed:
                    return {"type": "none"}
            except Exception:
                pass
        if modal:
            title_el = modal.ele('css:.ant-modal-title', timeout=0.2)
            content_el = modal.ele('css:.ant-modal-body', timeout=0.2)
            buttons = [b.text for b in modal.eles('css:.ant-btn', timeout=0.2) if b.text]
            has_close = modal.ele('css:.ant-modal-close', timeout=0.2) is not None
            is_confirm = modal.ele('css:.ant-confirm-body', timeout=0.1) is not None
            return {
                "type": "confirm" if is_confirm else "interactive",
                "title": title_el.text if title_el else "",
                "content": content_el.text[:200] if content_el else "",
                "buttons": buttons,
                "hasClose": has_close,
            }

        notif = target.ele('css:.ant-notification-notice', timeout=0.2)
        if notif:
            msg_el = notif.ele('css:.ant-notification-notice-message', timeout=0.1)
            desc_el = notif.ele('css:.ant-notification-notice-description', timeout=0.1)
            return {
                "type": "notification",
                "message": (msg_el.text if msg_el else "") or (desc_el.text if desc_el else ""),
            }

        msg = target.ele('css:.ant-message-notice', timeout=0.2)
        if msg:
            text_el = msg.ele('css:.ant-message-notice-content', timeout=0.1)
            return {"type": "message", "message": text_el.text[:200] if text_el else ""}

    except Exception:
        pass
    return {"type": "none"}


def detect_modal(timeout: float = 0):
    """按优先级检测弹窗：①活动 iframe 内业务弹窗/消息 ②top 层系统确认弹窗 ③none。
    使用 DrissionPage 原生 ele() 检测，不依赖 JS 注入。
    """
    deadline = time.time() + timeout if timeout > 0 else None
    while True:
        tab = browser_session.get_tab()
        fr = browser_session.get_active_frame(tab)
        if fr is not None:
            info = _detect_in_target(fr)
            if info.get("type") != "none":
                info["scope"] = "iframe"
                return info
        top = _detect_in_target(tab)
        if top.get("type") == "confirm":
            top["type"] = "system_confirm"
            top["scope"] = "top"
            return top
        if deadline is None or time.time() >= deadline:
            waited = round(time.time() - (deadline - timeout), 2) if deadline else timeout
            return {"type": "none", "waited": waited}
        time.sleep(0.15)


def mouse_trail(on: bool = True):
    """开启/关闭鼠标轨迹可视化(红色圆点跟踪 mousemove/click)。同时开启 top 层和活动 iframe。"""
    tab = browser_session.get_tab()
    tab.set.show_trail(on)
    fr = browser_session.get_active_frame(tab)
    if fr is not None:
        try:
            fr.set.show_trail(on)
        except Exception as e:
            logger.debug("iframe show_trail 失败: %s", e)
    return {"ok": True, "on": on}

def close_modal(tab=None):
    """关闭当前残留的弹窗/通知/消息，避免积累在 DOM 中干扰后续操作。
    通知→点×关闭；业务弹窗→点取消或×。
    使用 DrissionPage 原生方法 + wait.ele_deleted 等待关闭完成。
    返回 {ok, closed:[...], errors:[...]}，调用方可判断清理是否真正成功。
    """
    tab = tab or browser_session.get_tab()
    fr = browser_session.get_active_frame(tab)
    target = fr if fr is not None else tab
    closed = []
    errors = []
    try:
        # 关闭通知
        notices = target.eles('css:.ant-notification-notice', timeout=0.5)
        for n in notices:
            btn = n.ele('css:.ant-notification-notice-close', timeout=0.3)
            if btn:
                btn.click()
                try:
                    n.wait.ele_deleted(timeout=2)
                    closed.append("notification")
                except Exception:
                    errors.append("notification: 等待关闭超时")

        # 关闭消息
        for m in target.eles('css:.ant-message-notice', timeout=0.3):
            btn = m.ele('css:.ant-message-notice-close', timeout=0.3)
            try:
                if btn:
                    btn.click()
                else:
                    m.run_js("this.remove()")
                closed.append("message")
            except Exception as e:
                errors.append("message: %s" % e)

        # 关闭业务弹窗（点取消优先，其次×）
        modal = target.ele('css:.ant-modal-content', timeout=0.5)
        if modal:
            cancel = modal.ele('css:.ant-btn:not(.ant-btn-primary)', timeout=0.3)
            if cancel:
                cancel.click()
            else:
                close_x = modal.ele('css:.ant-modal-close', timeout=0.3)
                if close_x:
                    close_x.click()
                else:
                    errors.append("modal: 无可点击的取消/关闭按钮")
            if "modal: 无可点击的取消/关闭按钮" not in errors:
                # 优先等元素从 DOM 删除，超时后降级检查 ant-modal-wrap 是否 display:none
                try:
                    modal.wait.ele_deleted(timeout=3)
                    closed.append("modal")
                except Exception:
                    # React 组件卸载不彻底时 ant-modal 残留但 wrap 已隐藏
                    try:
                        wrap_hidden = target.run_js(
                            "var w=document.querySelector('.ant-modal-wrap');"
                            "if(!w)return true;"
                            "return window.getComputedStyle(w).display==='none';"
                        )
                        if wrap_hidden:
                            closed.append("modal")
                        else:
                            errors.append("modal: 等待关闭超时")
                    except Exception:
                        errors.append("modal: 等待关闭超时")
    except Exception as e:
        logger.debug("close_modal 失败: %s", e)
        errors.append(str(e))
    return {"ok": not errors, "closed": closed, "errors": errors}
