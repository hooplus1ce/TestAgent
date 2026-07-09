// 鼠标轨迹跟踪器（注入后用 window.mt.on()/off() 控制）
(function(){
  if (window.__mt) return;
  window.__mt = true;
  if (!document.getElementById('mt-s')) {
    var s = document.createElement('style');
    s.id = 'mt-s';
    s.textContent = '.mt-d{position:fixed;width:8px;height:8px;background:rgba(255,0,0,0.6);border-radius:50%;pointer-events:none;z-index:99999;transition:opacity .3s;opacity:1}';
    document.head.appendChild(s);
  }
  function dot(x,y,k){
    var d = document.createElement('div');
    d.className = 'mt-d';
    d.style.left = x + 'px';
    d.style.top = y + 'px';
    d.style.transform = 'translate(-50%,-50%) scale(' + (k||1) + ')';
    document.body.appendChild(d);
    setTimeout(function(){ d.style.opacity = '0'; }, 700);
    setTimeout(function(){ d.remove(); }, 1000);
  }
  window.mt = {
    on: function() {
      if (window.__mten) return;
      window.__mten = true;
      window.__mv = function(e){ dot(e.pageX, e.pageY); };
      window.__mc = function(e){ dot(e.pageX, e.pageY, 5); };
      document.addEventListener('mousemove', window.__mv);
      document.addEventListener('click', window.__mc);
    },
    off: function() {
      if (!window.__mten) return;
      window.__mten = false;
      document.removeEventListener('mousemove', window.__mv);
      document.removeEventListener('click', window.__mc);
      window.__mv = null;
      window.__mc = null;
    }
  };
})();
