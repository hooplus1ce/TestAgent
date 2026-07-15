"""点击后统一观察器 + 原子信号检测工具。

设计动机：detect_modal 三级轮询会漏掉「寿命短 + 出现在顶层」的 toast（如保存成功的
.ant-message-notice，~3s 后自动消失）。本模块用两条互补路径解决：

1. 两段式 observe（推荐，抓短寿命 toast）：
   - observe_start() 在**点击前**安装 MutationObserver + 网络监听，立即返回；
   - click() 触发动作；
   - observe_wait() 轮询 observer 缓冲，first-signal-wins，返回后清理。
   - observer 在点击前就监听，消除「点击→观察」调用间隙（agent 思考时间可能 > toast 寿命）。
2. observe_post_click —— 便捷封装（observe_start + observe_wait 一次调用），适用于点击已发生
   或不需要在点击间隙观察的场景。
3. 原子工具（detect_notification/detect_message/detect_url_change/detect_tab_change）：
   单点排查用，基于 DrissionPage wait.ele_displayed / wait.url_change 事件驱动等待。

DOM 信号走 MutationObserver：元素被添加到 DOM 的当帧即捕获，非固定 sleep 轮询；
网络用后台线程 wait，不阻塞 DOM 轮询。
"""
import json
from collections import deque
import logging
import queue
import threading
import time

from ..core import ui_contract
from . import browser_session, network_record

_COLLECT_WINDOW = 0.6  # 首次信号后继续收集的窗口秒数，避免 first-signal-wins 漏掉 .ant-message
# 固定 UI 框架的所有可见反馈优先于导航和网络；否则“打开日历 + 请求接口”会把
# network 误选为主事件，生成器无法知道用户实际看到的组件状态。
_SIGNAL_PRIORITY = {
    signal_type: 0 for signal_type in (
        "message", "notification", "confirm", "interactive", "drawer",
        "popover", "tooltip", "dropdown", "select-dropdown", "calendar",
        "vtable-filter-menu", "vtable-tooltip", "vtable-menu",
        "layer", "layer-msg",
    )
}
_SIGNAL_PRIORITY.update({"url_change": 1, "tab_change": 1, "network": 2})


def _pick_primary(events: list) -> dict:
    """按优先级选主事件：message/notification/modal > url/tab > network。"""
    best = events[0] if events else {}
    best_prio = _SIGNAL_PRIORITY.get(best.get("type", ""), 9)
    for ev in events[1:]:
        prio = _SIGNAL_PRIORITY.get(ev.get("type", ""), 9)
        if prio < best_prio:
            best_prio = prio
            best = ev
    return best


def _event_payload(event: dict) -> dict:
    payload = event.get("payload") if isinstance(event, dict) else None
    return payload if isinstance(payload, dict) else {}


def _short_text(value, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _rect_key(rect: dict):
    if not isinstance(rect, dict):
        return ()
    keys = ("x", "y", "width", "height")
    out = []
    for key in keys:
        try:
            out.append(round(float(rect.get(key) or 0), 1))
        except (TypeError, ValueError):
            out.append(0)
    return tuple(out)


def _event_identity(event: dict):
    """Build a compact identity key for de-duplicating repeated DOM signals."""
    payload = _event_payload(event)
    etype = event.get("type", "")
    if etype == "network":
        return (
            etype,
            event.get("method", ""),
            event.get("url", ""),
            event.get("api_target", ""),
            event.get("status", ""),
        )
    title = payload.get("title") or event.get("title") or ""
    message = payload.get("message") or event.get("message") or ""
    content = payload.get("content") or event.get("content") or ""
    options = payload.get("options") or []
    option_key = tuple(
        _short_text(item.get("text") if isinstance(item, dict) else item, 80)
        for item in options[:10]
    ) if isinstance(options, list) else ()
    semantic_key = (_short_text(title, 80), _short_text(message or content, 120), option_key)
    # Ant modal 入场动画会连续改变 rect，MutationObserver 因而收到多次属性事件；
    # 有标题/正文/选项时以语义去重，只有无文本浮层才用几何位置区分。
    rect = payload.get("rect") or event.get("rect") or {}
    return (
        etype,
        event.get("scope") or payload.get("scope") or "",
        *semantic_key,
        () if any(semantic_key) else _rect_key(rect),
    )


def _dedupe_events(events: list) -> list:
    out = []
    seen = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        key = _event_identity(event)
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out


def _event_summary(event: dict) -> dict:
    """Return a dense timeline item without duplicating full payload."""
    payload = _event_payload(event)
    out = {
        "type": event.get("type"),
        "elapsedMs": event.get("elapsedMs", 0),
    }
    scope = event.get("scope") or payload.get("scope")
    if scope:
        out["scope"] = scope

    title = payload.get("title") or event.get("title")
    message = payload.get("message") or event.get("message")
    content = payload.get("content") or event.get("content")
    if title:
        out["title"] = _short_text(title, 120)
    if message:
        out["message"] = _short_text(message, 160)
    elif content:
        out["content"] = _short_text(content, 160)

    if event.get("type") == "network":
        for key in ("method", "url", "api_target", "status"):
            value = event.get(key)
            if value not in (None, "", False):
                out[key] = value
    elif event.get("type") in (
        "calendar", "select-dropdown", "dropdown", "vtable-filter-menu",
        "vtable-tooltip", "vtable-menu",
    ):
        if payload.get("mode"):
            out["mode"] = payload.get("mode")
        if payload.get("cellCount") is not None:
            out["cellCount"] = payload.get("cellCount")
        options = payload.get("options")
        if isinstance(options, list):
            out["optionCount"] = len(options)
        if payload.get("activeTab"):
            out["activeTab"] = payload.get("activeTab")
        if payload.get("valueCount") is not None:
            out["valueCount"] = payload.get("valueCount")

    return out


def _compact_events(events: list) -> tuple[list, list]:
    unique = _dedupe_events(events)
    return unique, [_event_summary(event) for event in unique]

logger = logging.getLogger("drissionpage-mcp")

# ---- 信号选择器 ----
_SEL_MODAL = ui_contract.MODAL_CONTENT
_SEL_NOTIFICATION = ui_contract.NOTIFICATION
_SEL_MESSAGE = ui_contract.MESSAGE
_SEL_LAYER_MSG = ui_contract.LAYER_MSG
_ALL_SELS = list(ui_contract.OBSERVABLE_OVERLAYS)

# ---- MutationObserver 注入脚本（在 target.document 内安装）----
# 注意：必须用顶层 return（不能用 IIFE），否则 DrissionPage run_js 拿不到返回值。
# 两段式 observe_start 默认忽略安装前已存在的浮层；observe_post_click 才采集现状。
_INSTALL_OBSERVER_JS = r"""
if (window.__du_obs) { try{ window.__du_obs.disconnect(); }catch(e){} }
window.__du_signals = [];
window.__du_t0 = Date.now();
// 选择器由 ui_contract.OBSERVABLE_OVERLAYS 注入；前端组件升级只改契约文件，
// 不再维护观察器与页面模型两份容易漂移的列表。
var SELS = __DU_OVERLAY_SELECTORS__;
var CAPTURE_EXISTING = __DU_CAPTURE_EXISTING__;
var scope = (window.top === window) ? 'top' : 'iframe';
function queueSignal(sig){
  if (!sig) return;
  if (window.__du_signals.length >= 50) window.__du_signals.shift();
  window.__du_signals.push(sig);
}
function cleanText(t){ return (t || '').replace(/\s+/g, ' ').trim(); }
function rectOf(el){
  var r = el.getBoundingClientRect();
  return {
    x: Math.round(r.left * 10) / 10,
    y: Math.round(r.top * 10) / 10,
    width: Math.round(r.width * 10) / 10,
    height: Math.round(r.height * 10) / 10
  };
}
function buttonTexts(el){
  return [].slice.call(el.querySelectorAll('button,.ant-btn,[role="button"],a[href]'))
    .map(function(b){ return cleanText(b.textContent || b.getAttribute('aria-label') || b.getAttribute('title') || ''); })
    .filter(Boolean)
    .slice(0, 20);
}
function optionTexts(el){
  return [].slice.call(el.querySelectorAll(
    '.ant-dropdown-menu-item,.ant-select-dropdown-menu-item,.ant-select-item-option,li,[role="option"]'
  )).map(function(i){ return cleanText(i.textContent); }).filter(Boolean).slice(0, 50);
}
function vtableFilterPayload(el){
  var activeTab = '';
  var tabs = [].slice.call(el.querySelectorAll('button')).map(function(b){
    var s = window.getComputedStyle(b);
    var active = s.color === 'rgb(0, 123, 255)' || s.borderBottomColor === 'rgb(0, 123, 255)' ||
      (b.style && b.style.borderBottomColor === 'rgb(0, 123, 255)');
    var text = cleanText(b.textContent);
    if (active && !activeTab) activeTab = text;
    return {text:text, active:active};
  }).filter(function(t){ return t.text; }).slice(0, 10);
  var search = el.querySelector('input[type="text"][placeholder*="关键词"], input[type="search"]');
  var valueRows = [].slice.call(el.querySelectorAll('input[type="checkbox"]')).map(function(cb){
    var label = cb.closest('label');
    var row = cb.closest('div');
    var countEl = row ? row.querySelector('span') : null;
    return {
      text: cleanText(label ? label.textContent : cb.value),
      value: cb.value || '',
      checked: !!cb.checked,
      count: countEl ? cleanText(countEl.textContent) : ''
    };
  }).filter(function(v){ return v.text || v.value; }).slice(0, 80);
  var selects = [].slice.call(el.querySelectorAll('select')).map(function(sel){
    return {
      value: sel.value || '',
      options: [].slice.call(sel.options || []).map(function(opt){
        return {value: opt.value || '', text: cleanText(opt.textContent), selected: !!opt.selected};
      }).slice(0, 30)
    };
  }).slice(0, 5);
  var inputs = [].slice.call(el.querySelectorAll('input[type="text"]')).map(function(input){
    return {placeholder: input.getAttribute('placeholder') || '', value: input.value || ''};
  }).slice(0, 10);
  var clear = el.querySelector('a');
  var style = window.getComputedStyle(el);
  return {
    type:'vtable-filter-menu',
    scope:scope,
    title:'VTable列头筛选',
    display: style.display,
    activeTab: activeTab,
    tabs: tabs,
    search: search ? {placeholder: search.getAttribute('placeholder') || '', value: search.value || ''} : null,
    values: valueRows,
    valueCount: valueRows.filter(function(v){ return v.value; }).length,
    condition: {selects: selects, inputs: inputs},
    buttons: buttonTexts(el),
    clearDisabled: clear ? (window.getComputedStyle(clear).pointerEvents === 'none' ||
      parseFloat(window.getComputedStyle(clear).opacity || '1') < 1) : null,
    rect: rectOf(el)
  };
}
function vtableOverlayKind(el){
  var cls = el.className || ''; if (typeof cls !== 'string') cls = '';
  if (cls.indexOf('vtable__bubble-tooltip-element') >= 0) return 'vtable-tooltip';
  if (cls.indexOf('vtable__menu-element') >= 0) return 'vtable-menu';
  return '';
}
function vtableOverlayState(el){
  var cls = el.className || ''; if (typeof cls !== 'string') cls = '';
  if (cls.indexOf('--hidden') >= 0) return 'hidden';
  if (cls.indexOf('--shown') >= 0) return 'shown';
  return isVis(el) ? 'visible' : 'hidden';
}
function vtableOverlayPayload(el){
  var style = window.getComputedStyle(el);
  var kind = vtableOverlayKind(el) || 'vtable-overlay';
  var text = cleanText(el.innerText || el.textContent);
  var options = [];
  var seen = {};
  [].slice.call(el.querySelectorAll(
    '.vtable__menu-item,.vtable__menu-item-text,[role="menuitem"],li,button,a[href]'
  )).forEach(function(item){
    var itemText = cleanText(item.innerText || item.textContent || item.getAttribute('title') || '');
    if (!itemText || seen[itemText]) return;
    seen[itemText] = true;
    options.push(itemText);
  });
  options = options.slice(0, 50);
  var title = kind === 'vtable-tooltip'
    ? (text || 'VTable工具提示')
    : (options[0] || text || 'VTable菜单');
  return {
    type:kind,
    scope:scope,
    title:title,
    content:text.slice(0,200),
    text:text.slice(0,200),
    state:vtableOverlayState(el),
    display:style.display,
    visibility:style.visibility,
    opacity:style.opacity,
    options:options,
    buttons:buttonTexts(el),
    rect:rectOf(el)
  };
}
function hasClass(el, name){
  return !!(el && el.classList && el.classList.contains(name));
}
function isCalendarNode(el){
  return hasClass(el, 'ant-calendar-picker-container') || hasClass(el, 'ant-calendar');
}
function canonicalSignalElement(el){
  if (hasClass(el, 'ant-calendar')) {
    return el.closest('.ant-calendar-picker-container') || el;
  }
  return el;
}
function isVTableFilterNode(el){
  var cls = el.className || ''; if (typeof cls !== 'string') cls = '';
  return cls.indexOf('vtable-filter-menu') >= 0;
}
function isVTableOverlayNode(el){
  return !!vtableOverlayKind(el);
}
function isVTableOverlayActive(el){
  var cls = el.className || ''; if (typeof cls !== 'string') cls = '';
  if (cls.indexOf('vtable__bubble-tooltip-element--hidden') >= 0 ||
      cls.indexOf('vtable__menu-element--hidden') >= 0) {
    return false;
  }
  return isVis(el);
}
function classify(el){
  var cls = el.className || ''; if (typeof cls !== 'string') cls = '';
  if (cls.indexOf('vtable-filter-menu') >= 0) {
    return vtableFilterPayload(el);
  }
  if (isVTableOverlayNode(el)) {
    return vtableOverlayPayload(el);
  }
  if (cls.indexOf('ant-modal-content') >= 0) {
    var isConfirm = !!el.querySelector('.ant-confirm-body');
    var title = el.querySelector('.ant-modal-title');
    var body = el.querySelector('.ant-modal-body');
    var btns = buttonTexts(el);
    return {type: isConfirm ? 'confirm' : 'interactive', scope: scope,
            title: title ? cleanText(title.textContent) : '',
            content: body ? cleanText(body.textContent).slice(0,200) : '',
            buttons: btns, hasClose: !!el.querySelector('.ant-modal-close'),
            rect: rectOf(el)};
  }
  if (cls.indexOf('ant-drawer') >= 0) {
    var dt = el.querySelector('.ant-drawer-title');
    var db = el.querySelector('.ant-drawer-body');
    return {type:'drawer', scope:scope, title: dt ? cleanText(dt.textContent) : '',
            content: db ? cleanText(db.textContent).slice(0,200) : '',
            buttons: buttonTexts(el), hasClose: !!el.querySelector('.ant-drawer-close'),
            rect: rectOf(el)};
  }
  if (isCalendarNode(el)) {
    var root = hasClass(el, 'ant-calendar') ? el : (el.querySelector('.ant-calendar') || el);
    var isRange = hasClass(root, 'ant-calendar-range') ||
      !!root.querySelector('.ant-calendar-range-left,.ant-calendar-range-right');
    var ye = root.querySelector('.ant-calendar-year-select');
    var me = root.querySelector('.ant-calendar-month-select');
    var cellNodes = [].slice.call(root.querySelectorAll('td[title] .ant-calendar-date'));
    var cells = cellNodes.map(function(c){
      var td = c.closest('td');
      return {title: td ? (td.getAttribute('title') || '') : '', text: cleanText(c.textContent)};
    }).filter(function(c){ return c.title || c.text; }).slice(0, 80);
    return {type:'calendar', scope:scope, mode:isRange ? 'range' : 'single',
            title: [cleanText(ye ? ye.textContent : ''), cleanText(me ? me.textContent : '')].filter(Boolean).join(''),
            cellCount: cellNodes.length, cells: cells, cellsTruncated: cellNodes.length > cells.length,
            rect: rectOf(el)};
  }
  if (cls.indexOf('ant-select-dropdown') >= 0) {
    return {type:'select-dropdown', scope:scope, options: optionTexts(el), rect: rectOf(el)};
  }
  if (cls.indexOf('ant-dropdown') >= 0) {
    return {type:'dropdown', scope:scope, options: optionTexts(el), rect: rectOf(el)};
  }
  if (cls.indexOf('ant-popover') >= 0) {
    var pt = el.querySelector('.ant-popover-title');
    var pc = el.querySelector('.ant-popover-inner-content');
    return {type:'popover', scope:scope, title: pt ? cleanText(pt.textContent) : '',
            content: pc ? cleanText(pc.textContent).slice(0,200) : cleanText(el.textContent).slice(0,200),
            buttons: buttonTexts(el), rect: rectOf(el)};
  }
  if (cls.indexOf('ant-tooltip') >= 0) {
    var ti = el.querySelector('.ant-tooltip-inner');
    return {type:'tooltip', scope:scope, content: ti ? cleanText(ti.textContent).slice(0,200) : cleanText(el.textContent).slice(0,200),
            rect: rectOf(el)};
  }
  if (cls.indexOf('ant-notification-notice') >= 0) {
    var m = el.querySelector('.ant-notification-notice-message');
    var d = el.querySelector('.ant-notification-notice-description');
    return {type:'notification', scope:scope,
            message: cleanText((m ? m.textContent : '') + (d ? (' ' + (d.textContent||'')) : '')),
            rect: rectOf(el)};
  }
  if (cls.indexOf('ant-message-notice') >= 0) {
    var c = el.querySelector('.ant-message-notice-content');
    var kind = '';
    var cc = el.querySelector('[class*="ant-message-"]');
    if (cc) { var mm = (cc.className||'').match(/ant-message-(success|info|warning|error|loading)/); if (mm) kind = mm[1]; }
    return {type:'message', scope:scope, kind:kind, message: c ? cleanText(c.textContent) : '',
            rect: rectOf(el)};
  }
  // layer.js（遗留 jQuery 页弹层 / toast；page 型内容可能嵌套 iframe）
  if (cls.indexOf('layui-layer') >= 0) {
    var isMsg = cls.indexOf('layui-layer-msg') >= 0 || (
      cls.indexOf('layui-layer-dialog') >= 0 && !el.querySelector('.layui-layer-title')
    );
    // 纯 shade 不作为业务信号
    if (cls.indexOf('layui-layer-shade') >= 0) return null;
    var lt = el.querySelector('.layui-layer-title');
    var lc = el.querySelector('.layui-layer-content');
    var lbtns = buttonTexts(el);
    // layer 底部按钮区常为 a 标签
    [].slice.call(el.querySelectorAll('.layui-layer-btn a')).forEach(function(a){
      var t = cleanText(a.textContent);
      if (t && lbtns.indexOf(t) < 0) lbtns.push(t);
    });
    var nested = [].slice.call(el.querySelectorAll('iframe')).map(function(f){
      return {src: f.src || '', id: f.id || '', name: f.name || ''};
    }).slice(0, 3);
    var layerKind = 'page';
    if (cls.indexOf('layui-layer-iframe') >= 0) layerKind = 'iframe';
    else if (cls.indexOf('layui-layer-dialog') >= 0) layerKind = 'dialog';
    else if (isMsg) layerKind = 'msg';
    if (isMsg && !lt) {
      return {type:'layer-msg', scope:scope, layerKind:layerKind,
              message: lc ? cleanText(lc.textContent).slice(0,200) : cleanText(el.textContent).slice(0,200),
              buttons: lbtns, hasClose: !!el.querySelector('.layui-layer-close'),
              nestedIframes: nested, rect: rectOf(el)};
    }
    return {type:'layer', scope:scope, layerKind:layerKind,
            title: lt ? cleanText(lt.textContent) : '',
            content: lc ? cleanText(lc.textContent).slice(0,200) : '',
            buttons: lbtns,
            hasClose: !!el.querySelector('.layui-layer-close'),
            nestedIframes: nested,
            rect: rectOf(el)};
  }
  return null;
}
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
function isActiveSignal(el){
  // 本系统 ant-calendar 打开/关闭由 DOM 挂载决定，不以 display:none 作为唯一依据。
  if (isCalendarNode(el)) return !!(el && el.isConnected);
  // VTable 列头筛选菜单会残留在 DOM 中并通过 display:block/none 切换。
  if (isVTableFilterNode(el)) return isVis(el);
  // VTable 工具栏 tooltip / 列设置菜单会以 --hidden/--shown class 常驻 DOM。
  if (isVTableOverlayNode(el)) return isVTableOverlayActive(el);
  return isVis(el);
}
function signalFromNode(n){
  if (!n || n.nodeType !== 1) return null;
  for (var k=0;k<SELS.length;k++){
    if (n.matches && n.matches(SELS[k])) {
      var direct = canonicalSignalElement(n);
      if (isActiveSignal(direct)) return classify(direct);
    }
  }
  if (n.querySelector) {
    for (var k=0;k<SELS.length;k++){
      var e = n.querySelector(SELS[k]);
      if (e) {
        e = canonicalSignalElement(e);
        if (isActiveSignal(e)) return classify(e);
      }
    }
  }
  return null;
}
// 点击前安装时旧浮层是基线，不应抢占本次动作反馈；点击后便捷观察才读取现状。
if (CAPTURE_EXISTING) {
  var initialSeen = [];
  for (var i=0;i<SELS.length;i++){
    var els = document.querySelectorAll(SELS[i]);
    for (var j=0;j<els.length;j++){
      var candidate = canonicalSignalElement(els[j]);
      if (initialSeen.indexOf(candidate) >= 0 || !isActiveSignal(candidate)) continue;
      initialSeen.push(candidate);
      var s0 = classify(candidate); if (s0) { s0.elapsedMs = 0; queueSignal(s0); }
      break;
    }
  }
}
var obs = new MutationObserver(function(muts){
  for (var i=0;i<muts.length;i++){
    var sig = null;
    if (muts[i].type === 'attributes') {
      sig = signalFromNode(muts[i].target);
      if (sig) { sig.elapsedMs = Date.now() - window.__du_t0; queueSignal(sig); continue; }
    }
    var added = muts[i].addedNodes;
    for (var j=0;j<added.length;j++){
      var n = added[j];
      sig = signalFromNode(n);
      if (sig) { sig.elapsedMs = Date.now() - window.__du_t0; queueSignal(sig); }
    }
  }
});
obs.observe(document.body, {
  childList:true,
  subtree:true,
  attributes:true,
  attributeFilter:['style','class','hidden','aria-hidden']
});
window.__du_obs = obs;
return JSON.stringify({installed:true, scope:scope, initialCount: window.__du_signals.length});
"""
_INSTALL_OBSERVER_JS = _INSTALL_OBSERVER_JS.replace(
    "__DU_OVERLAY_SELECTORS__", json.dumps(_ALL_SELS, ensure_ascii=False)
)

def _observer_script(capture_existing: bool) -> str:
    return _INSTALL_OBSERVER_JS.replace(
        "__DU_CAPTURE_EXISTING__", "true" if capture_existing else "false"
    )

# 读取并清空信号缓冲（消费语义，避免重复回报）
_POLL_SIGNALS_JS = r"""
var s = window.__du_signals || [];
var out = s.slice(0, 20);
s.splice(0, out.length);
return JSON.stringify(out);
"""

_CLEANUP_OBSERVER_JS = r"""
if (window.__du_obs) { try{ window.__du_obs.disconnect(); }catch(e){} window.__du_obs = null; }
window.__du_signals = [];
return JSON.stringify({ok:true});
"""


def _run_js_safe(target, script):
    """run_js 包一层异常，target 为 None 或执行失败返回 None。"""
    if target is None:
        return None
    try:
        return target.run_js(script)
    except Exception as e:
        logger.debug("run_js 失败: %s", e)
        return None


def _parse_json(res):
    if res is None:
        return None
    if isinstance(res, str):
        try:
            return json.loads(res)
        except (ValueError, TypeError):
            return None
    return res


# ==================== 两段式 observe 会话状态 ====================
# observe_start 写入，observe_wait 读取并清理。模块级单会话（MCP 单进程串行）。
_session = {}
_session_lock = threading.Lock()


def _build_session(signals, listen_targets, timeout_for_net=None,
                   native_wait: bool = False, capture_existing: bool = False):
    """清理旧会话后安装观察器；点击前默认把现有浮层视为基线。"""
    if signals is None:
        signals = ["overlay", "notification", "message", "tab", "url"]
    sigset = {str(signal).lower() for signal in signals}

    overlay_types = {
        "interactive", "confirm", "drawer", "popover", "tooltip",
        "dropdown", "select-dropdown", "vtable-filter-menu",
        "vtable-tooltip", "vtable-menu", "calendar",
    }
    dom_types = set()
    if "overlay" in sigset:
        dom_types.update(overlay_types)
    if "modal" in sigset:
        dom_types.update({"interactive", "confirm"})
    for signal in overlay_types | {"notification", "message"}:
        if signal in sigset:
            dom_types.add(signal)
    if "dropdown" in sigset:
        dom_types.update({"dropdown", "select-dropdown", "vtable-filter-menu"})
    if "vtable-filter" in sigset or "vtable-filter-menu" in sigset:
        dom_types.add("vtable-filter-menu")

    with _session_lock:
        old = dict(_session) if _session.get("active") else None
        _session.clear()
    if old:
        _teardown_session(old)

    watch_tab = "tab" in sigset
    watch_url = "url" in sigset
    watch_network = "network" in sigset and bool(listen_targets)
    tab = browser_session.get_tab()
    fr = browser_session.get_active_frame(tab)
    base_tab_count = browser_session.tab_count()
    base_url = (fr.url if fr else tab.url) or ""

    observer_scopes = []
    if dom_types:
        install_script = _observer_script(capture_existing)
        top_status = _parse_json(_run_js_safe(tab, install_script))
        if isinstance(top_status, dict) and top_status.get("installed"):
            observer_scopes.append("top")
        if fr is not None:
            frame_status = _parse_json(_run_js_safe(fr, install_script))
            if isinstance(frame_status, dict) and frame_status.get("installed"):
                observer_scopes.append("iframe")

    net_queue = queue.Queue()
    net_thread = None
    if watch_network:
        targets = listen_targets
        if isinstance(targets, str):
            targets = [item.strip() for item in targets.split(",") if item.strip()]
        try:
            try:
                tab.listen.stop()
            except Exception:
                pass
            network_record.start_http_listener(tab.listen, targets or True, None)
        except Exception as exc:
            logger.debug("listen start 失败: %s", exc)
            watch_network = False

        if watch_network and not native_wait:
            net_timeout = timeout_for_net if timeout_for_net is not None else 120

            def _net_waiter():
                try:
                    packets = network_record.wait_for_business_packets(
                        tab.listen, count=1, timeout=net_timeout,
                    )
                    if packets:
                        net_queue.put(packets[0])
                except Exception as exc:
                    logger.debug("net waiter 失败: %s", exc)

            net_thread = threading.Thread(target=_net_waiter, daemon=True)
            net_thread.start()

    new_session = {
        "active": True,
        "tab": tab,
        "fr": fr,
        "sigset": sigset,
        "dom_types": dom_types,
        "observer_scopes": observer_scopes,
        "pending_dom": deque(),
        "watch_tab": watch_tab,
        "watch_url": watch_url,
        "watch_network": watch_network,
        "native_wait": bool(native_wait),
        "base_url": base_url,
        "base_tab_count": base_tab_count,
        "net_queue": net_queue,
        "net_thread": net_thread,
        "start": time.monotonic(),
    }
    with _session_lock:
        _session.update(new_session)
    return new_session


def _teardown_session(sess):
    """清理 observer、listener，并短暂等待网络消费线程退出。"""
    if sess.get("dom_types"):
        _run_js_safe(sess.get("tab"), _CLEANUP_OBSERVER_JS)
        if sess.get("fr") is not None:
            _run_js_safe(sess["fr"], _CLEANUP_OBSERVER_JS)
    if sess.get("watch_network"):
        try:
            sess["tab"].listen.stop()
        except Exception:
            pass
        thread = sess.get("net_thread")
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=0.5)


def _poll_once(sess, now):
    """单次轮询。命中返回信号 dict，未命中返回 None。"""
    # ① DOM 信号（MutationObserver 有界缓冲，批量读取后逐个消费）
    if sess["dom_types"]:
        pending = sess["pending_dom"]
        if not pending:
            for target in (sess["fr"], sess["tab"]):
                if target is None:
                    continue
                signals = _parse_json(_run_js_safe(target, _POLL_SIGNALS_JS)) or []
                pending.extend(
                    signal for signal in signals
                    if isinstance(signal, dict) and signal.get("type") in sess["dom_types"]
                )
        if pending:
            signal = pending.popleft()
            return {
                "type": signal["type"],
                "scope": signal.get("scope"),
                "payload": signal,
                "elapsedMs": int(
                    signal.get("elapsedMs", 0) or (now - sess["start"]) * 1000
                ),
            }
    # ② Tab 变化
    if sess["watch_tab"]:
        cur = browser_session.tab_count()
        if cur != sess["base_tab_count"]:
            return {"type": "tab_change", "tab_count": cur, "old_count": sess["base_tab_count"],
                    "elapsedMs": int((now - sess["start"]) * 1000)}
    # ③ URL 变化（活动 iframe 跳转）
    if sess["watch_url"] and sess["fr"] is not None:
        try:
            cur_url = sess["fr"].url
        except Exception:
            cur_url = ""
        if cur_url and cur_url != sess["base_url"]:
            return {"type": "url_change", "url": cur_url, "old_url": sess["base_url"],
                    "elapsedMs": int((now - sess["start"]) * 1000)}
    # ④ 网络响应（后台线程投递，非阻塞取）
    if sess["watch_network"]:
        while True:
            try:
                pkt = sess["net_queue"].get_nowait()
            except queue.Empty:
                break
            p = pkt[0] if isinstance(pkt, list) else pkt
            if network_record.is_noise_packet(p):
                continue
            packet = network_record.packet_to_dict(p)
            return {
                "type": "network",
                "url": packet.get("url", ""),
                "method": packet.get("method", ""),
                "api_target": packet.get("api_target", ""),
                "post_data": packet.get("post_data"),
                "status": packet.get("status"),
                "packet": packet,
                "elapsedMs": int((now - sess["start"]) * 1000),
            }
    return None


def observe_snapshot(only_visible: bool = True, include_table_data: bool = False,
                     detail: str = "summary") -> dict:
    """统一观察器快照：复用结构化浮层扫描能力，作为当前 UI 状态的唯一推荐读取入口。

    返回 overlays 字段，覆盖 modal/drawer/popover/tooltip/dropdown/calendar/message/notification。
    scan_floats 继续作为内部兼容实现保留，但外部模型应优先调用本工具。

    Args:
        only_visible: 是否只返回可见浮层。
        include_table_data: 是否包含浮层内表格数据。
        detail: 详情级别，"summary"（默认）精简日历单元格和字段详情，
                "full" 返回完整日历单元格/按钮/字段细节。
    """
    try:
        from . import page_model

        data = page_model.scan_floats(
            only_visible=only_visible,
            include_table_data=include_table_data,
            detail=detail,
        )
        overlays = data.get("floats", []) if isinstance(data, dict) else []
        return {
            "ok": bool(data.get("ok", False)) if isinstance(data, dict) else False,
            "type": "snapshot",
            "count": len(overlays),
            "overlays": overlays,
            "page": {
                "active_tab": data.get("active_tab", "") if isinstance(data, dict) else "",
                "has_active_frame": data.get("has_active_frame", False) if isinstance(data, dict) else False,
                "frame_url": data.get("frame_url", "") if isinstance(data, dict) else "",
            },
            "source": "scan_floats",
        }
    except Exception as e:
        logger.debug("observe_snapshot 失败: %s", e)
        return {"ok": False, "type": "snapshot", "count": 0, "overlays": [], "reason": str(e)}


def _attach_snapshot(result: dict, include_snapshot: bool, include_table_data: bool = False,
                     detail: str = "summary") -> dict:
    if not include_snapshot:
        return result
    try:
        result["snapshot_after"] = observe_snapshot(include_table_data=include_table_data,
                                                     detail=detail)
    except Exception as e:
        result["snapshot_after"] = {
            "ok": False,
            "type": "snapshot",
            "count": 0,
            "overlays": [],
            "reason": str(e),
        }
    return result


def observe_start(signals=None, listen_targets=None, native_wait: bool = False,
                  capture_existing: bool = False) -> dict:
    """两段式观察器·启动：点击前安装 MutationObserver + 网络监听。

    默认忽略安装前已存在的 modal/dropdown 等基线组件，避免在弹窗内继续操作时把旧
    modal 误报为本次动作结果。只有点击已经发生的 ``observe_post_click`` 会显式设置
    ``capture_existing=True``。
    """
    sess = _build_session(
        signals, listen_targets, native_wait=native_wait,
        capture_existing=capture_existing,
    )
    return {
        "ok": True,
        "session": "active",
        "watched": sorted(sess["sigset"]),
        "base_url": sess["base_url"],
        "base_tab_count": sess["base_tab_count"],
        "observer_scopes": sess["observer_scopes"],
        "network_active": sess["watch_network"],
    }


def _observe_wait_native(sess: dict, timeout: float, include_snapshot: bool,
                         detail: str) -> dict:
    """Wait through DrissionPage primitives only for formal recipe execution."""
    try:
        signal = _poll_once(sess, time.monotonic())
        if not signal:
            if sess.get("watch_network"):
                packets = network_record.wait_for_business_packets(
                    sess["tab"].listen, count=1, timeout=timeout,
                )
                if packets:
                    sess["net_queue"].put(packets[0])
            elif sess.get("dom_types"):
                selector = "c:" + ",".join(_ALL_SELS)
                targets = [target for target in (sess.get("fr"), sess.get("tab")) if target is not None]
                target = targets[0] if targets else None
                if target is not None:
                    target.wait.ele_displayed(selector, timeout=timeout, raise_err=False)
            elif sess.get("watch_url") and sess.get("fr") is not None:
                sess["fr"].wait.url_change(
                    sess.get("base_url", ""), exclude=True, timeout=timeout, raise_err=False
                )

            signal = _poll_once(sess, time.monotonic())

        if signal:
            events, summaries = _compact_events([signal])
            result = dict(events[0])
            result["events"] = summaries
            result["event_count"] = len(events)
        else:
            result = {
                "type": "none", "events": [],
                "elapsedMs": int((time.monotonic() - sess["start"]) * 1000),
                "watched": sorted(sess["sigset"]),
            }
        return _attach_snapshot(result, include_snapshot, detail=detail)
    finally:
        with _session_lock:
            _teardown_session(sess)
            _session.clear()


def observe_wait(timeout: float = 8.0, poll_interval: float = 0.12,
                 include_snapshot: bool = True, detail: str = "summary",
                 native_wait: bool = False) -> dict:
    """两段式观察器·等待: 轮询 observe_start 安装的 observer, 在短窗口内收集多个事件,
    按优先级选主事件返回, 避免 first-signal-wins 漏掉 .ant-message.

    收集窗口 _COLLECT_WINDOW=0.6s, 首次信号后继续收集, 窗口结束或超时返回.

    Args:
        timeout: 最长等待秒数 (默认 8).
        poll_interval: Python 侧读缓冲间隔秒数 (默认 0.12); DOM 由 MutationObserver 即时触发.
        include_snapshot: 返回时附带当前浮层快照 snapshot_after, 默认 True.
        detail: 快照详情级别, "summary"(默认) 精简日历单元格, "full" 返回完整单元格.

    Returns:
        有事件: {type, payload?, events: [摘要...], event_count, elapsedMs, snapshot_after}
        无事件: {type:'none', events:[], elapsedMs, watched:[...], snapshot_after}
        无 session: {type:'none', reason:'...', events:[], snapshot_after}
    """
    with _session_lock:
        sess = dict(_session)
    if not sess.get("active"):
        return _attach_snapshot(
            {"type": "none", "reason": "no active observe session; call observe_start first",
             "events": []},
            include_snapshot, detail=detail,
        )
    use_native_wait = bool(native_wait or sess.get("native_wait"))
    if use_native_wait:
        return _observe_wait_native(sess, timeout, include_snapshot, detail)

    timeout = max(float(timeout or 0), 0.0)
    poll_interval = max(float(poll_interval or 0), 0.01)
    deadline = time.monotonic() + timeout
    collect_deadline = None
    events = []
    first_pass = True
    try:
        while first_pass or time.monotonic() < deadline:
            first_pass = False
            now = time.monotonic()
            signal = _poll_once(sess, now)
            if signal:
                events.append(signal)
                if collect_deadline is None:
                    collect_deadline = now + _COLLECT_WINDOW
            now = time.monotonic()
            if collect_deadline is not None and now >= collect_deadline:
                break
            wait_until = min(deadline, collect_deadline or deadline)
            remaining = wait_until - now
            if remaining <= 0:
                break
            time.sleep(min(poll_interval, remaining))

        if not events:
            result = {"type": "none",
                      "events": [],
                      "elapsedMs": int((time.monotonic() - sess["start"]) * 1000),
                      "watched": sorted(sess["sigset"])}
        else:
            events, summaries = _compact_events(events)
            primary = _pick_primary(events)
            result = dict(primary)
            result["events"] = summaries
            result["event_count"] = len(events)

        return _attach_snapshot(result, include_snapshot, detail=detail)
    finally:
        with _session_lock:
            _teardown_session(sess)
            _session.clear()
def observe_post_click(timeout: float = 10.0, signals=None, listen_targets=None,
                       poll_interval: float = 0.12, include_snapshot: bool = True,
                       detail: str = "summary") -> dict:
    """点击后统一观察器（便捷封装）：observe_start + observe_wait 一次调用。
    适用于点击已发生、或不需要在点击间隙观察的场景。

    ⚠️ 若要捕获**短寿命 toast**（如保存成功 ~3s），且点击与 observe 之间有调用间隙
    （agent 思考时间），改用两段式：observe_start() → click() → observe_wait()，
    observer 在点击前就监听。

    Args:
        timeout: 最长观察秒数（默认 10）。窗口结束或超时返回多个事件。
        signals: 监听信号类型列表，默认 ['overlay','notification','message','tab','url']。
        listen_targets: 网络监听 URL 特征（逗号分隔）；仅 signals 含 'network' 时生效。
        poll_interval: 轮询间隔秒数（默认 0.12）。
        include_snapshot: 返回时附带当前浮层快照 snapshot_after，默认 True。
        detail: 快照详情级别，"summary"（默认）精简日历单元格，"full" 返回完整单元格。

    Returns:
        同 observe_wait：{type, payload?, events, event_count, elapsedMs, snapshot_after, ...}
    """
    observe_start(signals=signals, listen_targets=listen_targets, capture_existing=True)
    return observe_wait(timeout=timeout, poll_interval=poll_interval,
                        include_snapshot=include_snapshot, detail=detail)


# ==================== 原子检测工具（单点排查用） ====================

def _detect_toast(selector: str, content_selector: str, timeout: float, toast_type: str) -> dict:
    """用 DrissionPage 可见元素 waiter 在总预算内检测 iframe 与顶层 toast。"""
    timeout = max(float(timeout or 0), 0.0)
    started = time.monotonic()
    deadline = started + timeout
    tab = browser_session.get_tab_ro()
    fr = browser_session.get_active_frame_ro(tab, timeout=min(timeout, 0.5))
    first_pass = True
    while first_pass or time.monotonic() < deadline:
        first_pass = False
        scopes = ((fr, "iframe"), (tab, "top"))
        for target, scope in scopes:
            if target is None:
                continue
            remaining = max(deadline - time.monotonic(), 0.0)
            try:
                notice = target.wait.ele_displayed(
                    'c:%s' % selector,
                    timeout=min(remaining, 0.15),
                    raise_err=False,
                )
            except Exception:
                notice = None
            if not notice:
                continue
            text = ""
            if content_selector:
                try:
                    content = notice.ele('c:%s' % content_selector, timeout=0.1)
                    if content:
                        text = (content.text or "").strip()
                except Exception:
                    pass
            kind = ""
            if toast_type == "message":
                try:
                    content = notice.ele('c:[class*="ant-message-"]', timeout=0.05)
                    if content:
                        import re
                        match = re.search(
                            r"ant-message-(success|info|warning|error|loading)",
                            content.attr("class") or "",
                        )
                        if match:
                            kind = match.group(1)
                except Exception:
                    pass
            result = {"type": toast_type, "scope": scope, "message": text[:200]}
            if kind:
                result["kind"] = kind
            return result
    return {"type": "none", "waited": round(time.monotonic() - started, 2)}


def detect_notification(timeout: float = 2.0) -> dict:
    """原子工具：检测 .ant-notification-notice（iframe 优先，回退 top）。
    事件驱动 ele() 等待，非固定 sleep。用于单点排查通知类 toast。"""
    return _detect_toast(_SEL_NOTIFICATION, ".ant-notification-notice-message", timeout, "notification")


def detect_message(timeout: float = 2.0) -> dict:
    """原子工具：检测 .ant-message-notice（含 success/info/warning/error/loading，iframe+top）。
    事件驱动 ele() 等待。专门捕获「保存订单成功」这类短寿命 toast。"""
    return _detect_toast(_SEL_MESSAGE, ".ant-message-notice-content", timeout, "message")


def detect_layer_msg(timeout: float = 2.0) -> dict:
    """原子工具：检测 .layui-layer-msg / .layui-layer-dialog.layui-layer-msg。
    事件驱动 ele() 等待。捕获遗留 jQuery 页面 3 秒自动关闭的短寿命消息提示。"""
    return _detect_toast(_SEL_LAYER_MSG, ".layui-layer-content", timeout, "layer_msg")


def detect_url_change(old_url: str, timeout: float = 5.0) -> dict:
    """等待活动 iframe URL 离开 old_url；超时必须返回 none。"""
    tab = browser_session.get_tab_ro()
    fr = browser_session.get_active_frame_ro(tab, timeout=min(max(timeout, 0), 0.5))
    if fr is None:
        return {"type": "none", "reason": "无活动 iframe"}
    try:
        changed = fr.wait.url_change(
            old_url,
            exclude=True,
            timeout=timeout,
            raise_err=False,
        )
        if changed:
            return {
                "type": "url_change",
                "url": getattr(fr, "url", "") or "",
                "old_url": old_url,
            }
    except Exception:
        pass
    return {"type": "none", "waited": round(timeout, 2)}


def detect_tab_change(old_count: int, timeout: float = 5.0) -> dict:
    """等待浏览器标签数量变化；用 Tab waiter 限速而非 Python 固定 sleep。"""
    deadline = time.monotonic() + max(float(timeout or 0), 0.0)
    tab = browser_session.get_tab_ro()
    while time.monotonic() < deadline:
        current = browser_session.tab_count()
        if current != old_count:
            return {"type": "tab_change", "tab_count": current, "old_count": old_count}
        tab.wait(min(0.15, max(deadline - time.monotonic(), 0.0)))
    return {"type": "none", "waited": round(timeout, 2)}
