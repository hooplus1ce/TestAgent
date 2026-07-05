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
import logging
import queue
import threading
import time

import browser_session

logger = logging.getLogger("drission-ui")

# ---- 信号选择器 ----
_SEL_MODAL = ".ant-modal-content"
_SEL_NOTIFICATION = ".ant-notification-notice"
_SEL_MESSAGE = ".ant-message-notice"
_ALL_SELS = [_SEL_MODAL, _SEL_NOTIFICATION, _SEL_MESSAGE]

# ---- MutationObserver 注入脚本（在 target.document 内安装；含初始扫描，捕获已存在的信号）----
# 注意：必须用顶层 return（不能用 IIFE），否则 DrissionPage run_js 拿不到返回值
_INSTALL_OBSERVER_JS = r"""
if (window.__du_obs) { try{ window.__du_obs.disconnect(); }catch(e){} }
window.__du_signals = [];
window.__du_t0 = Date.now();
var SELS = ['.ant-modal-content','.ant-notification-notice','.ant-message-notice'];
var scope = (window.top === window) ? 'top' : 'iframe';
function classify(el){
  var cls = el.className || ''; if (typeof cls !== 'string') cls = '';
  if (cls.indexOf('ant-modal-content') >= 0) {
    var isConfirm = !!el.querySelector('.ant-confirm-body');
    var title = el.querySelector('.ant-modal-title');
    var body = el.querySelector('.ant-modal-body');
    var btns = [].slice.call(el.querySelectorAll('.ant-btn')).map(function(b){return (b.textContent||'').trim();}).filter(Boolean);
    return {type: isConfirm ? 'confirm' : 'interactive', scope: scope,
            title: title ? (title.textContent||'').trim() : '',
            content: body ? (body.textContent||'').trim().slice(0,200) : '',
            buttons: btns, hasClose: !!el.querySelector('.ant-modal-close')};
  }
  if (cls.indexOf('ant-notification-notice') >= 0) {
    var m = el.querySelector('.ant-notification-notice-message');
    var d = el.querySelector('.ant-notification-notice-description');
    return {type:'notification', scope:scope,
            message: ((m ? m.textContent : '') + (d ? (' ' + (d.textContent||'')) : '')).trim()};
  }
  if (cls.indexOf('ant-message-notice') >= 0) {
    var c = el.querySelector('.ant-message-notice-content');
    var kind = '';
    var cc = el.querySelector('[class*="ant-message-"]');
    if (cc) { var mm = (cc.className||'').match(/ant-message-(success|info|warning|error|loading)/); if (mm) kind = mm[1]; }
    return {type:'message', scope:scope, kind:kind, message: c ? (c.textContent||'').trim() : ''};
  }
  return null;
}
function isVis(el){ var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }
// 初始扫描：捕获 observer 安装前已存在的信号（如点击与观察之间已渲染完的 toast）
for (var i=0;i<SELS.length;i++){
  var els = document.querySelectorAll(SELS[i]);
  for (var j=0;j<els.length;j++){
    if (!isVis(els[j])) continue;
    var s0 = classify(els[j]); if (s0) { s0.elapsedMs = 0; window.__du_signals.push(s0); break; }
  }
  if (window.__du_signals.length) break;
}
var obs = new MutationObserver(function(muts){
  for (var i=0;i<muts.length;i++){
    var added = muts[i].addedNodes;
    for (var j=0;j<added.length;j++){
      var n = added[j];
      if (n.nodeType !== 1) continue;
      var sig = null;
      for (var k=0;k<SELS.length;k++){ if (n.matches && n.matches(SELS[k])) { sig = classify(n); break; } }
      if (!sig && n.querySelector) {
        for (var k=0;k<SELS.length;k++){ var e = n.querySelector(SELS[k]); if (e) { sig = classify(e); break; } }
      }
      if (sig) { sig.elapsedMs = Date.now() - window.__du_t0; window.__du_signals.push(sig); }
    }
  }
});
obs.observe(document.body, {childList:true, subtree:true});
window.__du_obs = obs;
return JSON.stringify({installed:true, scope:scope, initialCount: window.__du_signals.length});
"""

# 读取并清空信号缓冲（消费语义，避免重复回报）
_POLL_SIGNALS_JS = r"""
var s = window.__du_signals || [];
var out = s.slice(0, 5);
s.length = 0;
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


def _build_session(signals, listen_targets, timeout_for_net=None):
    """安装 MutationObserver + 启动网络监听，写入 _session。返回 session dict。"""
    if signals is None:
        signals = ["modal", "notification", "message", "tab", "url"]
    sigset = set(s.lower() for s in signals)

    dom_types = set()
    if "modal" in sigset:
        dom_types |= {"interactive", "confirm"}
    if "notification" in sigset:
        dom_types.add("notification")
    if "message" in sigset:
        dom_types.add("message")
    watch_tab = "tab" in sigset
    watch_url = "url" in sigset
    watch_network = "network" in sigset and bool(listen_targets)

    tab = browser_session.get_tab()
    fr = browser_session.get_active_frame(tab)
    base_tab_count = browser_session.tab_count()
    base_url = (fr.url if fr else tab.url) or ""

    # 安装 MutationObserver（top + iframe）
    _run_js_safe(tab, _INSTALL_OBSERVER_JS)
    if fr is not None:
        _run_js_safe(fr, _INSTALL_OBSERVER_JS)

    # 网络监听（后台线程 wait，不阻塞 DOM 轮询）
    net_queue = queue.Queue()
    if watch_network:
        tg = listen_targets
        if isinstance(tg, str):
            tg = [t.strip() for t in tg.split(",") if t.strip()]
        try:
            tab.listen.start(tg)
        except Exception as e:
            logger.debug("listen start 失败: %s", e)
            watch_network = False

        net_timeout = timeout_for_net if timeout_for_net is not None else 120
        def _net_waiter():
            try:
                pkt = tab.listen.wait(count=1, timeout=net_timeout)
                if pkt:
                    net_queue.put(pkt)
            except Exception as e:
                logger.debug("net waiter 失败: %s", e)

        threading.Thread(target=_net_waiter, daemon=True).start()

    new_sess = {
        "active": True,
        "tab": tab, "fr": fr, "sigset": sigset, "dom_types": dom_types,
        "watch_tab": watch_tab, "watch_url": watch_url, "watch_network": watch_network,
        "base_url": base_url, "base_tab_count": base_tab_count,
        "net_queue": net_queue, "start": time.time(),
    }
    with _session_lock:
        old = dict(_session) if _session.get("active") else None
        _session.clear()
        _session.update(new_sess)
    if old:
        _teardown_session(old)  # 清理上一轮残留
    return new_sess


def _teardown_session(sess):
    """清理 observer + listener。"""
    _run_js_safe(sess["tab"], _CLEANUP_OBSERVER_JS)
    if sess.get("fr") is not None:
        _run_js_safe(sess["fr"], _CLEANUP_OBSERVER_JS)
    if sess.get("watch_network"):
        try:
            sess["tab"].listen.stop()
        except Exception:
            pass


def _poll_once(sess, now):
    """单次轮询。命中返回信号 dict，未命中返回 None。"""
    # ① DOM 信号（MutationObserver 缓冲，即时）
    if sess["dom_types"]:
        for target in (sess["fr"], sess["tab"]):
            if target is None:
                continue
            sigs = _parse_json(_run_js_safe(target, _POLL_SIGNALS_JS))
            if not sigs:
                continue
            for sig in sigs:
                if not isinstance(sig, dict):
                    continue
                if sig.get("type") in sess["dom_types"]:
                    return {
                        "type": sig["type"],
                        "scope": sig.get("scope"),
                        "payload": sig,
                        "elapsedMs": int(sig.get("elapsedMs", 0) or (now - sess["start"]) * 1000),
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
        try:
            pkt = sess["net_queue"].get_nowait()
        except queue.Empty:
            pkt = None
        if pkt:
            p = pkt[0] if isinstance(pkt, list) else pkt
            return {
                "type": "network",
                "url": getattr(p, "url", ""),
                "method": getattr(p, "method", ""),
                "status": getattr(p.response, "status", None) if getattr(p, "response", None) else None,
                "elapsedMs": int((now - sess["start"]) * 1000),
            }
    return None


def observe_start(signals=None, listen_targets=None) -> dict:
    """两段式观察器·启动：**点击前**调用，安装 MutationObserver + 网络监听，立即返回。
    observer 在点击前就已监听，消除「点击→观察」调用间隙（agent 思考时间可能 > toast 寿命），
    可靠捕获短寿命 toast（如保存成功 ~3s）。

    必须配对调用 observe_wait() 读取信号并清理（否则 observer 泄漏）。

    Args:
        signals: 监听信号类型列表，默认 ['modal','notification','message','tab','url']。
                 可选：'modal'/'notification'/'message'/'tab'/'url'/'network'。
        listen_targets: 网络监听 URL 特征（逗号分隔）；仅 signals 含 'network' 时生效。

    Returns:
        {ok, session:'active', watched:[...], base_url, base_tab_count}

    典型用法：
        observe_start(signals=["message","network"], listen_targets="gateway")
        click(...)                       # 触发动作
        observe_wait(timeout=8)          # 读首个信号 + 清理
    """
    sess = _build_session(signals, listen_targets)
    return {"ok": True, "session": "active", "watched": sorted(sess["sigset"]),
            "base_url": sess["base_url"], "base_tab_count": sess["base_tab_count"]}


def observe_wait(timeout: float = 8.0, poll_interval: float = 0.12) -> dict:
    """两段式观察器·等待：轮询 observe_start 安装的 observer，任一信号命中立即返回（first-signal-wins），
    随后清理 observer + listener。

    Args:
        timeout: 最长等待秒数（默认 8）。
        poll_interval: Python 侧读缓冲间隔秒数（默认 0.12）；DOM 由 MutationObserver 即时触发。

    Returns:
        命中：{type, scope?, payload?, elapsedMs, ...信号专属字段}
        未命中：{type:'none', elapsedMs, watched:[...]}
        无活跃 session：{type:'none', reason:'no active observe session'}
    """
    with _session_lock:
        sess = dict(_session)
    if not sess.get("active"):
        return {"type": "none", "reason": "no active observe session; call observe_start first"}
    deadline = sess["start"] + timeout
    try:
        while time.time() < deadline:
            sig = _poll_once(sess, time.time())
            if sig:
                return sig
            time.sleep(poll_interval)
        return {"type": "none", "elapsedMs": int((time.time() - sess["start"]) * 1000),
                "watched": sorted(sess["sigset"])}
    finally:
        with _session_lock:
            _teardown_session(sess)
            _session.clear()


def observe_post_click(timeout: float = 10.0, signals=None, listen_targets=None,
                       poll_interval: float = 0.12) -> dict:
    """点击后统一观察器（便捷封装）：observe_start + observe_wait 一次调用。
    适用于点击已发生、或不需要在点击间隙观察的场景。

    ⚠️ 若要捕获**短寿命 toast**（如保存成功 ~3s），且点击与 observe 之间有调用间隙
    （agent 思考时间），改用两段式：observe_start() → click() → observe_wait()，
    observer 在点击前就监听。

    Args:
        timeout: 最长观察秒数（默认 10）。信号命中会立即提前返回。
        signals: 监听信号类型列表，默认 ['modal','notification','message','tab','url']。
        listen_targets: 网络监听 URL 特征（逗号分隔）；仅 signals 含 'network' 时生效。
        poll_interval: 轮询间隔秒数（默认 0.12）。

    Returns:
        命中：{type, scope?, payload?, elapsedMs, ...信号专属字段}
        未命中：{type:'none', elapsedMs, watched:[...]}
    """
    sess = _build_session(signals, listen_targets, timeout_for_net=timeout)
    with _session_lock:
        _session["start"] = time.time()  # 单次封装：start 重置为现在
        sess = dict(_session)
    deadline = sess["start"] + timeout
    try:
        while time.time() < deadline:
            sig = _poll_once(sess, time.time())
            if sig:
                return sig
            time.sleep(poll_interval)
        return {"type": "none", "elapsedMs": int((time.time() - sess["start"]) * 1000),
                "watched": sorted(sess["sigset"])}
    finally:
        with _session_lock:
            _teardown_session(sess)
            _session.clear()


# ==================== 原子检测工具（单点排查用） ====================

def _detect_toast(selector: str, content_selector: str, timeout: float, toast_type: str) -> dict:
    """原子 toast 检测通用实现：iframe 优先，回退 top。ele(timeout) 事件驱动等待。"""
    tab = browser_session.get_tab_ro()
    fr = browser_session.get_active_frame_ro(tab)
    deadline = time.time() + timeout
    while time.time() < deadline:
        for target, scope in ((fr, "iframe"), (tab, "top")):
            if target is None:
                continue
            try:
                n = target.ele('c:%s' % selector, timeout=0.15)
            except Exception:
                n = None
            if not n:
                continue
            try:
                if not n.states.is_displayed:
                    continue
            except Exception:
                pass
            text = ""
            if content_selector:
                try:
                    ce = n.ele('c:%s' % content_selector, timeout=0.1)
                    if ce:
                        text = (ce.text or "").strip()
                except Exception:
                    pass
            kind = ""
            if toast_type == "message":
                try:
                    cc = n.ele('c:[class*="ant-message-"]', timeout=0.05)
                    if cc:
                        import re
                        m = re.search(r"ant-message-(success|info|warning|error|loading)", cc.attr("class") or "")
                        if m:
                            kind = m.group(1)
                except Exception:
                    pass
            out = {"type": toast_type, "scope": scope, "message": text[:200]}
            if kind:
                out["kind"] = kind
            return out
        time.sleep(0.08)
    return {"type": "none", "waited": round(timeout, 2)}


def detect_notification(timeout: float = 2.0) -> dict:
    """原子工具：检测 .ant-notification-notice（iframe 优先，回退 top）。
    事件驱动 ele() 等待，非固定 sleep。用于单点排查通知类 toast。"""
    return _detect_toast(_SEL_NOTIFICATION, ".ant-notification-notice-message", timeout, "notification")


def detect_message(timeout: float = 2.0) -> dict:
    """原子工具：检测 .ant-message-notice（含 success/info/warning/error/loading，iframe+top）。
    事件驱动 ele() 等待。专门捕获「保存订单成功」这类短寿命 toast。"""
    return _detect_toast(_SEL_MESSAGE, ".ant-message-notice-content", timeout, "message")


def detect_url_change(old_url: str, timeout: float = 5.0) -> dict:
    """原子工具：等待活动 iframe URL 变化。用 DrissionPage wait.url_change 事件驱动。
    点击后判断是否跳转（如新增保存后 saleOrderCreate → saleOrderDetail）。"""
    tab = browser_session.get_tab_ro()
    fr = browser_session.get_active_frame_ro(tab)
    if fr is None:
        return {"type": "none", "reason": "无活动 iframe"}
    try:
        fr.wait.url_change(old_url, timeout=timeout)
        return {"type": "url_change", "url": getattr(fr, "url", "") or "", "old_url": old_url}
    except Exception:
        return {"type": "none", "waited": round(timeout, 2)}


def detect_tab_change(old_count: int, timeout: float = 5.0) -> dict:
    """原子工具：等待浏览器 tab 数量变化（新 tab 打开/关闭）。短轮询 tab_count()。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = browser_session.tab_count()
        if cur != old_count:
            return {"type": "tab_change", "tab_count": cur, "old_count": old_count}
        time.sleep(0.15)
    return {"type": "none", "waited": round(timeout, 2)}
