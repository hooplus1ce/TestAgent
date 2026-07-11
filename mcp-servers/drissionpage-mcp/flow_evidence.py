"""Structured, sanitized evidence captured from real browser explorations."""
from __future__ import annotations

import copy
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import resource_store


SCHEMA_VERSION = "1.0"
_SENSITIVE_KEY = re.compile(r"(?:authorization|cookie|password|passwd|token|secret|session)", re.I)
_MAX_TEXT = 12_000
_lock = threading.RLock()
_active_flow: dict | None = None
_last_flow: dict | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize(value, key: str = ""):
    """Remove credentials and bound arbitrary response content before persistence."""
    if _SENSITIVE_KEY.search(str(key or "")):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        text = value[:_MAX_TEXT] + "...(_truncated)" if len(value) > _MAX_TEXT else value
        if str(key).lower() in {"url", "uri"}:
            parts = urlsplit(text)
            if parts.query:
                query = [(name, "[REDACTED]" if _SENSITIVE_KEY.search(name) else item)
                         for name, item in parse_qsl(parts.query, keep_blank_values=True)]
                return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        try:
            decoded = json.loads(text)
        except (TypeError, ValueError):
            return text
        if isinstance(decoded, (dict, list)):
            return json.dumps(sanitize(decoded), ensure_ascii=False, separators=(",", ":"))
        return text
    return value


def start(module: str, flow_name: str = "exploration", capture_screenshots: bool = True,
          scenario_type: str = "功能测试", risk_type: str = "正常路径",
          destructive: bool = False, cleanup_strategy: str = "") -> dict:
    """Start one evidence flow. A second active flow is rejected to preserve ordering."""
    if not str(module or "").strip():
        return {"ok": False, "reason": "module is required"}
    with _lock:
        global _active_flow
        if _active_flow is not None:
            return {"ok": False, "reason": "an evidence flow is already active", "flow_id": _active_flow["flow_id"]}
        _active_flow = {
            "schema_version": SCHEMA_VERSION,
            "flow_id": uuid.uuid4().hex,
            "module": str(module).strip(),
            "flow_name": str(flow_name or "exploration").strip() or "exploration",
            "scenario_type": str(scenario_type or "功能测试").strip(),
            "risk_type": str(risk_type or "正常路径").strip(),
            "destructive": bool(destructive),
            "cleanup_strategy": str(cleanup_strategy or "").strip(),
            "started_at": _now(),
            "capture_screenshots": bool(capture_screenshots),
            "page_states": [],
            "steps": [],
        }
        return {"ok": True, "flow_id": _active_flow["flow_id"], "module": _active_flow["module"]}


def is_active() -> bool:
    with _lock:
        return _active_flow is not None


def wants_screenshot() -> bool:
    with _lock:
        return bool(_active_flow and _active_flow.get("capture_screenshots"))


def status() -> dict:
    with _lock:
        flow = _active_flow or _last_flow
        if flow is None:
            return {"ok": True, "active": False, "flow": None}
        return {
            "ok": True,
            "active": _active_flow is not None,
            "flow": {
                "flow_id": flow["flow_id"],
                "module": flow["module"],
                "flow_name": flow["flow_name"],
                "scenario_type": flow.get("scenario_type", "功能测试"),
                "risk_type": flow.get("risk_type", "正常路径"),
                "destructive": bool(flow.get("destructive")),
                "step_count": len(flow["steps"]),
                "page_state_count": len(flow.get("page_states", [])),
                "started_at": flow["started_at"],
            },
        }


def _network_events(signal: dict) -> list[dict]:
    events = []
    for event in (signal or {}).get("events", []) + [signal or {}]:
        if isinstance(event, dict) and event.get("type") == "network":
            packet = event.get("packet") or {
                key: event.get(key) for key in ("url", "method", "api_target", "status", "post_data")
                if event.get(key) not in (None, "")
            }
            events.append(sanitize(packet))
    unique = []
    seen = set()
    for event in events:
        key = (event.get("method"), event.get("url"), event.get("status"), event.get("timestamp"))
        if key not in seen:
            unique.append(event)
            seen.add(key)
    return unique


def _element_reference(parameters: dict, target: dict) -> dict:
    if target:
        return sanitize(target)
    reference = {
        key: parameters.get(key)
        for key in ("target", "locator", "field_name", "row", "col", "column_title", "kind", "icon_name")
        if parameters.get(key) not in (None, "", "auto")
    }
    return sanitize(reference)


def record_page_state(label: str, page_model: dict) -> dict | None:
    """Append the current element/DOM/table asset model to an active flow."""
    with _lock:
        if _active_flow is None:
            return None
        sequence = len(_active_flow["page_states"]) + 1
        _active_flow["page_states"].append({
            "sequence": sequence,
            "captured_at": _now(),
            "label": str(label or "page_state"),
            "page_model": sanitize(page_model),
        })
        return {"flow_id": _active_flow["flow_id"], "page_state_sequence": sequence}


def record_exploration(parameters: dict, result: dict, elapsed_ms: int,
                       screenshot: str | None = None) -> dict | None:
    """Append one aggregate explore_action result to the active evidence flow."""
    with _lock:
        if _active_flow is None:
            return None
        action_result = result.get("action") or {}
        target = result.get("target") or {}
        signal = result.get("signal") or {}
        sequence = len(_active_flow["steps"]) + 1
        artifacts = {}
        if screenshot:
            artifacts["screenshot"] = screenshot
        if result.get("before") is not None:
            artifacts["before_page_model"] = sanitize(result["before"])
        if result.get("after") is not None:
            artifacts["after_page_model"] = sanitize(result["after"])
        if _active_flow.get("page_states"):
            artifacts["page_state_sequence"] = _active_flow["page_states"][-1]["sequence"]
        step = {
            "sequence": sequence,
            "captured_at": _now(),
            "action": {
                "name": action_result.get("action") or parameters.get("action"),
                "input": sanitize(copy.deepcopy(parameters)),
            },
            "element": _element_reference(parameters, target),
            "observation": sanitize(signal),
            "network": _network_events(signal),
            "artifacts": artifacts,
            "performance": {"elapsed_ms": max(0, int(elapsed_ms or 0))},
            "outcome": "passed" if action_result.get("ok") else "failed",
            "error": sanitize(action_result.get("reason", "")),
        }
        _active_flow["steps"].append(step)
        reference = {"flow_id": _active_flow["flow_id"], "sequence": sequence}
        if screenshot:
            reference["screenshot"] = screenshot
        return reference


def stop(filename: str | None = None, cleanup_from_sequence: int | None = None) -> dict:
    """Finalize the active flow and persist it through the MCP resource store."""
    with _lock:
        global _active_flow, _last_flow
        if _active_flow is None:
            return {"ok": False, "reason": "no active evidence flow"}
        flow = _active_flow
        if cleanup_from_sequence is not None:
            try:
                cleanup_sequence = int(cleanup_from_sequence)
            except (TypeError, ValueError):
                return {"ok": False, "reason": "cleanup_from_sequence must be an integer"}
            if cleanup_sequence < 1 or cleanup_sequence > len(flow["steps"]):
                return {"ok": False, "reason": "cleanup_from_sequence is outside the recorded step range"}
            flow["cleanup_from_sequence"] = cleanup_sequence
        flow["finished_at"] = _now()
        flow["duration_ms"] = int((time.time() - datetime.fromisoformat(flow["started_at"]).timestamp()) * 1000)
        safe_name = filename or "flow_%s.json" % flow["flow_id"]
        path = resource_store.resolve_path(safe_name, category="flows")
        with open(path, "w", encoding="utf-8") as output:
            json.dump(sanitize(flow), output, ensure_ascii=False, indent=2)
        _last_flow = flow
        _active_flow = None
        return {"ok": True, "flow_id": flow["flow_id"], "step_count": len(flow["steps"]), "saved_to": path}


def load(filename: str) -> dict:
    """Load a persisted flow from the resource directory."""
    try:
        content = resource_store.read_text_resource(filename)
        data = json.loads(content)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": str(exc)}
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return {"ok": False, "reason": "unsupported flow evidence schema"}
    return {"ok": True, "flow": data}
