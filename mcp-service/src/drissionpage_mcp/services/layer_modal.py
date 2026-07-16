"""layer.js 弹层适配：支持 iframe 型内容页的字段/按钮扫描。

遗留 SCM 页（账号管理等）的「新增/编辑/改密」多为::

  .layui-layer.layui-layer-iframe
    └── iframe#layui-layer-iframeN  →  独立表单文档

字段与保存/取消在子 frame 内，壳层只提供标题与关闭按钮。
"""
from __future__ import annotations

import json
import logging
import re

from ..core import ui_contract_legacy as legacy
from . import browser_session

logger = logging.getLogger("drissionpage-mcp")

_LAYER_FORM_JS = r"""
return (function(){
  function clean(t){ return (t || '').replace(/\s+/g, ' ').trim(); }
  function visible(el){
    if (!el || !el.isConnected) return false;
    var s = window.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  function rect(el){
    var r = el.getBoundingClientRect();
    return {x:Math.round(r.left*10)/10,y:Math.round(r.top*10)/10,
            width:Math.round(r.width*10)/10,height:Math.round(r.height*10)/10};
  }
  function stripLabel(t){
    // 去掉必填 * 与多余空白
    return clean(t).replace(/[＊*]+/g, '').replace(/\s+/g, ' ').trim();
  }
  function labelNear(el){
    // 昊链标准头端: label.control-label.col-sm-2 + div.col-sm-4 > input
    var col = el.closest(
      '.col-sm-4,.col-sm-3,.col-sm-5,.col-sm-6,.col-sm-8,.col-sm-9,.col-sm-10,' +
      '.col-md-4,.col-md-6,[class*="col-sm-"],[class*="col-md-"]'
    );
    if (col) {
      var prev = col.previousElementSibling;
      if (prev && (prev.tagName === 'LABEL' || /control-label/.test(prev.className || ''))) {
        return stripLabel(prev.textContent);
      }
      var wrap = col.parentElement;
      if (wrap) {
        var lab = wrap.querySelector(':scope > label.control-label, :scope > label, label.control-label');
        if (lab) return stripLabel(lab.textContent);
      }
    }
    var group = el.closest('.form-group, .form-group-sm, .form-inline, tr, .row, td') || el.parentElement;
    if (group) {
      var lab2 = group.querySelector('label.control-label, label, .control-label, th');
      if (lab2) return stripLabel(lab2.textContent);
    }
    var id = el.id;
    if (id) {
      var byFor = document.querySelector('label[for="'+id+'"]');
      if (byFor) return stripLabel(byFor.textContent);
    }
    var prevEl = el.previousElementSibling;
    if (prevEl && (/label/i.test(prevEl.tagName) || /label/i.test(prevEl.className||''))) {
      return stripLabel(prevEl.textContent);
    }
    return clean(el.getAttribute('placeholder') || el.getAttribute('name') || el.id || '');
  }
  function selectDisplay(el){
    // bootstrap-select 展示按钮
    var wrap = el.closest ? el.closest('.bootstrap-select') : null;
    if (wrap) {
      var btn = wrap.querySelector('button.dropdown-toggle');
      if (btn) return clean(btn.textContent || btn.title || '');
    }
    if (el.tagName === 'SELECT' && el.selectedOptions && el.selectedOptions[0]) {
      return clean(el.selectedOptions[0].textContent);
    }
    return clean(el.value || '');
  }
  function optionsOf(el){
    if (el.tagName !== 'SELECT') return [];
    return [].slice.call(el.options || []).map(function(opt){
      return {value: opt.value || '', text: clean(opt.textContent), selected: !!opt.selected};
    }).filter(function(o){ return o.text || o.value; }).slice(0, 80);
  }

  var fields = [];
  var seen = [];
  var nodes = document.querySelectorAll(
    'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="image"]),'+
    'textarea,select,.bootstrap-select'
  );
  for (var i = 0; i < nodes.length && fields.length < 80; i++) {
    var el = nodes[i];
    // bootstrap-select 容器：落到内部 select
    if (el.classList && el.classList.contains('bootstrap-select')) {
      var inner = el.querySelector('select');
      if (inner) el = inner;
      else continue;
    }
    if (!visible(el) && el.type !== 'file') continue;
    if (seen.indexOf(el) >= 0) continue;
    seen.push(el);
    var type = (el.type || el.tagName || '').toLowerCase();
    if (el.tagName === 'SELECT') type = 'select';
    if (el.tagName === 'TEXTAREA') type = 'textarea';
    var label = labelNear(el);
    // file 控件标签常在更外层；若命中其它字段标签则回退 name/placeholder
    if (type === 'file' && label && !/文件|图片|头像|附件|上传|photo|pic|file|image/i.test(label)) {
      var fileLab = el.closest('div,td,li');
      var better = '';
      if (fileLab) {
        var fl = fileLab.parentElement
          ? fileLab.parentElement.querySelector('label.control-label, label')
          : null;
        if (fl) better = stripLabel(fl.textContent);
      }
      label = better || clean(el.getAttribute('name') || el.id || '文件');
    }
    var entry = {
      index: fields.length,
      label: label,
      type: type,
      name: el.name || '',
      id: el.id || '',
      value: type === 'select' ? selectDisplay(el) : (el.value || ''),
      placeholder: el.getAttribute('placeholder') || '',
      required: !!(el.required || (el.className || '').indexOf('required') >= 0),
      disabled: !!el.disabled,
      readOnly: !!el.readOnly,
      area: 'layer',
      options: type === 'select' ? optionsOf(el) : [],
      selectorHint: el.id ? ('#' + el.id) : (el.name ? (el.tagName.toLowerCase() + '[name="'+el.name+'"]') : el.tagName.toLowerCase()),
      rect: rect(el)
    };
    fields.push(entry);
  }

  var buttons = [];
  var btns = document.querySelectorAll(
    'button, input[type="submit"], input[type="button"], a.btn, .btn'
  );
  for (var bi = 0; bi < btns.length && buttons.length < 40; bi++) {
    var b = btns[bi];
    if (!visible(b)) continue;
    // 排除 bootstrap-select 展示按钮与校验隐藏 submit
    if (b.closest && b.closest('.bootstrap-select')) continue;
    if ((b.className || '').indexOf('bv-hidden-submit') >= 0) continue;
    if ((b.className || '').indexOf('dropdown-toggle') >= 0) continue;
    var text = clean(b.value || b.textContent || b.getAttribute('title') || '');
    if (!text) continue;
    buttons.push({
      text: text,
      tag: b.tagName.toLowerCase(),
      type: b.type || '',
      cls: (b.className || '').toString().slice(0, 80),
      disabled: !!b.disabled,
      selectorHint: b.id ? ('#' + b.id) : ('button, .btn'),
      rect: rect(b)
    });
  }
  return JSON.stringify({
    ok: true,
    href: location.href || '',
    title: document.title || '',
    fields: fields,
    buttons: buttons,
    fieldCount: fields.length,
    buttonCount: buttons.length
  });
})();
"""


def _parse_json(res, default=None):
    if default is None:
        default = {}
    if res is None:
        return default
    if isinstance(res, str):
        try:
            return json.loads(res)
        except (TypeError, ValueError):
            return default
    return res if isinstance(res, dict) else default


def _parent_contexts():
    """业务 iframe 优先，再 top。"""
    tab = browser_session.get_tab()
    contexts = []
    try:
        fr = browser_session.get_active_frame_ro(tab, timeout=0.5)
        if fr is None:
            fr = browser_session.get_active_frame(tab)
        if fr is not None:
            contexts.append(("iframe", fr))
    except Exception:
        pass
    if tab is not None:
        contexts.append(("top", tab))
    return tab, contexts


def _visible_layer_elements(parent):
    """返回 parent 文档内可见的 layer 根节点（跳过 shade）。"""
    layers = parent.eles("c:" + legacy.LAYER_ROOT, timeout=0.5) or []
    visible = []
    for layer in layers:
        try:
            states = getattr(layer, "states", None)
            if not bool(getattr(states, "is_displayed", False)):
                continue
            cls = str((layer.attrs or {}).get("class", ""))
            if "layui-layer-shade" in cls:
                continue
            visible.append(layer)
        except Exception:
            continue
    return visible


def _layer_meta_from_element(layer) -> dict:
    title = ""
    try:
        title_el = layer.ele("c:" + legacy.LAYER_TITLE, timeout=0.2)
        title = (title_el.text or "").strip() if title_el else ""
    except Exception:
        title = ""
    cls = str((layer.attrs or {}).get("class", ""))
    layer_kind = "page"
    if "layui-layer-iframe" in cls:
        layer_kind = "iframe"
    elif "layui-layer-msg" in cls:
        layer_kind = "msg"
    elif "layui-layer-dialog" in cls:
        layer_kind = "dialog"
    nested = []
    try:
        for iframe in layer.eles("t:iframe", timeout=0.3) or []:
            nested.append({
                "src": iframe.attr("src") or "",
                "id": iframe.attr("id") or "",
                "name": iframe.attr("name") or "",
            })
    except Exception:
        pass
    has_close = False
    try:
        has_close = bool(layer.ele("c:" + legacy.LAYER_CLOSE, timeout=0.1))
    except Exception:
        pass
    return {
        "title": title,
        "layerKind": layer_kind,
        "nestedIframes": nested,
        "hasClose": has_close,
        "className": cls,
    }


def get_layer_content_frame(parent=None, layer_index: int = -1, timeout: float = 3.0):
    """获取可见 layer 的内容文档（iframe 型则进入子 frame，否则为 layer 自身所在文档）。

    Returns:
        {
          ok, scope, layer_index, meta,
          content_frame,  # ChromiumFrame | parent（非 iframe 型时）
          content_kind: 'nested_iframe' | 'same_document'
        }
    """
    timeout = max(float(timeout or 0), 0.0)
    tab, contexts = _parent_contexts()
    if parent is not None:
        contexts = [("custom", parent)] + [
            item for item in contexts if item[1] is not parent
        ]

    errors = []
    for scope, ctx in contexts:
        try:
            # 事件驱动等待 layer 壳出现（iframe 型优先，兼容 dialog/msg）。
            try:
                ctx.wait.eles_loaded(
                    "css:.layui-layer.layui-layer-iframe, "
                    "css:.layui-layer:not(.layui-layer-shade)",
                    timeout=min(timeout, 3.0) if timeout else 0.5,
                    raise_err=False,
                )
            except Exception:
                pass
            layers = _visible_layer_elements(ctx)
            if not layers:
                continue
            idx = layer_index if layer_index >= 0 else len(layers) - 1
            if idx >= len(layers):
                errors.append("%s: layer_index out of range" % scope)
                continue
            layer = layers[idx]
            meta = _layer_meta_from_element(layer)
            nested = meta.get("nestedIframes") or []
            if nested:
                # 优先 id / name，再 CSS
                locators = []
                nid = nested[0].get("id") or ""
                nname = nested[0].get("name") or ""
                if nid:
                    locators.append(nid)
                    locators.append("css:#%s" % nid)
                if nname:
                    locators.append(nname)
                locators.extend([
                    "css:.layui-layer-content iframe",
                    "css:iframe[id*='layui-layer-iframe']",
                    "css:.layui-layer iframe",
                ])
                nested_frame = None
                last_err = ""
                for loc in locators:
                    try:
                        nested_frame = ctx.get_frame(loc, timeout=min(timeout, 2.0))
                        if nested_frame is not None:
                            break
                    except Exception as exc:
                        last_err = str(exc)
                        nested_frame = None
                if nested_frame is None:
                    # 元素句柄再试
                    try:
                        iframe_el = layer.ele("t:iframe", timeout=0.5)
                        if iframe_el is not None:
                            nested_frame = ctx.get_frame(iframe_el, timeout=min(timeout, 2.0))
                    except Exception as exc:
                        last_err = str(exc)
                if nested_frame is None:
                    errors.append("%s: nested iframe not resolved (%s)" % (scope, last_err))
                    # 仍返回 meta，content 不可用
                    return {
                        "ok": False,
                        "reason": "layer iframe content not accessible",
                        "scope": scope,
                        "layer_index": idx,
                        "meta": meta,
                        "errors": errors,
                    }
                return {
                    "ok": True,
                    "scope": scope,
                    "layer_index": idx,
                    "meta": meta,
                    "content_frame": nested_frame,
                    "content_kind": "nested_iframe",
                    "content_url": getattr(nested_frame, "url", "") or nested[0].get("src") or "",
                }
            # 同文档 layer（dialog / page 内容直接在 content 节点）
            return {
                "ok": True,
                "scope": scope,
                "layer_index": idx,
                "meta": meta,
                "content_frame": ctx,
                "content_kind": "same_document",
                "content_url": getattr(ctx, "url", "") or "",
                "content_root_selector": legacy.LAYER_CONTENT,
            }
        except Exception as exc:
            errors.append("%s: %s" % (scope, exc))
    return {
        "ok": False,
        "reason": "未找到可见 layer 弹层",
        "errors": errors,
    }


def scan_layer_content(layer_index: int = -1, timeout: float = 3.0) -> dict:
    """扫描可见 layer 内容区的表单字段与按钮（自动进入嵌套 iframe）。"""
    resolved = get_layer_content_frame(layer_index=layer_index, timeout=timeout)
    if not resolved.get("ok"):
        return resolved
    content = resolved["content_frame"]
    meta = resolved.get("meta") or {}
    try:
        if resolved.get("content_kind") == "same_document":
            # 限制在 layer content 节点内扫
            js = (
                "var root=document.querySelector(%s)||document.body;"
                "return (function(document){ %s }).call(root);"
            )
            # 简化：直接整页扫（同文档 layer 时字段也在 content 内）
            data = _parse_json(content.run_js(_LAYER_FORM_JS), {})
        else:
            data = _parse_json(content.run_js(_LAYER_FORM_JS), {})
        if not data.get("ok"):
            return {
                "ok": False,
                "reason": data.get("reason") or "layer content scan failed",
                "meta": meta,
                "resolved": {k: v for k, v in resolved.items() if k != "content_frame"},
            }
        return {
            "ok": True,
            "type": "layer",
            "title": meta.get("title") or data.get("title") or "",
            "layerKind": meta.get("layerKind"),
            "nestedIframes": meta.get("nestedIframes") or [],
            "hasClose": meta.get("hasClose"),
            "content_kind": resolved.get("content_kind"),
            "content_url": resolved.get("content_url") or data.get("href") or "",
            "scope": resolved.get("scope"),
            "layer_index": resolved.get("layer_index"),
            "fields": data.get("fields") or [],
            "buttons": data.get("buttons") or [],
            "fieldCount": data.get("fieldCount", 0),
            "buttonCount": data.get("buttonCount", 0),
        }
    except Exception as exc:
        logger.debug("scan_layer_content failed: %s", exc)
        return {
            "ok": False,
            "reason": str(exc),
            "meta": meta,
        }


def enrich_layer_overlay(item: dict, layer_index: int = -1) -> dict:
    """把 scan_floats 中的 layer 项补全子 frame 字段/按钮。"""
    if not isinstance(item, dict):
        return item
    if item.get("type") not in ("layer", "layer-msg"):
        return item
    # 已有字段则跳过
    if item.get("fields") and item.get("content_enriched"):
        return item
    scanned = scan_layer_content(layer_index=layer_index)
    if not scanned.get("ok"):
        item["content_enrich_error"] = scanned.get("reason")
        return item
    item["fields"] = scanned.get("fields") or item.get("fields") or []
    # 合并按钮：壳层关闭 + 内容区保存/取消
    shell_buttons = item.get("buttons") or []
    content_buttons = scanned.get("buttons") or []
    seen = {str(b.get("text") or "") for b in shell_buttons}
    merged = list(shell_buttons)
    for btn in content_buttons:
        text = str(btn.get("text") or "")
        if text and text not in seen:
            btn = dict(btn)
            btn["source"] = "layer_content"
            merged.append(btn)
            seen.add(text)
    item["buttons"] = merged
    item["content_url"] = scanned.get("content_url")
    item["content_kind"] = scanned.get("content_kind")
    item["content_enriched"] = True
    item["fieldCount"] = len(item["fields"])
    if not item.get("title"):
        item["title"] = scanned.get("title") or ""
    layer_meta = item.get("layer") if isinstance(item.get("layer"), dict) else {}
    layer_meta = dict(layer_meta)
    layer_meta.setdefault("layerKind", scanned.get("layerKind"))
    layer_meta.setdefault("nestedIframes", scanned.get("nestedIframes") or [])
    item["layer"] = layer_meta
    return item


def list_visible_layers() -> dict:
    """列出 top/iframe 中可见 layer 摘要。"""
    _, contexts = _parent_contexts()
    items = []
    for scope, ctx in contexts:
        for index, layer in enumerate(_visible_layer_elements(ctx)):
            meta = _layer_meta_from_element(layer)
            items.append({
                "scope": scope,
                "index": index,
                "title": meta.get("title"),
                "layerKind": meta.get("layerKind"),
                "nestedIframes": meta.get("nestedIframes"),
                "hasClose": meta.get("hasClose"),
            })
    return {"ok": True, "count": len(items), "layers": items}


def _xpath_literal(value: str) -> str:
    text = str(value or "")
    if "'" not in text:
        return "'%s'" % text
    if '"' not in text:
        return '"%s"' % text
    return "concat(%s)" % ", \"'\", ".join("'%s'" % part for part in text.split("'"))


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).replace("＊", "").replace("*", "")


def set_layer_field_value(
    field_name: str,
    value: str,
    clear: bool = True,
    select_index: int = 0,
    timeout: float = 5.0,
    layer_index: int = -1,
) -> dict:
    """按中文标签/name 在可见 layer 内容 frame 内写入字段。

    优先尝试 bootstrap-select / 原生 select（与遗留「编辑弹窗」交互一致），
    未命中再写 input/textarea。
    """
    field_name = str(field_name or "").strip()
    if not field_name:
        return {"ok": False, "reason": "field_name is required"}
    text_value = "" if value is None else str(value)
    select_index = max(int(select_index or 0), 0)
    timeout = max(float(timeout or 0), 0.0)

    resolved = get_layer_content_frame(layer_index=layer_index, timeout=min(timeout, 3.0))
    if not resolved.get("ok"):
        return resolved

    # 下拉优先：遗留账号/角色弹窗多为 label + bootstrap-select 按钮。
    if text_value:
        try:
            from . import bootstrap_select

            select_budget = min(max(timeout * 0.45, 0.8), 2.5)
            selected = bootstrap_select.select_bootstrap_option(
                field_name=field_name,
                option_text=text_value,
                select_index=select_index,
                prefer_layer=True,
                timeout=select_budget,
            )
            if selected.get("ok"):
                selected = dict(selected)
                selected.setdefault("action", "set_field_value")
                selected["field_name"] = field_name
                selected["value"] = text_value
                selected["via"] = selected.get("adapter") or "bootstrap-select"
                selected["content_kind"] = resolved.get("content_kind")
                selected["content_url"] = resolved.get("content_url")
                selected["title"] = (resolved.get("meta") or {}).get("title")
                return selected
        except Exception as exc:
            logger.debug("layer bootstrap-select path skipped: %s", exc)

    content = resolved["content_frame"]
    lit = _xpath_literal(field_name)
    compact = _compact(field_name)

    # 候选：control-label 邻接 col 内的 input/textarea
    locators = [
        "xpath://label[contains(@class,'control-label')]"
        "[contains(translate(normalize-space(.),'＊*',''), %s)]"
        "/following-sibling::div[1]"
        "//*[self::input or self::textarea]"
        "[not(@type='hidden') and not(@type='submit') and not(@type='button') "
        "and not(@type='checkbox') and not(@type='radio') and not(@type='file')]"
        % lit,
        "xpath://label[contains(translate(normalize-space(.),'＊*',''), %s)]"
        "/following-sibling::*[1]//*[self::input or self::textarea]"
        "[not(@type='hidden') and not(@type='submit') and not(@type='button')]"
        % lit,
        "xpath://*[@name=%s or @id=%s or contains(@placeholder,%s)]"
        "[self::input or self::textarea]"
        % (lit, lit, lit),
        "xpath://div[contains(@class,'form-group') or contains(@class,'form-group-sm')]"
        "[.//label[contains(translate(normalize-space(.),'＊*',''), %s)]]"
        "//*[self::input or self::textarea]"
        "[not(@type='hidden') and not(@type='submit') and not(@type='button') "
        "and not(@type='checkbox') and not(@type='radio') and not(@type='file')]"
        % lit,
    ]

    controls = []
    for loc in locators:
        try:
            found = content.eles(loc, timeout=0.6) or []
            for el in found:
                try:
                    states = getattr(el, "states", None)
                    if not bool(getattr(states, "is_displayed", True)):
                        continue
                except Exception:
                    pass
                controls.append(el)
            if controls:
                break
        except Exception:
            continue

    if not controls:
        # 用扫描结果按 label 精确/包含匹配 name/id
        scanned = scan_layer_content(layer_index=layer_index, timeout=timeout)
        if scanned.get("ok"):
            matches = []
            for field in scanned.get("fields") or []:
                if field.get("type") in ("select", "file", "checkbox", "radio"):
                    continue
                label = str(field.get("label") or "")
                if _compact(label) == compact or compact in _compact(label) or _compact(label) in compact:
                    matches.append(field)
            if matches:
                field = matches[min(select_index, len(matches) - 1)]
                hint = field.get("selectorHint") or ""
                name = field.get("name") or ""
                fid = field.get("id") or ""
                candidates = []
                if fid:
                    candidates.append("css:#%s" % fid)
                if name:
                    name_lit = _xpath_literal(name)
                    candidates.append(
                        "xpath://*[@name=%s][self::input or self::textarea]" % name_lit
                    )
                if hint and (hint.startswith("#") or "[" in hint or hint.startswith("input")):
                    candidates.append("css:%s" % hint)
                for cand in candidates:
                    try:
                        el = content.ele(cand, timeout=0.5)
                        if el is not None:
                            controls = [el]
                            break
                    except Exception:
                        continue

    if not controls:
        return {
            "ok": False,
            "reason": "layer field not found: %s" % field_name,
            "scope": "layer",
            "title": (resolved.get("meta") or {}).get("title"),
        }

    control = controls[min(select_index, len(controls) - 1)]
    try:
        states = getattr(control, "states", None)
        if not bool(getattr(states, "is_enabled", True)):
            return {"ok": False, "reason": "field is disabled: %s" % field_name}
        if control.attr("readonly") not in (None, False, "", "false"):
            return {"ok": False, "reason": "field is read-only: %s" % field_name}
    except Exception:
        pass

    try:
        try:
            control.input(text_value, clear=clear, by_js=False)
        except TypeError:
            if clear:
                try:
                    control.clear()
                except Exception:
                    pass
            control.input(text_value)
        actual = None
        try:
            actual = control.property("value")
        except Exception:
            try:
                actual = control.attr("value")
            except Exception:
                actual = None
        return {
            "ok": True,
            "action": "set_field_value",
            "field_name": field_name,
            "value": text_value,
            "actual_value": actual,
            "matches_requested": None if actual is None else str(actual) == text_value,
            "scope": "layer",
            "area": "layer",
            "content_kind": resolved.get("content_kind"),
            "content_url": resolved.get("content_url"),
            "title": (resolved.get("meta") or {}).get("title"),
            "select_index": select_index,
        }
    except Exception as exc:
        return {
            "ok": False,
            "reason": "layer field input failed: %s" % exc,
            "field_name": field_name,
            "scope": "layer",
        }
