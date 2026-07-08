"""Network timeline recorder built on DrissionPage 4.2 Listener."""
import json
import os
import time

import browser_session
import resource_store


_session = {
    "active": False,
    "started_at": None,
    "targets": None,
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


def _json_safe(value, max_chars: int = 12000):
    try:
        json.dumps(value, ensure_ascii=False)
        out = value
    except (TypeError, ValueError):
        out = str(value)
    if isinstance(out, str) and len(out) > max_chars:
        return out[:max_chars] + "...(_truncated)"
    return out


def packet_to_dict(packet, max_body_chars: int = 12000) -> dict:
    request = getattr(packet, "request", None)
    response = getattr(packet, "response", None)
    headers = {}
    api_target = ""
    post_data = None
    if request:
        try:
            headers = dict(request.headers) if hasattr(request, "headers") else {}
        except Exception:
            headers = {}
        api_target = headers.get("api-target", "") or headers.get("Api-Target", "")
        post_data = getattr(request, "postData", None)

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


def start(targets=None, method: str = None) -> dict:
    tab = browser_session.get_tab()
    urls = _normalize_targets(targets)
    try:
        tab.listen.stop()
    except Exception:
        pass
    tab.listen.set_res_type.all()
    effective_method = _set_http_method(tab.listen, method)
    tab.listen.start(urls=urls)

    _session.clear()
    _session.update({
        "active": True,
        "started_at": time.time(),
        "targets": urls,
        "method": effective_method,
        "packets": [],
    })
    return {"ok": True, "session": "active", "targets": urls,
            "method": effective_method, "resource_type": "ALL"}


def stop(timeout: float = 3.0, max_packets: int = 50,
         fit_count: bool = False, max_body_chars: int = 12000) -> dict:
    if not _session.get("active"):
        return {"ok": False, "reason": "no active network record session; call network_record_start first"}
    tab = browser_session.get_tab()
    packets = []
    try:
        caught = tab.listen.wait(count=max_packets, timeout=timeout, fit_count=fit_count)
        if caught:
            if not isinstance(caught, list):
                caught = [caught]
            packets = [packet_to_dict(p, max_body_chars=max_body_chars) for p in caught[:max_packets]]
    finally:
        try:
            tab.listen.stop()
        except Exception:
            pass

    _session["active"] = False
    _session["packets"] = packets
    return {
        "ok": True,
        "packets": packets,
        "count": len(packets),
        "targets": _session.get("targets"),
        "method": _session.get("method"),
        "elapsedMs": int((time.time() - (_session.get("started_at") or time.time())) * 1000),
    }


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
