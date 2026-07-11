"""Generate replayable business scenarios from real browser evidence.

The unit of generation is an evidence flow, not an individual click.  Page
states provide the coverage inventory while flow steps provide ordered actions,
real input data, and assertions observed from the browser or network.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from urllib.parse import parse_qsl, urlsplit


_RECIPE_KEYS = {
    "action", "target", "locator", "x", "y", "field_name", "text", "date",
    "start_date", "end_date", "row", "col", "column_title", "kind",
    "table_index", "icon_name", "option_text", "key", "modifiers", "by_js",
    "in_frame", "expect", "signals", "listen_targets", "observe_mode",
}
_DIRECT_RECIPE_ACTIONS = {
    "find_vtable_row", "count_vtable_rows", "get_vtable_row_values",
    "get_table_values", "get_vtable_cell_render_info",
    "vtable_action", "click_table_cell", "observe_snapshot",
}
_DIRECT_RECIPE_KEYS = {
    "field_name", "value", "in_frame", "clear", "timeout", "scope", "select_index",
    "option_text", "column_title", "raw", "match", "header_rows", "save_as",
    "expected_count",
    "key_column", "key_value", "column_titles", "row", "col", "kind", "table_index",
    "icon_name", "target", "icon_index", "hover_first", "duration", "detail",
    "only_visible", "include_table_data",
}
_INPUT_ACTIONS = {"input", "select_option", "set_date", "date_range"}
_MODAL_TYPES = {"interactive", "confirm", "system_confirm", "drawer", "popover"}
_TOAST_TYPES = {"message", "notification"}
_VOLATILE_BODY_KEYS = {
    "id", "uuid", "timestamp", "time", "created_at", "updated_at", "traceid",
    "trace_id", "requestid", "request_id",
}


def _clean_text(value, limit: int = 160) -> str:
    return " ".join(str(value or "").split())[:limit]


def _display_name(value, limit: int = 80) -> str:
    text = _clean_text(value, limit * 2).replace("text:", "", 1)
    # SCM button captions often contain layout spaces, for example ``保 存``.
    if text and not re.search(r"[A-Za-z0-9]", text):
        text = text.replace(" ", "")
    return text[:limit]


def _norm(value) -> str:
    return re.sub(r"[\s：:（）()\[\]【】'\"“”]+", "", str(value or "")).lower()


def _is_network_noise(value) -> bool:
    text = str(value or "").lower()
    return any(noise in text for noise in ("/account.json", "/heartbeat", "/keepalive"))


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _business_name(step: dict) -> str:
    element = step.get("element") or {}
    action_input = (step.get("action") or {}).get("input") or {}
    target = action_input.get("target") if isinstance(action_input.get("target"), dict) else {}
    candidates = (
        element.get("text"), element.get("label"), element.get("field_name"),
        element.get("column_title"), element.get("name"), action_input.get("field_name"),
        action_input.get("column_title"), target.get("text"), target.get("label"),
        target.get("name"),
    )
    for value in candidates:
        if value:
            return _display_name(value)
    locator = action_input.get("locator")
    if locator and str(locator).startswith("text:"):
        return _display_name(locator)
    action = (step.get("action") or {}).get("name") or action_input.get("action")
    return {
        "input": "输入内容", "select_option": "选择选项", "set_date": "选择日期",
        "date_range": "选择日期范围", "table_cell": "数据表格单元格",
        "press_key": "键盘操作", "click": "页面按钮",
    }.get(str(action or "").lower(), "页面操作")


def _action_input(step: dict) -> dict:
    value = (step.get("action") or {}).get("input") or {}
    return value if isinstance(value, dict) else {}


def _action_name(step: dict) -> str:
    return str((step.get("action") or {}).get("name") or _action_input(step).get("action") or "click").lower()


def _page_models(flow: dict):
    for state in _as_list(flow.get("page_states")):
        if not isinstance(state, dict):
            continue
        model = state.get("page_model") or state.get("snapshot") or state.get("model")
        if isinstance(model, dict):
            yield state, model


def _nested_items(section, keys: tuple[str, ...]) -> list:
    if isinstance(section, list):
        return section
    if not isinstance(section, dict):
        return []
    for key in keys:
        value = section.get(key)
        if isinstance(value, list):
            return value
    return []


def _asset(assets: dict, kind: str, name: str, state: dict, metadata: dict | None = None):
    name = _display_name(name)
    if not name:
        return
    key = (kind, _norm(name))
    current = assets.get(key)
    if state.get("evidence_source") == "flow_step":
        evidence = {
            "flow_id": state.get("flow_id", ""),
            "sequence": state.get("sequence"),
            "screenshot": state.get("screenshot"),
        }
    else:
        evidence = {
            "page_state_sequence": state.get("sequence"),
            "page_state_label": state.get("label", ""),
        }
    if current is None:
        current = {
            "asset_type": kind, "name": name, "metadata": dict(metadata or {}),
            "evidence_refs": [evidence],
        }
        assets[key] = current
    else:
        current["metadata"].update({k: v for k, v in (metadata or {}).items() if v not in (None, "", [])})
        if evidence not in current["evidence_refs"]:
            current["evidence_refs"].append(evidence)


def _extract_page_assets(flow: dict) -> list[dict]:
    """Return assets proven by page snapshots or recorded interaction evidence."""
    assets = {}
    for state, model in _page_models(flow):
        filters = _nested_items(model.get("filters"), ("fields", "filters", "items"))
        for item in filters:
            item = item if isinstance(item, dict) else {"field": item}
            name = item.get("field") or item.get("label") or item.get("name") or item.get("placeholder")
            _asset(assets, "filter", name, state, {
                "area": "筛选区",
                "value_mode": item.get("valueMode") or item.get("value_mode") or item.get("type"),
                "operators": item.get("operatorOptions") or item.get("operators") or [],
                "required": bool(item.get("required")),
                "options": item.get("options") or [],
            })

        actions = _nested_items(model.get("actions"), ("actions", "buttons", "items"))
        # Compatibility with early page models that stored ``actions`` as a list.
        if isinstance(model.get("actions"), list):
            actions = model["actions"]
        for item in actions:
            item = item if isinstance(item, dict) else {"text": item}
            name = item.get("text") or item.get("label") or item.get("name") or item.get("title")
            _asset(assets, "action", name, state, {
                "area": item.get("area", "页面"), "disabled": bool(item.get("disabled")),
                "kind": item.get("kind", ""), "has_dropdown": bool(item.get("hasDropdown")),
            })

        column_sources = []
        tables = model.get("tables") or model.get("table") or {}
        if isinstance(tables, dict):
            scan = tables.get("scan") if isinstance(tables.get("scan"), dict) else tables
            column_sources.extend(_nested_items(scan, ("columns", "headers", "items")))
        table_data = model.get("table_data") or {}
        column_sources.extend(_nested_items(table_data, ("columns", "headers")))
        row_count = table_data.get("count") if isinstance(table_data, dict) else None
        seen_columns = set()
        for item in column_sources:
            item = item if isinstance(item, dict) else {"title": item}
            name = item.get("title") or item.get("label") or item.get("name") or item.get("field")
            if not name or _norm(name) in seen_columns:
                continue
            seen_columns.add(_norm(name))
            _asset(assets, "table_column", name, state, {
                "area": "数据表格",
                "editable": bool(item.get("bodyEditable") or item.get("editable")),
                "behavior": item.get("bodyBehavior") or item.get("behavior") or "",
                "row_count": row_count,
            })

        for section_name in ("modals", "drawers"):
            section = model.get(section_name) or {}
            overlays = _nested_items(section, ("overlays", "items", "modals", "drawers"))
            for index, item in enumerate(overlays, start=1):
                item = item if isinstance(item, dict) else {"title": item}
                kind = item.get("type") or item.get("kind") or section_name.rstrip("s")
                name = item.get("title") or item.get("name") or item.get("message") or "%s%d" % (kind, index)
                _asset(assets, "modal", name, state, {
                    "area": "弹窗",
                    "kind": kind, "fields": item.get("fields") or [], "buttons": item.get("buttons") or [],
                })

        for section_name in ("interfaces", "apis", "network"):
            section = model.get(section_name)
            items = _nested_items(section, ("interfaces", "apis", "requests", "packets", "items", "events"))
            if isinstance(section, list):
                items = section
            for item in items:
                item = item if isinstance(item, dict) else {"url": item}
                name = item.get("api_target") or item.get("name") or item.get("url") or item.get("path")
                _asset(assets, "interface", name, state, {
                    "method": item.get("method", ""), "status": item.get("status"),
                    "body": item.get("body") or (item.get("response") or {}).get("body")
                    if isinstance(item.get("response"), dict) else item.get("body"),
                })

    # A later flow may intentionally reuse the initial page inventory instead
    # of recording another full snapshot. Preserve actions and interfaces that
    # were still observed directly, then de-duplicate them with snapshot assets.
    for step in _as_list(flow.get("steps")):
        if not isinstance(step, dict):
            continue
        state = {
            "evidence_source": "flow_step",
            "flow_id": flow.get("flow_id", ""),
            "sequence": step.get("sequence"),
            "screenshot": ((step.get("artifacts") or {}).get("screenshot")),
        }
        action = _action_name(step)
        if action in {"click", "click_xy", "field_click"}:
            name = _business_name(step)
            if name not in {"", "页面操作", "页面按钮"}:
                element = step.get("element") or {}
                _asset(assets, "action", name, state, {
                    "area": element.get("area", "页面"),
                    "kind": ((element.get("matched") or {}).get("kind", "")),
                    "evidence_source": "flow_step",
                })

        observation = step.get("observation") or {}
        network_items = []
        if isinstance(observation, dict) and observation.get("type") == "network":
            network_items.append(observation)
        network_items.extend(item for item in _as_list(step.get("network")) if isinstance(item, dict))
        for item in network_items:
            packet = item.get("packet") if isinstance(item.get("packet"), dict) else {}
            name = item.get("api_target") or packet.get("api_target") or item.get("url") or packet.get("url")
            if not name or _is_network_noise(name):
                continue
            status = item.get("status", packet.get("status"))
            body = packet.get("body") if "body" in packet else item.get("body")
            _asset(assets, "interface", name, state, {
                "method": item.get("method") or packet.get("method", ""),
                "status": status,
                "body": body,
                "area": "接口",
                "evidence_source": "flow_step",
            })
    return list(assets.values())


def _canonical(path: str, operator: str, value, description: str) -> dict:
    return {"path": path, "operator": operator, "value": value, "description": description}


def _trace(sequence, kind: str, assertion: dict, source: str, primary: bool = True, executable: bool = True) -> dict:
    return {
        **assertion, "sequence": sequence, "kind": kind, "source": source,
        "primary": bool(primary), "executable": bool(executable),
    }


def _stable_body_subset(body):
    """Keep deterministic business leaves and avoid volatile identifiers/times."""
    if isinstance(body, list):
        stable_items = []
        for item in body[:5]:
            stable = _stable_body_subset(item)
            if stable not in (None, {}, []):
                stable_items.append(stable)
        return stable_items
    if not isinstance(body, dict):
        return body
    preferred = ("ok", "success", "code", "status", "message", "msg", "result", "count", "total")
    out = {key: body[key] for key in preferred if key in body and not isinstance(body[key], (dict, list))}
    for key, value in body.items():
        if key in out or str(key).lower() in _VOLATILE_BODY_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, dict):
            nested = _stable_body_subset(value)
            if nested not in ({}, None):
                out[key] = nested
        elif isinstance(value, list):
            nested = _stable_body_subset(value)
            if nested:
                out[key] = nested
        if len(out) >= 4:
            break
    return out


def _query_operation(value: str) -> str | None:
    query = urlsplit(str(value or "")).query
    if not query:
        return None
    pairs = parse_qsl(query, keep_blank_values=True)
    blank_keys = [key.strip() for key, item_value in pairs if key.strip() and item_value == ""]
    if blank_keys:
        return blank_keys[-1]
    action_words = ("query", "search", "list", "save", "update", "remove", "delete", "export", "submit", "audit")
    return next((key for key, _ in reversed(pairs) if any(word in key.lower() for word in action_words)), None)


def _network_identity(value: str) -> tuple[str, str]:
    """Return a replay-stable operator/value pair for an API target."""
    text = str(value or "").strip()
    parsed = urlsplit(text)
    operation = _query_operation(text)
    if operation:
        return "contains", operation
    if parsed.scheme and parsed.netloc and parsed.path:
        path = "/" + parsed.path.lstrip("/")
        return "contains", path
    return "equals", text


def _network_parts(event: dict):
    packet = event.get("packet") if isinstance(event.get("packet"), dict) else {}
    response = packet.get("response") if isinstance(packet.get("response"), dict) else {}
    target = event.get("api_target") or packet.get("api_target") or event.get("url") or packet.get("url") or "接口"
    status = event.get("status")
    if status is None:
        status = packet.get("status", response.get("status"))
    if "body" in packet:
        return target, status, packet.get("body"), "signal.packet.body"
    if "body" in response:
        return target, status, response.get("body"), "signal.packet.response.body"
    return target, status, event.get("body"), "signal.body"


def _event_assertions(step: dict) -> tuple[list[dict], list[dict]]:
    """Return executable canonical assertions and richer trace assertions."""
    observation = step.get("observation") or {}
    if not isinstance(observation, dict):
        return [], []
    sequence = step.get("sequence")
    executable, traces, seen = [], [], set()
    secondary_types = {
        event.get("type") for event in _as_list(observation.get("events"))
        if isinstance(event, dict)
        and not (event.get("type") == "network" and _is_network_noise(event.get("api_target") or event.get("url")))
    }
    # Secondary network summaries are deliberately non-executable, so they
    # must not suppress a primary modal assertion.
    has_secondary_outcome = bool(secondary_types & (_TOAST_TYPES | {"url_change", "tab_change"}))

    def add(kind, assertion, source, primary=True, can_execute=True):
        identity = (assertion["path"], assertion["operator"], json.dumps(assertion["value"], ensure_ascii=False, sort_keys=True, default=str))
        if identity in seen:
            return
        seen.add(identity)
        if can_execute:
            executable.append(assertion)
        traces.append(_trace(sequence, kind, assertion, source, primary, can_execute))

    event_type = observation.get("type", "")
    payload = observation.get("payload") if isinstance(observation.get("payload"), dict) else {}
    if event_type in _TOAST_TYPES:
        message = payload.get("message") or observation.get("message") or payload.get("content") or observation.get("content")
        if message:
            path = "signal.payload.message" if payload.get("message") else "signal.message"
            add("toast", _canonical(path, "equals", message, "页面显示“%s”" % _clean_text(message)), "observation")
    elif event_type in _MODAL_TYPES and not has_secondary_outcome:
        title = payload.get("title") or observation.get("title")
        content = payload.get("content") or observation.get("content")
        if title:
            path = "signal.payload.title" if payload.get("title") else "signal.title"
            add("modal", _canonical(path, "equals", title, "页面显示标题为“%s”的弹窗" % _clean_text(title)), "observation")
        elif content:
            path = "signal.payload.content" if payload.get("content") else "signal.content"
            add("modal", _canonical(path, "contains", _clean_text(content), "弹窗内容包含“%s”" % _clean_text(content)), "observation")
    elif event_type == "url_change" and observation.get("url"):
        url = observation["url"]
        add("url", _canonical("signal.url", "equals", url, "页面地址跳转为“%s”" % _clean_text(url)), "observation")
    elif event_type == "tab_change" and observation.get("tab_count") is not None:
        count = observation["tab_count"]
        add("tab", _canonical("signal.tab_count", "equals", count, "浏览器页签数量变为 %s" % count), "observation")
    elif event_type == "network":
        target, status, body, body_path = _network_parts(observation)
        packet = observation.get("packet") if isinstance(observation.get("packet"), dict) else {}
        api_target = observation.get("api_target") or packet.get("api_target")
        if api_target:
            operator, identity = _network_identity(api_target)
            add("network_identity", _canonical(
                "signal.api_target", operator, identity,
                "接口标识匹配“%s”" % _clean_text(identity),
            ), "observation")
        elif observation.get("url") or packet.get("url"):
            url = observation.get("url") or packet.get("url")
            add("network_identity", _canonical(
                "signal.url", "contains", _listener_target_from_url(url),
                "请求地址匹配“%s”" % _clean_text(_listener_target_from_url(url)),
            ), "observation")
        if status is not None:
            add("network_status", _canonical("signal.status", "equals", status, "接口“%s”返回状态码 %s" % (_clean_text(target), status)), "observation")
        subset = _stable_body_subset(body)
        if subset not in (None, {}, []):
            operator = "contains" if isinstance(subset, (dict, list)) else "equals"
            rendered = json.dumps(subset, ensure_ascii=False, separators=(",", ":"), default=str)
            add("network_body", _canonical(body_path, operator, subset, "接口“%s”响应体包含 %s" % (_clean_text(target), rendered[:180])), "observation")
    elif event_type == "structured_result":
        action = _action_name(step)
        if action == "count_vtable_rows" and payload.get("match_count") is not None:
            count = payload["match_count"]
            add("row_count", _canonical(
                "match_count", "equals", count,
                "“%s”匹配业务记录数为 %s" % (_clean_text(payload.get("value")), count),
            ), "structured_result")
        elif action == "get_vtable_row_values" and isinstance(payload.get("values"), dict):
            for column, value in payload["values"].items():
                add("table_data", _canonical(
                    "values.%s" % column, "equals", value,
                    "目标业务记录的“%s”为“%s”" % (_clean_text(column), _clean_text(value)),
                ), "structured_result")

    # Secondary events are compact summaries at replay time.  They can assert
    # API identity/status, but their response bodies are not available there.
    for event in _as_list(observation.get("events")):
        if not isinstance(event, dict):
            continue
        secondary_type = event.get("type")
        if secondary_type == "network" and event_type != "network":
            target, status, _, _ = _network_parts(event)
            target_text = str(target or "").lower()
            if _is_network_noise(target_text):
                continue
            expected = {"type": "network"}
            if event.get("api_target"):
                expected["api_target"] = event["api_target"]
            elif event.get("url"):
                expected["url"] = event["url"]
            if status is not None:
                expected["status"] = status
            if len(expected) > 1:
                description = "接口“%s”返回状态码 %s" % (_clean_text(target), status) if status is not None else "已调用接口“%s”" % _clean_text(target)
                # Secondary network events are timing-sensitive summaries. Keep
                # them traceable, but require a primary packet for replay gating.
                add(
                    "network_status",
                    _canonical("signal.events", "contains", expected, description),
                    "observation.events", primary=False, can_execute=False,
                )
        elif secondary_type in _TOAST_TYPES and event_type not in _TOAST_TYPES:
            message = event.get("message") or event.get("content")
            if message:
                expected = {"type": secondary_type, "message": message}
                add("toast", _canonical(
                    "signal.events", "contains", expected,
                    "页面显示“%s”" % _clean_text(message),
                ), "observation.events", primary=True)

    # Preserve response-body evidence captured in the persisted flow even when
    # it was a secondary event and therefore cannot be replay-asserted directly.
    for packet in _as_list(step.get("network")):
        if not isinstance(packet, dict):
            continue
        target, _, body, _ = _network_parts(packet)
        if _is_network_noise(target):
            continue
        subset = _stable_body_subset(body)
        if subset in (None, {}, []):
            continue
        rendered = json.dumps(subset, ensure_ascii=False, separators=(",", ":"), default=str)
        assertion = _canonical("evidence.network.body", "contains", subset, "接口“%s”真实响应体包含 %s" % (_clean_text(target), rendered[:180]))
        add("network_body", assertion, "persisted_network", primary=False, can_execute=False)
    return executable, traces


def _signal_summary(signal: dict) -> str:
    """Backward-compatible summary helper used by downstream callers/tests."""
    step = {"sequence": 1, "observation": signal if isinstance(signal, dict) else {}}
    assertions, _ = _event_assertions(step)
    return assertions[0]["description"] if assertions else ""


def _observed_test_data(action_input: dict) -> dict:
    """Use only values recorded during the real action; never synthesize inputs."""
    action = str(action_input.get("action") or "").lower()
    field = _display_name(action_input.get("field_name") or action_input.get("column_title"))
    if action == "input" and action_input.get("text") is not None:
        return {field or "输入内容": action_input["text"]}
    if action == "select_option" and action_input.get("option_text") is not None:
        return {field or "下拉选项": action_input["option_text"]}
    if action == "set_date" and action_input.get("date") is not None:
        return {field or "日期": action_input["date"]}
    if action == "date_range" and action_input.get("start_date") is not None and action_input.get("end_date") is not None:
        return {field or "日期范围": "%s 至 %s" % (action_input["start_date"], action_input["end_date"])}
    if action == "table_cell":
        column = field or "数据表格"
        row = int(action_input.get("row", 0)) + 1
        return {column: "第%d行" % row}
    if action in {"find_vtable_row", "count_vtable_rows"} and action_input.get("value") is not None:
        return {field or "表格定位值": action_input["value"]}
    if action == "get_vtable_row_values" and action_input.get("key_value") is not None:
        key_column = _display_name(action_input.get("key_column")) or "表格业务键"
        return {key_column: action_input["key_value"]}
    return {}


def _merge_test_data(steps: list[dict]) -> dict:
    merged = {}
    for step in steps:
        for key, value in _observed_test_data(_action_input(step)).items():
            if key not in merged:
                merged[key] = value
            elif merged[key] != value:
                values = merged[key] if isinstance(merged[key], list) else [merged[key]]
                if value not in values:
                    values.append(value)
                merged[key] = values
    return merged


def _chinese_step(step: dict) -> str:
    action = _action_name(step)
    data = _action_input(step)
    name = _business_name(step)
    if action == "input":
        return "在“%s”输入“%s”" % (name, _clean_text(data.get("text"), 200))
    if action == "select_option":
        return "在“%s”选择“%s”" % (name, _clean_text(data.get("option_text"), 120))
    if action == "set_date":
        return "在“%s”选择日期“%s”" % (name, _clean_text(data.get("date"), 80))
    if action == "date_range":
        return "在“%s”选择日期范围“%s”至“%s”" % (
            name, _clean_text(data.get("start_date"), 80), _clean_text(data.get("end_date"), 80),
        )
    if action == "table_cell":
        return "点击数据表格第%d行的“%s”" % (int(data.get("row", 0)) + 1, name)
    if action == "press_key":
        return "在“%s”按下“%s”键" % (name, _display_name(data.get("key")) or "指定")
    if action == "find_vtable_row":
        return "按“%s”等于“%s”唯一定位业务记录" % (
            _display_name(data.get("column_title")), _clean_text(data.get("value"), 120),
        )
    if action == "count_vtable_rows":
        return "统计“%s”等于“%s”的业务记录数" % (
            _display_name(data.get("column_title")), _clean_text(data.get("value"), 120),
        )
    if action == "get_vtable_row_values":
        return "核对备注为“%s”的业务记录关键数据" % _clean_text(data.get("key_value"), 120)
    if action in {"click_table_cell", "vtable_action"}:
        return "选择动态定位到的目标业务记录"
    if action in {"click", "click_xy", "field_click"}:
        return "点击“%s”" % name
    return "执行“%s”操作" % name


def _recipe_args(step: dict) -> dict:
    raw = _action_input(step)
    action = _action_name(step)
    allowed = _DIRECT_RECIPE_KEYS if action in _DIRECT_RECIPE_ACTIONS else _RECIPE_KEYS
    args = {key: value for key, value in raw.items() if key in allowed and value not in (None, "", [])}
    if action not in _DIRECT_RECIPE_ACTIONS:
        args["action"] = action
    return args


def _listener_target_from_url(url: str):
    """Prefer a gateway operation key over an exact, environment-specific URL."""
    text = str(url or "").strip()
    if not text:
        return None
    operation = _query_operation(text)
    if operation:
        return operation
    parsed = urlsplit(text)
    return parsed.path or text


def _is_replayable(command: dict) -> bool:
    args = command.get("args") or {}
    runner_action = str(command.get("action") or "").lower()
    action = str(args.get("action") or runner_action).lower()
    if runner_action != "explore_action":
        if runner_action == "find_vtable_row":
            return bool(args.get("column_title") and args.get("value") is not None)
        if runner_action == "count_vtable_rows":
            return bool(args.get("column_title") and args.get("value") is not None)
        if runner_action == "get_vtable_row_values":
            return bool(args.get("key_column") and args.get("key_value") is not None and args.get("column_titles"))
        if runner_action in {"click_table_cell", "vtable_action"}:
            return args.get("row") is not None and (args.get("col") is not None or bool(args.get("column_title")))
        if runner_action == "set_field_value":
            return bool(args.get("field_name")) and args.get("value") is not None
        if runner_action == "select_option":
            return bool(args.get("field_name") and args.get("option_text"))
        return runner_action in _DIRECT_RECIPE_ACTIONS
    if action in {"click", "click_xy", "field_click"}:
        return bool(args.get("target") or args.get("locator") or (args.get("x") is not None and args.get("y") is not None) or (action == "field_click" and args.get("field_name")))
    if action == "input":
        return args.get("text") is not None and bool(args.get("locator") or args.get("target") or args.get("field_name"))
    if action == "select_option":
        return bool(args.get("field_name") and args.get("option_text"))
    if action == "set_date":
        return bool(args.get("field_name") and args.get("date"))
    if action == "date_range":
        return bool(args.get("field_name") and args.get("start_date") and args.get("end_date"))
    if action == "table_cell":
        return args.get("row") is not None and (args.get("col") is not None or bool(args.get("column_title")))
    if action == "press_key":
        return bool(args.get("key"))
    return False


def _build_recipe(flow: dict, steps: list[dict]) -> tuple[dict, list[dict]]:
    commands, cleanup, all_traces = [], [], []
    cleanup_from = flow.get("cleanup_from_sequence")
    try:
        cleanup_from = int(cleanup_from) if cleanup_from not in (None, "") else None
    except (TypeError, ValueError):
        cleanup_from = None
    for index, step in enumerate(steps, start=1):
        assertions, traces = _event_assertions(step)
        is_cleanup = cleanup_from is not None and int(step.get("sequence", index)) >= cleanup_from
        for trace in traces:
            trace["phase"] = "cleanup" if is_cleanup else "steps"
        action = _action_name(step)
        args = _recipe_args(step)
        if action not in _DIRECT_RECIPE_ACTIONS and "signals" not in args:
            signal_groups = {
                "modal": ["modal"],
                "toast": ["message", "notification"],
                "network_status": ["network"],
                "network_body": ["network"],
                "network_identity": ["network"],
                "url": ["url"],
                "tab": ["tab"],
            }
            required_signals = []
            for trace in traces:
                if not trace.get("executable"):
                    continue
                for signal in signal_groups.get(trace.get("kind"), []):
                    if signal not in required_signals:
                        required_signals.append(signal)
            if required_signals:
                args["signals"] = required_signals
                if "network" in required_signals and "listen_targets" not in args:
                    observation = step.get("observation") or {}
                    packet = observation.get("packet") if isinstance(observation.get("packet"), dict) else {}
                    listen_target = observation.get("url") or packet.get("url")
                    if not listen_target:
                        for event in _as_list(observation.get("events")) + _as_list(step.get("network")):
                            if isinstance(event, dict) and event.get("url"):
                                listen_target = event["url"]
                                break
                    # The observer only starts its HTTP listener when a target is
                    # supplied.  Listening to all is the honest fallback when an
                    # older evidence flow retained an API header but not its URL.
                    args["listen_targets"] = _listener_target_from_url(listen_target) or True
        command = {
            "sequence": index,
            "action": action if action in _DIRECT_RECIPE_ACTIONS else "explore_action",
            "args": args,
            "expect": {"path": "ok", "equals": True}, "assertions": assertions,
            "business_assertions": traces, "evidence_sequence": step.get("sequence", index),
        }
        (cleanup if is_cleanup else commands).append(command)
        all_traces.extend(traces)
    return {"setup": [], "steps": commands, "cleanup": cleanup}, all_traces


def _area_category(value) -> str | None:
    text = _norm(value)
    if any(word in text for word in ("筛选", "filter", "search")):
        return "filter"
    if any(word in text for word in ("弹窗", "modal", "drawer", "popover")):
        return "modal"
    if any(word in text for word in ("表格", "table", "vtable", "grid")):
        return "table"
    if any(word in text for word in ("页面", "page", "toolbar", "工具栏")):
        return "page"
    return None


def _step_area_category(step: dict) -> str | None:
    action = _action_name(step)
    if action in {
        "find_vtable_row", "count_vtable_rows", "get_vtable_row_values",
        "get_table_values", "get_vtable_cell_render_info", "vtable_action",
        "click_table_cell", "table_cell",
    }:
        return "table"
    element = step.get("element") or {}
    data = _action_input(step)
    target = data.get("target") if isinstance(data.get("target"), dict) else {}
    matched = element.get("matched") if isinstance(element.get("matched"), dict) else {}
    for value in (
        element.get("area"), element.get("scope"), matched.get("area"),
        target.get("area"), target.get("scope"),
    ):
        category = _area_category(value)
        if category:
            return category
    return None


def _step_matching_asset(step: dict, asset: dict) -> bool:
    target = _norm(asset.get("name"))
    asset_type = asset.get("asset_type")
    if asset_type == "interface":
        observation = step.get("observation") or {}
        candidates = []
        if isinstance(observation, dict):
            packet = observation.get("packet") if isinstance(observation.get("packet"), dict) else {}
            candidates.extend((observation.get("api_target"), observation.get("url"), packet.get("api_target"), packet.get("url")))
        for item in _as_list(step.get("network")):
            if isinstance(item, dict):
                candidates.extend((item.get("api_target"), item.get("url")))
        return any(target and (target in _norm(value) or _norm(value) in target) for value in candidates if value)
    if asset_type == "filter" and _step_area_category(step) != "filter":
        return False
    if asset_type == "table_column":
        if _step_area_category(step) != "table":
            return False
        data = _action_input(step)
        observation = step.get("observation") or {}
        payload = observation.get("payload") if isinstance(observation.get("payload"), dict) else {}
        columns = [
            data.get("column_title"), data.get("key_column"), payload.get("column_title"),
        ]
        columns.extend(_as_list(data.get("column_titles")))
        if isinstance(payload.get("values"), dict):
            columns.extend(payload["values"].keys())
        return any(target and target == _norm(value) for value in columns if value)
    if asset_type == "modal":
        observation = step.get("observation") or {}
        payload = observation.get("payload") if isinstance(observation.get("payload"), dict) else {}
        candidates = [payload.get("title"), observation.get("title")]
        snapshot = observation.get("snapshot_after") if isinstance(observation.get("snapshot_after"), dict) else {}
        candidates.extend(
            item.get("title") for item in _as_list(snapshot.get("overlays")) if isinstance(item, dict)
        )
        return any(target and (target in _norm(value) or _norm(value) in target) for value in candidates if value)
    data = _action_input(step)
    candidates = [_business_name(step), data.get("field_name"), data.get("column_title"), data.get("locator")]
    name_matches = any(target and (target in _norm(value) or _norm(value) in target) for value in candidates if value)
    if not name_matches or asset_type != "action":
        return name_matches
    asset_area = _area_category((asset.get("metadata") or {}).get("area"))
    step_area = _step_area_category(step)
    return not (asset_area and step_area and asset_area != step_area)


def _coverage_row(row_id: str, asset: dict, scenario: str, test_type: str, status: str,
                  evidence: str, sequence=None, risk: str = "") -> dict:
    return {
        "coverage_id": row_id, "evidence_sequence": sequence,
        "area": (asset.get("metadata") or {}).get("area", "页面"),
        "asset_type": asset["asset_type"], "function": asset["name"],
        "scenario": scenario, "risk": risk, "test_type": test_type,
        "status": status, "evidence": evidence,
        "asset_evidence_refs": asset.get("evidence_refs", []),
    }


def build_coverage_matrix(flow: dict) -> list[dict]:
    """Build coverage from captured assets and typed business risks."""
    assets = _extract_page_assets(flow)
    steps = sorted(_as_list(flow.get("steps")), key=lambda item: item.get("sequence", 0) if isinstance(item, dict) else 0)
    try:
        cleanup_from = int(flow.get("cleanup_from_sequence")) if flow.get("cleanup_from_sequence") not in (None, "") else None
    except (TypeError, ValueError):
        cleanup_from = None
    business_steps = [
        step for step in steps
        if isinstance(step, dict)
        and (cleanup_from is None or int(step.get("sequence", 0) or 0) < cleanup_from)
    ]
    step_assertions = {
        step.get("sequence"): _event_assertions(step)[0] for step in business_steps
    }
    complete_flow = (
        bool(business_steps)
        and all(step.get("outcome") == "passed" for step in steps)
        and any(step_assertions.values())
    )
    rows, number = [], Counter()

    def add(asset, scenario, test_type, status, evidence, sequence=None, risk=""):
        kind = asset["asset_type"]
        number[kind] += 1
        code = {"filter": "FLT", "action": "ACT", "table_column": "COL", "modal": "MOD", "interface": "API"}.get(kind, "AST")
        rows.append(_coverage_row("%s-%03d" % (code, number[kind]), asset, scenario, test_type, status, evidence, sequence, risk))

    flow_asset = {
        "asset_type": "requirement",
        "name": _display_name(flow.get("flow_name") or flow.get("module") or "业务流程"),
        "metadata": {"area": "业务流程"},
        "evidence_refs": [],
    }
    flow_risks = [item.strip() for item in re.split(r"[+＋,，/]+", str(flow.get("risk_type") or "业务结果")) if item.strip()]
    for risk in flow_risks or ["业务结果"]:
        add(
            flow_asset,
            "完整执行“%s”并校验真实业务结果" % flow_asset["name"],
            str(flow.get("scenario_type") or "功能测试"),
            "已验证" if complete_flow else "待验证",
            "完整业务流全部步骤成功且包含真实业务断言" if complete_flow else "业务流尚未完整通过质量门",
            steps[-1].get("sequence") if steps else None,
            risk,
        )

    for asset in assets:
        kind = asset["asset_type"]
        used = [step for step in steps if _step_matching_asset(step, asset)]
        sequence = used[0].get("sequence") if used else None
        verified = complete_flow and bool(used)
        evidence = "已由完整业务流 %s 的真实反馈验证" % flow.get("flow_name", flow.get("flow_id", "")) if verified else "页面资产已采集，尚未完成真实业务反馈验证"
        if kind == "filter":
            add(asset, "使用页面真实值查询并校验结果", "功能测试", "已验证" if verified else "待验证", evidence, sequence, "正常路径")
            mode = str((asset.get("metadata") or {}).get("value_mode") or "")
            if "date" in mode:
                risk = "起止日期相同、清空及非法日期范围"
            elif "select" in mode:
                risk = "清空选择及不可自由输入约束"
            else:
                risk = "空值、超长值及特殊字符边界"
            add(asset, risk, "边界值测试", "待验证", "由字段类型和值域推导，需在真实页面执行", risk="边界值")
        elif kind == "action":
            observed_abnormal = (
                verified
                and (asset.get("metadata") or {}).get("evidence_source") == "flow_step"
                and str(flow.get("scenario_type") or "") == "异常测试"
            )
            observed_risk = (flow_risks[0] if observed_abnormal and flow_risks else "正常路径")
            observed_type = "异常测试" if observed_abnormal else "功能测试"
            observed_scenario = (
                "在“%s”中执行“%s”并校验异常反馈" % (flow_asset["name"], asset["name"])
                if observed_abnormal else "执行“%s”并校验业务反馈" % asset["name"]
            )
            add(asset, observed_scenario, observed_type, "已验证" if verified else "待验证", evidence, sequence, observed_risk)
            core = _norm(asset["name"])
            if any(word in core for word in ("保存", "提交", "新增", "添加", "删除", "作废", "审核", "审批")):
                add(asset, "必填缺失、无选择或非法状态时操作被拦截", "异常测试", "待验证", "核心写操作的真实反向反馈尚未采集", risk="异常路径")
                add(asset, "重复点击时不产生重复业务数据", "异常测试", "待验证", "防重复提交行为尚未采集", risk="幂等")
            else:
                add(asset, "前置条件不足或禁用状态下不可执行", "异常测试", "待验证", "按钮状态风险尚未验证", risk="权限/状态")
        elif kind == "table_column":
            row_count = (asset.get("metadata") or {}).get("row_count")
            display_verified = row_count is not None and int(row_count or 0) > 0
            add(asset, "页面显示该列且表格已加载数据行", "功能测试", "已验证" if display_verified else "待验证",
                "真实页面快照已采集该列标题及表格 %s 行数据" % row_count if display_verified else "仅采集到列定义，尚缺真实行数据", risk="数据展示")
            add(asset, "空值、长文本及横向滚动时展示稳定", "边界值测试", "待验证", "展示边界尚未逐项验证", risk="边界值")
            metadata = asset.get("metadata") or {}
            if metadata.get("editable") or metadata.get("behavior") not in (None, "", "none"):
                add(asset, "点击或编辑后产生可验证反馈", "功能测试", "已验证" if verified else "待验证", evidence, sequence, "UI交互")
        elif kind == "modal":
            captured = bool(asset.get("evidence_refs"))
            add(asset, "真实操作后弹窗可见且标题已采集", "功能测试",
                "已验证" if captured else "待验证",
                "真实页面快照已捕获可见弹窗" if captured else "尚缺可见弹窗快照", risk="打开")
            add(asset, "取消或关闭后不提交业务数据", "功能测试", "待验证", "关闭路径尚未采集", risk="取消/关闭")
            add(asset, "确认时校验必填字段并反馈结果", "异常测试", "待验证", "确认与字段校验路径尚未采集", risk="字段校验")
        elif kind == "interface":
            metadata = asset.get("metadata") or {}
            has_result = metadata.get("status") is not None or metadata.get("body") not in (None, "", {})
            observed_risk = flow_risks[0] if verified and flow_risks else "数据一致性"
            observed_type = str(flow.get("scenario_type") or "功能测试") if verified else "功能测试"
            add(asset, "真实请求返回已记录的状态码与业务响应体", observed_type,
                "已验证" if verified and has_result else "待验证",
                evidence if verified and has_result else "仅采集到接口标识，尚缺响应证据",
                sequence, observed_risk)
            if observed_risk == "异常路径":
                add(asset, "接口业务成功时页面与数据正确更新", "功能测试", "待验证",
                    "当前仅验证了业务错误响应", risk="正常路径")
            else:
                add(asset, "接口失败、超时或业务错误码时页面正确反馈", "异常测试", "待验证",
                    "接口异常注入尚未执行", risk="异常路径")

    filters = [asset for asset in assets if asset["asset_type"] == "filter"]
    if len(filters) > 1:
        synthetic = {"asset_type": "filter", "name": "组合筛选与重置", "metadata": {"area": "筛选区"}, "evidence_refs": []}
        add(synthetic, "组合多个筛选条件后查询并重置", "功能测试", "待验证", "由多个真实筛选资产推导，需执行组合与重置", risk="组合条件")
    return rows


def _coverage_summary(coverage: list[dict], assets: list[dict]) -> dict:
    status_counts = Counter(row.get("status", "待验证") for row in coverage)
    type_counts = Counter(asset.get("asset_type", "unknown") for asset in assets)
    total = len(coverage)
    verified = status_counts.get("已验证", 0)
    requirements = {
        "label": "需求场景覆盖",
        "definition": "覆盖矩阵中已由真实证据验证的场景数 / 全部候选场景数",
        "verified": verified, "total": total,
        "rate": round(verified * 100 / total, 2) if total else 0.0,
        "by_status": dict(status_counts),
    }

    risk_rows = {}
    for row in coverage:
        risk = _clean_text(row.get("risk"))
        if not risk:
            continue
        risk_rows.setdefault(risk, []).append(row)
    risk_items = {
        risk: {
            "verified": any(row.get("status") == "已验证" for row in rows),
            "verified_scenarios": sum(row.get("status") == "已验证" for row in rows),
            "scenario_total": len(rows),
        }
        for risk, rows in risk_rows.items()
    }
    verified_risks = sum(item["verified"] for item in risk_items.values())
    risks = {
        "label": "风险维度覆盖",
        "definition": "至少有一个真实已验证场景的去重风险维度数 / 全部去重风险维度数",
        "verified": verified_risks, "total": len(risk_items),
        "rate": round(verified_risks * 100 / len(risk_items), 2) if risk_items else 0.0,
        "items": risk_items,
    }

    verified_asset_keys = {
        (row.get("asset_type"), _norm(row.get("function")))
        for row in coverage if row.get("status") == "已验证"
    }
    asset_items = {}
    for asset in assets:
        key = "%s:%s" % (asset.get("asset_type", "unknown"), asset.get("name", ""))
        asset_items[key] = (asset.get("asset_type"), _norm(asset.get("name"))) in verified_asset_keys
    verified_assets = sum(asset_items.values())
    asset_coverage = {
        "label": "页面资产覆盖",
        "definition": "至少有一个真实已验证场景的去重页面资产数 / 已采集页面资产总数",
        "verified": verified_assets, "total": len(assets),
        "rate": round(verified_assets * 100 / len(assets), 2) if assets else 0.0,
        "by_type": dict(type_counts), "items": asset_items,
    }
    return {
        "total": total, "verified": verified,
        "pending": status_counts.get("待验证", 0),
        "needs_confirmation": status_counts.get("需用户确认", 0),
        "tool_gaps": status_counts.get("工具缺口", 0),
        "coverage_rate": round(verified * 100 / total, 2) if total else 0.0,
        "by_status": dict(status_counts), "asset_counts": dict(type_counts),
        "coverage_complete": bool(total) and verified == total,
        "requirements": requirements, "risks": risks, "assets": asset_coverage,
    }


def _case_prefix(steps) -> str:
    if isinstance(steps, str):
        return "F" if steps in _INPUT_ACTIONS else "I"
    names = [_norm(_business_name(step)) for step in steps]
    actions = {_action_name(step) for step in steps}
    joined = " ".join(names)
    if any(word in joined for word in ("新增", "添加", "创建")):
        return "A"
    if any(word in joined for word in ("编辑", "修改", "删除", "作废", "审核", "审批")):
        return "I"
    if any(word in joined for word in ("批量", "导出", "打印")) or actions & {"export", "print", "batch"}:
        return "B"
    if any(word in joined for word in ("保存", "提交")) and actions & _INPUT_ACTIONS:
        return "A"
    if actions & _INPUT_ACTIONS:
        return "F"
    if actions <= {"navigate", "tab", "refresh", "back", "forward"}:
        return "P"
    return "I"


def _case_number(module_info: dict, prefix: str) -> int:
    value = (module_info or {}).get("case_id_start", 1)
    if isinstance(value, dict):
        value = value.get(prefix, value.get("default", 1))
    match = re.search(r"(\d+)$", str(value or "1"))
    return max(1, int(match.group(1))) if match else 1


def _priority(steps: list[dict]) -> str:
    names = " ".join(_norm(_business_name(step)) for step in steps)
    if any(word in names for word in ("登录", "核心数据加载")):
        return "高级"
    if any(word in names for word in ("保存", "提交", "新增", "添加", "编辑", "修改", "删除", "作废", "审核", "审批")):
        return "中级"
    return "低级"


def _function_name(flow: dict, steps: list[dict]) -> str:
    configured = flow.get("function") or flow.get("flow_name")
    if configured and _norm(configured) not in {"exploration", "探索", "业务流"}:
        return _display_name(configured)
    return _business_name(steps[-1]) if steps else _display_name(flow.get("module")) or "业务流程"


def _expected_result(traces: list[dict], include_cleanup: bool = False) -> str:
    executable = [
        item for item in traces
        if item.get("executable") and (include_cleanup or item.get("phase") != "cleanup")
    ]
    selected = [item for item in executable if item.get("primary")]
    descriptions = []
    for item in selected:
        description = item.get("description")
        if description and description not in descriptions:
            descriptions.append(description)
    if len(descriptions) == 1:
        return descriptions[0]
    return "\n".join("%d. %s" % (index, value) for index, value in enumerate(descriptions, start=1))


def _quality_gates(flow: dict, steps: list[dict], recipe: dict, traces: list[dict], assets: list[dict]) -> dict:
    commands = recipe.get("steps", [])
    cleanup = recipe.get("cleanup", [])
    all_commands = commands + cleanup
    business = [item for item in traces if item.get("phase") == "steps" and item.get("executable") and item.get("kind") in {"toast", "modal", "url", "tab", "network_identity", "network_status", "network_body", "row_count", "table_data"}]
    destructive = bool(flow.get("destructive"))
    checks = [
        {"name": "存在真实业务步骤", "required": True, "passed": bool(commands), "detail": "业务步骤 %d 个，清理步骤 %d 个" % (len(commands), len(cleanup))},
        {"name": "步骤全部成功执行", "required": True, "passed": bool(steps) and all(step.get("outcome") == "passed" for step in steps), "detail": "失败步骤不会进入正式用例"},
        {"name": "完整步骤链可回放", "required": True, "passed": len(all_commands) == len(steps) and all(_is_replayable(command) for command in all_commands), "detail": "自动化配方覆盖 %d/%d 个步骤" % (len(all_commands), len(steps))},
        {"name": "包含真实业务断言", "required": True, "passed": bool(business), "detail": "共 %d 条可执行业务断言" % len(business)},
        {"name": "破坏性场景具备清理步骤", "required": destructive, "passed": not destructive or bool(cleanup), "detail": "破坏性场景必须用 cleanup_from_sequence 划分清理动作"},
        {"name": "已采集真实资产", "required": False, "passed": bool(assets), "detail": "共 %d 个页面或接口资产" % len(assets)},
    ]
    failures = [item["name"] for item in checks if item["required"] and not item["passed"]]
    return {"passed": not failures, "checks": checks, "failures": failures}


def generate_verified_cases(flow: dict, module_info: dict) -> dict:
    """Generate at most one formal case for one complete, asserted evidence flow."""
    module_info = dict(module_info or {})
    steps = sorted(
        [step for step in _as_list(flow.get("steps")) if isinstance(step, dict)],
        key=lambda item: item.get("sequence", 0),
    )
    assets = _extract_page_assets(flow)
    coverage = build_coverage_matrix(flow)
    recipe, traces = _build_recipe(flow, steps)
    quality = _quality_gates(flow, steps, recipe, traces, assets)
    cases = []
    if quality["passed"]:
        if flow.get("verify_fixture_in_setup"):
            fixture_check = next((
                command for command in recipe["cleanup"]
                if command.get("action") in {"get_vtable_row_values", "count_vtable_rows"}
            ), None)
            if fixture_check:
                setup_check = deepcopy(fixture_check)
                setup_check["sequence"] = 0
                recipe["setup"].insert(0, setup_check)
        business_sequences = {command.get("evidence_sequence") for command in recipe["steps"]}
        cleanup_sequences = {command.get("evidence_sequence") for command in recipe["cleanup"]}
        business_steps = [step for step in steps if step.get("sequence") in business_sequences]
        cleanup_steps = [step for step in steps if step.get("sequence") in cleanup_sequences]
        include_cleanup = bool(flow.get("include_cleanup_in_case"))
        display_steps = business_steps + cleanup_steps if include_cleanup else business_steps
        business_traces = [
            trace for trace in traces
            if trace.get("phase") == "steps" or (include_cleanup and trace.get("phase") == "cleanup")
        ]
        configured_prefix = re.sub(r"[^A-Za-z]", "", str(flow.get("case_prefix") or "")).upper()
        prefix = configured_prefix[:1] or _case_prefix(business_steps)
        number = _case_number(module_info, prefix)
        function = _function_name(flow, business_steps)
        flow_name = _display_name(flow.get("flow_name"))
        title_subject = flow_name if flow_name and _norm(flow_name) not in {"exploration", "探索"} else function
        expected = _expected_result(business_traces, include_cleanup=include_cleanup)
        evidence_refs = []
        for step in steps:
            reference = {"flow_id": flow.get("flow_id"), "sequence": step.get("sequence")}
            screenshot = (step.get("artifacts") or {}).get("screenshot")
            if screenshot:
                reference["screenshot"] = screenshot
            evidence_refs.append(reference)
        risk_type = str(flow.get("risk_type") or module_info.get("risk_type") or "")
        test_type = flow.get("scenario_type") or module_info.get("scenario_type")
        if not test_type:
            test_type = "边界值测试" if "边界" in risk_type else ("异常测试" if any(word in risk_type for word in ("异常", "反向", "非法")) else "功能测试")
        preconditions = ["已登录 SCM-MOM 系统", "已进入%s页面" % flow.get("module", "目标模块"), "页面与业务数据已加载"]
        if flow.get("verify_fixture_in_setup"):
            preconditions.append("测试环境预置业务夹具存在，自动化执行前会核验其初始值")
        case = {
            "case_id": "%s%03d" % (prefix, number),
            "case_title": "验证%s业务流程" % title_subject,
            "priority": _priority(business_steps),
            "verify_point": "%s完整业务链路及真实反馈" % function,
            "test_type": test_type,
            "function": function,
            "preconditions": preconditions,
            "test_steps": [_chinese_step(step) for step in display_steps],
            "test_data": _merge_test_data(display_steps),
            "expected_result": expected,
            "automation_suggestion": "按完整自动化配方依次回放 %d 个业务步骤和 %d 个清理步骤，并断言页面反馈、导航状态及接口响应" % (len(business_steps), len(recipe["cleanup"])),
            "automation_recipe": recipe,
            "business_assertions": traces,
            "evidence_refs": evidence_refs,
            "coverage_refs": [row["coverage_id"] for row in coverage if row.get("status") == "已验证" and row.get("evidence_sequence") in {step.get("sequence") for step in steps}],
            "destructive": bool(flow.get("destructive")),
        }
        if isinstance(flow.get("known_defect"), dict):
            case["known_defect"] = deepcopy(flow["known_defect"])
        cases.append(case)

    summary = _coverage_summary(coverage, assets)
    return {
        "ok": True, "module_info": module_info,
        "asset_inventory": assets,
        "coverage_matrix": coverage,
        "coverage_summary": summary,
        "quality_gates": quality,
        "test_cases": cases,
        "unverified_count": sum(1 for row in coverage if row.get("status") != "已验证"),
    }


def merge_generated_suites(payloads: list[dict], module_info: dict | None = None,
                           exclude_case_ids: list[str] | None = None,
                           exclude_known_defects: bool = False) -> dict:
    """Merge multi-flow outputs without double-counting shared assets/scenarios."""
    payloads = [item for item in payloads or [] if isinstance(item, dict)]
    info = dict(module_info or next((item.get("module_info") for item in payloads if item.get("module_info")), {}) or {})

    assets_by_key = {}
    for payload in payloads:
        for asset in payload.get("asset_inventory", []):
            if not isinstance(asset, dict):
                continue
            key = (asset.get("asset_type"), _norm(asset.get("name")))
            current = assets_by_key.setdefault(key, dict(asset))
            refs = current.setdefault("evidence_refs", [])
            for ref in asset.get("evidence_refs", []):
                if ref not in refs:
                    refs.append(ref)

    status_rank = {"工具缺口": 0, "需用户确认": 1, "待验证": 2, "已验证": 3}
    coverage_by_key = {}
    source_ref_keys = []
    for payload in payloads:
        ref_keys = {}
        for row in payload.get("coverage_matrix", []):
            if not isinstance(row, dict):
                continue
            key = (
                row.get("asset_type"), _norm(row.get("function")),
                _norm(row.get("scenario")), _norm(row.get("risk")),
            )
            if row.get("coverage_id"):
                ref_keys[row["coverage_id"]] = key
            current = coverage_by_key.get(key)
            if current is None or status_rank.get(row.get("status"), -1) > status_rank.get(current.get("status"), -1):
                coverage_by_key[key] = dict(row)
        source_ref_keys.append(ref_keys)

    coverage = list(coverage_by_key.values())
    final_id_by_key = {}
    for index, (key, row) in enumerate(coverage_by_key.items(), start=1):
        row["coverage_id"] = "SUITE-%03d" % index
        final_id_by_key[key] = row["coverage_id"]

    excluded_ids = {str(value) for value in (exclude_case_ids or [])}
    cases, excluded_cases, case_ids = [], [], set()
    duplicate_ids = []
    invalid_coverage_refs = []
    for source_index, payload in enumerate(payloads):
        for case in payload.get("test_cases", []):
            case_id = case.get("case_id")
            if str(case_id) in excluded_ids or (exclude_known_defects and isinstance(case.get("known_defect"), dict)):
                excluded_cases.append({
                    "case_id": case_id,
                    "reason": "known_defect" if isinstance(case.get("known_defect"), dict) else "excluded_case_id",
                })
                continue
            if case_id in case_ids:
                duplicate_ids.append(case_id)
                continue
            case_ids.add(case_id)
            merged_case = dict(case)
            mapped_refs = []
            for source_ref in case.get("coverage_refs", []):
                key = source_ref_keys[source_index].get(source_ref)
                final_ref = final_id_by_key.get(key)
                if not final_ref:
                    invalid_coverage_refs.append("%s:%s" % (case_id, source_ref))
                elif final_ref not in mapped_refs:
                    mapped_refs.append(final_ref)
            if "coverage_refs" in case:
                merged_case["coverage_refs"] = mapped_refs
            cases.append(merged_case)
    assets = list(assets_by_key.values())
    summary = _coverage_summary(coverage, assets)
    source_quality = [item.get("quality_gates") or {} for item in payloads]
    failures = []
    for index, quality in enumerate(source_quality, start=1):
        if not quality.get("passed"):
            failures.append("source_%d: %s" % (index, ", ".join(quality.get("failures") or ["quality gate failed"])))
    if duplicate_ids:
        failures.append("duplicate case ids: %s" % ", ".join(str(item) for item in duplicate_ids))
    if invalid_coverage_refs:
        failures.append("invalid coverage refs: %s" % ", ".join(invalid_coverage_refs))
    return {
        "ok": not failures,
        "module_info": info,
        "asset_inventory": assets,
        "coverage_matrix": coverage,
        "coverage_summary": summary,
        "quality_gates": {"passed": not failures, "failures": failures},
        "test_cases": cases,
        "unverified_count": sum(row.get("status") != "已验证" for row in coverage),
        "source_count": len(payloads),
        "excluded_cases": excluded_cases,
    }
