"""Recipe execution independent of DrissionPage, allowing deterministic tests."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone


_OPERATORS = {
    "equals", "not_equals", "contains", "truthy", "falsy", "in",
    "all_contains", "regex",
}
_MISSING = object()


def _get_path(data, path: str):
    """Read a dotted response path, including numeric list indexes."""
    if path in (None, "", "$"):
        return data
    value = data
    normalized = re.sub(r"\[([^]]+)\]", r".\1", str(path)).strip(".")
    for part in normalized.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, (list, tuple)) and part.isdigit():
            index = int(part)
            value = value[index] if index < len(value) else None
        else:
            return None
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


def _normalize_assertion(assertion: dict) -> tuple[str, object]:
    operator = assertion.get("operator")
    expected = assertion.get("value", assertion.get("expected", _MISSING))
    if not operator:
        for candidate in _OPERATORS:
            if candidate in assertion:
                operator = candidate
                expected = assertion[candidate]
                break
    operator = str(operator or "equals").lower()
    if expected is _MISSING:
        expected = None
    return operator, expected


def _contains(actual, expected) -> bool:
    """Match scalar values or a partial object anywhere in a nested response."""
    if actual == expected:
        return True
    if isinstance(expected, dict):
        if isinstance(actual, dict) and all(
            key in actual and _contains(actual[key], value)
            for key, value in expected.items()
        ):
            return True
        children = actual.values() if isinstance(actual, dict) else actual if isinstance(actual, (list, tuple)) else []
        return any(_contains(child, expected) for child in children)
    if isinstance(actual, str) and isinstance(expected, str):
        return expected in actual
    if isinstance(actual, dict):
        try:
            if expected in actual:
                return True
        except TypeError:
            pass
        return any(_contains(value, expected) for value in actual.values())
    if isinstance(actual, (list, tuple, set)):
        return any(_contains(item, expected) for item in actual)
    return False


def _evaluate_assertion(actual, operator: str, expected) -> tuple[bool, str]:
    try:
        if operator == "equals":
            return actual == expected, ""
        if operator == "not_equals":
            return actual != expected, ""
        if operator == "contains":
            return _contains(actual, expected), ""
        if operator == "truthy":
            return bool(actual), ""
        if operator == "falsy":
            return not bool(actual), ""
        if operator == "in":
            return actual in expected, ""
        if operator == "all_contains":
            values = expected if isinstance(expected, (list, tuple, set)) else [expected]
            return all(_contains(actual, item) for item in values), ""
        if operator == "regex":
            return re.search(str(expected), str(actual)) is not None, ""
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
    action = command.get("action") if isinstance(command, dict) else None
    args = dict(command.get("args") or {}) if isinstance(command, dict) else {}
    try:
        if not action:
            response = {"ok": False, "reason": "recipe command is missing action"}
        else:
            response = action_runner(action, args)
            if not isinstance(response, dict):
                response = {"ok": False, "reason": "action runner returned a non-object response", "actual": response}
    except Exception as exc:  # The runner boundary must turn browser errors into case results.
        response = {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    elapsed_ms = round((time.perf_counter() - began) * 1000, 2)
    _append_refs(evidence_refs, response)
    step = {
        "sequence": sequence,
        "phase": phase,
        "phase_index": index,
        "action": action,
        "args": args,
        "result": response,
        "elapsed_ms": elapsed_ms,
        "assertions": [],
    }
    if not response.get("ok"):
        failure_type = phase if phase in {"setup", "cleanup"} else "action"
        failure = _failure(
            phase, index, action, failure_type,
            response.get("reason") or "action failed", True, response.get("ok"),
        )
        step["status"] = "failed"
        return step, failure

    for assertion in _assertion_specs(command):
        path = assertion.get("path", "")
        operator, expected = _normalize_assertion(assertion)
        actual = _get_path(response, path)
        passed, detail = _evaluate_assertion(actual, operator, expected)
        assertion_result = {
            "path": path,
            "operator": operator,
            "expected": "truthy" if operator == "truthy" else "falsy" if operator == "falsy" else expected,
            "actual": actual,
            "description": assertion.get("description", ""),
            "passed": passed,
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
        "status": "passed" if response.get("ok") else "failed",
    }
    failure = None
    if not response.get("ok"):
        failure = _failure(phase, 0, name, phase, response.get("reason") or "%s failed" % name,
                           True, response.get("ok"))
    return step, failure


def _recipe_sections(recipe) -> tuple[list[dict], list[dict], list[dict]]:
    if isinstance(recipe, list):
        return [], recipe, []
    if not isinstance(recipe, dict):
        raise ValueError("automation_recipe must be a list or an object")
    sections = []
    for name in ("setup", "steps", "cleanup"):
        commands = recipe.get(name) or []
        if not isinstance(commands, list):
            raise ValueError("automation_recipe.%s must be a list" % name)
        sections.append(commands)
    return tuple(sections)


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
    if known_defect:
        draft["known_defect"] = dict(known_defect)
    if failure:
        draft.update(failure)
    return draft


def execute_cases(cases: list[dict], action_runner, before_case=None, after_case=None) -> dict:
    """Execute list or ``{setup, steps, cleanup}`` recipes through an action runner.

    ``before_case(case)`` and ``after_case(case, draft_result)`` are optional isolation
    hooks. Cleanup commands and the after hook run even when setup or test steps fail.
    """
    started = datetime.now(timezone.utc).isoformat()
    results = []
    for case in cases or []:
        began = time.perf_counter()
        recipe = case.get("automation_recipe")
        if not recipe:
            results.append({
                "case_id": case.get("case_id"), "status": "skipped",
                "reason": "missing automation_recipe", "steps": [],
            })
            continue

        trace: list[dict] = []
        evidence_refs: list[dict] = []
        cleanup_failures: list[dict] = []
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
            "steps": trace,
            "evidence_refs": evidence_refs,
        })
        if cleanup_failures:
            result["cleanup_failures"] = cleanup_failures
        results.append(result)
    return {
        "schema_version": "1.0",
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
