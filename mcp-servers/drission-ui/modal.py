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
    """用 JS 检测 target 自身文档内的弹窗（document.querySelector 不递归 iframe，scope 精确）。

    修复两个历史 bug：
    ① tab.ele('c:.ant-modal-content') 会递归进 iframe 找到残留隐藏 modal，导致跨 frame 误判；
    ② 找到隐藏 modal 后早退 return none，跳过 notification/message 检查（保存成功 toast 被漏抓的根因）。

    现：隐藏/残留 modal 不早退，落入 notification/message 检查；可见性用 getBoundingClientRect 判定。
    """
    try:
        res = target.run_js(r"""
        function isVis(el){ var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }
        var m = document.querySelector('.ant-modal-content');
        if (m) {
            var w = document.querySelector('.ant-modal-wrap');
            var wrapHidden = !w || window.getComputedStyle(w).display === 'none';
            if (!wrapHidden && isVis(m)) {
                var isConfirm = !!m.querySelector('.ant-confirm-body');
                var title = m.querySelector('.ant-modal-title');
                var body = m.querySelector('.ant-modal-body');
                var btns = [].slice.call(m.querySelectorAll('.ant-btn')).map(function(b){return (b.textContent||'').trim();}).filter(Boolean);
                return JSON.stringify({type: isConfirm ? 'confirm' : 'interactive',
                    title: title ? (title.textContent||'').trim() : '',
                    content: body ? (body.textContent||'').trim().slice(0,200) : '',
                    buttons: btns, hasClose: !!m.querySelector('.ant-modal-close')});
            }
            // modal 隐藏/残留 → 不早退，继续查 notification/message
        }
        var n = document.querySelector('.ant-notification-notice');
        if (n && isVis(n)) {
            var m1 = n.querySelector('.ant-notification-notice-message');
            var d1 = n.querySelector('.ant-notification-notice-description');
            return JSON.stringify({type:'notification',
                message: ((m1 ? m1.textContent : '') || (d1 ? d1.textContent : '')).trim()});
        }
        var g = document.querySelector('.ant-message-notice');
        if (g && isVis(g)) {
            var c = g.querySelector('.ant-message-notice-content');
            return JSON.stringify({type:'message', message: c ? (c.textContent||'').trim().slice(0,200) : ''});
        }
        return JSON.stringify({type:'none'});
        """)
        d = json.loads(res) if isinstance(res, str) else res
        return d if isinstance(d, dict) else {"type": "none"}
    except Exception:
        return {"type": "none"}


def detect_modal(timeout: float = 0):
    """按优先级检测弹窗：①活动 iframe 内业务弹窗/消息 ②top 层弹窗/通知/消息 ③none。
    使用 DrissionPage 原生 ele() 检测，不依赖 JS 注入。

    顶层覆盖：confirm（→system_confirm，向后兼容）/ interactive / notification / message。
    修复历史盲区：顶层 .ant-message-notice（如「保存订单成功」toast，寿命~3s）此前被丢弃。
    """
    deadline = time.time() + timeout if timeout > 0 else None
    while True:
        tab = browser_session.get_tab_ro()
        fr = browser_session.get_active_frame_ro(tab)
        if fr is not None:
            info = _detect_in_target(fr)
            if info.get("type") != "none":
                info["scope"] = "iframe"
                return info
        top = _detect_in_target(tab)
        if top.get("type") != "none":
            # 顶层系统确认弹窗保留 system_confirm 类型名（向后兼容旧契约）
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
        notices = target.eles('c:.ant-notification-notice', timeout=0.5)
        for n in notices:
            btn = n.ele('c:.ant-notification-notice-close', timeout=0.3)
            if btn:
                btn.click()
                try:
                    n.wait.ele_deleted(timeout=2)
                    closed.append("notification")
                except Exception:
                    errors.append("notification: 等待关闭超时")

        # 关闭消息
        for m in target.eles('c:.ant-message-notice', timeout=0.3):
            btn = m.ele('c:.ant-message-notice-close', timeout=0.3)
            try:
                if btn:
                    btn.click()
                else:
                    m.run_js("this.remove()")
                closed.append("message")
            except Exception as e:
                errors.append("message: %s" % e)

        # 关闭业务弹窗（点取消优先，其次×）
        modal = target.ele('c:.ant-modal-content', timeout=0.5)
        if modal:
            cancel = modal.ele('c:.ant-btn:not(.ant-btn-primary)', timeout=0.3)
            if cancel:
                cancel.click()
            else:
                close_x = modal.ele('c:.ant-modal-close', timeout=0.3)
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
