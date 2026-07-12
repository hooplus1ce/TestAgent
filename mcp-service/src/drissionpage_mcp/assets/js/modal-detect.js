// 弹窗检测（在当前 document 内）。在 frame 上下文检测业务弹窗，在 tab 上下文检测 top 层系统确认弹窗。
// 参考 references/modal-types.md 的三种类型与检测优先级。
function detectModalInDoc() {
  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    var cur = el;
    while (cur && cur.nodeType === 1) {
      var s = window.getComputedStyle(cur);
      if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse') return false;
      cur = cur.parentElement;
    }
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  var modal = document.querySelector('.ant-modal-content');
  var wrap = modal ? (modal.closest('.ant-modal-wrap') || document.querySelector('.ant-modal-wrap')) : null;
  if (modal && isVisible(modal) && (!wrap || isVisible(wrap))) {
    var isConfirm = !!modal.querySelector('.ant-confirm-body-wrapper');
    var titleEl = modal.querySelector('.ant-modal-title') || modal.querySelector('.ant-confirm-title');
    var contentEl = modal.querySelector('.ant-confirm-content') || modal.querySelector('.ant-modal-body');
    return {
      type: isConfirm ? 'confirm' : 'interactive',
      title: titleEl ? titleEl.textContent.trim() : '',
      content: contentEl ? contentEl.textContent.trim().replace(/\s+/g, ' ').slice(0, 200) : '',
      buttons: [].slice.call(modal.querySelectorAll('.ant-btn, button')).filter(isVisible).map(function (b) { return b.textContent.trim().replace(/\s+/g, ''); }),
      hasClose: !!modal.querySelector('.ant-modal-close')
    };
  }
  var notif = document.querySelector('.ant-notification-notice');
  if (notif && isVisible(notif)) {
    var msgEl = notif.querySelector('.ant-notification-notice-message');
    return { type: 'notification', message: msgEl ? msgEl.textContent.trim() : notif.textContent.trim().slice(0, 100) };
  }
  var msg = document.querySelector('.ant-message-notice');
  if (msg && isVisible(msg)) {
    return { type: 'message', message: msg.textContent.trim().slice(0, 100) };
  }
  return { type: 'none' };
}
