"""Network timeline recorder built on DrissionPage Listener.

Compatible with the official stable 4.1.1.4 listener API and the 4.2 beta
split filter API.
"""
import json
import os
import time

from ..resources import resource_store
from . import browser_session

_SENSITIVE_HEADERS = frozenset({
    "authorization", "cookie", "proxy-authorization", "set-cookie", "x-api-key"
})
_NOISE_NETWORK_SUFFIXES = ("account.json",)

_session = {
    "active": False,
    "started_at": None,
    "targets": None,
    "started_monotonic": None,
    "method": None,
    "packets": [],
}


def _normalize_targets(targets):
    if targets is None:
        return True
    if isinstance(targets, str):
        values = [t.strip() for t in targets.split(",") if t.strip()]
        return values or True
    return targets


def _listener_uses_split_filters(listener) -> bool:
    """Return True for DrissionPage 4.2+ listener.set_method/set_res_type API."""
    return hasattr(listener, "set_method") and hasattr(listener, "set_res_type")


def _legacy_method_arg(method: str = None):
    if not method:
        return ("GET", "POST"), "GET,POST"
    methods = [m.strip().upper() for m in str(method).split(",") if m.strip()]
    if not methods:
        return ("GET", "POST"), "GET,POST"
    if len(methods) == 1 and methods[0] == "ALL":
        return True, "ALL"
    return tuple(methods), ",".join(methods)


def _set_http_method(listener, method: str = None) -> str:
    if not method:
        listener.set_method.GET(only=True).POST()
        return "GET,POST"
    methods = [m.strip().upper() for m in str(method).split(",") if m.strip()]
    if not methods:
        listener.set_method.GET(only=True).POST()
        return "GET,POST"
    if len(methods) == 1 and methods[0] == "ALL":
        listener.set_method.all()
        return "ALL"
    try:
        getattr(listener.set_method, methods[0])(only=True)
        for method_name in methods[1:]:
            getattr(listener.set_method, method_name)()
    except (AttributeError, TypeError, ValueError):
        listener.set_method.GET(only=True).POST()
        return "GET,POST"
    return ",".join(methods)


def start_http_listener(listener, targets=None, method: str = None) -> tuple[str, str]:
    """Start HTTP listener using the installed DrissionPage API shape.

    Returns (effective_method, resource_type).
    """
    urls = _normalize_targets(targets)
    if _listener_uses_split_filters(listener):
        listener.set_res_type.all()
        effective_method = _set_http_method(listener, method)
        listener.start(urls=urls)
        return effective_method, "ALL"

    method_arg, effective_method = _legacy_method_arg(method)
    listener.start(targets=urls, method=method_arg, res_type=True)
    return effective_method, "ALL"


def start_ws_listener(listener, targets=None) -> tuple[str, str, str | None]:
    """Start WebSocket listener where supported.

    4.2 can filter WS with set_res_type.ws(only=True). 4.1 exposes ws_only;
    URL filtering may be ignored by DrissionPage when ws_only=True, so a hint is
    returned for callers.
    """
    urls = _normalize_targets(targets)
    if _listener_uses_split_filters(listener):
        listener.set_method.all()
        listener.set_res_type.ws(only=True)
        listener.start(urls=urls)
        return "ALL", "WebSocket", None

    try:
        listener.start(targets=urls, method=True, res_type=True, ws_only=True)
        return "ALL", "WebSocket", "legacy ws_only may ignore URL targets"
    except TypeError:
        try:
            listener.include_ws(True, ws_only=True)
        except Exception:
            pass
        listener.start(targets=urls)
        return "ALL", "WebSocket", "legacy include_ws fallback; URL filtering may be limited"


def _json_safe(value, max_chars: int = 12000):
    """保留小型 JSON 值；大型结构也按序列化字符数硬截断。"""
    max_chars = max(int(max_chars or 0), 0)
    try:
        encoded = json.dumps(value, ensure_ascii=False)
        output = value
    except (TypeError, ValueError):
        output = str(value)
        encoded = output
    if len(encoded) <= max_chars:
        return output
    if isinstance(output, str):
        return output[:max_chars] + "...(_truncated)"
    return encoded[:max_chars] + "...(_truncated JSON)"


def _safe_headers(raw_headers) -> dict:
    try:
        headers = dict(raw_headers or {})
    except Exception:
        return {}
    return {
        key: "<redacted>" if str(key).lower() in _SENSITIVE_HEADERS else value
        for key, value in headers.items()
    }


def is_noise_packet(packet) -> bool:
    """Return whether a raw listener packet or serialized packet is not business evidence."""
    if isinstance(packet, dict):
        values = (packet.get("url"), packet.get("api_target"))
    else:
        request = getattr(packet, "request", None)
        try:
            headers = dict(getattr(request, "headers", {}) or {})
        except Exception:
            headers = {}
        values = (
            getattr(packet, "url", ""),
            headers.get("api-target", "") or headers.get("Api-Target", ""),
        )
    return any(
        str(value or "").split("?", 1)[0].lower().rstrip("/").endswith(suffix)
        for value in values
        for suffix in _NOISE_NETWORK_SUFFIXES
    )


def wait_for_business_packets(listener, count: int = 1, timeout: float = 10.0,
                              fit_count: bool = False) -> list:
    """Consume listener packets until a business packet arrives or the deadline expires."""
    wanted = max(int(count or 0), 1)
    deadline = time.monotonic() + max(float(timeout or 0), 0.0)
    accepted = []
    discarded = 0
    first_wait = True
    while len(accepted) < wanted and discarded < 100:
        remaining = max(float(timeout or 0), 0.0) if first_wait else max(deadline - time.monotonic(), 0.0)
        first_wait = False
        if remaining <= 0:
            break
        try:
            try:
                caught = listener.wait(
                    count=max(wanted - len(accepted), 1), timeout=remaining,
                    fit_count=fit_count, raise_err=False,
                )
            except TypeError:
                caught = listener.wait(
                    count=max(wanted - len(accepted), 1), timeout=remaining,
                    fit_count=fit_count,
                )
        except Exception:
            break
        if not caught:
            break
        raw_packets = caught if isinstance(caught, list) else [caught]
        for item in raw_packets:
            if is_noise_packet(item):
                discarded += 1
                continue
            accepted.append(item)
            if len(accepted) >= wanted:
                break
        # Preserve the historical fit_count=False behavior once a business packet arrives.
        if accepted and not fit_count:
            break
    return accepted


def packet_to_dict(packet, max_body_chars: int = 12000) -> dict:
    request = getattr(packet, "request", None)
    response = getattr(packet, "response", None)
    headers = {}
    api_target = ""
    post_data = None
    if request:
        try:
            raw_headers = dict(request.headers) if hasattr(request, "headers") else {}
        except Exception:
            raw_headers = {}
        api_target = raw_headers.get("api-target", "") or raw_headers.get("Api-Target", "")
        headers = _safe_headers(raw_headers)
        post_data = getattr(request, "postData", getattr(request, "post_data", None))

    body = getattr(response, "body", None) if response else None
    return {
        "url": getattr(packet, "url", "") or "",
        "method": getattr(packet, "method", "") or "",
        "api_target": api_target,
        "post_data": _json_safe(post_data, max_body_chars),
        "status": getattr(response, "status", None) if response else None,
        "body": _json_safe(body, max_body_chars),
        "headers": headers,
        "timestamp": getattr(packet, "timestamp", None),
    }


def ws_packet_to_dict(packet, max_payload_chars: int = 12000) -> dict:
    return {
        "is_sent": getattr(packet, "is_sent", None),
        "payload": _json_safe(getattr(packet, "data", None), max_payload_chars),
        "url": getattr(packet, "url", None),
        "timestamp": getattr(packet, "timestamp", None),
    }


def start(targets=None, method: str = None) -> dict:
    tab = browser_session.get_tab()
    urls = _normalize_targets(targets)
    _session["active"] = False
    try:
        tab.listen.stop()
    except Exception:
        pass
    try:
        effective_method, resource_type = start_http_listener(tab.listen, urls, method)
    except Exception as exc:
        return {"ok": False, "reason": "network listener start failed: %s" % exc}

    _session.clear()
    _session.update({
        "active": True,
        "started_at": time.time(),
        "started_monotonic": time.monotonic(),
        "targets": urls,
        "method": effective_method,
        "packets": [],
    })
    return {"ok": True, "session": "active", "targets": urls,
            "method": effective_method, "resource_type": resource_type}


def stop(timeout: float = 3.0, max_packets: int = 50,
         fit_count: bool = False, max_body_chars: int = 12000) -> dict:
    if not _session.get("active"):
        return {"ok": False, "reason": "no active network record session; call network_record_start first"}
    tab = browser_session.get_tab()
    max_packets = max(int(max_packets or 0), 1)
    timeout = max(float(timeout or 0), 0.0)
    packets = []
    wait_error = None
    try:
        caught = wait_for_business_packets(
            tab.listen, count=max_packets, timeout=timeout, fit_count=fit_count,
        )
        packets = [
            packet_to_dict(packet, max_body_chars=max_body_chars)
            for packet in caught[:max_packets]
        ]
    except Exception as exc:
        wait_error = str(exc)
    finally:
        try:
            tab.listen.stop()
        except Exception:
            pass
        _session["active"] = False
        _session["packets"] = packets

    result = {
        "ok": wait_error is None,
        "packets": packets,
        "count": len(packets),
        "targets": _session.get("targets"),
        "method": _session.get("method"),
        "elapsedMs": int(
            (time.monotonic() - (_session.get("started_monotonic") or time.monotonic())) * 1000
        ),
    }
    if wait_error:
        result["reason"] = "network listener wait failed: %s" % wait_error
    return result


def export(filename: str = None) -> dict:
    packets = _session.get("packets") or []
    if not filename:
        filename = "network_record_%d.json" % int(time.time())
    full_path = resource_store.resolve_path(filename, category="network")
    payload = {
        "ok": True,
        "started_at": _session.get("started_at"),
        "targets": _session.get("targets"),
        "method": _session.get("method"),
        "count": len(packets),
        "packets": packets,
    }
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return {"ok": True, "saved_to": os.path.abspath(full_path), "count": len(packets)}


# ==================== MCP-facing listen helpers ====================

def listen_start(targets=None, method: str = None) -> dict:
    """Start one-shot HTTP listen (listen_start / listen_wait / listen_stop)."""
    tab = browser_session.get_tab()
    urls = _normalize_targets(targets)
    try:
        tab.listen.stop()
    except Exception:
        pass
    try:
        effective_method, resource_type = start_http_listener(tab.listen, urls, method)
    except Exception as exc:
        return {"ok": False, "reason": "监听启动失败: %s" % exc}
    return {
        "ok": True,
        "targets": urls,
        "method": effective_method,
        "resource_type": resource_type,
    }


def listen_wait(count: int = 1, timeout: float = 10, fit_count: bool = False) -> dict:
    """Wait for packets after listen_start."""
    tab = browser_session.get_tab()
    try:
        packets = wait_for_business_packets(
            tab.listen, count=count, timeout=timeout, fit_count=fit_count,
        )
    except Exception as exc:
        return {"ok": False, "reason": "监听等待失败: %s" % exc}
    if not packets:
        return {
            "ok": False,
            "reason": "timeout",
            "hint": "确认 listen_start 的 targets 是否正确，或增大 timeout",
        }
    if count > 1 or len(packets) > 1:
        return {
            "ok": True,
            "packets": [packet_to_dict(item) for item in packets],
        }
    return {"ok": True, **packet_to_dict(packets[0])}


def listen_stop() -> dict:
    """Stop the active one-shot HTTP listener."""
    tab = browser_session.get_tab()
    try:
        tab.listen.stop()
    except Exception as exc:
        return {"ok": False, "reason": "停止监听失败: %s" % exc}
    return {"ok": True}


def listen_ws_start(targets=None) -> dict:
    """Start WebSocket-only listen."""
    tab = browser_session.get_tab()
    urls = _normalize_targets(targets)
    try:
        tab.listen.stop()
    except Exception:
        pass
    try:
        method, resource_type, hint = start_ws_listener(tab.listen, urls)
    except Exception as exc:
        return {"ok": False, "reason": "WebSocket 监听启动失败: %s" % exc}
    result = {
        "ok": True,
        "targets": urls,
        "method": method,
        "resource_type": resource_type,
    }
    if hint:
        result["hint"] = hint
    return result


def listen_ws_wait(count: int = 1, timeout: float = 10, fit_count: bool = False) -> dict:
    """Wait for WebSocket packets after listen_ws_start."""
    tab = browser_session.get_tab()
    try:
        try:
            packet = tab.listen.wait(
                count=count, timeout=timeout, fit_count=fit_count, raise_err=False,
            )
        except TypeError:
            packet = tab.listen.wait(
                count=count, timeout=timeout, fit_count=fit_count,
            )
    except Exception as exc:
        return {"ok": False, "reason": "WebSocket 监听等待失败: %s" % exc}
    if not packet:
        return {
            "ok": False,
            "reason": "timeout",
            "hint": "确认 listen_ws_start 的 targets 是否正确，或增大 timeout",
        }
    if isinstance(packet, list):
        return {
            "ok": True,
            "packets": [ws_packet_to_dict(item) for item in packet],
        }
    return {"ok": True, **ws_packet_to_dict(packet)}
