"""bootstrap-select / 原生 <select> 适配（遗留 jQuery 页）。

交互：点击 `.bootstrap-select > button.dropdown-toggle` → 在打开的
`.dropdown-menu.inner li a` 中点选项；原生 select 直接设值并触发 change。
"""
from __future__ import annotations

import logging
import time

from ..core import ui_contract_legacy as legacy
from . import browser_session

logger = logging.getLogger("drissionpage-mcp")


def _active_targets(prefer_layer: bool = True):
    """候选文档：layer 内容 frame → 业务 iframe → top。"""
    from . import layer_modal

    tab = browser_session.get_tab()
    targets = []
    if prefer_layer:
        try:
            resolved = layer_modal.get_layer_content_frame(timeout=1.5)
            if resolved.get("ok") and resolved.get("content_frame") is not None:
                targets.append(("layer", resolved["content_frame"]))
        except Exception:
            pass
    try:
        fr = browser_session.get_active_frame_ro(tab, timeout=0.5)
        if fr is None:
            fr = browser_session.get_active_frame(tab)
        if fr is not None:
            targets.append(("iframe", fr))
    except Exception:
        pass
    if tab is not None:
        targets.append(("top", tab))
    # 去重
    seen = set()
    ordered = []
    for name, obj in targets:
        key = id(obj)
        if key in seen:
            continue
        seen.add(key)
        ordered.append((name, obj))
    return ordered


def _xpath_literal(value: str) -> str:
    text = str(value or "")
    if "'" not in text:
        return "'%s'" % text
    if '"' not in text:
        return '"%s"' % text
    return "concat(%s)" % ", \"'\", ".join("'%s'" % part for part in text.split("'"))


def _find_field_container(target, field_name: str, timeout: float = 1.0):
    """按标签/name/placeholder 定位字段容器。"""
    field_name = str(field_name or "").strip()
    if not field_name:
        return target
    lit = _xpath_literal(field_name)
    locators = [
        # form-group 标签
        "xpath://div[contains(@class,'form-group')]"
        "[.//label[contains(normalize-space(.), %s)]]" % lit,
        "xpath://tr[.//th[contains(normalize-space(.), %s)] or "
        ".//label[contains(normalize-space(.), %s)]]" % (lit, lit),
        "xpath://*[self::label or contains(@class,'control-label')]"
        "[contains(normalize-space(.), %s)]/ancestor::*[self::div or self::tr][1]" % lit,
        "xpath://*[@name=%s or @id=%s or @placeholder=%s]"
        "/ancestor::*[contains(@class,'form-group') or contains(@class,'bootstrap-select') or self::td][1]"
        % (lit, lit, lit),
        "xpath://*[contains(@placeholder, %s)]/ancestor::*[contains(@class,'form-group')][1]"
        % lit,
    ]
    for loc in locators:
        try:
            el = target.ele(loc, timeout=min(timeout, 0.6))
            if el is not None:
                return el
        except Exception:
            continue
    # 文本节点
    try:
        label = target.ele("text:%s" % field_name, timeout=min(timeout, 0.4))
        if label is not None:
            return label.parent(2) or label.parent() or label
    except Exception:
        pass
    return None


def _bootstrap_select_root(container):
    """容器内找 bootstrap-select 根。"""
    if container is None:
        return None
    try:
        if "bootstrap-select" in str((container.attrs or {}).get("class", "")):
            return container
    except Exception:
        pass
    for loc in (
        "c:.bootstrap-select",
        "css:.bootstrap-select",
        "xpath:.//*[contains(@class,'bootstrap-select')]",
    ):
        try:
            el = container.ele(loc, timeout=0.3)
            if el is not None:
                return el
        except Exception:
            continue
    return None


def _native_select(container):
    for loc in ("t:select", "css:select", "css:select.selectpicker"):
        try:
            el = container.ele(loc, timeout=0.3)
            if el is not None:
                return el
        except Exception:
            continue
    return None


def list_bootstrap_select_options(field_name: str = "", prefer_layer: bool = True) -> dict:
    """列出字段可选值。"""
    field_name = str(field_name or "").strip()
    for scope, target in _active_targets(prefer_layer=prefer_layer):
        container = _find_field_container(target, field_name) if field_name else target
        if container is None:
            continue
        select_el = _native_select(container)
        if select_el is None:
            root = _bootstrap_select_root(container)
            if root is not None:
                select_el = _native_select(root)
        if select_el is None:
            continue
        try:
            raw = select_el.run_js(
                "return JSON.stringify([].slice.call(this.options||[]).map(function(o){"
                "return {value:o.value||'', text:(o.textContent||'').replace(/\\s+/g,' ').trim(),"
                "selected:!!o.selected};}))"
            )
            import json
            options = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            options = []
            try:
                for opt in select_el.eles("t:option", timeout=0.3) or []:
                    options.append({
                        "value": opt.attr("value") or "",
                        "text": (opt.text or "").strip(),
                        "selected": opt.attr("selected") is not None,
                    })
            except Exception:
                pass
        return {
            "ok": True,
            "scope": scope,
            "field_name": field_name,
            "options": options,
            "count": len(options),
        }
    return {"ok": False, "reason": "select field not found: %s" % field_name}


def select_bootstrap_option(
    field_name: str,
    option_text: str,
    select_index: int = 0,
    prefer_layer: bool = True,
    timeout: float = 5.0,
) -> dict:
    """在 bootstrap-select 或原生 select 上选择选项。

    Args:
        field_name: 标签/name/placeholder
        option_text: 选项可见文本（精确优先，其次包含）
        select_index: 同名字段多个 select 时的序号
        prefer_layer: 优先在可见 layer 内容 frame 内查找
    """
    expected = str(option_text or "").strip()
    if not expected:
        return {"ok": False, "reason": "option_text is required"}
    field_name = str(field_name or "").strip()
    select_index = max(int(select_index or 0), 0)
    timeout = max(float(timeout or 0), 0.0)
    deadline = time.monotonic() + timeout

    def remaining(cap=None):
        value = max(deadline - time.monotonic(), 0.0)
        return min(value, cap) if cap is not None else value

    last_reason = "select not found"
    for scope, target in _active_targets(prefer_layer=prefer_layer):
        container = (
            _find_field_container(target, field_name, timeout=remaining(1.0))
            if field_name else target
        )
        if container is None:
            last_reason = "field container not found: %s" % field_name
            continue

        # 多个 bootstrap-select
        roots = container.eles("c:.bootstrap-select", timeout=remaining(0.5)) or []
        native_selects = container.eles("t:select", timeout=remaining(0.3)) or []

        root = None
        select_el = None
        if roots:
            root = roots[min(select_index, len(roots) - 1)]
            select_el = _native_select(root)
        elif native_selects:
            select_el = native_selects[min(select_index, len(native_selects) - 1)]
        else:
            # 容器本身
            root = _bootstrap_select_root(container)
            select_el = _native_select(root or container)

        if select_el is None and root is None:
            last_reason = "no select control in field: %s" % field_name
            continue

        # 路径 A：bootstrap-select UI
        if root is not None:
            try:
                toggle = root.ele("c:button.dropdown-toggle", timeout=remaining(0.5)) or root
                # 关闭其它下拉
                try:
                    target.actions.key_down("ESCAPE").key_up("ESCAPE")
                except Exception:
                    pass
                toggle.click(by_js=False, timeout=remaining(2.0), wait_stop=False)
                # 等待菜单打开
                menu = None
                open_deadline = time.monotonic() + min(remaining(), 2.5)
                while time.monotonic() < open_deadline:
                    menu = (
                        root.ele("css:.dropdown-menu.open, .dropdown-menu.inner", timeout=0.15)
                        or target.ele(
                            "css:.bootstrap-select.open .dropdown-menu, "
                            ".bootstrap-select .dropdown-menu.open",
                            timeout=0.15,
                        )
                    )
                    if menu is not None:
                        try:
                            if menu.states.is_displayed:
                                break
                        except Exception:
                            break
                    menu = None
                    time.sleep(0.08)
                if menu is None:
                    # 回退原生设值
                    pass
                else:
                    items = menu.eles("css:li a, li, a", timeout=remaining(0.5)) or []
                    enabled = []
                    for item in items:
                        try:
                            if not item.states.is_displayed:
                                continue
                            cls = str((item.attrs or {}).get("class", ""))
                            if "disabled" in cls:
                                continue
                            text = (item.text or "").strip()
                            if not text:
                                continue
                            enabled.append((text, item))
                        except Exception:
                            continue
                    exact = next((it for text, it in enabled if text == expected), None)
                    partial = [it for text, it in enabled if expected in text]
                    if exact is None and len(partial) > 1:
                        return {
                            "ok": False,
                            "reason": "option match is ambiguous: %s" % expected,
                            "available": [t for t, _ in enabled[:50]],
                            "scope": scope,
                            "adapter": "bootstrap-select",
                        }
                    match = exact or (partial[0] if partial else None)
                    if match is None:
                        last_reason = "option not found: %s" % expected
                        # 尝试原生
                    else:
                        match.click(by_js=False, timeout=remaining(2.0), wait_stop=False)
                        time.sleep(0.15)
                        # 回读展示值（按钮文案）
                        display = ""
                        try:
                            toggle2 = root.ele("c:button.dropdown-toggle", timeout=0.3) or toggle
                            display = (toggle2.text or "").strip()
                        except Exception:
                            try:
                                display = (toggle.text or "").strip()
                            except Exception:
                                display = ""
                        # 若按钮未刷新，用原生路径强制同步
                        if expected not in display and select_el is not None:
                            try:
                                import json as _json
                                sync = select_el.run_js(
                                    """
                                    var expected = %s;
                                    for (var i=0;i<this.options.length;i++){
                                      var t=(this.options[i].textContent||'').replace(/\\s+/g,' ').trim();
                                      if (t===expected || t.indexOf(expected)>=0){
                                        this.selectedIndex=i;
                                        if (window.jQuery && window.jQuery(this).selectpicker){
                                          window.jQuery(this).selectpicker('val', this.value);
                                          window.jQuery(this).selectpicker('refresh');
                                        }
                                        break;
                                      }
                                    }
                                    var wrap=this.closest('.bootstrap-select');
                                    var btn=wrap&&wrap.querySelector('button.dropdown-toggle');
                                    return (btn&&(btn.textContent||btn.title)||'').replace(/\\s+/g,' ').trim();
                                    """ % _json.dumps(expected, ensure_ascii=False)
                                )
                                if sync:
                                    display = str(sync).strip()
                            except Exception:
                                pass
                        return {
                            "ok": True,
                            "scope": scope,
                            "adapter": "bootstrap-select",
                            "field_name": field_name,
                            "option_text": expected,
                            "display": display,
                        }
            except Exception as exc:
                last_reason = "bootstrap-select click failed: %s" % exc

        # 路径 B：原生 select 设值
        if select_el is not None:
            try:
                # 精确匹配 option text
                js = """
                var expected = %s;
                var sel = this;
                var matched = -1;
                for (var i = 0; i < sel.options.length; i++) {
                  var t = (sel.options[i].textContent || '').replace(/\\s+/g,' ').trim();
                  if (t === expected) { matched = i; break; }
                }
                if (matched < 0) {
                  for (var i = 0; i < sel.options.length; i++) {
                    var t = (sel.options[i].textContent || '').replace(/\\s+/g,' ').trim();
                    if (t.indexOf(expected) >= 0) { matched = i; break; }
                  }
                }
                if (matched < 0) {
                  return JSON.stringify({ok:false, reason:'option not found',
                    available:[].slice.call(sel.options).map(function(o){
                      return (o.textContent||'').replace(/\\s+/g,' ').trim();
                    }).slice(0,50)});
                }
                sel.selectedIndex = matched;
                var val = sel.options[matched].value;
                sel.value = val;
                // bootstrap-select 必须先 val 再 refresh，否则按钮文案不更新
                try {
                  if (window.jQuery) {
                    var $ = window.jQuery;
                    var $sel = $(sel);
                    if ($sel.selectpicker) {
                      $sel.selectpicker('val', val);
                      $sel.selectpicker('render');
                      $sel.selectpicker('refresh');
                    }
                  }
                } catch(e) {}
                sel.dispatchEvent(new Event('change', {bubbles:true}));
                sel.dispatchEvent(new Event('input', {bubbles:true}));
                var text = (sel.options[matched].textContent || '').replace(/\\s+/g,' ').trim();
                var display = text;
                try {
                  var wrap = sel.closest ? sel.closest('.bootstrap-select') : null;
                  var btn = wrap ? wrap.querySelector('button.dropdown-toggle') : null;
                  if (btn) display = (btn.textContent || btn.title || text).replace(/\\s+/g,' ').trim();
                } catch(e2) {}
                return JSON.stringify({ok:true, value: sel.value || '', text: text, display: display});
                """
                import json as _json
                raw = select_el.run_js(js % _json.dumps(expected, ensure_ascii=False))
                data = _json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(data, dict) and data.get("ok"):
                    return {
                        "ok": True,
                        "scope": scope,
                        "adapter": "native-select",
                        "field_name": field_name,
                        "option_text": expected,
                        "value": data.get("value"),
                        "display": data.get("display") or data.get("text"),
                    }
                if isinstance(data, dict):
                    last_reason = data.get("reason") or last_reason
                    if data.get("available"):
                        return {
                            "ok": False,
                            "reason": data.get("reason") or last_reason,
                            "available": data.get("available"),
                            "scope": scope,
                            "adapter": "native-select",
                        }
            except Exception as exc:
                last_reason = "native select failed: %s" % exc

    return {
        "ok": False,
        "reason": last_reason,
        "field_name": field_name,
        "option_text": expected,
    }
