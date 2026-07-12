"""Recipe execution independent of DrissionPage, allowing deterministic tests."""
from __future__ import annotations

import re
import regex
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


_OPERATORS = {
    "equals", "not_equals", "contains", "truthy", "falsy", "in",
    "all_contains", "all_equals", "all_each_contains", "regex",
}
_MISSING = object()
_MAX_CASES = 1_000
_MAX_COMMANDS_PER_CASE = 1_000
_MAX_ASSERTIONS_PER_COMMAND = 100


_FILTER_OPERATOR_ALIASES = {
    "包含": "contains", "contains": "contains",
    "等于": "equals", "equals": "equals", "eq": "equals",
    "不等于": "not_equals", "not_equals": "not_equals", "ne": "not_equals",
    "为空": "empty", "empty": "empty", "is_empty": "empty",
    "不为空": "not_empty", "not_empty": "not_empty", "is_not_empty": "not_empty",
    "在列表中": "in_list", "in": "in_list", "in_list": "in_list",
    "不在列表中（含空）": "not_in_list_including_empty",
    "不在列表中(含空)": "not_in_list_including_empty",
    "not_in_list_including_empty": "not_in_list_including_empty",
    "范围": "range", "介于": "range", "range": "range", "between": "range",
}


def normalize_filter_operator(operator: str) -> str | None:
    raw = str(operator or "").strip().lower().replace(" ", "_")
    return _FILTER_OPERATOR_ALIASES.get(raw)


def _filter_expected_values(expected) -> list:
    if isinstance(expected, (list, tuple, set)):
        return list(expected)
    return [expected]


def _filter_range(expected) -> tuple[object, object] | None:
    if isinstance(expected, dict):
        start, end = expected.get("start"), expected.get("end")
    elif isinstance(expected, (list, tuple)) and len(expected) == 2:
        start, end = expected
    else:
        return None
    if start in (None, "") or end in (None, ""):
        return None
    return start, end


def _filter_datetime(value, end_of_day: bool = False):
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip().replace("/", "-")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if end_of_day and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _filter_decimal(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None




def evaluate_filter_values(values, operator: str, expected=None,
                           allow_empty: bool = False) -> dict:
    """根据页面操作符校验返回列中的每一行，禁止空包含与错误范围假通过。"""
    normalized = normalize_filter_operator(operator)
    if normalized is None:
        return {"ok": False, "matched": False,
                "reason": "unsupported filter operator: %s" % operator}
    if not isinstance(values, (list, tuple)):
        return {"ok": False, "matched": False, "reason": "filter values must be a list"}
    if normalized in {"contains", "equals", "not_equals"} and expected is None:
        return {"ok": False, "matched": False,
                "reason": "%s operator requires a value" % normalized}
    if normalized == "contains" and expected == "":
        return {"ok": False, "matched": False,
                "reason": "contains operator requires a non-empty value"}

    bounds = _filter_range(expected) if normalized == "range" else None
    if normalized == "range" and bounds is None:
        return {"ok": False, "matched": False,
                "reason": "range operator requires start and end"}
    start_dt = end_dt = start_number = end_number = None
    if bounds is not None:
        start_dt = _filter_datetime(bounds[0])
        end_dt = _filter_datetime(bounds[1], end_of_day=True)
        start_number = _filter_decimal(bounds[0])
        end_number = _filter_decimal(bounds[1])
        if ((start_dt is None) != (end_dt is None)
                or (start_number is None) != (end_number is None)):
            return {"ok": False, "matched": False,
                    "reason": "range bounds must use the same comparable type"}
        if start_dt is None and start_number is None:
            return {"ok": False, "matched": False,
                    "reason": "range bounds must be valid dates or finite numbers"}
        try:
            reversed_range = (
                start_dt > end_dt if start_dt is not None and end_dt is not None
                else start_number > end_number
            )
        except TypeError:
            reversed_range = True
        if reversed_range:
            return {"ok": False, "matched": False,
                    "reason": "range start must not exceed end"}

    actual_values = list(values)
    if not actual_values:
        return {
            "ok": True, "matched": bool(allow_empty), "operator": normalized,
            "row_count": 0, "mismatch_count": 0,
            "reason": "查询结果为空" if not allow_empty else "查询结果为空且已允许空结果",
            "mismatches": [],
        }
    expected_values = _filter_expected_values(expected)
    def text(value) -> str:
        return "" if value is None else str(value)

    def equivalent(actual, desired) -> bool:
        if actual is None or desired is None:
            return actual is desired
        if isinstance(actual, bool) or isinstance(desired, bool):
            return type(actual) is type(desired) and actual == desired
        return actual == desired or text(actual) == text(desired)

    def matches(actual) -> bool:
        if normalized == "contains":
            return str(expected) in text(actual)
        if normalized == "equals":
            return equivalent(actual, expected)
        if normalized == "not_equals":
            return not equivalent(actual, expected)
        if normalized == "empty":
            return actual is None or str(actual) == ""
        if normalized == "not_empty":
            return actual is not None and str(actual) != ""
        if normalized == "in_list":
            return any(equivalent(actual, item) for item in expected_values)
        if normalized == "not_in_list_including_empty":
            return actual is None or str(actual) == "" or not any(
                equivalent(actual, item) for item in expected_values
            )
        if start_dt is not None and end_dt is not None:
            actual_dt = _filter_datetime(actual)
            if actual_dt is None:
                return False
            try:
                return start_dt <= actual_dt <= end_dt
            except TypeError:
                return False
        if start_number is not None and end_number is not None:
            actual_number = _filter_decimal(actual)
            return actual_number is not None and start_number <= actual_number <= end_number
        return False

    mismatches = [
        {"index": index, "actual": actual}
        for index, actual in enumerate(actual_values)
        if not matches(actual)
    ]
    return {
        "ok": True, "matched": not mismatches, "operator": normalized,
        "row_count": len(actual_values), "mismatch_count": len(mismatches),
        "mismatches": mismatches[:20],
        "truncated_mismatches": max(0, len(mismatches) - 20),
    }


_DESTRUCTIVE_ACTIONS = {
    "delete", "remove", "save", "submit", "approve", "reject", "void", "cancel_order",
}
_DESTRUCTIVE_LABELS = ("删除", "移除", "保存", "提交", "审批", "审核", "驳回", "作废")


def _destructive_command(command: dict) -> bool:
    if not isinstance(command, dict):
        return False
    action = str(command.get("action") or "").strip().lower()
    if action in _DESTRUCTIVE_ACTIONS:
        return True
    args = command.get("args") if isinstance(command.get("args"), dict) else {}
    labels = []
    target = args.get("target")
    if isinstance(target, dict):
        labels.extend(target.get(key) for key in ("text", "name", "value", "label")
                      if target.get(key))
    elif isinstance(target, str):
        labels.append(target)
    labels.extend(args.get(key) for key in ("locator", "icon_name") if args.get(key))
    normalized = " ".join(str(item) for item in labels).lower()
    if ("查询" in normalized or "search" in normalized or "query" in normalized) and not any(
        token in normalized for token in ("删除", "移除", "保存", "审批", "审核", "驳回", "作废")
    ):
        return False
    return any(token in normalized for token in _DESTRUCTIVE_LABELS) or any(
        re.search(r"\b%s\b" % re.escape(token), normalized)
        for token in _DESTRUCTIVE_ACTIONS
    )


def is_destructive_command(command: dict) -> bool:
    """Return whether a recipe command mutates persistent business state."""
    return _destructive_command(command)


_NON_BUSINESS_ASSERTION_PATHS = {
    "", "$", "ok", "success", "query_completed", "loading_complete",
    "http_ok", "business_ok",
}
ROLE_SESSION_ACTIONS = frozenset({
    "role_session_start",
    "role_session_open",
    "role_session_login",
    "role_session_activate",
    "role_session_list",
    "role_session_close",
})
_ROLE_ACTIONS_REQUIRING_ID = ROLE_SESSION_ACTIONS - {"role_session_list"}


def _is_business_assertion(assertion: dict) -> bool:
    """Root action-success flags are synchronization checks, not business outcomes."""
    if not isinstance(assertion, dict):
        return False
    path = str(assertion.get("path") or "").strip().lower()
    return path not in _NON_BUSINESS_ASSERTION_PATHS


def uses_role_session_actions(case: dict) -> bool:
    """Return whether a recipe explicitly manages or switches role sessions."""
    if not isinstance(case, dict):
        return False
    try:
        setup, steps, cleanup = _recipe_sections(case.get("automation_recipe"))
    except ValueError:
        return False
    return any(
        isinstance(command, dict)
        and str(command.get("action") or "").strip() in ROLE_SESSION_ACTIONS
        for command in setup + steps + cleanup
    )


def _role_recipe_reasons(setup: list[dict], steps: list[dict]) -> list[str]:
    """Require a role switch before a role recipe performs a browser operation."""
    commands = [command for command in setup + steps if isinstance(command, dict)]
    if not any(
        str(command.get("action") or "").strip() in ROLE_SESSION_ACTIONS
        for command in commands
    ):
        return []

    reasons = []
    activated = False
    for command in commands:
        action = str(command.get("action") or "").strip()
        args = command.get("args") if isinstance(command.get("args"), dict) else {}
        if action in _ROLE_ACTIONS_REQUIRING_ID:
            role_id = args.get("role_id")
            if not isinstance(role_id, str) or not role_id.strip():
                reasons.append("%s 必须提供非空 args.role_id" % action)
        if action == "role_session_open" and "proxy" in args:
            reasons.append("role_session_open 配方不支持 proxy；请通过服务配置提供代理")
        if action == "role_session_activate":
            activated = True
            continue
        if action not in ROLE_SESSION_ACTIONS and not activated:
            reasons.append("角色回归用例必须在首个业务操作前执行 role_session_activate")
            break
    if not activated:
        reasons.append("角色回归用例必须至少包含一次 role_session_activate")
    return list(dict.fromkeys(reasons))


def weak_recipe_reasons(case: dict) -> list[str]:
    """Reject recipes that can report a stale or only partially matched table as pass.

    This deliberately targets the historical filter recipe shape. It does not try to
    infer arbitrary page semantics, but it prevents the known false-positive pattern:
    coordinate click with observation disabled followed by a truthy row count.
    """
    if not isinstance(case, dict):
        return ["test case must be an object"]
    try:
        setup, steps, cleanup = _recipe_sections(case.get("automation_recipe"))
    except ValueError as exc:
        return [str(exc)]
    reasons = []
    reasons.extend(_role_recipe_reasons(setup, steps))
    destructive_value = case.get("destructive", False)
    if not isinstance(destructive_value, bool):
        reasons.append("destructive 必须是布尔值")
    destructive_declared = destructive_value is True
    if case.get("known_defect") is not None and not isinstance(case.get("known_defect"), dict):
        reasons.append("known_defect 必须是对象")
    elif isinstance(case.get("known_defect"), dict):
        defect_id = case["known_defect"].get("defect_id")
        if not isinstance(defect_id, str) or not defect_id.strip():
            reasons.append("known_defect.defect_id 必须是非空字符串")
    destructive_commands = [
        command for command in setup + steps + cleanup if _destructive_command(command)
    ]
    if destructive_commands and not destructive_declared:
        reasons.append("破坏性操作必须显式声明 destructive=true")
    if destructive_declared and not cleanup:
        reasons.append("破坏性用例必须包含 automation_recipe.cleanup")
    for phase_name, commands in (("setup", setup), ("steps", steps), ("cleanup", cleanup)):
        for index, command in enumerate(commands, start=1):
            if not isinstance(command, dict):
                reasons.append("automation_recipe.%s[%d] 必须是对象" % (phase_name, index - 1))
            else:
                assertion_error = _assertion_schema_error(command)
                if assertion_error:
                    reasons.append("automation_recipe.%s[%d]: %s" %
                                   (phase_name, index - 1, assertion_error))
                if len(_assertion_specs(command)) > _MAX_ASSERTIONS_PER_COMMAND:
                    reasons.append("automation_recipe.%s[%d] exceeds %d assertions" %
                                   (phase_name, index - 1, _MAX_ASSERTIONS_PER_COMMAND))
    if not any(
        any(_is_business_assertion(assertion) for assertion in _assertion_specs(command))
        or command.get("action") == "verify_filter_query"
        for command in steps if isinstance(command, dict)
    ):
        reasons.append("正式回放步骤必须包含至少一个业务断言，根级 ok 不能替代业务结果")
    if destructive_declared and cleanup and not any(
        any(_is_business_assertion(assertion) for assertion in _assertion_specs(command))
        for command in cleanup if isinstance(command, dict)
    ):
        reasons.append("破坏性用例的 cleanup 必须包含至少一个业务清理断言")
    for command in steps:
        if not isinstance(command, dict):
            continue
        args = command.get("args") if isinstance(command.get("args"), dict) else {}
        if command.get("action") == "explore_action":
            action = str(args.get("action") or "").lower()
            target = args.get("target") if isinstance(args.get("target"), dict) else {}
            if args.get("by_js"):
                reasons.append("正式回放禁止 by_js；请改用 DrissionPage 原生元素动作")
            if action == "click_xy" or str(target.get("type") or "").lower() in {
                "xy", "point", "coord", "coordinate",
            }:
                reasons.append("正式回放禁止普通坐标点击；VTable 请使用专用动作")
    query_positions = [
        index for index, item in enumerate(steps)
        if isinstance(item, dict) and item.get("action") == "query_filter"
    ]
    full_assertion_positions = [
        index for index, command in enumerate(steps)
        if isinstance(command, dict) and command.get("action") == "get_table_values"
        and any(
            str(item.get("path") or "").strip() == "values"
            and _normalize_assertion(item)[0] in {"all_equals", "all_each_contains"}
            for item in _assertion_specs(command)
        )
    ]
    has_query_barrier = bool(query_positions)
    has_full_table_assertion = bool(full_assertion_positions)
    for query_index, query_position in enumerate(query_positions):
        next_query = (query_positions[query_index + 1]
                      if query_index + 1 < len(query_positions) else len(steps))
        if not any(query_position < full < next_query for full in full_assertion_positions):
            reasons.append("每次 query_filter 后、下一次查询前必须使用 get_table_values.values 的全量列断言")
            break
    for command in steps:
        if not isinstance(command, dict):
            continue
        if command.get("action") != "count_vtable_rows":
            continue
        assertions = _assertion_specs(command)
        if not has_full_table_assertion and any(
            _normalize_assertion(item)[0] == "truthy" and item.get("path") == "match_count"
            for item in assertions
        ):
            reasons.append("count_vtable_rows 的 truthy 断言只能证明存在匹配行，不能证明全量结果匹配")
    if any(
        item.get("action") == "explore_action"
        and str((item.get("args") or {}).get("observe_mode", "")).lower() in {"none", "off"}
        and str((item.get("args") or {}).get("action", "")).lower() in {"click", "click_xy"}
        for item in steps if isinstance(item, dict)
    ) and not has_query_barrier:
        reasons.append("查询点击关闭了观察且未使用 query_filter，无法证明数据已刷新")
    return list(dict.fromkeys(reasons))


def _get_path(data, path: str):
    """Read a dotted response path, including numeric list indexes."""
    if path in (None, "", "$"):
        return data
    value = data
    normalized = re.sub(r"\[([^]]+)\]", r".\1", str(path)).strip(".")
    for part in normalized.split("."):
        if isinstance(value, dict):
            if part not in value:
                return _MISSING
            value = value[part]
        elif isinstance(value, (list, tuple)) and part.isdigit():
            index = int(part)
            if index >= len(value):
                return _MISSING
            value = value[index]
        else:
            return _MISSING
    return value


def _assertion_specs(command: dict) -> list[dict]:
    raw = command.get("assertions", _MISSING)
    if raw is _MISSING:
        raw = command.get("expect")
    if not raw:
        return []
    if isinstance(raw, dict):
        return [raw]
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _assertion_schema_error(command: dict) -> str | None:
    raw = command.get("assertions", _MISSING)
    if raw is _MISSING:
        raw = command.get("expect", _MISSING)
    if raw is _MISSING or raw in (None, []):
        return None
    if isinstance(raw, dict):
        assertions = [raw]
    elif isinstance(raw, list) and all(isinstance(item, dict) for item in raw):
        assertions = raw
    else:
        return "assertions/expect must be an assertion object or a list of objects"
    for index, assertion in enumerate(assertions):
        explicit = assertion.get("operator")
        shorthands = sorted(candidate for candidate in _OPERATORS if candidate in assertion)
        if explicit and shorthands:
            return "assertion[%d] cannot combine operator with shorthand keys" % index
        if len(shorthands) > 1:
            return "assertion[%d] has multiple shorthand operators" % index
        if "value" in assertion and "expected" in assertion and assertion["value"] != assertion["expected"]:
            return "assertion[%d] has conflicting value and expected fields" % index
        operator = str(explicit or (shorthands[0] if shorthands else "")).lower()
        if not operator:
            return "assertion[%d] is missing operator" % index
        if operator not in _OPERATORS:
            return "assertion[%d] uses unsupported operator: %s" % (index, operator)
        has_expected = "value" in assertion or "expected" in assertion or bool(shorthands)
        if operator not in {"truthy", "falsy"} and not has_expected:
            return "assertion[%d] is missing expected value" % index
        path = assertion.get("path", "")
        if not isinstance(path, str) or len(path) > 1_000:
            return "assertion[%d] path must be a string no longer than 1000 characters" % index
        description = assertion.get("description", "")
        if not isinstance(description, str) or len(description) > 2_000:
            return "assertion[%d] description must be a string no longer than 2000 characters" % index
        _, expected = _normalize_assertion(assertion)
        if operator == "in" and not isinstance(expected, (list, tuple, set)):
            return "assertion[%d] in operator requires a collection" % index
    return None


def _normalize_assertion(assertion: dict) -> tuple[str, object]:
    operator = assertion.get("operator")
    expected = assertion.get("value", assertion.get("expected", _MISSING))
    if not operator:
        shorthands = sorted(candidate for candidate in _OPERATORS if candidate in assertion)
        if shorthands:
            operator = shorthands[0]
            expected = assertion[operator]
    operator = str(operator or "equals").lower()
    if expected is _MISSING:
        expected = None
    return operator, expected


def _contains(actual, expected, _depth: int = 0, _seen=None, _budget=None) -> bool:
    """在深度和节点预算内匹配标量或嵌套局部对象。"""
    if _budget is None:
        _budget = [10_000]
    if _depth >= 30 or _budget[0] <= 0:
        return False
    _budget[0] -= 1
    if actual is expected:
        return True
    containers = (dict, list, tuple, set)
    if not isinstance(actual, containers) and not isinstance(expected, containers):
        try:
            if actual == expected:
                return True
        except (RecursionError, TypeError, ValueError):
            pass
    if _seen is None:
        _seen = set()
    identity = id(actual) if isinstance(actual, (dict, list, tuple, set)) else None
    if identity is not None:
        if identity in _seen:
            return False
        _seen.add(identity)
    try:
        if isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
            if len(actual) == len(expected):
                return all(
                    _contains(actual_item, expected_item, _depth + 1, _seen, _budget)
                    for actual_item, expected_item in zip(actual, expected)
                )
            return False
        if isinstance(expected, set) and isinstance(actual, set):
            return len(actual) <= 2_000 and actual == expected
        if isinstance(expected, dict):
            if isinstance(actual, dict) and all(
                key in actual and _contains(actual[key], value, _depth + 1, _seen, _budget)
                for key, value in expected.items()
            ):
                return True
            children = (actual.values() if isinstance(actual, dict)
                        else actual if isinstance(actual, (list, tuple)) else [])
            return any(_contains(child, expected, _depth + 1, _seen, _budget)
                       for child in children)
        if isinstance(actual, str) and isinstance(expected, str):
            return expected in actual
        if isinstance(actual, dict):
            try:
                if expected in actual:
                    return True
            except TypeError:
                pass
            return any(_contains(value, expected, _depth + 1, _seen, _budget)
                       for value in actual.values())
        if isinstance(actual, (list, tuple, set)):
            return any(_contains(item, expected, _depth + 1, _seen, _budget)
                       for item in actual)
        return False
    finally:
        if identity is not None:
            _seen.discard(identity)


def _regex_quantifier_reason(pattern: str) -> str:
    index = 0
    escaped = False
    in_class = False
    while index < len(pattern):
        char = pattern[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "[":
            in_class = True
        elif char == "]" and in_class:
            in_class = False
        elif char == "{" and not in_class:
            end = pattern.find("}", index + 1)
            if end >= 0:
                match = re.fullmatch(r"(\d+)(?:,(\d*))?", pattern[index + 1:end])
                if match:
                    lower = int(match.group(1))
                    upper_text = match.group(2)
                    upper = int(upper_text) if upper_text not in (None, "") else None
                    if lower > 10_000 or (upper is not None and upper > 10_000):
                        return "regex quantifier exceeds safety limit"
                index = end
        index += 1
    return ""


def _safe_regex(pattern, actual) -> tuple[bool, str]:
    pattern = str(pattern)
    text = str(actual)
    if len(pattern) > 500 or len(text) > 10_000:
        return False, "regex pattern or input exceeds safety limit"
    quantifier_reason = _regex_quantifier_reason(pattern)
    if quantifier_reason:
        return False, quantifier_reason
    if (re.search(r"\([^)]*(?:[+*?]|\{\d+(?:,\d*)?\})[^)]*\)(?:[+*?]|\{)", pattern)
            or re.search(r"\([^)]*\|[^)]*\)(?:[+*]|\{)", pattern)):
        return False, "regex uses a backtracking-prone construct"
    try:
        return regex.search(pattern, text, timeout=0.05, concurrent=True) is not None, ""
    except TimeoutError:
        return False, "regex evaluation timed out"
    except regex.error as exc:
        return False, str(exc)

def _semantic_truth(value) -> bool:
    """Interpret serialized boolean tokens without treating ``"false"`` as true."""
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "false", "0", "no", "n", "off", "null", "none", "failed", "failure", "error"}:
            return False
        if normalized in {"true", "1", "yes", "y", "on", "ok", "passed", "success"}:
            return True
    return bool(value)


def _evaluate_assertion(actual, operator: str, expected) -> tuple[bool, str]:
    try:
        if operator == "equals":
            return actual == expected, ""
        if operator == "not_equals":
            return actual != expected, ""
        if operator == "contains":
            if expected in (None, ""):
                return False, "contains operator requires a non-empty value"
            return _contains(actual, expected), ""
        if operator == "truthy":
            return _semantic_truth(actual), ""
        if operator == "falsy":
            return not _semantic_truth(actual), ""
        if operator == "in":
            return actual in expected, ""
        if operator == "all_contains":
            values = expected if isinstance(expected, (list, tuple, set)) else [expected]
            if not values or any(item in (None, "") for item in values):
                return False, "all_contains requires non-empty values"
            return all(_contains(actual, item) for item in values), ""
        if operator == "all_equals":
            values = actual if isinstance(actual, (list, tuple, set)) else []
            return bool(values) and all(item == expected for item in values), ""
        if operator == "all_each_contains":
            if expected in (None, ""):
                return False, "all_each_contains requires a non-empty value"
            values = actual if isinstance(actual, (list, tuple, set)) else []
            return bool(values) and all(
                str(expected) in ("" if item is None else str(item)) for item in values
            ), ""
        if operator == "regex":
            if expected in (None, ""):
                return False, "regex requires a non-empty pattern"
            return _safe_regex(expected, actual)
    except (TypeError, ValueError, re.error) as exc:
        return False, str(exc)
    return False, "unsupported assertion operator: %s" % operator


def _flow_refs(response: dict) -> list[dict]:
    flow_step = response.get("flow_step") if isinstance(response, dict) else None
    candidates = flow_step if isinstance(flow_step, list) else [flow_step]
    return [dict(ref) for ref in candidates if isinstance(ref, dict)]


def _append_refs(target: list[dict], response: dict) -> None:
    for ref in _flow_refs(response):
        if ref not in target:
            target.append(ref)


def _failure(phase: str, index: int, action: str, failure_type: str,
             reason: str, expected=None, actual=None) -> dict:
    return {
        "failure_type": failure_type,
        "reason": str(reason or "%s failed" % failure_type),
        "failure_step": {"phase": phase, "index": index, "action": action},
        "expected": expected,
        "actual": actual,
    }


def _run_command(command: dict, phase: str, index: int, sequence: int,
                 action_runner, evidence_refs: list[dict]) -> tuple[dict, dict | None]:
    began = time.perf_counter()
    command = command if isinstance(command, dict) else {}
    action = command.get("action")
    raw_args = command.get("args")
    if raw_args is None:
        args, argument_error = {}, None
    elif isinstance(raw_args, dict):
        args, argument_error = dict(raw_args), None
    else:
        args, argument_error = {}, "recipe command args must be an object"
    assertions = _assertion_specs(command)
    assertion_error = _assertion_schema_error(command)
    validation_type = "action"
    validation_error = argument_error
    if assertion_error:
        validation_type, validation_error = "assertion", assertion_error
    elif len(assertions) > _MAX_ASSERTIONS_PER_COMMAND:
        validation_type = "assertion"
        validation_error = "recipe command exceeds %d assertions" % _MAX_ASSERTIONS_PER_COMMAND
    elif not isinstance(action, str) or not action.strip():
        validation_error = "recipe command is missing action"

    # Validate the complete command before invoking the runner: a malformed
    # assertion must never execute a save/delete action and fail afterwards.
    try:
        if validation_error:
            response = {"ok": False, "reason": validation_error}
        else:
            action = action.strip()
            response = action_runner(action, args)
            if not isinstance(response, dict):
                response = {"ok": False, "reason": "action runner returned a non-object response",
                            "actual": response}
    except Exception as exc:
        response = {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    elapsed_ms = round((time.perf_counter() - began) * 1000, 2)
    _append_refs(evidence_refs, response)
    step = {
        "sequence": sequence, "phase": phase, "phase_index": index,
        "action": action, "args": args, "result": response,
        "elapsed_ms": elapsed_ms, "assertions": [],
    }
    if response.get("ok") is not True:
        failure_type = (phase if phase in {"setup", "cleanup"}
                        else validation_type if validation_error else "action")
        failure = _failure(
            phase, index, action, failure_type,
            response.get("reason") or "action failed", True, response.get("ok"),
        )
        step["status"] = "failed"
        return step, failure

    for assertion in assertions:
        path = assertion.get("path", "")
        operator, expected = _normalize_assertion(assertion)
        actual = _get_path(response, path)
        missing = actual is _MISSING
        if missing:
            actual = None
            passed, detail = False, "assertion path not found"
        else:
            passed, detail = _evaluate_assertion(actual, operator, expected)
        assertion_result = {
            "path": path, "operator": operator,
            "expected": ("truthy" if operator == "truthy"
                         else "falsy" if operator == "falsy" else expected),
            "actual": actual, "description": assertion.get("description", ""),
            "passed": passed, "path_missing": missing,
        }
        if detail:
            assertion_result["detail"] = detail
        step["assertions"].append(assertion_result)
        if not passed:
            failure_type = phase if phase in {"setup", "cleanup"} else "assertion"
            label = assertion.get("description") or path or "response"
            reason = "%s: assertion failed (%s)" % (label, operator)
            if detail:
                reason += ": " + detail
            step["status"] = "failed"
            return step, _failure(
                phase, index, action, failure_type, reason,
                assertion_result["expected"], actual,
            )
    step["status"] = "passed"
    return step, None


def _run_hook(name: str, hook, case: dict, sequence: int, phase: str,
              evidence_refs: list[dict], result: dict | None = None) -> tuple[dict, dict | None]:
    began = time.perf_counter()
    try:
        response = hook(case) if result is None else hook(case, result)
        if response is None:
            response = {"ok": True}
        elif isinstance(response, bool):
            response = {"ok": response}
        elif not isinstance(response, dict):
            response = {"ok": False, "reason": "hook returned a non-object response", "actual": response}
    except Exception as exc:
        response = {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    _append_refs(evidence_refs, response)
    step = {
        "sequence": sequence,
        "phase": phase,
        "phase_index": 0,
        "action": name,
        "args": {},
        "result": response,
        "elapsed_ms": round((time.perf_counter() - began) * 1000, 2),
        "assertions": [],
        "status": "passed" if response.get("ok") is True else "failed",
    }
    failure = None
    if response.get("ok") is not True:
        failure = _failure(phase, 0, name, phase, response.get("reason") or "%s failed" % name,
                           True, response.get("ok"))
    return step, failure


def _recipe_sections(recipe) -> tuple[list[dict], list[dict], list[dict]]:
    if isinstance(recipe, list):
        sections = ([], recipe, [])
    elif isinstance(recipe, dict):
        collected = []
        for name in ("setup", "steps", "cleanup"):
            commands = recipe.get(name, [])
            if commands is None:
                commands = []
            if not isinstance(commands, list):
                raise ValueError("automation_recipe.%s must be a list" % name)
            collected.append(commands)
        sections = tuple(collected)
    else:
        raise ValueError("automation_recipe must be a list or an object")
    command_count = sum(len(section) for section in sections)
    if command_count == 0:
        raise ValueError("automation_recipe contains no commands")
    if command_count > _MAX_COMMANDS_PER_CASE:
        raise ValueError("automation_recipe exceeds %d commands" % _MAX_COMMANDS_PER_CASE)
    return sections


def _draft_result(case: dict, failure: dict | None) -> dict:
    known_defect = case.get("known_defect") if isinstance(case.get("known_defect"), dict) else None
    status = "failed" if failure else "xfailed" if known_defect else "passed"
    draft = {
        "case_id": case.get("case_id"),
        "case_title": case.get("case_title", ""),
        "status": status,
        "reason": failure.get("reason", "") if failure else (
            "known defect reproduced: %s" % known_defect.get("defect_id", "") if known_defect else ""
        ),
    }
    for key in ("priority", "test_type", "function", "verify_point", "coverage_refs", "destructive"):
        if key in case:
            value = case[key]
            draft[key] = list(value) if isinstance(value, list) else dict(value) if isinstance(value, dict) else value
    if known_defect:
        draft["known_defect"] = dict(known_defect)
    if failure:
        draft.update(failure)
    return draft


def execute_cases(cases: list[dict], action_runner, before_case=None, after_case=None) -> dict:
    """执行结构化配方；隔离钩子与 cleanup 在主步骤失败后仍会运行。"""
    started = datetime.now(timezone.utc).isoformat()
    results = []
    normalized_cases = list(cases or [])
    for case in normalized_cases[:_MAX_CASES]:
        if not isinstance(case, dict):
            results.append({"case_id": None, "status": "skipped",
                            "reason": "test case must be an object", "steps": []})
            continue
        began = time.perf_counter()
        recipe = case.get("automation_recipe")
        if not recipe:
            results.append({
                "case_id": case.get("case_id"), "case_title": case.get("case_title", ""),
                "status": "skipped", "reason": "missing automation_recipe", "steps": [],
            })
            continue

        trace = []
        evidence_refs = []
        cleanup_failures = []
        primary_failure = None
        try:
            setup, steps, cleanup = _recipe_sections(recipe)
        except ValueError as exc:
            setup, steps, cleanup = [], [], []
            primary_failure = _failure("setup", 0, "recipe", "setup", str(exc))

        if before_case is not None and primary_failure is None:
            hook_step, primary_failure = _run_hook(
                "before_case", before_case, case, len(trace) + 1, "setup", evidence_refs,
            )
            trace.append(hook_step)
        for index, command in enumerate(setup, start=1):
            if primary_failure:
                break
            step, primary_failure = _run_command(
                command, "setup", index, len(trace) + 1, action_runner, evidence_refs,
            )
            trace.append(step)
        for index, command in enumerate(steps, start=1):
            if primary_failure:
                break
            step, primary_failure = _run_command(
                command, "steps", index, len(trace) + 1, action_runner, evidence_refs,
            )
            trace.append(step)
        for index, command in enumerate(cleanup, start=1):
            step, cleanup_failure = _run_command(
                command, "cleanup", index, len(trace) + 1, action_runner, evidence_refs,
            )
            trace.append(step)
            if cleanup_failure:
                if primary_failure is None:
                    primary_failure = cleanup_failure
                else:
                    cleanup_failures.append(cleanup_failure)
        if after_case is not None:
            hook_step, cleanup_failure = _run_hook(
                "after_case", after_case, case, len(trace) + 1, "cleanup", evidence_refs,
                _draft_result(case, primary_failure),
            )
            trace.append(hook_step)
            if cleanup_failure:
                if primary_failure is None:
                    primary_failure = cleanup_failure
                else:
                    cleanup_failures.append(cleanup_failure)

        result = _draft_result(case, primary_failure)
        result.update({
            "elapsed_ms": round((time.perf_counter() - began) * 1000, 2),
            "steps": trace, "evidence_refs": evidence_refs,
        })
        if cleanup_failures:
            result["cleanup_failures"] = cleanup_failures
        results.append(result)

    for case in normalized_cases[_MAX_CASES:]:
        results.append({
            "case_id": case.get("case_id") if isinstance(case, dict) else None,
            "status": "skipped", "reason": "execution exceeds %d cases" % _MAX_CASES,
            "steps": [],
        })
    return {
        "schema_version": "1.0", "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(), "results": results,
    }
