"""Converged legacy Bootstrap Table + layer.js dialog flow.

Canonical path for jQuery/AdminLTE modules (账号管理等)::

  clear shade → select bootstrap row → (optional toolbar click) → enter layer iframe

Public facades (``table_action`` / ``click_table_cell`` / ``scan_layer_content``)
remain the primary MCP surface; this module is the shared implementation spine.
"""

from __future__ import annotations

import logging

from . import bootstrap_table, browser_session, layer_modal

logger = logging.getLogger("drissionpage-mcp")


def clear_blocking_shade(timeout: float = 2.0) -> dict:
    """Step 1: clear leftover layer shade that blocks the bootstrap table."""
    return layer_modal.clear_layer_shade(timeout=timeout)


def select_bootstrap_row(
    row: int = 0,
    table_index: int = 0,
    select_all: bool = False,
    close_shade: bool = True,
) -> dict:
    """Step 2: select a bootstrap row (optionally clearing shade first)."""
    return bootstrap_table.click_bootstrap_row_selection(
        row=row,
        table_index=table_index,
        select_all=select_all,
        close_shade=close_shade,
    )


def click_frame_toolbar(text: str, timeout: float = 5.0) -> dict:
    """Click a toolbar/button by visible text in the active business iframe."""
    label = str(text or "").strip()
    if not label:
        return {"ok": False, "reason": "toolbar text is required"}
    timeout = max(float(timeout or 0), 0.5)
    # Prefer button/link with exact or contains text (common: 编辑 / 新增 / 删除).
    locators = [
        "text:%s" % label,
        "tx:%s" % label,
        "xpath://button[contains(normalize-space(.), %s)]"
        % _xpath_literal(label),
        "xpath://a[contains(normalize-space(.), %s)]" % _xpath_literal(label),
        "xpath://*[@role='button' and contains(normalize-space(.), %s)]"
        % _xpath_literal(label),
    ]
    ele = None
    used = None
    for loc in locators:
        try:
            ele = browser_session.find(
                loc, in_frame=True, timeout=min(timeout, 1.5), wait_clickable=False,
            )
        except Exception:
            ele = None
        if ele is not None:
            used = loc
            break
    if ele is None:
        return {"ok": False, "reason": "toolbar control not found: %s" % label}
    try:
        try:
            ele.wait.clickable(timeout=min(timeout, 2.0), wait_stop=True, raise_err=False)
        except Exception:
            pass
        ele.click(by_js=False, timeout=2, wait_stop=False)
        return {"ok": True, "text": label, "locator": used}
    except Exception as exc:
        try:
            ele.click(by_js=True, timeout=2)
            return {
                "ok": True,
                "text": label,
                "locator": used,
                "fallback": "by_js",
            }
        except Exception as exc2:
            return {
                "ok": False,
                "reason": "toolbar click failed: %s / %s" % (exc, exc2),
                "text": label,
            }


def enter_layer_iframe(
    layer_index: int = -1,
    timeout: float = 3.0,
    scan: bool = True,
) -> dict:
    """Step 3: wait for layer shell and enter nested iframe (or same-document content)."""
    shell = layer_modal.wait_layer_shell(timeout=timeout)
    if not shell.get("ok"):
        return shell
    resolved = layer_modal.get_layer_content_frame(
        layer_index=layer_index, timeout=max(timeout, 1.0),
    )
    # Never return live ChromiumFrame in structured results for logging/tests.
    public = {
        k: v for k, v in resolved.items() if k != "content_frame"
    }
    if not resolved.get("ok"):
        public["shell"] = shell
        return public
    public["shell"] = shell
    public["entered"] = True
    if scan:
        scanned = layer_modal.scan_layer_content(
            layer_index=layer_index, timeout=timeout,
        )
        public["scan"] = {
            k: v for k, v in scanned.items()
            if k not in {"content_frame"}
        }
        if not scanned.get("ok"):
            public["ok"] = False
            public["reason"] = scanned.get("reason") or "layer content scan failed"
    return public


def select_row_open_layer(
    row: int = 0,
    table_index: int = 0,
    select_all: bool = False,
    close_shade: bool = True,
    toolbar_text: str = "编辑",
    layer_timeout: float = 3.0,
    scan_layer: bool = True,
) -> dict:
    """Full legacy path: shade → bootstrap row → toolbar → layer iframe.

    Typical 账号管理 edit flow. ``toolbar_text`` empty skips the button click
    (caller already opened the dialog).
    """
    steps = {}

    shade = clear_blocking_shade(timeout=2.0) if close_shade else {
        "ok": True, "had_shade": False, "closed": False, "skipped": True,
    }
    steps["shade"] = shade
    if close_shade and not shade.get("ok"):
        return {"ok": False, "reason": shade.get("reason") or "shade clear failed", "steps": steps}

    # Shade already cleared — avoid double-close races in row selection.
    selection = select_bootstrap_row(
        row=row,
        table_index=table_index,
        select_all=select_all,
        close_shade=False,
    )
    steps["selection"] = selection
    if not selection.get("ok"):
        return {
            "ok": False,
            "reason": selection.get("reason") or "row selection failed",
            "steps": steps,
        }

    toolbar = {"ok": True, "skipped": True}
    if str(toolbar_text or "").strip():
        toolbar = click_frame_toolbar(toolbar_text, timeout=5.0)
        steps["toolbar"] = toolbar
        if not toolbar.get("ok"):
            return {
                "ok": False,
                "reason": toolbar.get("reason") or "toolbar click failed",
                "steps": steps,
            }
    else:
        steps["toolbar"] = toolbar

    layer = enter_layer_iframe(
        timeout=layer_timeout,
        scan=scan_layer,
    )
    steps["layer"] = layer
    if not layer.get("ok"):
        return {
            "ok": False,
            "reason": layer.get("reason") or "enter layer iframe failed",
            "steps": steps,
        }

    return {
        "ok": True,
        "kind": "bootstrap+layer",
        "row": row,
        "table_index": table_index,
        "toolbar_text": toolbar_text,
        "layerKind": (layer.get("meta") or {}).get("layerKind")
        or (layer.get("shell") or {}).get("meta", {}).get("layerKind"),
        "content_kind": layer.get("content_kind"),
        "fieldCount": (layer.get("scan") or {}).get("fieldCount"),
        "buttonCount": (layer.get("scan") or {}).get("buttonCount"),
        "steps": steps,
    }


def _xpath_literal(value: str) -> str:
    text = str(value or "")
    if "'" not in text:
        return "'%s'" % text
    if '"' not in text:
        return '"%s"' % text
    return "concat(%s)" % ", \"'\", ".join("'%s'" % part for part in text.split("'"))
