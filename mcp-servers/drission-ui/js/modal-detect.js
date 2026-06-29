// 弹窗检测（在当前 document 内）。在 frame 上下文检测业务弹窗，在 tab 上下文检测 top 层系统确认弹窗。
// 参考 references/modal-types.md 的三种类型与检测优先级。
function detectModalInDoc() {
  var modal = document.querySelector('.ant-modal-content');
  if (modal && modal.offsetParent !== null) {
    var isConfirm = !!modal.querySelector('.ant-confirm-body-wrapper');
    var titleEl = modal.querySelector('.ant-modal-title') || modal.querySelector('.ant-confirm-title');
    var contentEl = modal.querySelector('.ant-confirm-content') || modal.querySelector('.ant-modal-body');
    return {
      type: isConfirm ? 'confirm' : 'interactive',
      title: titleEl ? titleEl.textContent.trim() : '',
      content: contentEl ? contentEl.textContent.trim().replace(/\s+/g, ' ').slice(0, 200) : '',
      buttons: [].slice.call(modal.querySelectorAll('.ant-btn, button')).filter(function (b) { return b.offsetParent !== null; }).map(function (b) { return b.textContent.trim().replace(/\s+/g, ''); }),
      hasClose: !!modal.querySelector('.ant-modal-close')
    };
  }
  var notif = document.querySelector('.ant-notification-notice');
  if (notif && notif.offsetParent !== null) {
    var msgEl = notif.querySelector('.ant-notification-notice-message');
    return { type: 'notification', message: msgEl ? msgEl.textContent.trim() : notif.textContent.trim().slice(0, 100) };
  }
  var msg = document.querySelector('.ant-message-notice');
  if (msg && msg.offsetParent !== null) {
    return { type: 'message', message: msg.textContent.trim().slice(0, 100) };
  }
  return { type: 'none' };
}
