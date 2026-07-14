"""页面族探测：区分壳层 AntD SPA、遗留 jQuery/Bootstrap 页、VTable 业务页。

探测结果驱动表格/弹层 adapter 路由，避免 auto 扫描只认 VTable/ant-table 而漏掉
Bootstrap Table 与 layer.js。
"""
from __future__ import annotations

import json
import logging

from . import ui_contract, ui_contract_legacy as legacy

logger = logging.getLogger("drissionpage-mcp")

FAMILY_SHELL = "scm_admin_shell"
FAMILY_LEGACY = "legacy_jq_bootstrap"
FAMILY_VTABLE = "antd_vtable"
FAMILY_MIXED = "mixed"
FAMILY_UNKNOWN = "unknown"

_DETECT_JS = r"""
return (function(){
  var out = {
    href: location.href || '',
    title: document.title || '',
    signals: {},
    counts: {},
    globals: {}
  };
  function count(sel){
    try { return document.querySelectorAll(sel).length; } catch(e){ return 0; }
  }
  function has(sel){ return count(sel) > 0; }

  out.counts.ant_layout = count('.ant-layout, .ant-menu, .ant-tabs');
  out.counts.ant_select_v3 = count('.ant-select-selection');
  out.counts.ant_select_v4 = count('.ant-select-selector');
  out.counts.ant_table = count('.ant-table-wrapper, .ant-table');
  out.counts.page_query = count('.page-query, .legions-pro-quick-filter');
  out.counts.vtable = count('.vtable, [class*="vtable"]');
  out.counts.bootstrap_table = count('.bootstrap-table, .fixed-table-container');
  out.counts.bootstrap_btn = count('.btn-default, .btn-primary, .form-control');
  out.counts.bootstrap_select = count('.bootstrap-select, select.selectpicker');
  out.counts.layer = count('.layui-layer');
  out.counts.layui_shade = count('.layui-layer-shade');
  out.counts.adminlte = count('body.skin-blue, body.sidebar-mini');
  out.counts.glyphicon = count('.glyphicon');

  out.signals.ant_shell = out.counts.ant_layout > 0;
  out.signals.ant_table = out.counts.ant_table > 0;
  out.signals.quick_filter = out.counts.page_query > 0;
  out.signals.vtable = out.counts.vtable > 0;
  out.signals.bootstrap_table = out.counts.bootstrap_table > 0;
  out.signals.bootstrap = out.counts.bootstrap_btn > 0 || out.counts.bootstrap_select > 0;
  out.signals.layer = out.counts.layer > 0 || typeof window.layer !== 'undefined';
  out.signals.adminlte = out.counts.adminlte > 0 ||
    !!(document.body && /skin-blue|sidebar-mini/.test(document.body.className || ''));

  try {
    out.globals.jQuery = !!(window.jQuery || window.$);
    out.globals.jQueryVersion = (window.jQuery && window.jQuery.fn && window.jQuery.fn.jquery) || null;
    out.globals.layer = typeof window.layer !== 'undefined';
    out.globals.layerVersion = (window.layer && (window.layer.v || window.layer.version)) || null;
    out.globals.bootstrapTablePlugin = !!(window.jQuery && window.jQuery.fn && window.jQuery.fn.bootstrapTable);
    out.globals.selectpickerPlugin = !!(window.jQuery && window.jQuery.fn && window.jQuery.fn.selectpicker);
    out.globals.VTable = typeof window.VTable !== 'undefined';
    out.globals.ReactStore = typeof window.ReactStore !== 'undefined';
    out.globals.webpackChunk = Object.keys(window).filter(function(k){
      return /^webpackChunk/i.test(k);
    }).slice(0, 5);
  } catch(e) {}

  // React fiber / container markers (shell)
  try {
    var body = document.body;
    var keys = body ? Object.keys(body) : [];
    out.signals.react_container = keys.some(function(k){
      return k.indexOf('__reactContainer') === 0 || k.indexOf('__reactFiber') === 0;
    });
  } catch(e) { out.signals.react_container = false; }

  out.signals.legacy_mpa = /\.do(\?|$)/.test(location.pathname || location.href || '') ||
    out.signals.bootstrap_table || out.signals.adminlte;
  out.signals.wms_spa = /scm-wms/i.test(location.href || '') ||
    /#\//.test(location.href || '') && out.signals.vtable;

  return JSON.stringify(out);
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


def _score_family(probe: dict) -> tuple[str, float, list[str]]:
    """根据单文档探针打分，返回 (family, confidence, reasons)."""
    signals = probe.get("signals") or {}
    counts = probe.get("counts") or {}
    globals_ = probe.get("globals") or {}
    reasons: list[str] = []
    scores = {
        FAMILY_SHELL: 0.0,
        FAMILY_LEGACY: 0.0,
        FAMILY_VTABLE: 0.0,
    }

    if signals.get("ant_shell") or signals.get("react_container") or globals_.get("ReactStore"):
        scores[FAMILY_SHELL] += 0.55
        reasons.append("ant/react shell markers")
    if counts.get("ant_select_v3", 0) > 0:
        scores[FAMILY_SHELL] += 0.1
    if globals_.get("webpackChunk"):
        scores[FAMILY_SHELL] += 0.1

    if signals.get("bootstrap_table") or globals_.get("bootstrapTablePlugin"):
        scores[FAMILY_LEGACY] += 0.55
        reasons.append("bootstrap-table")
    if signals.get("bootstrap") or signals.get("adminlte"):
        scores[FAMILY_LEGACY] += 0.2
        reasons.append("bootstrap/adminlte")
    if signals.get("layer") or globals_.get("layer"):
        scores[FAMILY_LEGACY] += 0.15
        reasons.append("layer.js")
    if globals_.get("jQuery"):
        scores[FAMILY_LEGACY] += 0.1
    if signals.get("legacy_mpa"):
        scores[FAMILY_LEGACY] += 0.15
        reasons.append("legacy mpa url/body")

    if signals.get("vtable") or globals_.get("VTable"):
        scores[FAMILY_VTABLE] += 0.6
        reasons.append("vtable")
    if signals.get("quick_filter"):
        scores[FAMILY_VTABLE] += 0.2
        reasons.append("legions quick-filter")
    if signals.get("ant_table") and not signals.get("bootstrap_table"):
        scores[FAMILY_VTABLE] += 0.15
        reasons.append("ant-table")

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_name, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0

    if best_score < 0.25:
        return FAMILY_UNKNOWN, best_score, reasons
    if second_score >= 0.35 and abs(best_score - second_score) < 0.2:
        return FAMILY_MIXED, best_score, reasons
    return best_name, min(best_score, 0.99), reasons


def _adapters_for(family: str, probe: dict) -> list[str]:
    signals = probe.get("signals") or {}
    adapters: list[str] = []
    if family in (FAMILY_VTABLE, FAMILY_MIXED) or signals.get("vtable"):
        adapters.append("vtable")
    if family in (FAMILY_VTABLE, FAMILY_MIXED) or signals.get("ant_table"):
        adapters.append("ant_table")
    if family in (FAMILY_LEGACY, FAMILY_MIXED) or signals.get("bootstrap_table"):
        adapters.append("bootstrap_table")
    if family in (FAMILY_LEGACY, FAMILY_MIXED) or signals.get("layer"):
        adapters.append("layer_modal")
    if family in (FAMILY_LEGACY, FAMILY_MIXED) or signals.get("bootstrap"):
        adapters.append("bootstrap_select")
    if family in (FAMILY_SHELL, FAMILY_MIXED) or signals.get("ant_shell"):
        adapters.append("antd_shell")
    # 保序去重
    seen = set()
    ordered = []
    for name in adapters:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _preferred_table_kind(family: str, probe: dict) -> str:
    signals = probe.get("signals") or {}
    if signals.get("vtable"):
        return "vtable"
    if signals.get("bootstrap_table"):
        return "bootstrap"
    if signals.get("ant_table"):
        return "html"
    if family == FAMILY_LEGACY:
        return "bootstrap"
    if family == FAMILY_VTABLE:
        return "vtable"
    return "auto"


def normalize_table_kind(kind: str) -> str:
    """规范化表格 kind 别名（供 facade / 单测共用）。"""
    kind = (kind or "auto").lower().replace("_", "-")
    if kind in {"bootstrap-table", "bootstrap_table", "bt"}:
        return "bootstrap"
    return kind if kind in {"auto", "vtable", "html", "bootstrap"} else "auto"


def auto_table_scan_order(preferred: str = "auto") -> list[str]:
    """根据 preferred_table_kind 生成 auto 扫描顺序。"""
    preferred = normalize_table_kind(preferred)
    order = ["vtable", "bootstrap", "html"]
    if preferred in {"vtable", "bootstrap", "html"}:
        return [preferred] + [item for item in order if item != preferred]
    return order


def detect_page_family(tab=None, include_top: bool = True, include_frame: bool = True) -> dict:
    """探测 top / active iframe 的页面族，并给出表格/弹层 adapter 建议。

    Returns:
        {
          ok, family, confidence, preferred_table_kind, adapters,
          scope, scopes: {top?, iframe?}, frameworks
        }
    """
    # 延迟导入避免循环依赖
    from ..services import browser_session

    try:
        tab = tab or browser_session.get_tab()
    except Exception as exc:
        return {"ok": False, "reason": "浏览器未连接: %s" % exc}

    scopes = {}
    errors = []

    if include_top and tab is not None:
        try:
            scopes["top"] = _parse_json(tab.run_js(_DETECT_JS), {})
        except Exception as exc:
            errors.append("top: %s" % exc)

    frame = None
    if include_frame:
        try:
            frame = browser_session.get_active_frame_ro(tab, timeout=0.5)
            if frame is None:
                frame = browser_session.get_active_frame(tab)
        except Exception:
            frame = None
        if frame is not None:
            try:
                scopes["iframe"] = _parse_json(frame.run_js(_DETECT_JS), {})
            except Exception as exc:
                errors.append("iframe: %s" % exc)

    if not scopes:
        return {
            "ok": False,
            "reason": "无法在 top/iframe 执行页面族探测",
            "errors": errors,
        }

    # 业务决策优先 active iframe；壳层信号保留在 top。
    primary_scope = "iframe" if "iframe" in scopes else "top"
    primary = scopes[primary_scope]
    family, confidence, reasons = _score_family(primary)

    # 若 iframe 未知但 top 明确是壳，标注 shell + 子页未知
    top_probe = scopes.get("top") or {}
    top_family, top_conf, _ = _score_family(top_probe) if top_probe else (FAMILY_UNKNOWN, 0.0, [])
    if family == FAMILY_UNKNOWN and top_family == FAMILY_SHELL:
        family = FAMILY_SHELL
        confidence = top_conf
        primary_scope = "top"
        reasons = ["fallback to top shell"]

    # iframe legacy + top shell 是最常见组合
    if (
        family == FAMILY_LEGACY
        and top_family == FAMILY_SHELL
        and primary_scope == "iframe"
    ):
        reasons.append("embedded in scm admin shell")

    preferred = _preferred_table_kind(family, primary)
    adapters = _adapters_for(family, primary)

    frameworks = {}
    if family in (FAMILY_SHELL, FAMILY_VTABLE, FAMILY_MIXED):
        frameworks["shell"] = dict(ui_contract.FRAMEWORKS)
    if family in (FAMILY_LEGACY, FAMILY_MIXED) or "bootstrap_table" in adapters:
        frameworks["legacy"] = dict(legacy.FRAMEWORKS)

    return {
        "ok": True,
        "family": family,
        "confidence": round(confidence, 3),
        "preferred_table_kind": preferred,
        "adapters": adapters,
        "scope": primary_scope,
        "reasons": reasons,
        "frameworks": frameworks,
        "signals": primary.get("signals") or {},
        "counts": primary.get("counts") or {},
        "globals": primary.get("globals") or {},
        "page": {
            "href": primary.get("href") or "",
            "title": primary.get("title") or "",
            "frame_url": (scopes.get("iframe") or {}).get("href") or "",
            "top_url": (scopes.get("top") or {}).get("href") or getattr(tab, "url", "") or "",
        },
        "scopes": {
            name: {
                "family": _score_family(probe)[0],
                "confidence": round(_score_family(probe)[1], 3),
                "href": probe.get("href") or "",
                "title": probe.get("title") or "",
                "signals": probe.get("signals") or {},
                "counts": probe.get("counts") or {},
            }
            for name, probe in scopes.items()
        },
        "contracts": {
            "shell": {
                "name": ui_contract.CONTRACT_NAME,
                "version": ui_contract.CONTRACT_VERSION,
            },
            "legacy": {
                "name": legacy.CONTRACT_NAME,
                "version": legacy.CONTRACT_VERSION,
            },
        },
        "errors": errors,
    }
