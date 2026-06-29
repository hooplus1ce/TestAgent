// 通用交互控件扫描（DOM，递归穿透同源 iframe）。在 tab 或 frame 上下文执行。
function scanInteractiveControls() {
  var out = [];
  function visible(el) {
    var r = el.getBoundingClientRect();
    var s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  }
  function txt(el) {
    var t = (el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder') || el.value || el.innerText || '').trim();
    return t.replace(/\s+/g, ' ').slice(0, 40);
  }
  var SEL = "button,a[href],input,select,textarea,[role=button],[role=menuitem],[role=tab],[role=checkbox],[role=switch],[role=link],[onclick],.el-button,.ant-btn,[class*=btn]";
  function scan(root, frame) {
    var nodes;
    try { nodes = root.querySelectorAll(SEL); } catch (e) { return; }
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (!visible(el)) continue;
      var r = el.getBoundingClientRect();
      out.push({
        frame: frame,
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute('type') || el.getAttribute('role') || '',
        text: txt(el),
        cls: (el.className && typeof el.className === 'string' ? el.className : '').slice(0, 50),
        disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'),
        cx: Math.round(r.left + r.width / 2),
        cy: Math.round(r.top + r.height / 2)
      });
    }
    var ifr = root.querySelectorAll('iframe');
    for (var j = 0; j < ifr.length; j++) {
      try {
        var doc = ifr[j].contentDocument;
        if (doc) scan(doc, (frame ? frame + '>' : '') + (ifr[j].id || ifr[j].name || 'iframe'));
      } catch (e) { /* 跨域，跳过 */ }
    }
  }
  scan(document, '');
  return { url: location.href, title: document.title, total: out.length, elements: out };
}

// 页面俯瞰：页签 + 可见按钮（不点任何东西）
function domOverview() {
  return {
    tabs: [].slice.call(document.querySelectorAll('.ant-radio-button-wrapper, .ant-tabs-tab')).map(function (t) {
      return { text: t.textContent.trim(), selected: t.classList.contains('ant-radio-button-wrapper-checked') || t.classList.contains('ant-tabs-tab-active') };
    }),
    buttons: [].slice.call(document.querySelectorAll('button')).filter(function (b) { return b.offsetParent !== null && b.textContent.trim(); }).map(function (b) {
      return { text: b.textContent.trim().replace(/\s+/g, ''), disabled: b.disabled };
    })
  };
}
