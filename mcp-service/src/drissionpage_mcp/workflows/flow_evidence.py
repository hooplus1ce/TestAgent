"""Structured, sanitized evidence captured from real browser explorations."""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..resources import resource_store
from ..services import network_record


SCHEMA_VERSION = "1.0"
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|password|passwd|token|secret|session|credential|"
    r"api[_-]?key|access[_-]?key|csrf|xsrf|jwt)", re.I,
)
_URL_KEY = re.compile(r"(?:url|uri|href|location)$", re.I)
_MAX_TEXT = 12_000
_MAX_ITEMS = 2_000
_MAX_DEPTH = 20
_MAX_PAGE_STATES = 200
_MAX_STEPS = 1_000
_BEARER_TEXT = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_AUTH_TEXT = re.compile(r"(?i)\b(authorization\s*[:=]\s*)[^\r\n,;&#]+")
_URL_USERINFO = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@")
_SECRET_TEXT = re.compile(
    r"(?i)\b(authorization|cookie|password|passwd|token|secret|session|credential|"
    r"api[_-]?key|access[_-]?key|csrf|xsrf|jwt)\s*[:=]\s*([^\s,;&#]+)"
)
_lock = threading.RLock()
_active_flow: dict | None = None
_last_flow: dict | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_text(value, name: str, default: str = "", max_length: int = 500):
    text = str(value if value is not None else default).strip() or default
    if len(text) > max_length:
        return None, "%s exceeds %d characters" % (name, max_length)
    return text, None

def sanitize(value, key: str = "", _depth: int = 0, _seen=None, _limits=None):
    """递归去除凭据，并限制深度、集合规模及任意文本体积。"""
    max_text, max_items, max_depth = _limits or (_MAX_TEXT, _MAX_ITEMS, _MAX_DEPTH)
    if _SENSITIVE_KEY.search(str(key or "")):
        return "[REDACTED]"
    if _depth > max_depth:
        return "[TRUNCATED_DEPTH]"
    if _seen is None:
        _seen = set()
    if isinstance(value, (dict, list, tuple, set)):
        identity = id(value)
        if identity in _seen:
            return "[CIRCULAR]"
        _seen.add(identity)
        try:
            if isinstance(value, dict):
                result = {}
                for index, (item_key, item_value) in enumerate(value.items()):
                    if index >= max_items:
                        result["_truncated_items"] = len(value) - max_items
                        break
                    result[str(item_key)] = sanitize(
                        item_value, str(item_key), _depth + 1, _seen, _limits
                    )
                return result
            source_items = sorted(value, key=repr) if isinstance(value, set) else list(value)
            items = [
                sanitize(item, "", _depth + 1, _seen, _limits)
                for item in source_items[:max_items]
            ]
            if len(value) > max_items:
                items.append({"_truncated_items": len(value) - max_items})
            return items
        finally:
            _seen.discard(identity)
    if isinstance(value, str):
        text = value[:max_text] + "...(_truncated)" if len(value) > max_text else value
        text = _AUTH_TEXT.sub(lambda match: match.group(1) + "[REDACTED]", text)
        text = _BEARER_TEXT.sub("Bearer [REDACTED]", text)
        text = _SECRET_TEXT.sub(lambda match: "%s=[REDACTED]" % match.group(1), text)
        text = _URL_USERINFO.sub(lambda match: match.group(1) + "%5BREDACTED%5D@", text)
        if _URL_KEY.search(str(key or "")):
            try:
                parts = urlsplit(text)
                query = [
                    (name, "[REDACTED]" if _SENSITIVE_KEY.search(name) else item)
                    for name, item in parse_qsl(parts.query, keep_blank_values=True)
                ]
                fragment = parts.fragment
                if _SENSITIVE_KEY.search(fragment):
                    fragment = "[REDACTED]"
                netloc = re.sub(r"^[^@]+@", "[REDACTED]@", parts.netloc)
                text = urlunsplit((parts.scheme, netloc, parts.path,
                                   urlencode(query), fragment))
            except (TypeError, ValueError):
                pass
        try:
            decoded = json.loads(text)
        except (TypeError, ValueError):
            return text
        if isinstance(decoded, (dict, list)):
            return json.dumps(
                sanitize(decoded, _depth=_depth + 1, _seen=_seen, _limits=_limits),
                ensure_ascii=False, separators=(",", ":"), allow_nan=False,
            )
        return text
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:max_text]


def sanitize_artifact(value):
    """脱敏完整测试产物，同时保留最多十万项的正常覆盖矩阵。"""
    return sanitize(value, _limits=(_MAX_TEXT, 100_000, 100))


def start(module: str, flow_name: str = "exploration", capture_screenshots: bool = True,
          scenario_type: str = "功能测试", risk_type: str = "正常路径",
          destructive: bool = False, cleanup_strategy: str = "") -> dict:
    """Start one evidence flow. A second active flow is rejected to preserve ordering."""
    if not isinstance(capture_screenshots, bool) or not isinstance(destructive, bool):
        return {"ok": False, "reason": "capture_screenshots and destructive must be booleans"}
    values = {}
    for name, value, default, limit in (
        ("module", module, "", 200),
        ("flow_name", flow_name, "exploration", 300),
        ("scenario_type", scenario_type, "功能测试", 100),
        ("risk_type", risk_type, "正常路径", 300),
        ("cleanup_strategy", cleanup_strategy, "", 1_000),
    ):
        text, error = _bounded_text(value, name, default=default, max_length=limit)
        if error:
            return {"ok": False, "reason": error}
        values[name] = text
    if not values["module"]:
        return {"ok": False, "reason": "module is required"}
    with _lock:
        global _active_flow
        if _active_flow is not None:
            return {"ok": False, "reason": "an evidence flow is already active", "flow_id": _active_flow["flow_id"]}
        _active_flow = {
            "schema_version": SCHEMA_VERSION,
            "flow_id": uuid.uuid4().hex,
            "module": values["module"],
            "flow_name": values["flow_name"],
            "scenario_type": values["scenario_type"],
            "risk_type": values["risk_type"],
            "destructive": destructive,
            "cleanup_strategy": values["cleanup_strategy"],
            "started_at": _now(),
            "capture_screenshots": capture_screenshots,
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
        steps = flow.get("steps") if isinstance(flow.get("steps"), list) else []
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
                "step_count": len(steps),
                "passed_step_count": sum(step.get("outcome") == "passed" for step in steps if isinstance(step, dict)),
                "failed_step_count": sum(step.get("outcome") == "failed" for step in steps if isinstance(step, dict)),
                "page_state_count": len(flow.get("page_states", [])),
                "started_at": flow["started_at"],
            },
        }


def _network_events(signal: dict) -> list[dict]:
    signal = signal if isinstance(signal, dict) else {}
    raw_events = signal.get("events") if isinstance(signal.get("events"), list) else []
    events = []
    for event in raw_events + [signal]:
        if not isinstance(event, dict) or event.get("type") != "network":
            continue
        packet = dict(event.get("packet")) if isinstance(event.get("packet"), dict) else {}
        for key in (
            "url", "method", "api_target", "status", "post_data", "request",
            "response", "body", "headers", "timestamp",
        ):
            if key not in packet and event.get(key) not in (None, ""):
                packet[key] = event.get(key)
        if network_record.is_noise_packet(packet):
            continue
        sanitized = sanitize(packet)
        if isinstance(sanitized, dict):
            events.append(sanitized)
    unique = []
    seen = set()
    for event in events:
        identity = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
        if identity not in seen:
            unique.append(event)
            seen.add(identity)
    return unique


def _element_reference(parameters: dict, target: dict) -> dict:
    if isinstance(target, dict) and target:
        return sanitize(target)
    parameters = parameters if isinstance(parameters, dict) else {}
    reference = {
        key: parameters.get(key)
        for key in ("target", "locator", "field_name", "row", "col", "column_title", "kind", "icon_name")
        if parameters.get(key) not in (None, "", "auto")
    }
    return sanitize(reference)


def record_page_state(label: str, page_model: dict) -> dict | None:
    """Append the current element/DOM/table asset model to an active flow."""
    if not isinstance(page_model, dict):
        return {"ok": False, "reason": "page_model must be an object"}
    label_text, error = _bounded_text(label, "label", default="page_state", max_length=200)
    if error:
        return {"ok": False, "reason": error}
    with _lock:
        if _active_flow is None:
            return None
        if len(_active_flow["page_states"]) >= _MAX_PAGE_STATES:
            return {"ok": False, "reason": "flow exceeds %d page states" % _MAX_PAGE_STATES}
        sequence = len(_active_flow["page_states"]) + 1
        _active_flow["page_states"].append({
            "sequence": sequence,
            "captured_at": _now(),
            "label": label_text,
            "page_model": sanitize(page_model),
        })
        return {"flow_id": _active_flow["flow_id"], "page_state_sequence": sequence}


def record_exploration(parameters: dict, result: dict, elapsed_ms: int,
                       screenshot: str | None = None) -> dict | None:
    """Append one aggregate explore_action result to the active evidence flow."""
    with _lock:
        if _active_flow is None:
            return None
        if len(_active_flow["steps"]) >= _MAX_STEPS:
            return {"ok": False, "reason": "flow exceeds %d steps" % _MAX_STEPS}
        if not isinstance(parameters, dict) or not isinstance(result, dict):
            return {"ok": False, "reason": "parameters and result must be objects"}
        try:
            elapsed = float(elapsed_ms or 0)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "elapsed_ms must be numeric"}
        if not math.isfinite(elapsed):
            return {"ok": False, "reason": "elapsed_ms must be finite"}
        if screenshot is not None and (not isinstance(screenshot, str) or len(screenshot) > 2_000):
            return {"ok": False, "reason": "screenshot must be a path no longer than 2000 characters"}
        action_result = result.get("action") if isinstance(result.get("action"), dict) else {}
        target = result.get("target") if isinstance(result.get("target"), dict) else {}
        signal = result.get("signal") if isinstance(result.get("signal"), dict) else {}
        observe_start = result.get("observe_start") if isinstance(result.get("observe_start"), dict) else {}
        action_name = action_result.get("action") or parameters.get("action")
        if not str(action_name or "").strip():
            return {"ok": False, "reason": "recorded action name is required"}
        overall_ok = result.get("ok")
        if overall_ok is None:
            overall_ok = action_result.get("ok")
        action_ok = action_result.get("ok", overall_ok)
        passed = (overall_ok is True and action_ok is not False
                  and observe_start.get("ok") is not False and signal.get("ok") is not False
                  and str(signal.get("type") or "").lower() not in {"error", "failed"})
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
        error = ""
        if not passed:
            error = (result.get("reason") or action_result.get("reason")
                     or observe_start.get("reason") or signal.get("reason")
                     or "recorded action failed")
        step = {
            "sequence": sequence,
            "captured_at": _now(),
            "action": {
                "name": action_name,
                "input": sanitize(parameters),
            },
            "element": _element_reference(parameters, target),
            "observation": sanitize(signal),
            "network": _network_events(signal),
            "artifacts": artifacts,
            "performance": {"elapsed_ms": max(0, int(elapsed))},
            "outcome": "passed" if passed else "failed",
            "error": sanitize(error),
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
            if isinstance(cleanup_from_sequence, bool):
                return {"ok": False, "reason": "cleanup_from_sequence must be an integer"}
            try:
                numeric_sequence = float(cleanup_from_sequence)
            except (TypeError, ValueError):
                return {"ok": False, "reason": "cleanup_from_sequence must be an integer"}
            if not math.isfinite(numeric_sequence) or not numeric_sequence.is_integer():
                return {"ok": False, "reason": "cleanup_from_sequence must be an integer"}
            cleanup_sequence = int(numeric_sequence)
            if cleanup_sequence < 1 or cleanup_sequence > len(flow["steps"]):
                return {"ok": False, "reason": "cleanup_from_sequence is outside the recorded step range"}
            flow["cleanup_from_sequence"] = cleanup_sequence
        flow["finished_at"] = _now()
        flow["duration_ms"] = max(
            0,
            int((time.time() - datetime.fromisoformat(flow["started_at"]).timestamp()) * 1000),
        )
        safe_name = filename or "flow_%s.json" % flow["flow_id"]
        path = resource_store.resolve_path(safe_name, category="flows")
        persisted = sanitize(flow)
        try:
            resource_store.write_json_atomic(path, persisted)
        except (OSError, TypeError, ValueError) as exc:
            return {"ok": False, "reason": "flow persistence failed: %s" % exc}
        _last_flow = persisted
        _active_flow = None
        return {
            "ok": True,
            "flow_id": flow["flow_id"],
            "step_count": len(flow["steps"]),
            "passed_step_count": sum(step.get("outcome") == "passed" for step in flow["steps"]),
            "failed_step_count": sum(step.get("outcome") == "failed" for step in flow["steps"]),
            "saved_to": path,
        }


def load(filename: str) -> dict:
    """Load a persisted flow from the resource directory."""
    try:
        data = resource_store.read_json_resource(filename)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": str(exc)}
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return {"ok": False, "reason": "unsupported flow evidence schema"}
    return {"ok": True, "flow": data}
