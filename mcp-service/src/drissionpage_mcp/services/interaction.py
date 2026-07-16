"""DOM interaction, navigation, field editing, and explore_action.

Extracted from server.py for shared use by MCP components and recipe dispatch.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from datetime import datetime
from typing import Literal

from DrissionPage.common import Keys

from ..core import page_family, recipe_context, tool_metadata, ui_contract
from ..resources import resource_store
from . import browser_session, filter_area, page_model, observe, table_facade, vtable
from . import layer_modal  # noqa: F401  may be used via set_field_value paths
from ..workflows import flow_evidence

logger = logging.getLogger("drissionpage-mcp")

_KEY_ALIASES = {
    "alt": Keys.ALT,
    "backspace": Keys.BACKSPACE,
    "control": Keys.CONTROL,
    "ctrl": Keys.CTRL,
    "del": Keys.DELETE,
    "delete": Keys.DELETE,
    "down": Keys.DOWN,
    "arrowdown": Keys.DOWN,
    "end": Keys.END,
    "enter": Keys.ENTER,
    "return": Keys.RETURN,
    "esc": Keys.ESCAPE,
    "escape": Keys.ESCAPE,
    "home": Keys.HOME,
    "left": Keys.LEFT,
    "arrowleft": Keys.LEFT,
    "meta": Keys.META,
    "command": Keys.COMMAND,
    "pagedown": Keys.PAGE_DOWN,
    "pageup": Keys.PAGE_UP,
    "right": Keys.RIGHT,
    "arrowright": Keys.RIGHT,
    "shift": Keys.SHIFT,
    "space": Keys.SPACE,
    "tab": Keys.TAB,
    "up": Keys.UP,
    "arrowup": Keys.UP,
}


def _official_key(value: str):
    """把 MCP 友好键名映射为 DrissionPage 4.2 官方 ``Keys`` 常量。"""
    text = str(value or "")
    if len(text) == 1:
        return text
    normalized = re.sub(r"[\s_-]+", "", text).lower()
    if normalized in _KEY_ALIASES:
        return _KEY_ALIASES[normalized]
    if re.fullmatch(r"f(?:[1-9]|1[0-2])", normalized):
        return getattr(Keys, normalized.upper())
    raise ValueError("unsupported key: %s" % value)


def _press_key_raw(target, key: str, modifiers: list = None, interval: float = 0.01) -> dict:
    """在动作链发送按键，并在主键释放失败时仍反向释放修饰键。"""
    modifiers = list(modifiers or [])
    if len(key) == 1 and not modifiers:
        target.actions.type(key, interval=interval)
        return {"ok": True, "key": key, "modifiers": []}

    main_key = _official_key(key)
    modifier_keys = [_official_key(item) for item in modifiers]
    pressed = []
    main_pressed = False
    release_error = None
    try:
        for modifier in modifier_keys:
            target.actions.key_down(modifier)
            pressed.append(modifier)
        target.actions.key_down(main_key)
        main_pressed = True
    finally:
        if main_pressed:
            try:
                target.actions.key_up(main_key)
            except Exception as exc:
                release_error = exc
        for modifier in reversed(pressed):
            try:
                target.actions.key_up(modifier)
            except Exception:
                logger.debug("释放修饰键失败", exc_info=True)
    if release_error is not None:
        raise release_error
    return {"ok": True, "key": key, "modifiers": modifiers}





def enter_module(menu_text: str, timeout: float = 8, expand_filter: bool = True) -> dict:
    """点击左侧菜单进入模块（按菜单文字匹配），并等待业务 iframe 导航完成。

    优先用 DrissionPage 原生 click 模拟鼠标点击；
    当元素不可见（无位置/大小）时自动降级为 JS click。
    """
    tab = browser_session.get_tab()
    old_fr = browser_session.get_active_frame(tab)
    old_url = old_fr.url if old_fr else None

    # 1. 点击菜单项
    ele = tab.ele(f'text:{menu_text}', timeout=3)
    if not ele:
        # 降级：Python 查找匹配菜单项并点击
        menu_items = browser_session.eles_with_fallback(
            tab,
            'css:.ant-menu-item, li[class*="ant-menu"]',
            'xpath://*[contains(@class, "ant-menu-item") or (local-name()="li" and contains(@class, "ant-menu"))]'
        )
        for item in menu_items:
            if menu_text in (item.text or ""):
                ele = item
                break
        if not ele:
            return {"ok": False, "reason": "menu not found"}

    try:
        ele.wait.clickable(timeout=3, wait_stop=True, raise_err=False)
        ele.click(by_js=False, wait_stop=True)
    except Exception:
        if recipe_context.requires_native_actions():
            return {"ok": False, "reason": "formal execution menu click failed without JS fallback"}
        # 浏览器探索可使用 DrissionPage 的 by_js 回退；正式回放禁止该路径。
        try:
            ele.click(by_js=True)
        except Exception as e:
            return {"ok": False, "reason": f"click menu failed: {str(e)}"}

    # 2. 等待 iframe 就绪（智能等待：iframe 元素在 DOM 中可见即视为就绪；超时不抛错，由下方 get_active_frame 兜底判定）
    wait_seconds = int(timeout)
    try:
        if old_url is None:
            tab.wait.ele_displayed(ui_contract.ACTIVE_FRAME, timeout=wait_seconds)
        else:
            new_fr = browser_session.get_active_frame(tab)
            if new_fr:
                new_fr.wait.url_change(old_url, exclude=True, timeout=wait_seconds)
            else:
                tab.wait.ele_displayed(ui_contract.ACTIVE_FRAME, timeout=wait_seconds)
    except Exception:
        pass

    if browser_session.get_active_frame(tab) is None:
        resource_context = resource_store.set_module(menu_text)
        return {"ok": False, "entered": menu_text, "iframe_ready": False,
                "resource_context": resource_context,
                "reason": "iframe 未在 %.0fs 内出现" % timeout}

    expand_result = {}
    if expand_filter:
        expand_result = filter_area.expand_filter_area(tab)
        logger.info("expand_filter_area: %s", expand_result.get("reason", ""))
    resource_context = resource_store.set_module(menu_text)
    # Module navigation invalidates any previously mounted VTable instance.
    try:
        vtable.invalidate_vtable(reason="enter_module:%s" % menu_text)
    except Exception:
        logger.debug("invalidate_vtable after enter_module failed", exc_info=True)
    return {"ok": True, "entered": menu_text, "iframe_ready": True,
            "expand_filter": expand_result, "resource_context": resource_context,
            "vtable_invalidated": True}


def reset_to_initial(module_text: str, timeout: float = 20) -> dict:
    """重置到初始状态：关闭当前业务 tab → 重进模块 → 等 iframe+VTable 就绪。用例间隔离用。"""
    tab = browser_session.get_tab()
    active_frame = browser_session.get_active_frame(tab)
    active_name = browser_session.get_active_tab_name()
    if active_frame is not None and str(active_name or "").strip() == str(module_text or "").strip():
        try:
            active_frame.refresh()
            try:
                active_frame.wait.doc_loaded(timeout=timeout)
            except Exception:
                pass
            try:
                vtable.invalidate_vtable(reason="reset_to_initial:iframe_refresh")
            except Exception:
                logger.debug("invalidate_vtable after iframe refresh failed", exc_info=True)
            expand_result = filter_area.expand_filter_area(tab)
            return {
                "ok": True, "entered": module_text, "iframe_ready": True,
                "reset_mode": "iframe_refresh", "expand_filter": expand_result,
                "resource_context": resource_store.set_module(module_text),
                "vtable_invalidated": True,
            }
        except Exception as exc:
            logger.debug("iframe refresh reset failed, falling back to tab reopen: %s", exc)
    close_btn = browser_session.ele_with_fallback(
        tab,
        'css:.ant-tabs-tab-active.outSide .anticon-close',
        'xpath://*[contains(@class, "ant-tabs-tab-active") and contains(@class, "outSide")]//*[contains(@class, "anticon-close")]',
        timeout=1.0
    )
    if close_btn:
        try:
            close_btn.wait.clickable(timeout=3, wait_stop=True, raise_err=False)
            close_btn.click(wait_stop=True)
        except Exception:
            if not recipe_context.requires_native_actions():
                try:
                    close_btn.click(by_js=True)
                except Exception:
                    pass
            else:
                logger.debug("native tab-close click failed during formal execution")
    # 智能等待：业务 iframe 从 DOM 消失即说明 tab 已关闭（最多 10s）；超时不阻断，交给后续 enter_module
    try:
        tab.wait.ele_deleted(ui_contract.ACTIVE_FRAME, timeout=10)
    except Exception:
        pass
    return enter_module(module_text, timeout=timeout)

def _short_click_timeout(timeout: float, default: float = 2.0, upper: float = 2.0) -> float:
    """Keep native click probes responsive before fallback paths run."""
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(value, upper))


def _extract_text_locator(locator: str) -> str | None:
    for prefix in ("text:", "text=", "tx:", "tx="):
        if isinstance(locator, str) and locator.startswith(prefix):
            return locator[len(prefix):]
    return None


def _xpath_literal(value: str) -> str:
    text = str(value)
    if "'" not in text:
        return "'%s'" % text
    if '"' not in text:
        return '"%s"' % text
    parts = text.split("'")
    return "concat(%s)" % ', "\'", '.join("'%s'" % part for part in parts)


def _clickable_text_locators(raw_text: str) -> list[str]:
    text = str(raw_text).strip()
    if not text:
        return []
    literal = _xpath_literal(text)
    clickable = (
        "self::button or self::a or @role='button' or @role='tab' or @role='menuitem' "
        "or contains(concat(' ', normalize-space(@class), ' '), ' ant-btn ') "
        "or contains(concat(' ', normalize-space(@class), ' '), ' ant-tabs-tab ') "
        "or contains(concat(' ', normalize-space(@class), ' '), ' ant-dropdown-menu-item ') "
        "or contains(concat(' ', normalize-space(@class), ' '), ' ant-pagination-item ')"
    )
    return [
        "x://*[%s][normalize-space(.)=%s]" % (clickable, literal),
        "x://*[%s][contains(normalize-space(.), %s)]" % (clickable, literal),
    ]


def _click_text_by_js(locator: str, in_frame: bool = True) -> dict | None:
    raw_text = _extract_text_locator(locator)
    if not raw_text:
        return None

    target = (browser_session.get_active_frame() if in_frame else None) or browser_session.get_tab()
    needle = json.dumps("".join(str(raw_text).split()), ensure_ascii=False)
    js = f"""
        var needle = {needle};
        var preferredSelector = [
          'button', 'a', '[role="button"]', '[role="tab"]', '[role="menuitem"]',
          'input[type="button"]', 'input[type="submit"]',
          '.ant-btn', '.ant-tabs-tab', '.ant-dropdown-menu-item', '.ant-pagination-item'
        ].join(',');
        var allSelector = preferredSelector + ',span,div';
        function norm(v) {{ return (v || '').trim().replace(/\\s+/g, ''); }}
        function visible(el) {{
          var style = window.getComputedStyle(el);
          var rect = el.getBoundingClientRect();
          return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
        }}
        function disabled(el) {{
          return el.disabled || el.getAttribute('aria-disabled') === 'true' || el.classList.contains('disabled');
        }}
        function clickTarget(el) {{
          return el.closest(preferredSelector) || el;
        }}
        function probe(selector) {{
          var els = Array.from(document.querySelectorAll(selector));
          for (var i = 0; i < els.length; i++) {{
            var el = els[i];
            if (!visible(el) || norm(el.innerText || el.textContent) !== needle) continue;
            var target = clickTarget(el);
            if (!visible(target) || disabled(target)) continue;
            target.click();
            return JSON.stringify({{
              ok: true,
              tag: target.tagName,
              className: target.className || '',
              text: (target.innerText || target.textContent || '').trim().slice(0, 80)
            }});
          }}
          return null;
        }}
        return probe(preferredSelector) || probe(allSelector) || JSON.stringify({{ok:false}});
    """
    res = target.run_js(js)
    if isinstance(res, str):
        try:
            res = json.loads(res)
        except json.JSONDecodeError:
            return {"ok": False, "reason": "JS 文本点击返回非 JSON: %s" % res}
    return res or {"ok": False}


def _compact_text(text: str) -> str:
    return "".join(str(text or "").split()).lower()



def _normalize_target_dict(target):
    if target is None:
        return None
    if isinstance(target, str):
        return {"type": "locator", "locator": target}
    if isinstance(target, dict):
        return dict(target)
    return {"type": "locator", "locator": str(target)}


def _target_get(target: dict, *names, default=None):
    for name in names:
        if name in target and target[name] is not None:
            return target[name]
    return default


def _as_locator(prefix: str, value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    for known in ("css:", "c:", "xpath:", "x:", "text:", "tx:", "tag:", "t:", "ax:", "#", "."):
        if value.startswith(known):
            return value
    return f"{prefix}:{value}"


def _resolve_visible_action_target(target: dict, in_frame: bool) -> dict:
    text = _target_get(target, "text", "name", "label", "title")
    if not text:
        return {"ok": False, "reason": "action/button target requires text/name/label"}
    scope = str(_target_get(target, "scope", default="auto") or "auto").lower()
    max_items = int(_target_get(target, "max_items", default=160) or 160)

    def _matching_button(overlays: list[dict], area: str):
        needle = _compact_text(text)
        for exact in (True, False):
            for overlay in reversed(overlays or []):
                # dropdown/select 的可选项不是 button；页面模型把它们放在 options，
                # 语义点击统一消费两类元素，避免退化成不稳定坐标点击。
                candidates = list(overlay.get("buttons") or []) + list(overlay.get("options") or [])
                for item in reversed(candidates):
                    hay = _compact_text(item.get("text") or item.get("title") or "")
                    matched = hay == needle if exact else bool(needle and needle in hay)
                    if not matched or item.get("disabled"):
                        continue
                    semantic_xpath = item.get("semanticXPath")
                    structural_xpath = item.get("xpath")
                    selector_hint = item.get("selectorHint")
                    locator = (
                        _as_locator("xpath", semantic_xpath)
                        if semantic_xpath else
                        _as_locator("css", selector_hint)
                        if selector_hint else
                        _as_locator("xpath", structural_xpath)
                        if structural_xpath else ""
                    )
                    overlay_type = overlay.get("type") or ""
                    resolved_area = (
                        "modal" if overlay_type in {"modal", "confirm", "system_confirm", "interactive"}
                        else overlay_type or area
                    )
                    result = {
                        "ok": True,
                        "action": "click",
                        "locator": locator,
                        "in_frame": overlay.get("scope") == "iframe",
                        "meta": {
                            "target_type": "action",
                            "text": item.get("text") or text,
                            "area": resolved_area,
                            "overlay_type": overlay_type,
                            "overlay_title": overlay.get("title") or "",
                            "scope": overlay.get("scope") or "",
                            "matched": item,
                        },
                    }
                    rect = item.get("rect") or {}
                    result["x"] = item.get("cx", item.get("viewportX", rect.get("cx")))
                    result["y"] = item.get("cy", item.get("viewportY", rect.get("cy")))
                    if result["x"] is None and {"x", "width"} <= set(rect):
                        result["x"] = float(rect["x"]) + float(rect["width"]) / 2
                    if result["y"] is None and {"y", "height"} <= set(rect):
                        result["y"] = float(rect["y"]) + float(rect["height"]) / 2
                    if not locator and result["x"] is not None and result["y"] is not None:
                        result["action"] = "click_xy"
                    return result
        return None

    overlay_types = {
        "modal": {"modal", "confirm", "system_confirm", "interactive"},
        "drawer": {"drawer"},
        "dropdown": {"dropdown", "select-dropdown", "vtable-filter-menu", "vtable-menu"},
        "select-dropdown": {"select-dropdown"},
        "calendar": {"calendar"},
        "popover": {"popover"},
        "tooltip": {"tooltip", "vtable-tooltip"},
        "notification": {"notification"},
        "message": {"message"},
        "vtable-filter-menu": {"vtable-filter-menu"},
        "vtable-menu": {"vtable-menu"},
    }
    all_overlay_types = set().union(*overlay_types.values())
    overlay_scopes = set(overlay_types) | {"auto", "all", "overlay"}
    if scope in overlay_scopes:
        snapshot = observe.observe_snapshot(
            only_visible=True, include_table_data=False, detail="summary",
        )
        visible_overlays = snapshot.get("overlays") or []
        accepted_types = (
            all_overlay_types if scope in {"auto", "all", "overlay"}
            else overlay_types[scope]
        )
        typed = [item for item in visible_overlays if item.get("type") in accepted_types]
        resolved = _matching_button(typed, "overlay" if scope in {"auto", "all", "overlay"} else scope)
        if resolved:
            return resolved
        # 显式限定浮层时绝不退回页面同名按钮；这是提交/删除类误点击的安全边界。
        if scope not in {"auto", "all"}:
            return {"ok": False, "reason": "visible %s action not found: %s" % (scope, text)}

    toolbar_scope = "toolbar" if scope == "auto" else scope
    data = page_model.scan_toolbar_actions(
        scope=toolbar_scope, in_frame=in_frame, max_items=max_items,
    )
    if not data.get("ok"):
        return {"ok": False, "reason": data.get("reason", "scan toolbar actions failed")}

    needle = _compact_text(text)
    candidates = []
    for item in data.get("actions", []) or []:
        hay = _compact_text(item.get("text") or item.get("title") or "")
        if hay == needle:
            candidates.insert(0, item)
        elif needle and needle in hay:
            candidates.append(item)
    if not candidates:
        return {"ok": False, "reason": "visible action not found: %s" % text}

    item = candidates[0]
    semantic_xpath = item.get("semanticXPath")
    structural_xpath = item.get("xpath")
    selector_hint = item.get("selectorHint")
    locator = (
        _as_locator("xpath", semantic_xpath) if semantic_xpath else
        _as_locator("css", selector_hint) if selector_hint else
        _as_locator("xpath", structural_xpath) if structural_xpath else ""
    )
    if locator:
        return {
            "ok": True,
            "action": "click",
            "locator": locator,
            "in_frame": in_frame,
            "meta": {
                "target_type": "action",
                "text": item.get("text") or text,
                "area": item.get("area") or toolbar_scope,
                "matched": item,
            },
        }
    cx = item.get("cx") or item.get("viewportX")
    cy = item.get("cy") or item.get("viewportY")
    if cx is None or cy is None:
        rect = item.get("rect") or {}
        if {"x", "y", "width", "height"} <= set(rect):
            cx = float(rect["x"]) + float(rect["width"]) / 2
            cy = float(rect["y"]) + float(rect["height"]) / 2
    if cx is None or cy is None:
        return {"ok": False, "reason": "visible action has no usable coordinates: %s" % text}
    return {
        "ok": True,
        "action": "click_xy",
        "x": float(cx),
        "y": float(cy),
        "locator": "",
        "in_frame": in_frame,
        "meta": {
            "target_type": "action",
            "text": item.get("text") or text,
            "area": item.get("area") or toolbar_scope,
            "matched": item,
        },
    }


def _element_is_visible(element) -> bool:
    states = getattr(element, "states", None)
    return bool(getattr(states, "is_displayed", True))


def _field_container_label(container) -> str:
    try:
        label = container.ele(
            "css:.ant-form-item-label label,.ant-form-item-label,label,[class*='label']",
            timeout=0.2,
        )
        return str(getattr(label, "text", "") or "").strip().rstrip("：:")
    except Exception:
        return ""


def _semantic_field_candidates(target, field_name: str, area: str, timeout: float) -> list:
    label_lit = _xpath_literal(field_name)
    label_predicate = (
        ".//*[self::label or contains(@class, 'label')]"
        "[contains(normalize-space(.), %s)]" % label_lit
    )
    form_item = (
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' ant-form-item ') or "
        "contains(concat(' ', normalize-space(@class), ' '), ' el-form-item ') or "
        "contains(concat(' ', normalize-space(@class), ' '), ' arco-form-item ') or "
        "contains(concat(' ', normalize-space(@class), ' '), ' ivu-form-item ') or "
        "contains(concat(' ', normalize-space(@class), ' '), ' semi-form-field ') or "
        "contains(concat(' ', normalize-space(@class), ' '), ' form-group ') or "
        "contains(@class, 'MuiFormControl-root')][%s]" % label_predicate
    )
    if area == "modal":
        locator = (
            "xpath://div[contains(@class, 'ant-modal')][%s]//div[contains(@class, 'ant-form-item')]"
            "[%s]" % (label_predicate, label_predicate)
        )
    elif area == "drawer":
        locator = (
            "xpath://div[contains(@class, 'ant-drawer')][%s]//div[contains(@class, 'ant-form-item')]"
            "[%s]" % (label_predicate, label_predicate)
        )
    elif area == "filter":
        locator = (
            "xpath://div[contains(@class, 'page-query') or contains(@class, 'quick-filter')]"
            "//*[contains(@class, 'ant-form-item') or contains(@class, 'ant-col')][%s]"
            % label_predicate
        )
    else:
        locator = "xpath:" + form_item
    try:
        return [item for item in target.eles(locator, timeout=min(timeout, 2.0)) if _element_is_visible(item)]
    except Exception:
        return []


def _generic_field_controls(target, field_name: str, area: str, timeout: float) -> list:
    """Resolve native/ARIA fields when no component-specific form item matched."""
    literal = _xpath_literal(field_name)
    control = (
        "self::input[not(@type='hidden')] or self::textarea or "
        "@contenteditable='true' or @role='textbox' or @role='combobox'"
    )
    roots = {
        "modal": "//*[@role='dialog' or contains(@class,'modal') or contains(@class,'dialog')]",
        "drawer": "//*[contains(@class,'drawer')]",
        "filter": "//*[contains(@class,'filter') or contains(@class,'query') or contains(@class,'search')]",
    }
    root = roots.get(area, "")
    prefix = root + "//" if root else "//"
    label_match = "normalize-space(.)=%s" % literal
    expressions = [
        "%s*[(%s) and (@aria-label=%s or @placeholder=%s or @name=%s)]"
        % (prefix, control, literal, literal, literal),
        "%s*[(%s) and @id=//label[%s]/@for]"
        % (prefix, control, label_match),
        "%slabel[%s]//*[%s]" % (prefix, label_match, control),
    ]
    try:
        found = target.eles(
            "xpath:" + " | ".join(expressions), timeout=min(timeout, 1.0)
        ) or []
    except Exception:
        return []
    visible = []
    seen = set()
    for item in found:
        identity = id(item)
        if identity in seen or not _element_is_visible(item):
            continue
        seen.add(identity)
        visible.append(item)
    return visible


def _native_element_input(control, value: str, clear: bool, timeout: float) -> None:
    """Use DrissionPage element input; the fallback only supports lightweight test doubles."""
    waiter = getattr(control, "wait", None)
    if waiter is not None:
        waiter.clickable(timeout=timeout, wait_stop=True, raise_err=False)
    try:
        control.input(value, clear=clear, by_js=False)
    except TypeError:
        if clear:
            control.clear()
        control.input(value)


def set_field_value(field_name: str, value: str, in_frame: bool = True,
                    clear: bool = True, timeout: float = 5.0,
                    scope: str = "auto", select_index: int = 0) -> dict:
    """按可见标签写入文本字段；所有候选定位共享一个总超时预算。

    scope 支持 layer / layui：写入可见 layer.js 嵌套 iframe 表单字段。
    auto 时优先 Ant/页面字段，未命中再尝试 layer 内容。
    """
    field_name = str(field_name or "").strip()
    if not field_name:
        return {"ok": False, "reason": "field_name is required"}
    scope = str(scope or "auto").lower()
    supported = {
        "auto", "top", "frame", "iframe", "modal", "drawer", "overlay",
        "filter", "page", "layer", "layui", "layui-layer",
    }
    if scope not in supported:
        return {"ok": False, "reason": "unsupported scope: %s" % scope}
    timeout = max(float(timeout or 0), 0.0)
    deadline = time.monotonic() + timeout
    select_index = max(int(select_index or 0), 0)

    def remaining(cap=None):
        available = max(deadline - time.monotonic(), 0.0)
        return min(available, cap) if cap is not None else available

    if scope in {"layer", "layui", "layui-layer"}:
        from . import layer_modal as _layer_modal
        return _layer_modal.set_layer_field_value(
            field_name=field_name,
            value=value,
            clear=clear,
            select_index=select_index,
            timeout=timeout,
        )

    tab = browser_session.get_tab()
    contexts = []
    if in_frame and scope != "top":
        frame = browser_session.get_active_frame(tab)
        if frame is not None:
            contexts.append(("iframe", frame))
    if scope in {"auto", "top", "modal", "drawer", "overlay", "filter", "page"} or not contexts:
        contexts.append(("top", tab))

    if scope in {"frame", "iframe"} and not any(name == "iframe" for name, _ in contexts):
        return {"ok": False, "reason": "未找到活动 iframe"}
    if scope == "modal":
        areas = ["modal"]
    elif scope == "drawer":
        areas = ["drawer"]
    elif scope == "overlay":
        areas = ["modal", "drawer"]
    elif scope == "filter":
        areas = ["filter"]
    elif scope == "page":
        areas = ["page"]
    else:
        areas = ["modal", "drawer", "filter", "page"]

    text_value = "" if value is None else str(value)

    # auto 路径：快速检测可见 layer 字段，避免无 layer 感知的穷举搜索浪费超时
    if scope in {"auto"} and remaining() > 1.0 \
            and any(name == "iframe" for name, _ in contexts):
        from . import layer_modal as _layer_modal
        _layer_quick = _layer_modal.set_layer_field_value(
            field_name=field_name,
            value=text_value,
            clear=clear,
            select_index=select_index,
            timeout=min(remaining(), 1.5),
        )
        if _layer_quick.get("ok"):
            _layer_quick["fallback_from"] = "layer-pre-check"
            return _layer_quick

    control_locator = (
        "css:input:not([type='hidden']),textarea,.ant-input-number-input,"
        "[contenteditable='true']"
    )

    def apply_control(control, scope_name, area, index):
        states = getattr(control, "states", None)
        if not bool(getattr(states, "is_enabled", True)):
            return {"ok": False, "reason": "field is disabled: %s" % field_name}
        try:
            if control.attr("readonly") not in (None, False, "", "false"):
                return {"ok": False, "reason": "field is read-only: %s" % field_name}
        except Exception:
            pass
        try:
            _native_element_input(control, text_value, clear, remaining())
            try:
                actual = control.property("value")
            except Exception:
                actual = None
            if actual is None:
                try:
                    actual = control.attr("value")
                except Exception:
                    actual = None
            if actual is None and getattr(control, "tag", "") not in {"input", "textarea"}:
                actual = getattr(control, "text", None)
            return {
                "ok": True,
                "action": "set_field_value",
                "field_name": field_name,
                "value": text_value,
                "actual_value": actual,
                "matches_requested": None if actual is None else str(actual) == text_value,
                "scope": scope_name,
                "area": "overlay" if area in {"modal", "drawer"} else area,
                "select_index": index,
            }
        except Exception as exc:
            return {"ok": False, "reason": "field input failed: %s" % exc,
                    "field_name": field_name, "scope": scope_name, "area": area}

    for scope_name, context in contexts:
        for area in areas:
            if remaining() <= 0:
                break
            containers = _semantic_field_candidates(
                context, field_name, area, remaining(2.0)
            )
            exact = [
                item for item in containers
                if _compact_text(_field_container_label(item)) == _compact_text(field_name)
            ]
            for container in reversed(exact or containers):
                try:
                    controls = [
                        item for item in container.eles(
                            control_locator, timeout=remaining(0.3)
                        )
                        if _element_is_visible(item)
                    ]
                except Exception:
                    controls = []
                if not controls:
                    continue
                index = min(select_index, len(controls) - 1)
                return apply_control(controls[index], scope_name, area, index)
            controls = _generic_field_controls(
                context, field_name, area, remaining(1.0)
            )
            if controls:
                index = min(select_index, len(controls) - 1)
                result = apply_control(controls[index], scope_name, area, index)
                if result.get("ok"):
                    result["adapter"] = "generic-dom"
                return result

    if "filter" in areas:
        for scope_name, context in contexts:
            column, _ = filter_area._quick_filter_field_column(context, field_name)
            if column is None:
                continue
            try:
                controls = [
                    item for item in column.eles(control_locator, timeout=remaining(0.3))
                    if _element_is_visible(item)
                ]
            except Exception:
                controls = []
            if controls:
                index = min(select_index, len(controls) - 1)
                return apply_control(controls[index], scope_name, "filter", index)

    # auto/modal：Ant 路径未命中时尝试 layer 嵌套表单
    if scope in {"auto", "modal", "overlay"} and remaining() > 0:
        from . import layer_modal as _layer_modal
        legacy = _layer_modal.set_layer_field_value(
            field_name=field_name,
            value=value,
            clear=clear,
            select_index=select_index,
            timeout=remaining(),
        )
        if legacy.get("ok"):
            legacy["fallback_from"] = "ant-field"
            return legacy
        layer_reason = legacy.get("reason")
    else:
        layer_reason = None

    reason = "field lookup timed out" if remaining() <= 0 else "field not found"
    result = {
        "ok": False,
        "reason": "%s: %s" % (reason, field_name),
        "scope": scope,
        "in_frame": in_frame,
    }
    if layer_reason:
        result["layer_reason"] = layer_reason
    return result


def _click_field_raw(field_name: str, in_frame: bool = True, timeout: float = 5.0,
                     scope: str = "auto", select_index: int = 0) -> dict:
    """按可见标签点击固定 Ant Design 字段，严格遵守 frame/浮层区域。

    与 ``set_field_value`` 共用语义候选逻辑：显式 modal/drawer/filter 不会退回页面同名
    字段；所有候选共享一个总超时预算。日期、Select 等复合控件点击其稳定 opener。
    """
    field_name = str(field_name or "").strip()
    if not field_name:
        return {"ok": False, "reason": "field target requires name/field_name"}
    scope = str(scope or "auto").lower()
    supported = {"auto", "top", "frame", "iframe", "modal", "drawer", "overlay", "filter", "page"}
    if scope not in supported:
        return {"ok": False, "reason": "unsupported scope: %s" % scope}
    timeout = max(float(timeout or 0), 0.0)
    deadline = time.monotonic() + timeout
    select_index = max(int(select_index or 0), 0)

    def remaining(cap=None):
        available = max(deadline - time.monotonic(), 0.0)
        return min(available, cap) if cap is not None else available

    tab = browser_session.get_tab()
    contexts = []
    if in_frame and scope != "top":
        frame = browser_session.get_active_frame(tab)
        if frame is not None:
            contexts.append(("iframe", frame))
    if scope in {"auto", "top", "modal", "drawer", "overlay", "filter", "page"} or not contexts:
        contexts.append(("top", tab))
    if scope in {"frame", "iframe"} and not any(name == "iframe" for name, _ in contexts):
        return {"ok": False, "reason": "未找到活动 iframe"}

    if scope == "modal":
        areas = ["modal"]
    elif scope == "drawer":
        areas = ["drawer"]
    elif scope == "overlay":
        areas = ["modal", "drawer"]
    elif scope == "filter":
        areas = ["filter"]
    elif scope == "page":
        areas = ["page"]
    else:
        areas = ["modal", "drawer", "filter", "page"]

    control_locator = "css:" + ui_contract.FORM_CONTROL
    opener_locator = (
        "css:.ant-calendar-picker-input,.ant-picker-input input,.ant-select-selection,"
        ".ant-select-selector,[role='combobox'],input:not([type='hidden']),textarea,"
        ".ant-input-number-input,.ant-checkbox-wrapper,.ant-radio-group,.ant-switch"
    )

    def click_container(container, scope_name, area):
        try:
            controls = [
                item for item in container.eles(control_locator, timeout=remaining(0.3))
                if _element_is_visible(item)
            ]
        except Exception:
            controls = []
        if not controls:
            return None
        index = min(select_index, len(controls) - 1)
        control = controls[index]
        try:
            opener = control.ele(opener_locator, timeout=remaining(0.2)) or control
            opener.wait.clickable(timeout=remaining(), wait_stop=True, raise_err=False)
            opener.click(by_js=False, wait_stop=True)
            cls = str(control.attr("class") or "")
            control_type = "field"
            if "calendar" in cls or "ant-picker" in cls:
                control_type = "date-picker"
            elif "ant-select" in cls:
                control_type = "select"
            elif "input-number" in cls:
                control_type = "number"
            return {
                "ok": True,
                "action": "field_click",
                "field_name": field_name,
                "scope": scope_name,
                "area": "overlay" if area in {"modal", "drawer"} else area,
                "control_type": control_type,
                "select_index": index,
            }
        except Exception as exc:
            return {"ok": False, "reason": "field click failed: %s" % exc,
                    "field_name": field_name, "scope": scope_name, "area": area}

    for scope_name, context in contexts:
        for area in areas:
            if remaining() <= 0:
                break
            containers = _semantic_field_candidates(context, field_name, area, remaining(2.0))
            exact = [
                item for item in containers
                if _compact_text(_field_container_label(item)) == _compact_text(field_name)
            ]
            for container in reversed(exact or containers):
                result = click_container(container, scope_name, area)
                if result is not None:
                    return result

    if "filter" in areas:
        for scope_name, context in contexts:
            column, _ = filter_area._quick_filter_field_column(context, field_name)
            if column is None:
                continue
            result = click_container(column, scope_name, "filter")
            if result is not None:
                return result

    reason = "field lookup timed out" if remaining() <= 0 else "field not found"
    return {"ok": False, "reason": "%s: %s" % (reason, field_name),
            "scope": scope, "in_frame": in_frame}


def _normalize_date_value(value: str) -> dict:
    raw = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return {
                "ok": True,
                "dash": parsed.strftime("%Y-%m-%d"),
                "slash": parsed.strftime("%Y/%m/%d"),
                "year": parsed.year,
                "month": parsed.month,
                "day": parsed.day,
            }
        except ValueError:
            pass
    return {"ok": False, "reason": "date must be YYYY-MM-DD or YYYY/MM/DD"}


def _field_snapshot(target, field_name: str, select_index: int = 0) -> dict:
    js = r"""
var FIELD_NAME = __FIELD_NAME__;
var SELECT_INDEX = __SELECT_INDEX__;
function clean(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
function frameOffset() {
  var fe = window.frameElement;
  if (!fe) return {left: 0, top: 0};
  var r = fe.getBoundingClientRect();
  return {left: r.left, top: r.top};
}
function rectOf(el) {
  if (!el) return null;
  var off = frameOffset();
  var r = el.getBoundingClientRect();
  var x = Math.round((r.x + off.left) * 10) / 10;
  var y = Math.round((r.y + off.top) * 10) / 10;
  var w = Math.round(r.width * 10) / 10;
  var h = Math.round(r.height * 10) / 10;
  return {x: x, y: y, width: w, height: h,
          cx: Math.round((x + w / 2) * 10) / 10,
          cy: Math.round((y + h / 2) * 10) / 10};
}
function visible(el) {
  if (!el || !el.isConnected) return false;
  var style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  var r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
function controlType(container) {
  if (container.querySelector('.ant-calendar-picker,.ant-picker')) return 'date-picker';
  if (container.querySelector('.ant-select')) return 'select';
  if (container.querySelector('.ant-input-number')) return 'number';
  if (container.querySelector('textarea')) return 'textarea';
  return 'text';
}
var containers = [].slice.call(document.querySelectorAll('.ant-form-item'));
var matches = [];
for (var i = 0; i < containers.length; i++) {
  var c = containers[i];
  if (!visible(c)) continue;
  var labelEl = c.querySelector('.ant-form-item-label label,.ant-form-item-label');
  var label = clean(labelEl ? labelEl.textContent : '');
  var text = clean(c.textContent);
  if ((label && label.indexOf(FIELD_NAME) >= 0) || text.indexOf(FIELD_NAME) >= 0) {
    matches.push({container: c, label: label || FIELD_NAME});
  }
}
if (!matches.length) {
  return JSON.stringify({ok: false, reason: 'field not found: ' + FIELD_NAME});
}
var picked = matches[Math.min(Math.max(SELECT_INDEX, 0), matches.length - 1)];
var container = picked.container;
var control = container.querySelector('.ant-calendar-picker,.ant-picker,.ant-select,.ant-input-number,textarea,input:not([type="hidden"])') || container;
var input = container.querySelector('.ant-calendar-picker input,.ant-picker input,input:not([type="hidden"]),textarea');
var value = input ? (input.value || input.getAttribute('value') || '') : clean(control.textContent);
return JSON.stringify({
  ok: true,
  label: picked.label,
  type: controlType(container),
  value: value,
  readOnly: !!(input && input.readOnly),
  disabled: !!((input && input.disabled) || control.className.indexOf('disabled') >= 0),
  rect: rectOf(control)
});
""".replace("__FIELD_NAME__", json.dumps(str(field_name or ""), ensure_ascii=False)).replace(
        "__SELECT_INDEX__", str(int(select_index or 0))
    )
    return page_model._run_json(target, js, {"ok": False, "reason": "field snapshot failed"})


def _calendar_snapshot(target, target_date_slash: str = "") -> dict:
    js = r"""
var TARGET_DATE = __TARGET_DATE__;
function clean(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
function visible(el) {
  if (!el || !el.isConnected) return false;
  var style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  var r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
function frameOffset() {
  var fe = window.frameElement;
  if (!fe) return {left: 0, top: 0};
  var r = fe.getBoundingClientRect();
  return {left: r.left, top: r.top};
}
function rectOf(el) {
  if (!el) return null;
  var off = frameOffset();
  var r = el.getBoundingClientRect();
  var x = Math.round((r.x + off.left) * 10) / 10;
  var y = Math.round((r.y + off.top) * 10) / 10;
  var w = Math.round(r.width * 10) / 10;
  var h = Math.round(r.height * 10) / 10;
  return {x: x, y: y, width: w, height: h,
          cx: Math.round((x + w / 2) * 10) / 10,
          cy: Math.round((y + h / 2) * 10) / 10};
}
function panelInfo(root) {
  var panel = root.querySelector('.ant-calendar-range-left') || root;
  var ye = panel.querySelector('.ant-calendar-year-select');
  var me = panel.querySelector('.ant-calendar-month-select');
  var yearText = clean(ye ? ye.textContent : '');
  var monthText = clean(me ? me.textContent : '');
  var year = parseInt(yearText.replace(/\D/g, ''), 10) || null;
  var month = parseInt(monthText.replace(/\D/g, ''), 10) || null;
  return {yearText: yearText, monthText: monthText,
          title: yearText + monthText, year: year, month: month};
}
function cellInfo(root, dateTitle) {
  if (!dateTitle) return null;
  var td = root.querySelector('td[title="' + dateTitle + '"]');
  var cell = td ? td.querySelector('.ant-calendar-date') : null;
  if (!td || !cell) return null;
  var cls = td.className || '';
  return {
    title: dateTitle,
    text: clean(cell.textContent),
    disabled: /\bant-calendar-disabled-cell\b/.test(cls),
    selected: /\bant-calendar-selected-date\b|\bant-calendar-selected-start-date\b|\bant-calendar-selected-end-date\b/.test(cls),
    today: /\bant-calendar-today\b/.test(cls),
    inView: !(/\bant-calendar-last-month-cell\b|\bant-calendar-next-month-cell\b|\bant-calendar-next-month-btn-day\b/.test(cls)),
    rect: rectOf(cell)
  };
}
var roots = [].slice.call(document.querySelectorAll('.ant-calendar-picker-container .ant-calendar,.ant-calendar'))
  .filter(function(el) {
    return visible(el) && !el.parentElement.closest('.ant-calendar');
  });
if (!roots.length) {
  return JSON.stringify({ok: false, reason: 'calendar not found'});
}
var root = roots[0];
var cls = root.className || '';
var isRange = /\bant-calendar-range\b/.test(cls) ||
  !!root.querySelector('.ant-calendar-range-left,.ant-calendar-range-right');
var selectedDates = [];
[].slice.call(root.querySelectorAll('td[title]')).forEach(function(td) {
  var t = td.getAttribute('title') || '';
  var tdCls = td.className || '';
  if (t && /\bant-calendar-selected-date\b|\bant-calendar-selected-start-date\b|\bant-calendar-selected-end-date\b/.test(tdCls)) {
    selectedDates.push(t);
  }
});
var info = panelInfo(root);
return JSON.stringify({
  ok: true,
  mode: isRange ? 'range' : 'single',
  title: info.title,
  year: info.year,
  month: info.month,
  selectedDates: selectedDates,
  cellCount: root.querySelectorAll('td[title] .ant-calendar-date').length,
  targetCell: cellInfo(root, TARGET_DATE),
  nav: {
    prevMonth: rectOf(root.querySelector('.ant-calendar-prev-month-btn')),
    nextMonth: rectOf(root.querySelector('.ant-calendar-next-month-btn')),
    prevYear: rectOf(root.querySelector('.ant-calendar-prev-year-btn')),
    nextYear: rectOf(root.querySelector('.ant-calendar-next-year-btn'))
  },
  rect: rectOf(root)
});
""".replace("__TARGET_DATE__", json.dumps(str(target_date_slash or ""), ensure_ascii=False))
    return page_model._run_json(target, js, {"ok": False, "reason": "calendar snapshot failed"})


def _find_calendar_root(target, timeout: float):
    try:
        return target.ele("c:.ant-calendar-picker-container .ant-calendar", timeout=timeout)
    except Exception:
        pass
    try:
        return target.ele("c:.ant-calendar", timeout=timeout)
    except Exception:
        return None


def _calendar_shown_ym(cal) -> tuple[int, int]:
    panel = None
    try:
        panel = cal.ele("c:.ant-calendar-range-left", timeout=0.2)
    except Exception:
        panel = None
    panel = panel or cal
    ye = panel.ele("c:.ant-calendar-year-select", timeout=1)
    me = panel.ele("c:.ant-calendar-month-select", timeout=1)
    year = int("".join(ch for ch in str(ye.text or "") if ch.isdigit()))
    month = int("".join(ch for ch in str(me.text or "") if ch.isdigit()))
    return year, month


def _wait_calendar_ym_change(cal, previous: tuple[int, int], timeout: float) -> tuple[int, int]:
    """Wait with DrissionPage's element waiter, then read the calendar state once."""
    try:
        cal.wait.stop_moving(timeout=max(timeout, 0), raise_err=False)
        current = _calendar_shown_ym(cal)
        return current
    except Exception:
        return previous


def _date_field_contexts(in_frame: bool, scope: str) -> tuple[object, list[tuple[str, object]], list[str]]:
    """Resolve the same scope ordering used by the semantic field facades."""
    tab = browser_session.get_tab()
    contexts = []
    if in_frame and scope != "top":
        frame = browser_session.get_active_frame(tab)
        if frame is not None:
            contexts.append(("iframe", frame))
    if scope in {"auto", "top", "modal", "drawer", "overlay", "filter", "page"} or not contexts:
        contexts.append(("top", tab))

    if scope == "modal":
        areas = ["modal"]
    elif scope == "drawer":
        areas = ["drawer"]
    elif scope == "overlay":
        areas = ["modal", "drawer"]
    elif scope == "filter":
        areas = ["filter"]
    elif scope == "page":
        areas = ["page"]
    else:
        areas = ["modal", "drawer", "filter", "page"]
    return tab, contexts, areas


def _date_picker_inputs(picker) -> list:
    try:
        inputs = picker.eles(
            "css:input.ant-calendar-range-picker-input,input.ant-calendar-picker-input,"
            ".ant-picker-input input,input:not([type='hidden'])",
            timeout=0.3,
        ) or []
    except Exception:
        inputs = []
    return [item for item in inputs if _element_is_visible(item)]


def _date_picker_values(picker) -> list[str]:
    values = []
    for item in _date_picker_inputs(picker):
        value = None
        try:
            value = item.property("value")
        except Exception:
            pass
        if value is None:
            try:
                value = item.attr("value")
            except Exception:
                value = None
        values.append(str(value or ""))
    return values


def _resolve_date_picker(field_name: str, in_frame: bool = True, scope: str = "auto",
                         select_index: int = 0, timeout: float = 5.0) -> dict:
    """Find only the date value control, never a sibling Quick Filter select."""
    field_name = str(field_name or "").strip()
    scope = str(scope or "auto").strip().lower()
    supported = {"auto", "top", "frame", "iframe", "modal", "drawer", "overlay", "filter", "page"}
    if not field_name:
        return {"ok": False, "reason": "field_name is required"}
    if scope not in supported:
        return {"ok": False, "reason": "unsupported scope: %s" % scope}

    timeout = max(float(timeout or 0), 0.0)
    deadline = time.monotonic() + timeout
    select_index = max(int(select_index or 0), 0)

    def remaining(cap=None):
        available = max(deadline - time.monotonic(), 0.0)
        return min(available, cap) if cap is not None else available

    tab, contexts, areas = _date_field_contexts(in_frame, scope)
    if scope in {"frame", "iframe"} and not any(name == "iframe" for name, _ in contexts):
        return {"ok": False, "reason": "未找到活动 iframe"}

    picker_locator = "css:.ant-calendar-picker,.ant-picker"

    def from_container(container, scope_name: str, area: str, component: str):
        try:
            pickers = [
                item for item in container.eles(picker_locator, timeout=remaining(0.4))
                if _element_is_visible(item)
            ]
        except Exception:
            pickers = []
        if not pickers:
            return None
        index = min(select_index, len(pickers) - 1)
        picker = pickers[index]
        inputs = _date_picker_inputs(picker)
        range_inputs = []
        try:
            range_inputs = [
                item for item in picker.eles("css:input.ant-calendar-range-picker-input", timeout=0.2)
                if _element_is_visible(item)
            ]
        except Exception:
            pass
        return {
            "ok": True,
            "tab": tab,
            "target": dict(contexts).get(scope_name),
            "container": container,
            "picker": picker,
            "inputs": inputs,
            "picker_mode": "range" if len(range_inputs) >= 2 else "single",
            "scope": scope_name,
            "area": area,
            "component": component,
            "select_index": index,
        }

    for scope_name, context in contexts:
        for area in areas:
            if remaining() <= 0:
                break
            if area == "filter":
                column, _ = filter_area._quick_filter_field_column(context, field_name)
                if column is not None:
                    resolved = from_container(
                        column, scope_name, area, "legions-pro-quick-filter",
                    )
                    if resolved is not None:
                        return resolved
                continue

            containers = _semantic_field_candidates(context, field_name, area, remaining(2.0))
            exact = [
                item for item in containers
                if _compact_text(_field_container_label(item)) == _compact_text(field_name)
            ]
            for container in reversed(exact or containers):
                resolved = from_container(container, scope_name, area, "ant-design")
                if resolved is not None:
                    return resolved

    reason = "field lookup timed out" if remaining() <= 0 else "date field not found"
    return {"ok": False, "reason": "%s: %s" % (reason, field_name),
            "scope": scope, "in_frame": in_frame}


def _open_date_calendar(resolved: dict, timeout: float) -> tuple[object, object] | tuple[None, None]:
    picker = resolved["picker"]
    inputs = resolved.get("inputs") or _date_picker_inputs(picker)
    opener = inputs[0] if inputs else None
    if opener is None:
        try:
            opener = picker.ele(
                "css:.ant-calendar-picker-input,.ant-picker-input", timeout=min(timeout, 0.5)
            )
        except Exception:
            opener = None
    opener = opener or picker
    try:
        waiter = getattr(opener, "wait", None)
        if waiter is not None:
            waiter.clickable(timeout=timeout, wait_stop=True, raise_err=False)
        try:
            opener.click(by_js=False, timeout=timeout, wait_stop=True)
        except TypeError:
            opener.click()
    except Exception:
        return None, None

    targets = [resolved.get("target"), resolved.get("tab")]
    seen = set()
    deadline = time.monotonic() + max(float(timeout or 0), 0.0)
    for target in targets:
        if target is None or id(target) in seen:
            continue
        seen.add(id(target))
        remaining = max(deadline - time.monotonic(), 0.0)
        if remaining <= 0:
            break
        cal = _find_calendar_root(target, timeout=min(remaining, 3.0))
        if cal is not None and _element_is_visible(cal):
            return target, cal
    return None, None


def _calendar_date_cell(cal, normalized: dict):
    selectors = (
        'c:td[title="%s"]:not(.ant-calendar-last-month-cell)'
        ':not(.ant-calendar-next-month-btn-day)'
        ':not(.ant-calendar-next-month-cell) .ant-calendar-date' % normalized["slash"],
        'c:td[title="%s"] .ant-calendar-date' % normalized["slash"],
    )
    for selector in selectors:
        try:
            cell = cal.ele(selector, timeout=0.2)
        except Exception:
            cell = None
        if cell is not None and _element_is_visible(cell):
            return cell
    return None


def _select_calendar_date(cal, normalized: dict, deadline: float) -> dict:
    navigations = []
    target_index = normalized["year"] * 12 + normalized["month"]
    for _ in range(600):
        if time.monotonic() >= deadline:
            return {"ok": False, "reason": "日期选择超时", "navigations": navigations}
        cell = _calendar_date_cell(cal, normalized)
        if cell is not None:
            try:
                cell.click(by_js=False, timeout=max(deadline - time.monotonic(), 0), wait_stop=True)
            except TypeError:
                cell.click()
            return {"ok": True, "navigations": navigations}

        current = _calendar_shown_ym(cal)
        current_index = current[0] * 12 + current[1]
        try:
            has_right_panel = cal.ele("c:.ant-calendar-range-right", timeout=0.1) is not None
        except Exception:
            has_right_panel = False
        visible_span = 1 if has_right_panel else 0
        delta = target_index - current_index
        if 0 <= delta <= visible_span:
            return {
                "ok": False,
                "reason": "未找到日期单元格: %s" % normalized["slash"],
                "navigations": navigations,
            }
        forward = delta > visible_span
        selector = "c:.ant-calendar-next-month-btn" if forward else "c:.ant-calendar-prev-month-btn"
        try:
            button = cal.ele(selector, timeout=min(max(deadline - time.monotonic(), 0), 1.0))
        except Exception:
            button = None
        if button is None:
            return {"ok": False, "reason": "未找到日历翻月按钮", "navigations": navigations}
        try:
            button.click(by_js=False, timeout=min(max(deadline - time.monotonic(), 0), 1.5), wait_stop=True)
        except TypeError:
            button.click()
        after = _wait_calendar_ym_change(cal, current, min(max(deadline - time.monotonic(), 0), 1.5))
        navigations.append({
            "direction": "next" if forward else "prev",
            "from": "%04d-%02d" % current,
            "to": "%04d-%02d" % after,
        })
        if after == current:
            return {"ok": False, "reason": "日历翻月后月份未变化", "navigations": navigations}
    return {"ok": False, "reason": "日历翻月超过上限", "navigations": navigations}


def _date_part(value: str) -> str:
    matched = re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", str(value or ""))
    return matched.group(0).replace("/", "-") if matched else ""


def set_date(field_name: str, date: str = None, start_date: str = None,
             end_date: str = None, in_frame: bool = True, timeout: float = 8,
             select_index: int = 0, scope: str = "auto") -> dict:
    """统一设置日期字段，自动适配 Ant DatePicker/RangePicker 与 Legions Quick Filter。

    单日期传 ``date``；日期范围传 ``start_date`` 和 ``end_date``。工具按真实控件形态
    自动选择单日或范围交互。Quick Filter 的单边界日期字段只能接收一个日期。
    日期格式支持 YYYY-MM-DD 或 YYYY/MM/DD。
    """
    timeout = max(float(timeout or 0), 0.0)
    started = time.monotonic()
    deadline = started + timeout

    def remaining(cap=None):
        value = max(deadline - time.monotonic(), 0.0)
        return min(value, cap) if cap is not None else value
    has_single = date not in (None, "")
    has_range = start_date not in (None, "") or end_date not in (None, "")
    if has_single and has_range:
        return {"ok": False, "reason": "date 不能与 start_date/end_date 同时使用"}
    if not has_single and not (start_date not in (None, "") and end_date not in (None, "")):
        return {"ok": False, "reason": "请传 date，或同时传 start_date 和 end_date"}

    requested_mode = "single" if has_single else "range"
    start_raw = date if has_single else start_date
    end_raw = date if has_single else end_date
    normalized_start = _normalize_date_value(start_raw)
    if not normalized_start.get("ok"):
        return normalized_start
    normalized_end = _normalize_date_value(end_raw)
    if not normalized_end.get("ok"):
        return normalized_end
    if normalized_start["dash"] > normalized_end["dash"]:
        return {"ok": False, "reason": "开始日期不能晚于结束日期"}

    resolved = _resolve_date_picker(
        field_name, in_frame=in_frame, scope=scope, select_index=select_index,
        timeout=remaining(5),
    )
    if not resolved.get("ok"):
        resolved["action"] = "set_date"
        resolved["field_name"] = field_name
        resolved["elapsedMs"] = int((time.monotonic() - started) * 1000)
        return resolved

    picker_mode = resolved["picker_mode"]
    if picker_mode == "single" and normalized_start["dash"] != normalized_end["dash"]:
        return {
            "ok": False,
            "reason": "目标是单边界日期控件，不能写入不同的开始和结束日期；请结合筛选操作符并传 date",
            "action": "set_date",
            "field_name": field_name,
            "component": resolved["component"],
            "picker_mode": picker_mode,
            "elapsedMs": int((time.monotonic() - started) * 1000),
        }

    before_values = _date_picker_values(resolved["picker"])
    calendar_target, cal = _open_date_calendar(resolved, remaining(4))
    if cal is None:
        return {
            "ok": False, "reason": "日历面板未弹出", "action": "set_date",
            "field_name": field_name, "component": resolved["component"],
            "picker_mode": picker_mode,
            "elapsedMs": int((time.monotonic() - started) * 1000),
        }

    opened = _calendar_snapshot(calendar_target, normalized_start["slash"])
    selected_start = _select_calendar_date(cal, normalized_start, deadline)
    if not selected_start.get("ok"):
        return {
            **selected_start, "action": "set_date", "field_name": field_name,
            "component": resolved["component"], "picker_mode": picker_mode,
            "opened": opened, "elapsedMs": int((time.monotonic() - started) * 1000),
        }

    navigations = list(selected_start.get("navigations") or [])
    if picker_mode == "range":
        cal = _find_calendar_root(calendar_target, timeout=remaining(2)) or cal
        selected_end = _select_calendar_date(cal, normalized_end, deadline)
        navigations.extend(selected_end.get("navigations") or [])
        if not selected_end.get("ok"):
            return {
                **selected_end, "action": "set_date", "field_name": field_name,
                "component": resolved["component"], "picker_mode": picker_mode,
                "opened": opened, "navigations": navigations,
                "elapsedMs": int((time.monotonic() - started) * 1000),
            }

    try:
        calendar_target.wait.ele_hidden(
            "c:.ant-calendar-picker-container .ant-calendar",
            timeout=remaining(2), raise_err=False,
        )
    except Exception:
        pass

    refreshed = _resolve_date_picker(
        field_name, in_frame=in_frame, scope=scope, select_index=select_index,
        timeout=remaining(1),
    )
    after_picker = refreshed.get("picker") if refreshed.get("ok") else resolved["picker"]
    after_values = _date_picker_values(after_picker)
    actual_start = _date_part(after_values[0] if after_values else "")
    actual_end = _date_part(after_values[1] if len(after_values) > 1 else "")
    if picker_mode == "range":
        ok = actual_start == normalized_start["dash"] and actual_end == normalized_end["dash"]
    else:
        ok = actual_start == normalized_start["dash"]
    result = {
        "ok": ok,
        "action": "set_date",
        "field_name": field_name,
        "requested_mode": requested_mode,
        "picker_mode": picker_mode,
        "component": resolved["component"],
        "scope": resolved["scope"],
        "area": resolved["area"],
        "field": {
            "before": before_values,
            "after": after_values,
        },
        "calendar": {
            "opened": {
                "title": opened.get("title"),
                "cellCount": opened.get("cellCount"),
                "rect": opened.get("rect"),
            },
            "navigations": navigations,
        },
        "elapsedMs": int((time.monotonic() - started) * 1000),
    }
    if requested_mode == "single":
        result["date"] = normalized_start["dash"]
    else:
        result["startDate"] = normalized_start["dash"]
        result["endDate"] = normalized_end["dash"]
    if not ok:
        result["reason"] = "日期字段值校验失败"
    return result


def _resolve_target_action(target, action_name: str, locator, x, y, field_name,
                           row, col, column_title, kind, table_index, icon_name,
                           option_text, key, modifiers, in_frame: bool):
    """Normalize semantic target input into the legacy action parameters."""
    meta = {}
    target = _normalize_target_dict(target)
    if not target:
        return {
            "action": action_name, "locator": locator, "x": x, "y": y,
            "field_name": field_name, "row": row, "col": col,
            "column_title": column_title, "kind": kind, "table_index": table_index,
            "icon_name": icon_name, "option_text": option_text, "key": key,
            "modifiers": modifiers, "target_meta": meta, "target_error": None,
            "in_frame": in_frame,
        }

    target_type = str(_target_get(target, "type", "kind", default="") or "").lower()
    if not target_type:
        if _target_get(target, "x") is not None and _target_get(target, "y") is not None:
            target_type = "xy"
        elif _target_get(target, "field_name", "name") is not None:
            target_type = "field"
        elif _target_get(target, "row") is not None:
            target_type = "table_cell"
        else:
            target_type = "locator"

    meta["target_type"] = target_type
    if target_type in ("xy", "point", "coord", "coordinate"):
        x = float(_target_get(target, "x", "cx", "viewportX", default=x))
        y = float(_target_get(target, "y", "cy", "viewportY", default=y))
        action_name = "click_xy"
    elif target_type in ("action", "button", "toolbar"):
        resolved = _resolve_visible_action_target(target, in_frame=in_frame)
        if not resolved.get("ok"):
            return {"target_error": resolved, "target_meta": meta, "action": action_name,
                    "locator": locator, "x": x, "y": y, "field_name": field_name,
                    "row": row, "col": col, "column_title": column_title, "kind": kind,
                    "table_index": table_index, "icon_name": icon_name,
                    "option_text": option_text, "key": key, "modifiers": modifiers}
        action_name = resolved["action"]
        locator = resolved.get("locator") or locator
        x = resolved.get("x")
        y = resolved.get("y")
        in_frame = resolved.get("in_frame", in_frame)
        meta.update(resolved.get("meta") or {})
    elif target_type in ("field", "form_field", "date", "date-picker", "select"):
        field_name = str(_target_get(target, "field_name", "name", "label", default=field_name) or "")
        action_name = "field_click" if action_name in ("click", "click_xy") else action_name
        meta["field_name"] = field_name
        meta["scope"] = str(_target_get(target, "scope", default="auto") or "auto")
        meta["select_index"] = int(_target_get(target, "select_index", "selectIndex", default=0) or 0)
        if target_type in ("date", "date-picker") or any(word in field_name for word in ("日期", "时间")):
            meta["control_type"] = "date-picker"
        elif target_type == "select":
            meta["control_type"] = "select"
    elif target_type in ("css", "xpath", "text", "locator"):
        if target_type == "css":
            locator = _as_locator("css", _target_get(target, "value", "css", "selector", default=locator))
        elif target_type == "xpath":
            locator = _as_locator("xpath", _target_get(target, "value", "xpath", default=locator))
        elif target_type == "text":
            locator = _as_locator("text", _target_get(target, "value", "text", "name", default=locator))
        else:
            locator = _target_get(target, "locator", "value", default=locator)
        action_name = "click" if action_name == "click_xy" else action_name
    elif target_type in ("table_cell", "cell"):
        action_name = "table_cell"
        row = int(_target_get(target, "row", default=row) or 0)
        col = _target_get(target, "col", "column", default=col)
        col = int(col) if col is not None else None
        column_title = _target_get(target, "column_title", "columnTitle", "title", default=column_title)
        kind = _target_get(target, "table_kind", "kind", default=kind)
        table_index = int(_target_get(target, "table_index", "tableIndex", default=table_index) or 0)
        icon_name = _target_get(target, "icon_name", "iconName", default=icon_name)
    elif target_type in ("option", "select_option"):
        action_name = "select_option"
        field_name = _target_get(target, "field_name", "name", "label", default=field_name)
        option_text = _target_get(target, "option_text", "option", "value", "text", default=option_text)
    elif target_type in ("key", "keyboard"):
        action_name = "press_key"
        key = _target_get(target, "key", "value", default=key)
        modifiers = _target_get(target, "modifiers", default=modifiers)
    else:
        return {"target_error": {"ok": False, "reason": "unsupported target type: %s" % target_type},
                "target_meta": meta, "action": action_name, "locator": locator, "x": x, "y": y,
                "field_name": field_name, "row": row, "col": col,
                "column_title": column_title, "kind": kind, "table_index": table_index,
                "icon_name": icon_name, "option_text": option_text, "key": key,
                "modifiers": modifiers}

    return {
        "action": action_name, "locator": locator, "x": x, "y": y,
        "field_name": field_name, "row": row, "col": col,
        "column_title": column_title, "kind": kind, "table_index": table_index,
        "icon_name": icon_name, "option_text": option_text, "key": key,
        "modifiers": modifiers, "target_meta": meta, "target_error": None,
        "in_frame": in_frame,
    }


def _signals_for_expect(expect) -> list[str]:
    if not expect:
        return []
    if isinstance(expect, (list, tuple, set)):
        parts = [str(x).lower() for x in expect]
    else:
        parts = [p.strip().lower() for p in str(expect).replace("|", ",").split(",") if p.strip()]
    mapping = {
        "none": [],
        "modal": ["modal"],
        "drawer": ["drawer"],
        "overlay": ["overlay"],
        "calendar": ["calendar"],
        "date": ["calendar"],
        "dropdown": ["dropdown"],
        "select": ["dropdown"],
        "toast": ["message", "notification"],
        "message": ["message"],
        "notification": ["notification"],
        "network": ["network"],
        "navigation": ["url", "tab"],
        "url": ["url"],
        "tab": ["tab"],
    }
    out = []
    for part in parts:
        for sig in mapping.get(part, [part]):
            if sig and sig not in out:
                out.append(sig)
    return out


def _infer_expect(expect, action_name: str, target_meta: dict) -> str:
    raw = str(expect or "auto").strip().lower()
    if raw and raw != "auto":
        return raw
    control_type = (target_meta or {}).get("control_type", "")
    if control_type == "date-picker":
        return "calendar"
    if control_type in ("select", "searchable-select"):
        return "dropdown"
    target_type = (target_meta or {}).get("target_type", "")
    text = _compact_text((target_meta or {}).get("text", ""))
    if target_type == "action":
        if any(word in text for word in ("添加", "新增", "编辑", "详情", "查看")):
            return "modal"
        if any(word in text for word in ("保存", "确定", "提交", "删除", "审核")):
            return "toast"
    if action_name == "select_option":
        return "dropdown"
    return "auto"


def _resolve_observe_policy(signals, listen_targets, expect: str, observe_mode: str,
                            include_snapshot, detail: str, action_name: str,
                            target_meta: dict, timeout: float) -> dict:
    mode = str(observe_mode or "auto").strip().lower()
    if mode not in ("auto", "fast", "evidence", "full", "none", "off"):
        mode = "auto"
    inferred_expect = _infer_expect(expect, action_name, target_meta)
    semantic_requested = (
        str(expect or "auto").strip().lower() != "auto"
        or mode not in ("auto",)
        or bool(target_meta)
    )

    if signals is not None:
        effective_signals = signals
    elif semantic_requested and inferred_expect != "auto":
        effective_signals = _signals_for_expect(inferred_expect)
    elif semantic_requested and mode in ("fast", "none", "off"):
        effective_signals = []
    else:
        effective_signals = (
            ["overlay", "notification", "message", "tab", "url", "network"]
            if listen_targets else ["overlay", "notification", "message", "tab", "url"]
        )

    if mode in ("none", "off") or inferred_expect == "none":
        effective_signals = []

    effective_detail = "full" if mode == "full" and detail == "summary" else detail
    if include_snapshot is None:
        effective_snapshot = mode not in ("fast", "none", "off")
    else:
        effective_snapshot = bool(include_snapshot)

    wait_timeout = timeout
    if mode == "fast":
        wait_timeout = min(timeout, 2.0)

    return {
        "mode": mode,
        "expect": inferred_expect,
        "signals": effective_signals,
        "include_snapshot": effective_snapshot,
        "detail": effective_detail,
        "timeout": wait_timeout,
        "skip_observe": not effective_signals,
    }


def explore_action(action: Literal["click", "input", "set_date",
                                   "table_cell", "select_option", "press_key"] = "click",
                   target: dict = None,
                   locator: str = None, x: float = None, y: float = None,
                   row: int = 0, col: int = None, column_title: str = None, kind: str = "auto",
                   table_index: int = 0, icon_name: str = None, option_text: str = None,
                   field_name: str = None, text: str = None, date: str = None,
                   start_date: str = None, end_date: str = None,
                   key: str = None, modifiers: list[str] = None,
                   by_js: bool = False, in_frame: bool = True, timeout: float = 8,
                   signals: list[str] = None, listen_targets: str = None,
                   capture_before: bool = False, capture_after: bool = False,
                   include_snapshot: bool = None, detail: str = "summary",
                   expect: str = "auto", observe_mode: str = "auto",
                   clean_overlays: bool = True) -> dict:
    """动作探索封装：observe_start → 执行动作 → observe_wait → 可选页面模型快照。

    enterprise profile 的 action 可选 click/input/set_date/table_cell/select_option/press_key。
    set_date 通过 date 设置单日，通过 start_date/end_date 设置范围。target 可选语义目标：
    {"type":"field","name":"工作日期"}、{"type":"button","text":"添加"}、
    {"type":"css","value":"button.ant-btn"}、{"type":"xpath","value":"//button"}、
    也可使用旧参数 locator/field_name。enterprise profile 禁止显式坐标、JS 点击和跳过观察；
    这些兼容参数只在 full profile 的开发诊断中可用。

    瘦身说明（2026-07）：
    - capture_after 默认 False，避免返回冗余的完整页面模型（actions/fields/modals/tables）。
    - observe_mode=fast/none 可减少或跳过点击后观察；expect=modal/calendar/dropdown/toast/network 等表达观察意图。
    - include_snapshot 默认按 observe_mode 推断；旧调用默认仍返回精简浮层快照。
    - 只需确认浮层有无 → 用 signal.snapshot_after 即可。
    - 需要完整页面动作列表/表格数据 → 显式 capture_after=True。
    """
    flow_started = time.perf_counter()
    action_name = (action or "click").lower()
    requested_target_type = (
        str(target.get("type") or "").strip().lower()
        if isinstance(target, dict) else ""
    )
    if tool_metadata.ENABLED_PROFILE == "enterprise":
        violations = []
        if action_name == "click_xy" or requested_target_type == "xy" or x is not None or y is not None:
            violations.append("显式坐标动作")
        if by_js:
            violations.append("JS 点击")
        if str(observe_mode or "auto").strip().lower() == "none" or signals == []:
            violations.append("跳过动作观察")
        if violations:
            return {
                "ok": False,
                "reason": "enterprise profile 禁止%s；请使用语义 target 并保留业务反馈观察" % "、".join(violations),
                "profile": tool_metadata.ENABLED_PROFILE,
            }
    resolved = _resolve_target_action(
        target, action_name, locator, x, y, field_name, row, col, column_title,
        kind, table_index, icon_name, option_text, key, modifiers, in_frame,
    )
    target_meta = resolved.get("target_meta") or {}
    target_error = resolved.get("target_error")
    if not target_error:
        action_name = resolved["action"]
        locator = resolved["locator"]
        x = resolved["x"]
        y = resolved["y"]
        field_name = resolved["field_name"]
        row = resolved["row"]
        col = resolved["col"]
        column_title = resolved["column_title"]
        kind = resolved["kind"]
        table_index = resolved["table_index"]
        icon_name = resolved["icon_name"]
        option_text = resolved["option_text"]
        key = resolved["key"]
        modifiers = resolved["modifiers"]
        in_frame = resolved.get("in_frame", in_frame)

    if recipe_context.requires_native_actions() and action_name == "click_xy":
        target_error = {
            "ok": False,
            "reason": "run_test_cases 禁止普通坐标点击；请提供可定位的 DOM 控件，VTable 请使用专用动作",
        }

    observe_policy = _resolve_observe_policy(
        signals, listen_targets, expect, observe_mode, include_snapshot, detail,
        action_name, target_meta, timeout,
    )
    if capture_after and include_snapshot is None:
        observe_policy["include_snapshot"] = False
    before = None
    if capture_before:
        before = page_model.capture_page_model(include_filters=False, include_table_data=False,
                                               max_table_rows=20, max_elements=80)

    if observe_policy["skip_observe"]:
        observe_start_result = {"ok": True, "session": "skipped",
                                "reason": "observe disabled by expect/observe_mode"}
    else:
        observe_start_result = observe.observe_start(
            signals=observe_policy["signals"],
            listen_targets=listen_targets,
            native_wait=recipe_context.requires_native_actions(),
        )
    action_result = {"ok": False, "reason": "action not executed"}
    cleanup = table_facade.pre_click_cleanup(clean_overlays)
    try:
        tab = browser_session.get_tab()
        if target_error:
            action_result = dict(target_error)
        elif action_name == "click":
            if not locator:
                action_result = {"ok": False, "reason": "locator is required for click"}
            else:
                action_result = _resolve_and_click(
                    locator, in_frame=in_frame, by_js=by_js, timeout=timeout,
                )
                if action_result.get("ok"):
                    action_result["action"] = "click"
        elif action_name == "click_xy":
            if x is None or y is None:
                action_result = {"ok": False, "reason": "x/y are required for click_xy"}
            else:
                tab.actions.move_to((x, y), duration=0.3).click()
                action_result = {"ok": True, "action": "click_xy", "x": x, "y": y}
        elif action_name == "input":
            if text is None:
                action_result = {"ok": False, "reason": "text is required for input"}
            elif field_name:
                action_result = set_field_value(
                    field_name, text, in_frame=in_frame, clear=True, timeout=timeout,
                    scope=target_meta.get("scope", "auto"),
                    select_index=int(target_meta.get("select_index", 0) or 0),
                )
                action_result["action"] = "input"
            elif not locator:
                action_result = {"ok": False, "reason": "locator or semantic field target is required for input"}
            else:
                action_result = input(locator, text, in_frame=in_frame, timeout=timeout)
                action_result["action"] = "input"
        elif action_name == "set_date":
            if not field_name or not (date or (start_date and end_date)):
                action_result = {
                    "ok": False,
                    "reason": "set_date requires field_name and either date or start_date/end_date",
                }
            else:
                action_result = set_date(
                    field_name, date=date, start_date=start_date, end_date=end_date,
                    in_frame=in_frame, timeout=timeout,
                    scope=target_meta.get("scope", "auto"),
                    select_index=int(target_meta.get("select_index", 0) or 0),
                )
                action_result["action"] = "set_date"
        elif action_name == "date_range":
            if not field_name or not start_date or not end_date:
                action_result = {"ok": False, "reason": "field_name, start_date and end_date are required for date_range"}
            else:
                action_result = set_date(
                    field_name, start_date=start_date, end_date=end_date,
                    in_frame=in_frame, timeout=timeout,
                    scope=target_meta.get("scope", "auto"),
                    select_index=int(target_meta.get("select_index", 0) or 0),
                )
                action_result["action"] = "date_range"
        elif action_name == "field_click":
            scope = "auto" if in_frame else "top"
            action_result = _click_field_raw(field_name or "", in_frame=in_frame,
                                             timeout=min(timeout, 5), scope=target_meta.get("scope", scope),
                                             select_index=int(target_meta.get("select_index", 0) or 0))
            if action_result.get("control_type") and "control_type" not in target_meta:
                target_meta["control_type"] = action_result["control_type"]
        elif action_name == "table_cell":
            action_result = _click_table_cell_raw(
                row=row, col=col, column_title=column_title, kind=kind,
                table_index=table_index, icon_name=icon_name,
            )
            action_result["action"] = "table_cell"
        elif action_name == "select_option":
            action_result = page_model.select_option(field_name=field_name or "",
                                                     option_text=option_text or "",
                                                     timeout=min(timeout, 5))
            action_result["action"] = "select_option"
        elif action_name == "press_key":
            action_result = _press_key_raw(tab, key or "", modifiers=modifiers)
            action_result["action"] = "press_key"
        else:
            action_result = {"ok": False, "reason": "unsupported action: %s" % action}
    except Exception as e:
        action_result = {"ok": False, "reason": str(e)}
    finally:
        action_result = table_facade.attach_cleanup(action_result, cleanup)
        if observe_policy["skip_observe"]:
            signal = {"type": "skipped", "reason": observe_start_result["reason"],
                      "events": []}
        elif not action_result.get("ok"):
            signal = observe.observe_wait(timeout=0, include_snapshot=False,
                                          detail=observe_policy["detail"],
                                          native_wait=recipe_context.requires_native_actions())
            signal["skipped_reason"] = "action_failed"
        else:
            signal = observe.observe_wait(
                timeout=observe_policy["timeout"],
                include_snapshot=observe_policy["include_snapshot"],
                detail=observe_policy["detail"],
                native_wait=recipe_context.requires_native_actions(),
            )

    after = None
    if capture_after:
        after = page_model.capture_page_model(include_filters=False, include_table_data=False,
                                             max_table_rows=20, max_elements=80)
    result = {
        "ok": bool(action_result.get("ok")),
        "observe_start": observe_start_result,
        "observe_policy": observe_policy,
        "target": target_meta or None,
        "action": action_result,
        "signal": signal,
        "before": before,
        "after": after,
    }
    screenshot_path = None
    if flow_evidence.wants_screenshot():
        try:
            screenshot_path = resource_store.resolve_path(
                default_name="flow_step_%d.png" % time.time_ns(),
                category="screenshots",
            )
            browser_session.get_tab().get_screenshot(path=screenshot_path)
        except Exception as exc:
            logger.debug("flow screenshot failed: %s", exc)
            screenshot_path = None
    flow_step = flow_evidence.record_exploration(
        {
            "action": action, "target": target, "locator": locator, "x": x, "y": y,
            "row": row, "col": col, "column_title": column_title, "kind": kind,
            "table_index": table_index, "icon_name": icon_name, "option_text": option_text,
            "field_name": field_name, "text": text, "date": date,
            "start_date": start_date, "end_date": end_date, "key": key, "modifiers": modifiers,
            "by_js": by_js, "in_frame": in_frame, "timeout": timeout,
            "signals": signals, "listen_targets": listen_targets, "expect": expect,
            "observe_mode": observe_mode, "detail": detail, "clean_overlays": clean_overlays,
        },
        result,
        elapsed_ms=int((time.perf_counter() - flow_started) * 1000),
        screenshot=screenshot_path,
    )
    if isinstance(flow_step, dict) and flow_step.get("ok") is False:
        result = dict(result)
        result["ok"] = False
        result["reason"] = "evidence recording failed: %s" % flow_step.get("reason", "unknown error")
        result["flow_recording"] = flow_step
    elif flow_step:
        result["flow_step"] = flow_step
    return result


def find_elements(locator: str, in_frame: bool = True, timeout: float = 5) -> dict:
    """查找所有匹配元素（eles 封装）。返回元素数量及文本预览。

    locator 为 DrissionPage 定位符，支持完整语法：
      #id / .cls / tag:div / t:div / text:文 / tx=文
      css:.cls / c:.cls / xpath://div / x://div
      @attr=v / @@k1=v@@k2=v / @|k1=v@|k2=v / @!id=v
      ax:@role=btn@name=xxx
    纯文本自动模糊匹配。简化写法：text→tx, tag→t, css→c, xpath→x
    文档：https://drissionpage.cn/browser_control/get_elements/syntax
    """
    els = browser_session.find_all(locator, in_frame=in_frame, timeout=timeout)
    if not els:
        return {"ok": True, "count": 0, "elements": []}
    previews = []
    for i, e in enumerate(els):
        if i >= 50:
            break
        item = {
            "tag": e.tag,
            "text": (e.text or "")[:100],
            "attrs": {k: e.attr(k) for k in ("id", "class", "href", "src", "title", "aria-label", "placeholder") if e.attr(k)}
        }
        try:
            vx, vy = e.rect.viewport_midpoint
            item.update({
                "cx": round(float(vx), 1),
                "cy": round(float(vy), 1),
                "viewportX": round(float(vx), 1),
                "viewportY": round(float(vy), 1),
                "coordinate_space": "top-viewport",
                "coord_source": "DrissionPage.Element.rect.viewport_midpoint",
            })
        except Exception:
            pass
        previews.append(item)
    return {"ok": True, "count": len(els), "elements": previews, "_truncated": len(els) > 50}


def get_element_coords(xpath: str, index: int = 1, timeout: float = 5) -> dict:
    """通过 XPath 定位元素并返回顶层视口绝对中心坐标。

    使用 DrissionPage 原生 rect.viewport_midpoint，已自动叠加 iframe 偏移，
    返回的 cx/cy 可直接用于 click_xy。

    Args:
        xpath: XPath 定位表达式（如 "//button[contains(@class, 'ant-btn-danger')]"）
        index: 第几个匹配元素（默认 1）
        timeout: 查找超时秒数

    Returns:
        {ok, cx, cy, tag, text, xpath}
    """
    return page_model.get_element_coords(xpath=xpath, index=index, timeout=timeout)


def find_static(locator: str = None, in_frame: bool = True, timeout: float = 5, index: int = 1) -> dict:
    """查找元素的静态版本（s_ele 封装）。速度极快，适合批量数据采集。

    静态元素（SessionElement）由纯文本构造，只能读取属性/文本，不能交互。
    locator 为 None 时返回页面/iframe 本身的静态副本。
    index 指定第几个匹配（1 开始，负数倒数）。
    """
    ele = browser_session.find_static(locator, in_frame=in_frame, timeout=timeout, index=index)
    if not ele:
        return {"ok": False, "reason": "元素未找到: %s" % (locator or "(self)")}
    return {
        "ok": True,
        "tag": ele.tag,
        "text": (ele.text or "")[:200],
        "html": (ele.html or "")[:500],
        "attrs": {k: ele.attr(k) for k in ("id", "class", "href", "src", "title", "aria-label", "placeholder", "data-*") if ele.attr(k)}
    }

def find_batch(locators: list[str], in_frame: bool = True, timeout: float = 5,
               any_one: bool = True, first_ele: bool = True) -> dict:
    """同时匹配多个定位符（find 封装）。一次调用查找多个不同元素。

    any_one=True: 返回第一个有结果的定位符及其元素
    any_one=False: 返回每个定位符的结果 dict
    first_ele=True: 每个定位符取第一个元素，False 取所有
    """
    res = browser_session.find_batch(locators, in_frame=in_frame, timeout=timeout,
                                     any_one=any_one, first_ele=first_ele)
    if any_one:
        loc, ele = res
        if loc is None:
            return {"ok": False, "reason": "所有定位符均未匹配", "matched_locator": None}
        return {
            "ok": True,
            "matched_locator": loc,
            "tag": ele.tag if hasattr(ele, "tag") else "",
            "text": (ele.text or "")[:200] if hasattr(ele, "text") else ""
        }
    else:
        result = {}
        for loc, ele in res.items():
            if ele is None:
                result[loc] = None
            elif isinstance(ele, list):
                result[loc] = [{"tag": e.tag, "text": (e.text or "")[:100]} for e in ele[:20]]
            else:
                result[loc] = {"tag": ele.tag, "text": (ele.text or "")[:200]}
        return {"ok": True, "results": result}


def get_frame(locator, timeout: float = 5) -> dict:
    """按定位符/序号/id/name 获取 iframe/frame 元素（get_frame 封装）。

    locator 可以是：
      - 定位字符串（如 '#iframe1', 't:iframe', 'c:iframe'）
      - 序号 int（1 开始，负数倒数）
      - id 属性内容
      - name 属性内容
    返回 ChromiumFrame 对象，可在其内部继续查找元素。
    """
    fr = browser_session.get_frame_by_locator(locator, timeout=timeout)
    if not fr:
        return {"ok": False, "reason": "iframe 未找到: %s" % locator}
    return {"ok": True, "url": getattr(fr, "url", "") or "", "title": getattr(fr, "title", "") or ""}


def _resolve_and_click(locator: str, in_frame: bool = True, by_js: bool = False,
                       timeout: float = 5) -> dict:
    """Resolve a locator, click it using actions chain (move_to + click)."""
    raw_text = _extract_text_locator(locator)
    ele = None
    native_only = recipe_context.requires_native_actions()
    if native_only and by_js:
        return {"ok": False, "locator": locator,
                "reason": "run_test_cases 禁止 by_js 点击"}
    if raw_text:
        for candidate in _clickable_text_locators(raw_text):
            ele = browser_session.find(candidate, in_frame=in_frame, timeout=min(timeout, 1.0),
                                       wait_clickable=False)
            if ele:
                break
    if not ele:
        ele = browser_session.find(locator, in_frame=in_frame, timeout=timeout,
                                   wait_clickable=False)
    if not ele and raw_text and in_frame:
        # 1. @@text(): 搜索整个元素内所有文本（非仅直接文本节点）
        if " " in raw_text:
            ele = browser_session.find(f"@@text():{raw_text}", in_frame=in_frame,
                                       timeout=timeout, wait_clickable=False)
        # 2. tx: 简化写法
        if not ele:
            ele = browser_session.find(f"tx:{raw_text}", in_frame=in_frame,
                                       timeout=timeout, wait_clickable=False)
        # 3. tx= 精确匹配
        if not ele:
            ele = browser_session.find(f"tx={raw_text}", in_frame=in_frame,
                                       timeout=timeout, wait_clickable=False)
        # 4. JS 降级只供交互探索使用；正式回放必须可由 DrissionPage 定位。
        if not ele and not native_only:
            res = _click_text_by_js(locator, in_frame=in_frame)
            if res and res.get("ok"):
                return {"ok": True, "locator": locator, "fallback": "js-text"}
            return {
                "ok": False,
                "reason": "元素未找到: %s（等待 %.1fs，DP 降级+JS 均失败）" % (locator, timeout),
            }
    if not ele:
        return {"ok": False, "reason": "元素未找到: %s（等待 %.1fs）" % (locator, timeout)}
    try:
        waiter = getattr(ele, "wait", None)
        if waiter is not None:
            waiter.clickable(timeout=timeout, wait_stop=True, raise_err=False)
        clicked = ele.click(by_js=by_js, timeout=_short_click_timeout(timeout), wait_stop=False)
        if clicked is False:
            raise RuntimeError("DrissionPage click returned False")
    except Exception as e:
        # Formal execution deliberately does not fall back from an element
        # click to coordinates or JavaScript. VTable uses its dedicated facade.
        if native_only:
            return {"ok": False, "locator": locator,
                    "reason": "DrissionPage 原生元素点击失败: %s" % e}

        # Fallback 1: Try coordinate-based click
        if not by_js:
            try:
                mp = ele.rect.viewport_midpoint
                cx, cy = float(mp[0]), float(mp[1])
                if cx > 0 and cy > 0:
                    logger.info(
                        "Actions click failed on %s, trying coordinate-click fallback at (%.1f, %.1f)",
                        locator, cx, cy,
                    )
                    tab = browser_session.get_tab()
                    tab.actions.move_to((cx, cy)).click()
                    return {
                        "ok": True,
                        "locator": locator,
                        "fallback": "coordinate-click",
                        "coords": [cx, cy],
                        "native_error": str(e),
                    }
            except Exception as coord_err:
                logger.debug("Coordinate click fallback failed: %s", coord_err)

        # Fallback 2: Try JS click directly on the found element. Formal
        # execution deliberately stops after native coordinate fallback.
        if not by_js and not native_only:
            try:
                logger.info("Coordinate click failed or skipped, trying direct JS click on %s", locator)
                ele.click(by_js=True)
                return {
                    "ok": True,
                    "locator": locator,
                    "fallback": "direct-js",
                    "native_error": str(e),
                }
            except Exception as js_err:
                logger.debug("Direct JS click fallback failed: %s", js_err)

        # Fallback 3: Try text search JS click outside formal execution only.
        if not by_js and not native_only:
            res = _click_text_by_js(locator, in_frame=in_frame)
            if res and res.get("ok"):
                return {
                    "ok": True,
                    "locator": locator,
                    "fallback": "js-text",
                    "native_error": str(e),
                }
        return {"ok": False, "locator": locator, "reason": "点击失败: %s" % e}
    return {"ok": True, "locator": locator}


def click(locator: str, in_frame: bool = True, by_js: bool = False, timeout: float = 5,
          clean_overlays: bool = True) -> dict:
    """点击元素。locator 为 DrissionPage 定位符(#id/.cls/@attr=v/text:文/css:选择器)。
    in_frame 优先在活动 iframe 内查找。by_js=True 用 JS 点击(绕过遮挡)。timeout 为查找超时秒数。
    clean_overlays=True 时先清理上一操作残留的 Ant notification/message，避免干扰本次点击观察。

    定位语法参考：
      #id / .cls / tag:div / t:div / text:文 / tx=文
      css:.cls / c:.cls / xpath://div / x://div
      @attr=v / @@k1=v@@k2=v / @|k1=v@|k2=v / @!id=v
      ax:@role=btn@name=xxx
    简化写法：text→tx, tag→t, css→c, xpath→x
    文档：https://drissionpage.cn/browser_control/get_elements/syntax
    """
    cleanup = table_facade.pre_click_cleanup(clean_overlays)
    result = _resolve_and_click(locator, in_frame=in_frame, by_js=by_js, timeout=timeout)
    return table_facade.attach_cleanup(result, cleanup)


def click_xy(x: float, y: float, hover_first: bool = True, duration: float = 0.3,
             clean_overlays: bool = True, times: int = 1) -> dict:
    """按有限顶层视口坐标点击；``times`` 仅接受 1 到 10。"""
    if (
        isinstance(x, bool) or isinstance(y, bool)
        or not isinstance(x, (int, float)) or not isinstance(y, (int, float))
        or not math.isfinite(float(x)) or not math.isfinite(float(y))
    ):
        return {"ok": False, "reason": "x 和 y 必须是有限数值"}
    if isinstance(times, bool) or not isinstance(times, int) or not 1 <= times <= 10:
        return {"ok": False, "reason": "times 必须是 1 到 10 的整数"}
    if (
        isinstance(duration, bool) or not isinstance(duration, (int, float))
        or not math.isfinite(float(duration)) or duration < 0
    ):
        return {"ok": False, "reason": "duration 必须是非负有限数值"}

    x, y, duration = float(x), float(y), float(duration)
    cleanup = table_facade.pre_click_cleanup(clean_overlays)
    try:
        actions = browser_session.get_tab().actions.move_to(
            (x, y), duration=duration if hover_first else 0,
        )
        if times > 1:
            actions.click(times=times)
        else:
            actions.click()
    except Exception as exc:
        return table_facade.attach_cleanup(
            {"ok": False, "reason": "坐标点击失败: %s" % exc}, cleanup,
        )
    return table_facade.attach_cleanup({"ok": True, "x": x, "y": y, "times": times}, cleanup)

def select_date_range(field_name: str, start_date: str, end_date: str,
                      in_frame: bool = True, timeout: float = 8,
                      select_index: int = 0, scope: str = "auto") -> dict:
    """Backward-compatible recipe shim; the public MCP entry is ``set_date``."""
    return set_date(
        field_name, start_date=start_date, end_date=end_date,
        in_frame=in_frame, timeout=timeout, select_index=select_index, scope=scope,
    )


def input(locator: str, text: str, in_frame: bool = True, clear: bool = True, timeout: float = 5) -> dict:
    """定位一次后通过 DrissionPage 元素 input 写入，并返回实际值。"""
    timeout = max(float(timeout or 0), 0.0)
    element = browser_session.find(
        locator, in_frame=in_frame, timeout=timeout, wait_clickable=False
    )
    if not element:
        return {"ok": False, "reason": "元素未找到: %s（等待 %.1fs）" % (locator, timeout)}
    value = "" if text is None else str(text)
    try:
        _native_element_input(element, value, clear, timeout)
        try:
            actual = element.property("value")
        except Exception:
            actual = element.attr("value")
        return {
            "ok": True,
            "locator": locator,
            "method": "element.input",
            "actual_value": actual,
            "matches_requested": None if actual is None else str(actual) == value,
        }
    except Exception as exc:
        return {"ok": False, "locator": locator, "reason": "DrissionPage input failed: %s" % exc}


def insert_text(text: str) -> dict:
    """向当前焦点元素插入文本；活动业务 iframe 优先。"""
    tab = browser_session.get_tab()
    target = browser_session.get_active_frame_ro(tab, timeout=0.2) or tab
    try:
        target.actions.input("" if text is None else str(text))
        return {"ok": True, "scope": "iframe" if target is not tab else "top"}
    except Exception as exc:
        return {"ok": False, "reason": "DrissionPage actions.input failed: %s" % exc}


def hover(locator: str = None, x: float = None, y: float = None, in_frame: bool = True,
          duration: float = 0.3, timeout: float = 5) -> dict:
    """通过元素或完整坐标执行 DrissionPage 悬停。"""
    tab = browser_session.get_tab()
    duration = max(float(duration or 0), 0.0)
    timeout = max(float(timeout or 0), 0.0)
    try:
        if locator:
            element = browser_session.find(
                locator, in_frame=in_frame, timeout=timeout, wait_clickable=False
            )
            if not element:
                return {"ok": False, "reason": "元素未找到: %s（等待 %.1fs）" % (locator, timeout)}
            tab.actions.move_to(element, duration=duration)
            return {"ok": True, "locator": locator}
        if x is None or y is None:
            return {"ok": False, "reason": "locator 或 x/y 必须提供"}
        tab.actions.move_to((float(x), float(y)), duration=duration)
        return {"ok": True, "x": float(x), "y": float(y)}
    except Exception as exc:
        return {"ok": False, "reason": "DrissionPage hover failed: %s" % exc}


def screenshot(path: str = None, locator: str = None, in_frame: bool = True,
               timeout: float = 5) -> dict:
    """截图并将输出限制在资源目录；可截取匹配元素或当前 Tab。"""
    tab = browser_session.get_tab()
    timeout = max(float(timeout or 0), 0.0)
    resolved_path = resource_store.resolve_path(
        path,
        default_name="shot_%d.png" % int(time.time()),
        category="screenshots",
    )
    try:
        if locator:
            element = browser_session.find(
                locator, in_frame=in_frame, timeout=timeout, wait_clickable=False
            )
            if not element:
                return {"ok": False, "reason": "元素未找到: %s（等待 %.1fs）" % (locator, timeout)}
            element.get_screenshot(path=resolved_path)
        else:
            tab.get_screenshot(path=resolved_path)
        if not os.path.isfile(resolved_path):
            return {"ok": False, "reason": "截图未生成文件", "path": resolved_path}
        return {
            "ok": True,
            "path": os.path.abspath(resolved_path),
            "size": os.path.getsize(resolved_path),
        }
    except Exception as exc:
        return {"ok": False, "reason": "DrissionPage screenshot failed: %s" % exc}


