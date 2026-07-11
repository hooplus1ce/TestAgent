"""弹窗三级检测：iframe 内业务弹窗/消息 → top 层系统确认弹窗 → 无。

移植自 references/modal-types.md 的检测代码模板。

智能等待：timeout > 0 时会轮询直到弹窗出现或超时，找到就立即返回。
"""
import json
import logging
import time

import browser_session

logger = logging.getLogger("drissionpage-mcp")


_VISIBLE_ELEMENT_JS = r"""
function isVisible(el){
  if (!el || !el.isConnected) return false;
  var cur = el;
  while (cur && cur.nodeType === 1) {
    var s = window.getComputedStyle(cur);
    if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse') {
      return false;
    }
    cur = cur.parentElement;
  }
  var r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
"""


_VISIBLE_MODAL_STATE_JS = _VISIBLE_ELEMENT_JS + r"""
var m = this;
var w = m ? (m.closest('.ant-modal-wrap') || document.querySelector('.ant-modal-wrap')) : null;
return JSON.stringify({visible: !!(m && isVisible(m) && (!w || isVisible(w)))});
"""


def _parse_json(res):
    if res is None:
        return None
    if isinstance(res, str):
        try:
            return json.loads(res)
        except Exception:
            return None
    return res


def _modal_visibility(modal_ele):
    """Return False for hidden/closed modal residue, True for visible, None if unknown."""
    try:
        info = _parse_json(modal_ele.run_js(_VISIBLE_MODAL_STATE_JS))
        if isinstance(info, dict) and "visible" in info:
            return bool(info["visible"])
    except Exception:
        pass
    try:
        return bool(modal_ele.states.is_displayed)
    except Exception:
        return False


def _detect_in_target(target):
    """用 JS 检测 target 自身文档内的弹窗（document.querySelector 不递归 iframe，scope 精确）。

    修复两个历史 bug：
    ① tab.ele('c:.ant-modal-content') 会递归进 iframe 找到残留隐藏 modal，导致跨 frame 误判；
    ② 找到隐藏 modal 后早退 return none，跳过 notification/message 检查（保存成功 toast 被漏抓的根因）。

    现：隐藏/残留 modal 不早退，落入 notification/message 检查；可见性用 getBoundingClientRect 判定。
    """
    try:
        res = target.run_js(r"""
        function isVis(el){
            if (!el || !el.isConnected) return false;
            var cur = el;
            while (cur && cur.nodeType === 1) {
                var s = window.getComputedStyle(cur);
                if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse') {
                    return false;
                }
                cur = cur.parentElement;
            }
            var r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        }
        var modals = document.querySelectorAll('.ant-modal-content');
        for (var i = 0; i < modals.length; i++) {
            var m = modals[i];
            var w = m.closest('.ant-modal-wrap') || document.querySelector('.ant-modal-wrap');
            var wrapHidden = w && !isVis(w);
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
        d = _parse_json(res)
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
    """开启/关闭鼠标轨迹可视化。使用 tabpanel → iframe 方式确保 iframe 层级生效。"""
    tab = browser_session.get_tab()
    try:
        tab.set.show_trail(on)
    except Exception as e:
        logger.debug("tab.show_trail 失败: %s", e)
    # 用 tabpanel 方式获取 iframe（与 tools/main.py 一致）
    try:
        is_displayed = tab.wait.eles_loaded(
            "t:div@@role=tabpanel@@aria-hidden=false", timeout=3, raise_err=False
        )
        if is_displayed:
            active_tabpanel = tab.ele("t:div@@role=tabpanel@@aria-hidden=false", timeout=2)
            if active_tabpanel:
                iframe = active_tabpanel.get_frame("t:iframe", timeout=2)
                if iframe is not None:
                    iframe.set.show_trail(on)
    except Exception as e:
        logger.debug("iframe mouse_trail 失败: %s", e)
    return {"ok": True, "on": on}


_CLEAR_TRANSIENT_JS = _VISIBLE_ELEMENT_JS + r"""
function isVis(el){
  return isVisible(el);
}
var closed = [];
document.querySelectorAll('.ant-notification-notice').forEach(function(n){
  if (!isVis(n)) return;
  var m = n.querySelector('.ant-notification-notice-message');
  var d = n.querySelector('.ant-notification-notice-description');
  var msg = ((m ? m.textContent : '') || (d ? d.textContent : '')).trim();
  var btn = n.querySelector('.ant-notification-notice-close');
  try { if (btn) btn.click(); } catch(e) {}
  if (n.parentNode) n.parentNode.removeChild(n);
  closed.push({type: 'notification', message: msg});
});
document.querySelectorAll('.ant-message-notice').forEach(function(m){
  if (!isVis(m)) return;
  var c = m.querySelector('.ant-message-notice-content');
  var msg = c ? (c.textContent || '').trim() : '';
  if (m.parentNode) m.parentNode.removeChild(m);
  closed.push({type: 'message', message: msg});
});
return JSON.stringify(closed);
"""


_CLEAR_TRANSIENT_ALL_JS = r"""
var closed = [];
function clearDoc(doc, scope){
  if (!doc) return;
  var win = doc.defaultView || window;
  function isVis(el){
    if (!el || !el.isConnected) return false;
    var cur = el;
    while (cur && cur.nodeType === 1) {
      var s = win.getComputedStyle(cur);
      if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse') {
        return false;
      }
      cur = cur.parentElement;
    }
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  doc.querySelectorAll('.ant-notification-notice').forEach(function(n){
    if (!isVis(n)) return;
    var m = n.querySelector('.ant-notification-notice-message');
    var d = n.querySelector('.ant-notification-notice-description');
    var msg = ((m ? m.textContent : '') || (d ? d.textContent : '')).trim();
    var btn = n.querySelector('.ant-notification-notice-close');
    try { if (btn) btn.click(); } catch(e) {}
    if (n.parentNode) n.parentNode.removeChild(n);
    closed.push({scope: scope, type: 'notification', message: msg});
  });
  doc.querySelectorAll('.ant-message-notice').forEach(function(m){
    if (!isVis(m)) return;
    var c = m.querySelector('.ant-message-notice-content');
    var msg = c ? (c.textContent || '').trim() : '';
    if (m.parentNode) m.parentNode.removeChild(m);
    closed.push({scope: scope, type: 'message', message: msg});
  });
}
clearDoc(document, 'top');
try {
  var f = document.querySelector('[role="tabpanel"][aria-hidden="false"] iframe');
  if (f && (f.contentDocument || (f.contentWindow && f.contentWindow.document))) {
    clearDoc(f.contentDocument || f.contentWindow.document, 'iframe');
  }
} catch(e) {
  closed.push({scope: 'iframe', type: 'error', message: String(e && e.message || e)});
}
return JSON.stringify(closed);
"""


def _target_contexts(tab):
    contexts = []
    try:
        fr = browser_session.get_active_frame(tab)
    except Exception:
        fr = None
    if fr is not None:
        contexts.append(("iframe", fr))
    contexts.append(("top", tab))
    return contexts


def _normalize_js_list(res):
    if not res:
        return []
    if isinstance(res, str):
        try:
            res = json.loads(res)
        except Exception:
            return []
    return res if isinstance(res, list) else []


def clear_transient_overlays(tab=None):
    """Close dismissible transient overlays through DrissionPage element APIs.

    This intentionally does not close business modals/confirm dialogs. A message
    without a visible close control is left to its normal auto-dismiss behavior;
    removing it with JavaScript would make a formal replay non-native.
    """
    tab = tab or browser_session.get_tab()
    errors = []
    closed = []
    for scope, target in _target_contexts(tab):
        try:
            notices = target.eles('c:.ant-notification-notice', timeout=0.2)
            for notice in notices:
                if not notice.states.is_displayed:
                    continue
                text = (notice.text or "").strip()
                close = notice.ele('c:.ant-notification-notice-close', timeout=0.2)
                if not close:
                    continue
                close.wait.clickable(timeout=1, wait_stop=True, raise_err=False)
                close.click(wait_stop=True)
                target.wait.ele_hidden(notice, timeout=1, raise_err=False)
                closed.append({"scope": scope, "type": "notification", "message": text})

            messages = target.eles('c:.ant-message-notice', timeout=0.2)
            for message in messages:
                if not message.states.is_displayed:
                    continue
                close = message.ele('c:.ant-message-close,.ant-message-notice-close', timeout=0.2)
                if not close:
                    continue
                text = (message.text or "").strip()
                close.wait.clickable(timeout=1, wait_stop=True, raise_err=False)
                close.click(wait_stop=True)
                target.wait.ele_hidden(message, timeout=1, raise_err=False)
                closed.append({"scope": scope, "type": "message", "message": text})
        except Exception as exc:
            logger.debug("clear transient overlays 失败(%s): %s", scope, exc)
            errors.append("%s: %s" % (scope, exc))
    return {"ok": not errors, "closed": closed, "errors": errors}


def close_modal(tab=None):
    """关闭当前残留的弹窗/通知/消息，避免积累在 DOM 中干扰后续操作。
    通知→点×关闭；业务弹窗→点取消或×。
    已隐藏的残留弹窗（如 display:none 的浮层 DOM）视为已关闭，不再尝试点击。
    使用 DrissionPage 原生方法 + wait.ele_deleted 等待关闭完成。
    返回 {ok, closed:[...], errors:[...]}，调用方可判断清理是否真正成功。
    """
    tab = tab or browser_session.get_tab()
    closed = []
    errors = []
    transient = clear_transient_overlays(tab)
    for item in transient.get("closed", []):
        closed.append("%s:%s" % (item.get("scope", ""), item.get("type", "")))
    errors.extend(transient.get("errors", []))

    try:
        for scope, target in _target_contexts(tab):
            # 关闭业务弹窗（根据 .ant-modal-wrap 的 visibility 寻找真正可见的那个）
            wrappers = target.eles('c:.ant-modal-wrap')
            active_wrap = None
            for w in wrappers:
                if w.states.is_displayed:
                    active_wrap = w
                    break
            
            if active_wrap:
                cancel = active_wrap.ele('c:.ant-btn:not(.ant-btn-primary)', timeout=0.3)
                if cancel:
                    cancel.wait.clickable(timeout=2, wait_stop=True, raise_err=False)
                    cancel.click(wait_stop=True)
                else:
                    close_x = active_wrap.ele('c:.ant-modal-close', timeout=0.3)
                    if close_x:
                        close_x.wait.clickable(timeout=2, wait_stop=True, raise_err=False)
                        close_x.click(wait_stop=True)
                    else:
                        errors.append("%s modal: 无可点击的取消/关闭按钮" % scope)
                if not any(e.startswith("%s modal: 无可点击" % scope) for e in errors):
                    # 使用原生方法等待最外层包裹元素 .ant-modal-wrap 隐藏
                    hidden = target.wait.ele_hidden(active_wrap, timeout=3, raise_err=False)
                    if hidden:
                        closed.append("%s:modal" % scope)
                    else:
                        errors.append("%s modal: 等待关闭超时" % scope)
    except Exception as e:
        logger.debug("close_modal 失败: %s", e)
        errors.append(str(e))
    return {"ok": not errors, "closed": closed, "errors": errors}
