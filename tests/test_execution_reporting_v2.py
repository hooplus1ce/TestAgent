"""Focused tests for reproducible execution and trustworthy reporting."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest


def test_business_assertion_prevents_false_pass_and_uses_execution_evidence():
    from drissionpage_mcp.workflows import test_execution
    case = {
        "case_id": "SAVE-001",
        "case_title": "保存采购单",
        "evidence_refs": [{"flow_id": "exploration", "sequence": 9, "screenshot": "old.png"}],
        "automation_recipe": {
            "steps": [{
                "action": "explore_action",
                "args": {"action": "click", "target": "保存"},
                "assertions": [{
                    "path": "signal.payload.message",
                    "operator": "contains",
                    "value": "保存成功",
                    "description": "保存反馈",
                }],
            }],
        },
    }

    execution = test_execution.execute_cases([case], lambda *_: {
        "ok": True,
        "signal": {"payload": {"message": "保存失败：单号重复"}},
        "flow_step": {
            "flow_id": "execution",
            "sequence": 1,
            "screenshot": "execution-step-1.png",
        },
    })

    result = execution["results"][0]
    assert result["status"] == "failed"
    assert result["failure_type"] == "assertion"
    assert result["failure_step"] == {"phase": "steps", "index": 1, "action": "explore_action"}
    assert result["expected"] == "保存成功"
    assert result["actual"] == "保存失败：单号重复"
    assert result["evidence_refs"] == [{
        "flow_id": "execution", "sequence": 1, "screenshot": "execution-step-1.png",
    }]
    assert result["steps"][0]["elapsed_ms"] >= 0


@pytest.mark.parametrize(
    ("operator", "actual", "expected"),
    [
        ("equals", 200, 200),
        ("not_equals", "failed", "passed"),
        ("contains", "保存成功", "成功"),
        ("truthy", [1], None),
        ("falsy", [], None),
        ("in", "approved", ["draft", "approved"]),
        ("all_contains", ["create", "read", "update"], ["create", "read"]),
        ("regex", "PO-20260711", r"^PO-\d{8}$"),
    ],
)
def test_supported_assertion_operators(operator, actual, expected):
    from drissionpage_mcp.workflows import test_execution
    assertion = {"path": "value", "operator": operator}
    if expected is not None:
        assertion["value"] = expected
    result = test_execution.execute_cases(
        [{"case_id": "OP", "automation_recipe": [{"action": "read", "assertions": [assertion]}]}],
        lambda *_: {"ok": True, "value": actual},
    )["results"][0]
    assert result["status"] == "passed"


def test_contains_matches_nested_network_event_summary():
    from drissionpage_mcp.workflows import test_execution
    response = {
        "ok": True,
        "signal": {
            "events": [{
                "type": "network",
                "packet": {
                    "api_target": "scm.purchase.order.save",
                    "status": 200,
                    "response": {"body": {"success": True, "document_no": "PO-001"}},
                },
            }],
        },
    }
    case = {
        "case_id": "NETWORK",
        "automation_recipe": [{
            "action": "save",
            "assertions": [{
                "path": "signal.events",
                "operator": "contains",
                "value": {"api_target": "scm.purchase.order.save", "status": 200},
            }],
        }],
    }

    result = test_execution.execute_cases([case], lambda *_: response)["results"][0]
    assert result["status"] == "passed"


def test_structured_recipe_hooks_and_cleanup_are_ordered():
    from drissionpage_mcp.workflows import test_execution
    calls = []

    def run(action, _args):
        calls.append(action)
        return {"ok": True, "flow_step": {"flow_id": "run", "sequence": len(calls)}}

    def before(case):
        calls.append("before:" + case["case_id"])
        return {"ok": True}

    def after(case, result):
        calls.append("after:" + case["case_id"] + ":" + result["status"])
        return {"ok": True}

    case = {
        "case_id": "FLOW-001",
        "automation_recipe": {
            "setup": [{"action": "reset"}],
            "steps": [{"action": "save", "expect": {"path": "ok", "equals": True}}],
            "cleanup": [{"action": "delete"}],
        },
    }
    result = test_execution.execute_cases([case], run, before_case=before, after_case=after)["results"][0]

    assert result["status"] == "passed"
    assert calls == ["before:FLOW-001", "reset", "save", "delete", "after:FLOW-001:passed"]
    assert [step["phase"] for step in result["steps"]] == ["setup", "setup", "steps", "cleanup", "cleanup"]


def test_reproduced_known_defect_is_xfailed_not_passed():
    from drissionpage_mcp.workflows import test_execution
    case = {
        "case_id": "DEFECT-1",
        "known_defect": {"defect_id": "SALARY-DEL-001", "status": "open"},
        "automation_recipe": {"steps": [{
            "action": "delete", "assertions": [{
                "path": "body.ok", "operator": "equals", "value": False,
            }],
        }]},
    }

    result = test_execution.execute_cases(
        [case], lambda *_: {"ok": True, "body": {"ok": False}},
    )["results"][0]

    assert result["status"] == "xfailed"
    assert result["known_defect"]["defect_id"] == "SALARY-DEL-001"
    assert "known defect reproduced" in result["reason"]


def test_setup_action_assertion_and_cleanup_failures_are_classified():
    from drissionpage_mcp.workflows import test_execution
    setup = test_execution.execute_cases(
        [{"case_id": "SETUP", "automation_recipe": {"setup": [{"action": "reset"}], "steps": []}}],
        lambda *_: {"ok": False, "reason": "cannot reset"},
    )["results"][0]
    assert setup["failure_type"] == "setup"

    action = test_execution.execute_cases(
        [{"case_id": "ACTION", "automation_recipe": [{"action": "save"}]}],
        lambda *_: {"ok": False, "reason": "request failed"},
    )["results"][0]
    assert action["failure_type"] == "action"

    cleanup = test_execution.execute_cases(
        [{"case_id": "CLEAN", "automation_recipe": {"steps": [{"action": "save"}], "cleanup": [{"action": "remove"}]}}],
        lambda action, _args: {"ok": action != "remove", "reason": "cannot remove"},
    )["results"][0]
    assert cleanup["failure_type"] == "cleanup"


def test_regression_detects_added_removed_status_performance_and_coverage():
    from drissionpage_mcp.workflows import test_reporting
    baseline = {
        "results": [
            {"case_id": "REMOVED", "status": "passed", "elapsed_ms": 10},
            {"case_id": "CHANGED", "status": "passed", "elapsed_ms": 100},
            {"case_id": "SLOW", "status": "passed", "elapsed_ms": 100},
        ],
        "coverage_summary": {"requirements": {"covered": 8, "total": 10}},
    }
    current = {
        "results": [
            {"case_id": "ADDED", "status": "passed", "elapsed_ms": 5},
            {"case_id": "CHANGED", "status": "failed", "elapsed_ms": 130},
            {"case_id": "SLOW", "status": "passed", "elapsed_ms": 130},
        ],
        "coverage_summary": {"requirements": {"covered": 9, "total": 10}},
    }

    regression = test_reporting.compare_regression(current, baseline)
    kinds = {change["kind"] for change in regression["changes"]}
    assert {"added", "removed", "status", "performance_regression", "coverage"} <= kinds
    assert not any(change.get("case_id") == "CHANGED" and change["kind"].startswith("performance_")
                   for change in regression["changes"])
    assert regression["summary"]["removed"] == 1


def test_report_contains_execution_metrics_structured_defects_and_authoritative_coverage():
    from drissionpage_mcp.workflows import test_reporting
    execution = {
        "results": [
            {
                "case_id": "PASS",
                "status": "passed",
                "elapsed_ms": 40,
                "steps": [{"phase": "steps", "sequence": 1, "action": "open", "elapsed_ms": 10}],
                "evidence_refs": [{"flow_id": "run", "sequence": 1, "screenshot": "pass.png"}],
            },
            {
                "case_id": "FAIL",
                "status": "failed",
                "elapsed_ms": 90,
                "reason": "保存反馈: assertion failed",
                "failure_type": "assertion",
                "failure_step": {"phase": "steps", "index": 2, "action": "save"},
                "expected": "保存成功",
                "actual": "保存失败",
                "steps": [{"phase": "steps", "sequence": 2, "action": "save", "elapsed_ms": 80}],
                "evidence_refs": [{"flow_id": "run", "sequence": 2, "screenshot": "fail.png"}],
            },
            {"case_id": "SKIP", "status": "skipped", "reason": "missing automation_recipe", "steps": []},
        ],
    }
    coverage = {
        "coverage_summary": {
            "requirements": {"label": "需求覆盖", "covered": 9, "total": 10},
            "risks": {"label": "风险覆盖", "covered": 4, "total": 5},
        },
    }

    report = test_reporting.render_markdown(execution, coverage)
    assert "| 已执行 | 2 |" in report
    assert "| 业务通过率 | 50.0% |" in report
    assert "需求覆盖" in report and "9 / 10" in report
    assert "风险覆盖" in report and "4 / 5" in report
    assert "P50" in report and "P95" in report and "最大步骤耗时" in report
    assert "assertion" in report and "保存成功" in report and "保存失败" in report
    assert "[run#2](fail.png)" in report


def test_report_prefers_explicit_coverage_dimensions_over_legacy_top_level_total():
    from drissionpage_mcp.workflows import test_reporting
    coverage = {
        "coverage_summary": {
            "verified": 9,
            "total": 12,
            "requirements": {"label": "需求场景", "verified": 9, "total": 12},
            "risks": {"label": "风险维度", "verified": 4, "total": 7},
            "assets": {"label": "页面资产", "verified": 15, "total": 18},
        },
    }

    report = test_reporting.render_markdown({"results": []}, coverage)

    assert "需求场景" in report and "9 / 12" in report
    assert "风险维度" in report and "4 / 7" in report
    assert "页面资产" in report and "15 / 18" in report


def test_report_renders_structured_known_defects_with_evidence():
    from drissionpage_mcp.workflows import test_reporting
    execution = {
        "results": [],
        "known_defects": [{
            "defect_id": "SALARY-DEL-001",
            "severity": "高级",
            "status": "open",
            "title": "删除工资明细返回系统异常",
            "expected": "记录删除且列表中不存在",
            "actual": "接口业务状态 -1，记录仍存在",
            "evidence_refs": [{"flow_id": "delete-flow", "sequence": 4, "screenshot": "delete-error.png"}],
        }],
    }

    report = test_reporting.render_markdown(execution)

    assert "SALARY-DEL-001" in report
    assert "接口业务状态 -1" in report
    assert "[delete-flow#4](delete-error.png)" in report


def test_report_separates_xfailed_known_defects_from_business_pass_rate():
    from drissionpage_mcp.workflows import test_reporting
    report = test_reporting.render_markdown({
        "results": [
            {"case_id": "PASS", "status": "passed", "steps": []},
            {"case_id": "DEFECT", "status": "xfailed", "steps": []},
        ],
    })

    assert "| 通过 | 1 |" in report
    assert "| 已知缺陷复现 | 1 |" in report
    assert "| 业务通过率 | 100.0% |" in report


def test_report_normalizes_windows_screenshot_paths_for_markdown_links():
    from drissionpage_mcp.workflows import test_reporting
    report = test_reporting.render_markdown({
        "results": [{
            "case_id": "WIN",
            "status": "passed",
            "evidence_refs": [{
                "flow_id": "run", "sequence": 1,
                "screenshot": r"D:\evidence folder\step.png",
            }],
        }],
    })

    assert "[run#1](/D:/evidence%20folder/step.png)" in report


def test_filter_recipe_quality_gate_rejects_stale_partial_match_pattern():
    from drissionpage_mcp.workflows import test_execution
    reasons = test_execution.weak_recipe_reasons({
        "automation_recipe": [
            {"action": "explore_action", "args": {
                "action": "click_xy", "x": 1496, "y": 168, "observe_mode": "none",
            }},
            {"action": "count_vtable_rows", "args": {
                "column_title": "生产部门", "value": "注塑车间",
            }, "assertions": [{"path": "match_count", "operator": "truthy"}]},
        ],
    })
    query_only = test_execution.weak_recipe_reasons({
        "automation_recipe": [{
            "action": "query_filter",
            "assertions": [{"path": "query_completed", "operator": "equals", "value": True}],
        }],
    })

    assert any("未使用 query_filter" in item for item in reasons)
    assert any("不能证明全量结果匹配" in item for item in reasons)
    assert any("全量列断言" in item for item in query_only)


def test_full_column_assertions_require_every_visible_row_to_match():
    from drissionpage_mcp.workflows import test_execution
    case = {"case_id": "ALL", "automation_recipe": [{
        "action": "get_table_values",
        "assertions": [{"path": "values", "operator": "all_equals", "value": "冲压部门"}],
    }]}
    passed = test_execution.execute_cases(
        [case], lambda *_: {"ok": True, "values": ["冲压部门", "冲压部门"]},
    )["results"][0]
    failed = test_execution.execute_cases(
        [case], lambda *_: {"ok": True, "values": ["冲压部门", "注塑车间"]},
    )["results"][0]

    assert passed["status"] == "passed"
    assert failed["status"] == "failed"


def test_query_filter_requires_a_successful_network_response(monkeypatch):
    from drissionpage_mcp import server
    monkeypatch.setattr(server.observe, "observe_start", lambda **_: {"ok": True})
    monkeypatch.setattr(server.filter_area, "submit_filter_area", lambda: {"ok": True, "clicked": "查询"})
    monkeypatch.setattr(server.browser_session, "get_active_frame", lambda: object())
    monkeypatch.setattr(server.vtable, "is_loading_complete", lambda *_: True)
    monkeypatch.setattr(server.observe, "observe_wait", lambda **_: {
        "type": "network", "status": 200, "packet": {"status": 200},
    })
    passed = server._run_recipe_action("query_filter", {})

    monkeypatch.setattr(server.observe, "observe_wait", lambda **_: {"type": "none"})
    failed = server._run_recipe_action("query_filter", {})

    assert passed["ok"] is True and passed["query_completed"] is True
    assert failed["ok"] is False and "查询未在" in failed["reason"]


def test_query_filter_requires_vtable_loading_mask_to_finish(monkeypatch):
    from drissionpage_mcp import server
    monkeypatch.setattr(server.observe, "observe_start", lambda **_: {"ok": True})
    monkeypatch.setattr(server.filter_area, "submit_filter_area", lambda: {"ok": True, "clicked": "查询"})
    monkeypatch.setattr(server.observe, "observe_wait", lambda **_: {
        "type": "network", "status": 200, "packet": {"status": 200},
    })
    monkeypatch.setattr(server.browser_session, "get_active_frame", lambda: object())
    monkeypatch.setattr(server.vtable, "is_loading_complete", lambda *_: False)

    result = server._run_recipe_action("query_filter", {})

    assert result["ok"] is False
    assert result["loading_complete"] is False
    assert "VTable 未稳定完成" in result["reason"]


def test_query_filter_rejects_business_failure_and_summarizes_network(monkeypatch):
    from drissionpage_mcp import server
    monkeypatch.setattr(server.observe, "observe_start", lambda **_: {"ok": True})
    monkeypatch.setattr(server.filter_area, "submit_filter_area", lambda: {"ok": True})
    monkeypatch.setattr(server.observe, "observe_wait", lambda **_: {
        "type": "network", "status": 200,
        "packet": {
            "status": 200, "url": "https://gateway/page", "method": "POST",
            "post_data": {"conditions": []},
            "body": {"ok": False, "msg": "查询失败", "data": {"records": [1, 2]}},
            "headers": {"api-cookie": "secret"},
        },
    })

    result = server._run_recipe_action("query_filter", {})

    assert result["ok"] is False and result["business_ok"] is False
    assert "业务响应失败" in result["reason"]
    assert result["network"]["response"] == {
        "ok": False, "status": None, "message": "查询失败", "total": None,
    }
    assert server._business_response_success({"status": -1, "msg": "系统异常"}) is False
    assert server._business_response_success({}) is True
    assert server._business_response_success({"success": "unknown"}) is False
    assert server._business_response_success({"success": 2}) is False
    assert server._business_response_success({"status": "processing"}) is False
    assert "headers" not in result["network"]


def test_formal_recipe_rejects_js_and_non_vtable_coordinate_clicks():
    from drissionpage_mcp import server
    from drissionpage_mcp.core import recipe_context
    recipe_context._recipe_context.native_actions_only = True
    try:
        js_click = server._run_recipe_action("explore_action", {
            "action": "click", "locator": "text:保存", "by_js": True,
        })
        coordinate_click = server._run_recipe_action("explore_action", {
            "action": "click_xy", "x": 100, "y": 100,
        })
    finally:
        recipe_context._recipe_context.native_actions_only = False

    assert js_click["ok"] is False and "禁止 by_js" in js_click["reason"]
    assert coordinate_click["ok"] is False and "禁止普通坐标点击" in coordinate_click["reason"]


def test_formal_recipe_rechecks_dynamic_refs_for_coordinates_and_mutation():
    from drissionpage_mcp import server
    from drissionpage_mcp.core import recipe_context
    recipe_context._recipe_context.native_actions_only = True
    try:
        server._reset_recipe_context()
        recipe_context._recipe_context.values = {
            "dynamic": {"locator": "text:保存", "target": {"type": "xy"}},
        }
        mutation = server._run_recipe_action(
            "click", {"locator": {"$ref": "dynamic.locator"}},
        )
        coordinate = server._run_recipe_action(
            "explore_action", {
                "action": "click", "target": {"$ref": "dynamic.target"},
            },
        )
    finally:
        recipe_context._recipe_context.native_actions_only = False
        server._reset_recipe_context()

    assert mutation["ok"] is False and "destructive=true" in mutation["reason"]
    assert coordinate["ok"] is False and "禁止普通坐标点击" in coordinate["reason"]


def test_formal_recipe_uses_drission_element_click_and_multi_click(monkeypatch):
    from drissionpage_mcp import server
    calls = []

    class Wait:
        def clickable(self, **kwargs):
            calls.append(("wait.clickable", kwargs))

    class Clicker:
        def __call__(self, **kwargs):
            calls.append(("click", kwargs))
            return True

        def multi(self, **kwargs):
            calls.append(("multi", kwargs))
            return True

    element = type("Element", (), {
        "wait": Wait(),
        "click": Clicker(),
        "states": type("States", (), {"is_clickable": True})(),
    })()
    monkeypatch.setattr(server.browser_session, "find", lambda *_args, **_kwargs: element)

    single = server._run_recipe_action("click", {"locator": "text:保存"})
    double = server._run_recipe_action("double_click", {"locator": "text:保存"})

    assert single["ok"] is True and single["method"] == "element.click"
    assert double["ok"] is True and double["method"] == "element.click.multi"
    assert ("click", {"by_js": False, "wait_stop": True}) in calls
    assert ("multi", {"times": 2}) in calls


def test_native_observer_wait_uses_drission_listener_without_python_sleep(monkeypatch):
    import queue
    from drissionpage_mcp.services import observe
    class Listener:
        def __init__(self):
            self.calls = []

        def wait(self, **kwargs):
            self.calls.append(kwargs)
            return {"packet": True}

    listener = Listener()
    session = {
        "watch_network": True,
        "tab": type("Tab", (), {"listen": listener})(),
        "net_queue": queue.Queue(),
        "start": observe.time.time(),
        "sigset": {"network"},
    }
    events = iter([None, {"type": "network", "elapsedMs": 1}])
    monkeypatch.setattr(observe, "_poll_once", lambda *_: next(events))
    monkeypatch.setattr(observe, "_teardown_session", lambda *_: None)
    monkeypatch.setattr(observe, "_attach_snapshot", lambda result, *_args, **_kwargs: result)
    monkeypatch.setattr(observe.time, "sleep", lambda *_: (_ for _ in ()).throw(AssertionError("sleep called")))

    result = observe._observe_wait_native(session, timeout=1, include_snapshot=False, detail="summary")

    assert result["type"] == "network"
    assert listener.calls == [{"count": 1, "timeout": 1, "fit_count": False, "raise_err": False}]


def test_run_test_cases_enables_native_mode_before_ready_gate(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp.workflows import flow_evidence
    from drissionpage_mcp import server
    from drissionpage_mcp.workflows import recipe_execution
    from drissionpage_mcp.workflows import test_execution
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    case_file = tmp_path / "cases.json"
    case_file.write_text(json.dumps({
        "module_info": {},
        "test_cases": [{
            "case_id": "NATIVE",
            "automation_recipe": [{
                "action": "click", "args": {"locator": "text:查询"},
                "assertions": [{"path": "signal.payload.message", "operator": "equals", "value": "查询完成"}],
            }],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    seen = []
    monkeypatch.setattr(recipe_execution, "_browser_ready_gate", lambda *_: seen.append(server._recipe_requires_native_actions()) or {"ok": True})
    monkeypatch.setattr(flow_evidence, "is_active", lambda: False)
    monkeypatch.setattr(flow_evidence, "start", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(flow_evidence, "stop", lambda: {"ok": True})
    monkeypatch.setattr(flow_evidence, "sanitize_artifact", lambda payload: {**payload, "sanitized": True})
    monkeypatch.setattr(test_execution, "execute_cases", lambda *_args, **_kwargs: {
        "results": [], "started_at": "", "finished_at": "",
    })

    result = server.run_test_cases(str(case_file), "native.json")

    assert result["ok"] is True
    assert seen == [True]
    assert server._recipe_requires_native_actions() is False
    assert result["execution"]["sanitized"] is True
    assert json.loads(Path(result["saved_to"]).read_text(encoding="utf-8"))["sanitized"] is True


def test_report_bundle_copies_execution_json_and_screenshots(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp import server
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    screenshot = tmp_path / "source.png"
    screenshot.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    ))
    execution = tmp_path / "execution.json"
    execution.write_text(json.dumps({
        "results": [{
            "case_id": "BUNDLE", "status": "passed", "steps": [],
            "evidence_refs": [{"flow_id": "run", "sequence": 1,
                               "artifacts": {"screenshot": str(screenshot)}}],
        }],
        "module_info": {"module_name": "归档验证"},
    }, ensure_ascii=False), encoding="utf-8")

    report = server.generate_test_report(str(execution), filename="bundle.md")
    bundle = tmp_path / "test_results" / "reports" / "归档验证" / "execution"

    assert report["ok"] is True
    assert report["bundle_dir"] == str(bundle)
    assert (bundle / "execution.json").is_file()
    assert (bundle / "assets" / "source.png").is_file()
    assert "(assets/source.png)" in (bundle / "bundle.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(("operator", "values", "expected"), [
    ("包含", ["1号工位", "2号工位"], "工位"),
    ("包含", [0], "0"),
    ("等于", ["生产报工", "生产报工"], "生产报工"),
    ("不等于", ["生产准备", None], "生产报工"),
    ("为空", ["", None], None),
    ("不为空", ["004", "诺贝小程序"], None),
    ("在列表中", ["生产准备", "生产报工"], ["生产准备", "生产报工"]),
    ("不在列表中（含空）", ["生产准备", "", None], ["生产报工"]),
    ("范围", ["2026-07-03", "2026-07-11 13:50:53"],
     {"start": "2026/07/03", "end": "2026/07/11"}),
])
def test_filter_value_evaluator_supports_page_operators(operator, values, expected):
    from drissionpage_mcp.workflows import test_execution
    result = test_execution.evaluate_filter_values(values, operator, expected)

    assert result["ok"] is True
    assert result["matched"] is True
    assert result["mismatch_count"] == 0


def test_filter_value_evaluator_rejects_mismatch_and_empty_result_by_default():
    from drissionpage_mcp.workflows import test_execution
    mismatch = test_execution.evaluate_filter_values(["生产准备", "生产报工"], "等于", "生产报工")
    empty = test_execution.evaluate_filter_values([], "包含", "工位")
    invalid_date = test_execution.evaluate_filter_values(
        ["2026-06-bad"], "范围", ["2026-01-01", "2026-12-31"],
    )
    mixed_bounds = test_execution.evaluate_filter_values(
        ["2026-06-01"], "范围", ["2026-01-01", "not-a-date"],
    )
    none_text = test_execution.evaluate_filter_values([None], "等于", "None")

    assert mismatch["matched"] is False
    assert mismatch["mismatches"] == [{"index": 0, "actual": "生产准备"}]
    assert empty["matched"] is False
    assert invalid_date["matched"] is False
    assert mixed_bounds["ok"] is False
    assert none_text["matched"] is False


def test_verify_filter_query_configures_once_queries_once_and_checks_every_column(monkeypatch):
    from drissionpage_mcp import server
    from drissionpage_mcp.workflows import recipe_execution
    configured = []
    monkeypatch.setattr(server.filter_area, "expand_filter_area", lambda: {"ok": True})
    monkeypatch.setattr(server.filter_area, "set_filter_condition", lambda field, operator, value, **kwargs: (
        configured.append((field, operator, value)) or {"ok": True}
    ))
    queries = []
    monkeypatch.setattr(recipe_execution, "_query_filter", lambda **kwargs: queries.append(kwargs) or {"ok": True})
    columns = {
        "工位名称": ["1号工位", "2号工位"],
        "操作模块": ["生产报工", "生产报工"],
    }
    monkeypatch.setattr(recipe_execution.table_facade, "get_table_values", lambda title, **kwargs: {
        "ok": True, "values": columns[title], "title": title,
    })

    result = server._verify_filter_query([
        {"field": "工位名称", "operator": "包含", "value": "工位"},
        {"field": "操作模块", "operator": "等于", "value": "生产报工"},
    ], timeout=12)

    assert result["ok"] is True and result["verified"] is True
    assert configured == [("工位名称", "包含", "工位"), ("操作模块", "等于", "生产报工")]
    assert queries == [{"timeout": 12, "listen_targets": "gateway"}]
    assert [item["evaluation"]["row_count"] for item in result["comparisons"]] == [2, 2]


def test_verify_filter_query_fails_when_any_corresponding_column_mismatches(monkeypatch):
    from drissionpage_mcp import server
    from drissionpage_mcp.workflows import recipe_execution
    monkeypatch.setattr(server.filter_area, "expand_filter_area", lambda: {"ok": True})
    monkeypatch.setattr(server.filter_area, "set_filter_condition", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(recipe_execution, "_query_filter", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(recipe_execution.table_facade, "get_table_values", lambda *args, **kwargs: {
        "ok": True, "values": ["生产准备", "生产报工"],
    })

    result = server._verify_filter_query([
        {"field": "操作模块", "operator": "等于", "value": "生产报工"},
    ])

    assert result["ok"] is False and result["verified"] is False
    assert result["comparisons"][0]["evaluation"]["mismatch_count"] == 1
    assert "操作模块" in result["reason"]


def test_recipe_quality_rejects_undeclared_mutation_and_malformed_assertions():
    from drissionpage_mcp.workflows import test_execution
    mutation = {
        "case_id": "MUTATE",
        "automation_recipe": {"steps": [{
            "action": "explore_action", "args": {"action": "click", "locator": "text:保存"},
            "assertions": [{"path": "signal.payload.message", "operator": "contains", "value": "成功"}],
        }]},
    }
    malformed = {
        "case_id": "MALFORMED",
        "automation_recipe": {"steps": [{"action": "read", "assertions": [{}]}]},
    }
    ambiguous = {
        "case_id": "AMBIGUOUS",
        "automation_recipe": {"steps": [{
            "action": "read", "assertions": [{"operator": "equals", "contains": "x"}],
        }]},
    }
    malformed_phase = {"case_id": "PHASE", "automation_recipe": {"steps": ""}}
    cleanup_mutation = {
        "case_id": "CLEANUP-MUTATE",
        "automation_recipe": {
            "steps": [{"action": "read", "assertions": [{"operator": "truthy"}]}],
            "cleanup": [{"action": "delete"}],
        },
    }
    invalid_metadata = dict(mutation, destructive="false", known_defect="BUG-1")
    missing_defect_id = dict(mutation, known_defect={})
    assert test_execution._evaluate_assertion([0], "all_each_contains", "0")[0] is True

    assert "破坏性操作必须显式声明 destructive=true" in test_execution.weak_recipe_reasons(mutation)
    assert "破坏性操作必须显式声明 destructive=true" in test_execution.weak_recipe_reasons(cleanup_mutation)
    assert any("missing operator" in reason for reason in test_execution.weak_recipe_reasons(malformed))
    assert any("cannot combine operator" in reason
               for reason in test_execution.weak_recipe_reasons(ambiguous))
    assert any("automation_recipe.steps must be a list" in reason
               for reason in test_execution.weak_recipe_reasons(malformed_phase))
    metadata_reasons = test_execution.weak_recipe_reasons(invalid_metadata)
    assert "destructive 必须是布尔值" in metadata_reasons
    assert "known_defect 必须是对象" in metadata_reasons
    assert "known_defect.defect_id 必须是非空字符串" in test_execution.weak_recipe_reasons(missing_defect_id)
    assert test_execution._evaluate_assertion("value", "contains", "")[0] is False
    assert test_execution._evaluate_assertion(
        {"data": [{"order_no": "PO-1", "amount": 7}]},
        "contains", {"data": [{"order_no": "PO-1", "amount": 7}]},
    )[0] is True
    assert test_execution._evaluate_assertion("a" * 1000 + "!", "regex", "(a?)+$")[0] is False
    assert test_execution._evaluate_assertion("a" * 1000 + "!", "regex", "a*a*b")[0] is False
    assert test_execution._evaluate_assertion(
        "https://example.test/items/42", "regex", r"^https?://[^/]+/items/\d+$",
    )[0] is True
    strict_ok = test_execution.execute_cases(
        [{"case_id": "STRICT", "automation_recipe": [{"action": "read"}]}],
        lambda *_: {"ok": "false"},
    )
    assert strict_ok["results"][0]["status"] == "failed"


def test_regression_classifies_direction_uses_strict_threshold_and_rejects_duplicate_ids():
    from drissionpage_mcp.workflows import test_reporting
    baseline = {
        "results": [
            {"case_id": "EXACT", "status": "passed", "elapsed_ms": 100},
            {"case_id": "DOWN", "status": "passed", "elapsed_ms": 100},
            {"case_id": "REMOVED", "status": "passed", "elapsed_ms": 10},
        ],
        "coverage_summary": {"requirements": {"covered": 8, "total": 10}},
    }
    current = {
        "results": [
            {"case_id": "EXACT", "status": "passed", "elapsed_ms": 120},
            {"case_id": "DOWN", "status": "failed", "elapsed_ms": 121},
        ],
        "coverage_summary": {"requirements": {"covered": 7, "total": 10}},
    }

    regression = test_reporting.compare_regression(current, baseline)
    assert not any(change["kind"].startswith("performance_") and change.get("case_id") == "EXACT"
                   for change in regression["changes"])
    assert any(change.get("case_id") == "DOWN" and change.get("direction") == "regression"
               for change in regression["changes"])
    assert any(change["kind"] == "coverage" and change["direction"] == "regression"
               for change in regression["changes"])
    assert regression["has_regressions"] is True

    duplicate = test_reporting.compare_regression(
        {"results": [{"case_id": "X", "status": "passed"},
                     {"case_id": "X", "status": "passed"}]}, {"results": []},
    )
    assert duplicate["ok"] is False
    assert "duplicate case ids" in duplicate["reason"]
    invalid = test_reporting.compare_regression(
        {"results": [{"case_id": "", "status": "passed"}]}, {"results": []},
    )
    assert invalid["ok"] is False
    assert "case_id must be a non-empty string" in invalid["reason"]


    skipped_to_failed = test_reporting.compare_regression(
        {"results": [{"case_id": "S", "status": "failed"}]},
        {"results": [{"case_id": "S", "status": "skipped"}]},
    )
    assert skipped_to_failed["changes"][0]["direction"] == "regression"
    added_failed = test_reporting.compare_regression(
        {"results": [{"case_id": "NEW", "status": "failed"}]}, {"results": []},
    )
    assert added_failed["has_regressions"] is True

def test_report_escapes_business_html_and_derives_xfailed_known_defect():
    from drissionpage_mcp.workflows import test_reporting
    report = test_reporting.render_markdown({
        "results": [{
            "case_id": "D001", "case_title": "<img src=x onerror=alert(1)> ![track](https://evil)",
            "status": "xfailed", "steps": [],
            "known_defect": {"defect_id": "BUG-1", "status": "open"},
            "evidence_refs": [{"flow_id": "f", "sequence": 1, "artifacts": "bad"}],
        }],
    })
    assert "<" not in test_reporting._markdown_target('step.png"><img src=x>')
    assert "%26#58;" in test_reporting._markdown_target("javascript&#58;alert(1)")

    assert "&lt;img src=x onerror=alert(1)&gt;" in report
    assert "\\!\\[track\\](https://evil)" in report
    assert "| BUG-1 |" in report
    assert "f#1" in report
    assert test_reporting._markdown_target("<javascript:alert(1)>") == "#unsafe-evidence-link"
    assert test_reporting._markdown_target("file:///tmp/secret.png") == "#unsafe-evidence-link"
    assert test_reporting._markdown_target("https://example.test/evidence.png") == "https://example.test/evidence.png"


def test_report_bundle_rejects_spoofed_image(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp import server
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    forged = tmp_path / "forged.png"
    forged.write_bytes(b"\x89PNG\r\n\x1a\nnot-an-image")
    execution = tmp_path / "forged-execution.json"
    execution.write_text(json.dumps({
        "results": [{
            "case_id": "FORGED", "status": "passed", "steps": [],
            "evidence_refs": [{"flow_id": "f", "sequence": 1, "screenshot": str(forged)}],
        }],
        "module_info": {"module_name": "伪造证据"},
    }, ensure_ascii=False), encoding="utf-8")

    report = server.generate_test_report(str(execution), filename="forged.md")

    assert report["ok"] is True
    assert report["copied_screenshots"] == 0
    assert report["missing_screenshots"] == [str(forged)]
    bundled_ref = json.loads(Path(report["execution_copy"]).read_text(encoding="utf-8"))["results"][0]["evidence_refs"][0]
    markdown = Path(report["saved_to"]).read_text(encoding="utf-8")
    assert bundled_ref["source_screenshot"] == str(forged)
    assert bundled_ref["screenshot_missing"] is True and "screenshot" not in bundled_ref
    assert str(forged) not in markdown


def test_browser_ready_gate_checks_active_frame_without_module_name(monkeypatch):
    from drissionpage_mcp import server
    from drissionpage_mcp.workflows import recipe_execution
    calls = []
    monkeypatch.setattr(recipe_execution, "connect", lambda *_: calls.append("connect") or {"ok": True})
    monkeypatch.setattr(recipe_execution, "check_session", lambda: {"expired": False})
    monkeypatch.setattr(recipe_execution, "get_active_frame", lambda: {"ok": True, "tab_name": "当前模块"})
    monkeypatch.setattr(recipe_execution, "enter_module", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("module navigation must not run")
    ))

    gate = server._browser_ready_gate("")

    assert gate["ok"] is True
    assert gate["entered"]["skipped"] is True
    assert calls == ["connect"]


def test_run_test_cases_skips_browser_when_every_case_fails_preflight(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp.workflows import flow_evidence
    from drissionpage_mcp.resources import resource_store
    from drissionpage_mcp import server
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    monkeypatch.setattr(flow_evidence, "_active_flow", None)
    monkeypatch.setattr(flow_evidence, "_last_flow", None)
    resource_store.clear_module()
    case_file = tmp_path / "rejected.json"
    case_file.write_text(json.dumps({
        "test_cases": [{
            "case_id": "REJECTED",
            "automation_recipe": [{"action": "click", "args": {"locator": "text:查询"}}],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(server, "_browser_ready_gate", lambda *_: (_ for _ in ()).throw(
        AssertionError("browser gate must be skipped")
    ))

    result = server.run_test_cases(str(case_file), "rejected-execution.json")

    assert result["ok"] is True
    assert result["counts"] == {"passed": 0, "failed": 0, "xfailed": 0, "skipped": 1}
    assert "至少一个业务断言" in result["execution"]["results"][0]["reason"]


def test_report_bundle_resolves_screenshot_relative_to_execution_file(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp import server
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    source_dir = tmp_path / "saved-run"
    source_assets = source_dir / "assets"
    source_assets.mkdir(parents=True)
    (source_assets / "relative.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    ))
    execution = source_dir / "execution.json"
    execution.write_text(json.dumps({
        "results": [{
            "case_id": "RELATIVE", "status": "passed", "steps": [],
            "evidence_refs": [{"flow_id": "f", "sequence": 1,
                               "screenshot": "assets/relative.png"}],
        }],
        "module_info": {"module_name": "相对证据"},
    }, ensure_ascii=False), encoding="utf-8")

    report = server.generate_test_report(str(execution), filename="relative.md")

    assert report["ok"] is True
    assert report["copied_screenshots"] == 1
    assert (Path(report["assets_dir"]) / "relative.png").is_file()
    rerun = server.generate_test_report(
        report["execution_copy"],
        filename=str(Path(report["bundle_dir"]) / "relative-rerun.md"),
    )
    assert rerun["ok"] is True
    assert rerun["copied_screenshots"] == 1
    assert rerun["missing_screenshots"] == []


def test_report_bundle_preserves_more_than_default_sanitize_limit(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp.resources import resource_store
    from drissionpage_mcp import server
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    execution_file = tmp_path / "large-execution.json"
    results = [
        {"case_id": "C%04d" % index, "status": "passed", "steps": []}
        for index in range(2_001)
    ]
    results[0].update({
        "steps": 1, "evidence_refs": 1, "cleanup_failures": 1,
        "elapsed_ms": 10 ** 400,
    })
    execution_file.write_text(json.dumps({
        "results": results,
        "module_info": "malformed",
        "ready_gate": "malformed",
        "known_defects": 1,
    }), encoding="utf-8")

    report = server.generate_test_report(str(execution_file), filename="large.md")

    assert report["ok"] is True
    copied = resource_store.read_json_resource(report["execution_copy"])
    assert len(copied["results"]) == 2_001


def test_generate_report_rejects_non_object_results_and_non_list_supplements(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp import server
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    malformed = tmp_path / "malformed.json"
    malformed.write_text(json.dumps({"results": [1]}), encoding="utf-8")
    rejected = server.generate_test_report(str(malformed), filename="malformed.md")
    assert rejected["ok"] is False
    assert "entries must be objects" in rejected["reason"]

    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps({"results": []}), encoding="utf-8")
    rejected = server.generate_test_report(
        str(valid), supplemental_execution_files="", filename="valid.md",
    )
    assert rejected["ok"] is False
    assert "supplemental_execution_files" in rejected["reason"]


def test_recipe_gate_requires_business_and_cleanup_assertions_per_query_window():
    from drissionpage_mcp.workflows import test_execution
    root_only = test_execution.weak_recipe_reasons({
        "case_id": "ROOT",
        "automation_recipe": {"steps": [{
            "action": "click", "assertions": [
                {"path": "ok", "operator": "equals", "value": True},
            ],
        }]},
    })
    cleanup_only_ok = test_execution.weak_recipe_reasons({
        "case_id": "CLEANUP", "destructive": True,
        "automation_recipe": {
            "steps": [{
                "action": "get_table_values", "assertions": [
                    {"path": "values", "operator": "all_equals", "value": "A"},
                ],
            }],
            "cleanup": [{
                "action": "delete", "assertions": [
                    {"path": "ok", "operator": "equals", "value": True},
                ],
            }],
        },
    })
    two_queries_one_assertion = test_execution.weak_recipe_reasons({
        "case_id": "WINDOWS",
        "automation_recipe": {"steps": [
            {"action": "query_filter", "assertions": [
                {"path": "query_completed", "operator": "equals", "value": True},
            ]},
            {"action": "query_filter", "assertions": [
                {"path": "query_completed", "operator": "equals", "value": True},
            ]},
            {"action": "get_table_values", "assertions": [
                {"path": "values", "operator": "all_equals", "value": "A"},
            ]},
        ]},
    })

    assert any("根级 ok" in reason for reason in root_only)
    assert any("业务清理断言" in reason for reason in cleanup_only_ok)
    assert any("下一次查询前" in reason for reason in two_queries_one_assertion)


def test_command_validation_precedes_action_and_serialized_false_is_falsy(monkeypatch):
    from drissionpage_mcp.workflows import test_execution
    calls = []
    assertions = [
        {"path": "value", "operator": "equals", "value": index}
        for index in range(test_execution._MAX_ASSERTIONS_PER_COMMAND + 1)
    ]
    result = test_execution.execute_cases([{
        "case_id": "LIMIT", "automation_recipe": [{
            "action": "delete", "assertions": assertions,
        }],
    }], lambda *_: calls.append("called") or {"ok": True, "value": 1})["results"][0]

    assert calls == []
    assert result["status"] == "failed" and result["failure_type"] == "assertion"
    assert test_execution._evaluate_assertion("false", "truthy", None)[0] is False
    assert test_execution._evaluate_assertion("0", "falsy", None)[0] is True
    reasons = test_execution.weak_recipe_reasons({
        "case_id": "IN", "automation_recipe": [{
            "action": "read", "assertions": [
                {"path": "status", "operator": "in", "value": "passed"},
            ],
        }],
    })
    assert any("requires a collection" in reason for reason in reasons)


def test_all_rejected_suite_does_not_touch_browser_or_active_flow(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp.workflows import flow_evidence
    from drissionpage_mcp import server
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    case_file = tmp_path / "rejected-only.json"
    case_file.write_text(json.dumps({
        "test_cases": [{
            "case_id": "BAD", "automation_recipe": [{
                "action": "click", "assertions": [
                    {"path": "ok", "operator": "equals", "value": True},
                ],
            }],
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(flow_evidence, "is_active", lambda: True)
    monkeypatch.setattr(flow_evidence, "start", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("flow start must be skipped")
    ))
    monkeypatch.setattr(flow_evidence, "stop", lambda: (_ for _ in ()).throw(
        AssertionError("active exploration flow must not be stopped")
    ))
    monkeypatch.setattr(server, "_browser_ready_gate", lambda *_: (_ for _ in ()).throw(
        AssertionError("browser gate must be skipped")
    ))

    result = server.run_test_cases(str(case_file), "rejected-only-execution.json")

    assert result["ok"] is True
    assert result["counts"]["skipped"] == 1
    assert result["execution"]["evidence_flow"]["skipped"] is True


def test_run_test_cases_preserves_source_order_across_preflight(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp.workflows import flow_evidence
    from drissionpage_mcp import server
    from drissionpage_mcp.workflows import recipe_execution
    from drissionpage_mcp.workflows import test_execution
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    case_file = tmp_path / "ordered.json"
    business = [{
        "action": "get_table_values", "assertions": [
            {"path": "values", "operator": "all_equals", "value": "A"},
        ],
    }]
    case_file.write_text(json.dumps({"test_cases": [
        {"case_id": "V1", "automation_recipe": business},
        {"case_id": "BAD", "automation_recipe": [{
            "action": "click", "assertions": [{"path": "ok", "operator": "equals", "value": True}],
        }]},
        {"case_id": "V2", "automation_recipe": business},
    ]}), encoding="utf-8")
    active = {"value": False}
    monkeypatch.setattr(recipe_execution, "_browser_ready_gate", lambda *_: {"ok": True})
    monkeypatch.setattr(flow_evidence, "is_active", lambda: active["value"])
    monkeypatch.setattr(flow_evidence, "start", lambda *_args, **_kwargs: active.update(value=True) or {"ok": True})
    monkeypatch.setattr(flow_evidence, "stop", lambda: active.update(value=False) or {"ok": True})
    monkeypatch.setattr(test_execution, "execute_cases", lambda cases, *_args, **_kwargs: {
        "schema_version": "1.0", "started_at": "", "finished_at": "",
        "results": [{"case_id": case["case_id"], "status": "passed", "steps": []} for case in cases],
    })

    result = server.run_test_cases(str(case_file), "ordered-execution.json")

    assert [item["case_id"] for item in result["execution"]["results"]] == ["V1", "BAD", "V2"]
    assert [item["status"] for item in result["execution"]["results"]] == ["passed", "skipped", "passed"]


def test_recipe_runtime_rejects_boolean_numeric_and_string_boolean_args():
    from drissionpage_mcp import server
    timeout = server._run_recipe_action("find_elements", {"locator": "text:查询", "timeout": True})
    raw = server._run_recipe_action("get_table_values", {"column_title": "状态", "raw": "false"})

    assert timeout == {"ok": False, "reason": "timeout must be numeric"}
    assert raw == {"ok": False, "reason": "raw must be a boolean"}


def test_report_rejects_invalid_baseline_and_cross_module_supplement(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp import server
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    current = tmp_path / "current-report.json"
    baseline = tmp_path / "bad-baseline.json"
    supplement = tmp_path / "other-module.json"
    current.write_text(json.dumps({
        "module_info": {"module_name": "工资明细"},
        "results": [{"case_id": "A", "status": "passed", "steps": []}],
    }, ensure_ascii=False), encoding="utf-8")
    baseline.write_text(json.dumps({"results": [
        {"case_id": "A", "status": "passed"},
        {"case_id": "A", "status": "passed"},
    ]}), encoding="utf-8")
    supplement.write_text(json.dumps({
        "module_info": {"module_name": "采购订单"},
        "results": [{"case_id": "B", "status": "passed", "steps": []}],
    }, ensure_ascii=False), encoding="utf-8")

    invalid_baseline = server.generate_test_report(
        str(current), baseline_file=str(baseline), filename="invalid-baseline.md",
    )
    cross_module = server.generate_test_report(
        str(current), supplemental_execution_files=[str(supplement)], filename="cross-module.md",
    )

    assert invalid_baseline["ok"] is False and "regression comparison failed" in invalid_baseline["reason"]
    assert cross_module["ok"] is False and "module_info conflicts" in cross_module["reason"]


def test_report_bundles_coverage_evidence_and_excludes_harness_from_business_rate(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp.resources import resource_store
    from drissionpage_mcp import server
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    screenshot = tmp_path / "coverage.png"
    screenshot.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    ))
    execution = tmp_path / "coverage-execution.json"
    execution.write_text(json.dumps({
        "results": [
            {"case_id": "PASS", "status": "passed", "steps": []},
            {"case_id": "__HARNESS__", "status": "failed", "failure_type": "harness", "steps": []},
        ],
        "coverage_matrix": [{
            "coverage_id": "GAP", "function": "备注", "risk": "边界值",
            "scenario": "超长文本", "status": "待验证",
            "asset_evidence_refs": [{"flow_id": "asset", "sequence": 1,
                                     "screenshot": str(screenshot)}],
        }],
    }, ensure_ascii=False), encoding="utf-8")

    report = server.generate_test_report(str(execution), filename="coverage-evidence.md")
    bundled = resource_store.read_json_resource(report["execution_copy"])
    markdown = resource_store.read_text_resource(report["saved_to"])

    ref = bundled["coverage_matrix"][0]["asset_evidence_refs"][0]
    assert ref["screenshot"] == "assets/coverage.png"
    assert "[asset#1](assets/coverage.png)" in markdown
    assert "| 计划用例 | 1 |" in markdown
    assert "| 失败 | 0 |" in markdown
    assert "| 回放框架失败 | 1 |" in markdown
    assert "| 业务通过率 | 100.0% |" in markdown


def test_report_deduplicates_known_defect_and_regression_rejects_duplicate_coverage():
    from drissionpage_mcp.workflows import test_reporting
    report = test_reporting.render_markdown({
        "known_defects": [{"defect_id": "BUG-1", "title": "删除失败"}],
        "results": [{
            "case_id": "D1", "case_title": "删除失败", "status": "xfailed", "steps": [],
            "known_defect": {"defect_id": "BUG-1", "status": "open"},
        }],
    })
    duplicate_coverage = test_reporting.compare_regression(
        {"results": [], "coverage_summary": [
            {"key": "requirements", "covered": 1, "total": 2},
            {"key": "requirements", "covered": 1, "total": 2},
        ]},
        {"results": [], "coverage_summary": []},
    )

    assert report.count("| BUG-1 |") == 1
    assert duplicate_coverage["ok"] is False
    assert "duplicate coverage dimensions" in duplicate_coverage["reason"]


def test_filter_range_rejects_lexical_bounds_and_boolean_coercion():
    from drissionpage_mcp import server
    from drissionpage_mcp.workflows import test_execution
    lexical = test_execution.evaluate_filter_values(["B"], "范围", ["A", "Z"])
    boolean_number = test_execution.evaluate_filter_values([True], "等于", 1)
    invalid_flags = server._verify_filter_query([], allow_empty="false")
    invalid_condition_flag = server._verify_filter_query([{
        "field": "状态", "operator": "等于", "value": "完成", "allow_empty": "false",
    }])

    assert lexical["ok"] is False and "valid dates or finite numbers" in lexical["reason"]
    assert boolean_number["ok"] is True and boolean_number["matched"] is False
    assert invalid_flags["ok"] is False and "布尔值" in invalid_flags["reason"]
    assert invalid_condition_flag["ok"] is False and "allow_empty" in invalid_condition_flag["reason"]
