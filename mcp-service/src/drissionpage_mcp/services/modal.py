"""弹窗三级检测：iframe 内业务弹窗/消息 → top 层系统确认弹窗 → 无。

移植自 references/modal-types.md 的检测代码模板。

智能等待：timeout > 0 时会轮询直到弹窗出现或超时，找到就立即返回。
"""
import json
import logging
from time import monotonic

from ..core import ui_contract
from . import browser_session

logger = logging.getLogger("drissionpage-mcp")




def _parse_json(res):
    if res is None:
        return None
    if isinstance(res, str):
        try:
            return json.loads(res)
        except Exception:
            return None
    return res




def _detect_in_target(target):
    """检测当前 document 内最高层可见反馈，不递归 iframe。

    Ant Design 3 会保留隐藏 modal 节点，且同一时刻可能叠加业务弹窗与确认框；因此按
    z-index + DOM 顺序选择最上层组件。所有根选择器来自 ``ui_contract``，前端升级时
    不必再修改本函数的业务逻辑。
    """
    script = r"""
var MODAL_CONTENT = __MODAL_CONTENT__;
var MODAL_WRAP = __MODAL_WRAP__;
var CONFIRM_BODY = __CONFIRM_BODY__;
var NOTIFICATION = __NOTIFICATION__;
var MESSAGE = __MESSAGE__;
function clean(value){ return (value || '').replace(/\s+/g, ' ').trim(); }
function isVis(el){
  if (!el || !el.isConnected) return false;
  var cur = el;
  while (cur && cur.nodeType === 1) {
    var style = window.getComputedStyle(cur);
    if (style.display === 'none' || style.visibility === 'hidden' ||
        style.visibility === 'collapse' || style.opacity === '0') return false;
    cur = cur.parentElement;
  }
  var rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
function zIndex(el){
  var current = el;
  var highest = 0;
  while (current && current.nodeType === 1) {
    var parsed = parseInt(window.getComputedStyle(current).zIndex, 10);
    if (!isNaN(parsed)) highest = Math.max(highest, parsed);
    current = current.parentElement;
  }
  return highest;
}
function topmost(selector, accept){
  var nodes = [].slice.call(document.querySelectorAll(selector)).filter(function(el){
    return isVis(el) && (!accept || accept(el));
  });
  nodes.sort(function(left, right){
    var delta = zIndex(left) - zIndex(right);
    if (delta) return delta;
    var pos = left.compareDocumentPosition(right);
    return (pos & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1;
  });
  return nodes.length ? nodes[nodes.length - 1] : null;
}
var modal = topmost(MODAL_CONTENT, function(content){
  var wrap = content.closest(MODAL_WRAP);
  return !wrap || isVis(wrap);
});
if (modal) {
  var isConfirm = !!modal.querySelector(CONFIRM_BODY);
  var title = modal.querySelector('.ant-modal-title');
  var body = modal.querySelector('.ant-modal-body');
  var buttons = [].slice.call(modal.querySelectorAll('.ant-btn'))
    .filter(isVis).map(function(button){ return clean(button.textContent); }).filter(Boolean);
  return JSON.stringify({
    type: isConfirm ? 'confirm' : 'interactive',
    title: title ? clean(title.textContent) : '',
    content: body ? clean(body.textContent).slice(0, 200) : '',
    buttons: buttons,
    hasClose: !!modal.querySelector('.ant-modal-close')
  });
}
var notice = topmost(NOTIFICATION);
if (notice) {
  var noticeTitle = notice.querySelector('.ant-notification-notice-message');
  var noticeDescription = notice.querySelector('.ant-notification-notice-description');
  return JSON.stringify({
    type:'notification',
    message: clean((noticeTitle ? noticeTitle.textContent : '') ||
      (noticeDescription ? noticeDescription.textContent : ''))
  });
}
var message = topmost(MESSAGE);
if (message) {
  var messageContent = message.querySelector('.ant-message-notice-content');
  return JSON.stringify({
    type:'message',
    message: messageContent ? clean(messageContent.textContent).slice(0, 200) : ''
  });
}
return JSON.stringify({type:'none'});
"""
    replacements = {
        "__MODAL_CONTENT__": ui_contract.MODAL_CONTENT,
        "__MODAL_WRAP__": ui_contract.MODAL_WRAP,
        "__CONFIRM_BODY__": ui_contract.CONFIRM_BODY,
        "__NOTIFICATION__": ui_contract.NOTIFICATION,
        "__MESSAGE__": ui_contract.MESSAGE,
    }
    for token, selector in replacements.items():
        script = script.replace(token, json.dumps(selector))
    try:
        parsed = _parse_json(target.run_js(script))
        return parsed if isinstance(parsed, dict) else {"type": "none"}
    except Exception:
        return {"type": "none"}


def detect_modal(timeout: float = 0):
    """按优先级检测活动 iframe 与顶层浮层，并用 DrissionPage 被动等待限速。"""
    timeout = max(float(timeout or 0), 0.0)
    started = monotonic()
    deadline = started + timeout
    while True:
        tab = browser_session.get_tab_ro()
        fr = browser_session.get_active_frame_ro(
            tab, timeout=min(max(deadline - monotonic(), 0.0), 0.5)
        )
        if fr is not None:
            info = _detect_in_target(fr)
            if info.get("type") != "none":
                info["scope"] = "iframe"
                return info
        top = _detect_in_target(tab)
        if top.get("type") != "none":
            if top.get("type") == "confirm":
                top["type"] = "system_confirm"
            top["scope"] = "top"
            return top

        remaining = deadline - monotonic()
        if remaining <= 0:
            return {"type": "none", "waited": round(monotonic() - started, 2)}
        # 用页面 waiter 代替 Python 固定 sleep；任一 iframe 浮层出现可提前唤醒。
        wait_target = fr if fr is not None else tab
        try:
            wait_target.wait.ele_displayed(
                "c:" + ",".join((
                    ui_contract.MODAL_CONTENT,
                    ui_contract.NOTIFICATION,
                    ui_contract.MESSAGE,
                )),
                timeout=min(remaining, 0.15),
                raise_err=False,
            )
        except Exception:
            pass


def mouse_trail(on: bool = True):
    """通过 4.2 set.show_trail 同步设置顶层 Tab 与活动业务 iframe。"""
    tab = browser_session.get_tab()
    applied = []
    errors = []
    try:
        tab.set.show_trail(on)
        applied.append("top")
    except Exception as exc:
        errors.append("top: %s" % exc)
    frame = browser_session.get_active_frame_ro(tab, timeout=0.5)
    if frame is None:
        frame = browser_session.get_active_frame(tab)
    if frame is not None:
        try:
            frame.set.show_trail(on)
            applied.append("iframe")
        except Exception as exc:
            errors.append("iframe: %s" % exc)
    return {"ok": not errors, "on": on, "applied": applied, "errors": errors}




def _target_contexts(tab):
    frame = browser_session.get_active_frame_ro(tab, timeout=0.5)
    if frame is None:
        try:
            frame = browser_session.get_active_frame(tab)
        except Exception:
            frame = None
    contexts = []
    if frame is not None:
        contexts.append(("iframe", frame))
    contexts.append(("top", tab))
    return contexts




def _document_root(target):
    """顶层 Tab 从 body 限定搜索，避免 DrissionPage 递归进入 iframe。"""
    if getattr(target, "_type", None) == "ChromiumFrame":
        return target
    try:
        body = target.ele("t:body", timeout=0.2)
        return body or target
    except Exception:
        return target


def clear_transient_overlays(tab=None):
    """仅通过可见关闭控件清理 notification/message，不删除业务 DOM。"""
    tab = tab or browser_session.get_tab()
    errors = []
    closed = []
    for scope, target in _target_contexts(tab):
        root = _document_root(target)
        try:
            for notice in root.eles("c:" + ui_contract.NOTIFICATION, timeout=0.2) or []:
                if not notice.states.is_displayed:
                    continue
                close = notice.ele('c:.ant-notification-notice-close', timeout=0.2)
                if not close:
                    continue
                text = (notice.text or "").strip()
                close.click(by_js=False, timeout=1, wait_stop=False)
                target.wait.ele_hidden(notice, timeout=1, raise_err=False)
                closed.append({"scope": scope, "type": "notification", "message": text})

            for message in root.eles("c:" + ui_contract.MESSAGE, timeout=0.2) or []:
                if not message.states.is_displayed:
                    continue
                close = message.ele(
                    'c:.ant-message-close,.ant-message-notice-close', timeout=0.2
                )
                if not close:
                    continue
                text = (message.text or "").strip()
                close.click(by_js=False, timeout=1, wait_stop=False)
                target.wait.ele_hidden(message, timeout=1, raise_err=False)
                closed.append({"scope": scope, "type": "message", "message": text})
        except Exception as exc:
            logger.debug("clear transient overlays 失败(%s): %s", scope, exc)
            errors.append("%s: %s" % (scope, exc))
    return {"ok": not errors, "closed": closed, "errors": errors}


def close_modal(tab=None):
    """安全关闭可见 modal/drawer，并清理可关闭的 notification/message。

    不点击“确定/提交/删除”等业务按钮；组件根和关闭控件来自固定 UI 契约。叠加浮层
    从 DOM 最后一个可见节点向外关闭，最多 10 层，防止异常页面形成无限循环。
    """
    tab = tab or browser_session.get_tab()
    closed = []
    errors = []
    transient = clear_transient_overlays(tab)
    closed.extend(
        "%s:%s" % (item.get("scope", ""), item.get("type", ""))
        for item in transient.get("closed", [])
    )
    errors.extend(transient.get("errors", []))
    safe_labels = {"取消", "关闭", "返回", "否", "暂不", "知道了"}

    def close_kind(scope, target, root, kind, root_selector, close_selector):
        for _ in range(10):
            wrappers = root.eles("c:" + root_selector, timeout=0.2) or []
            visible = [
                wrapper for wrapper in wrappers
                if bool(getattr(getattr(wrapper, "states", None), "is_displayed", False))
            ]
            if not visible:
                return
            active = visible[-1]
            buttons = active.eles('c:.ant-btn', timeout=0.2) or []
            cancel = None
            for button in buttons:
                states = getattr(button, "states", None)
                if not bool(getattr(states, "is_displayed", True)):
                    continue
                if not bool(getattr(states, "is_enabled", True)):
                    continue
                label = (button.text or "").replace(" ", "").strip()
                if label in safe_labels or any(label.startswith(prefix) for prefix in safe_labels):
                    cancel = button
                    break
            control = cancel or active.ele("c:" + close_selector, timeout=0.2)
            if not control:
                errors.append("%s %s: 无安全的取消/关闭按钮" % (scope, kind))
                return
            control.click(by_js=False, timeout=2, wait_stop=False)
            hidden = target.wait.ele_hidden(active, timeout=3, raise_err=False)
            if not hidden:
                errors.append("%s %s: 等待关闭超时" % (scope, kind))
                return
            closed.append("%s:%s" % (scope, kind))

    for scope, target in _target_contexts(tab):
        root = _document_root(target)
        try:
            close_kind(
                scope, target, root, "modal",
                ui_contract.MODAL_WRAP, ui_contract.MODAL_CLOSE,
            )
            close_kind(
                scope, target, root, "drawer",
                ui_contract.DRAWER, ".ant-drawer-close",
            )
        except Exception as exc:
            logger.debug("close_modal 失败(%s): %s", scope, exc)
            errors.append("%s: %s" % (scope, exc))
    return {"ok": not errors, "closed": closed, "errors": errors}
