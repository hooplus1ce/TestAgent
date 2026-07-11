"""Focused tests for reproducible execution and trustworthy reporting."""
from __future__ import annotations

import pytest


def test_business_assertion_prevents_false_pass_and_uses_execution_evidence():
    import test_execution

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
    import test_execution

    assertion = {"path": "value", "operator": operator}
    if expected is not None:
        assertion["value"] = expected
    result = test_execution.execute_cases(
        [{"case_id": "OP", "automation_recipe": [{"action": "read", "assertions": [assertion]}]}],
        lambda *_: {"ok": True, "value": actual},
    )["results"][0]
    assert result["status"] == "passed"


def test_contains_matches_nested_network_event_summary():
    import test_execution

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
    import test_execution

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
    import test_execution

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
    import test_execution

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
    import test_reporting

    baseline = {
        "results": [
            {"case_id": "REMOVED", "status": "passed", "elapsed_ms": 10},
            {"case_id": "CHANGED", "status": "passed", "elapsed_ms": 100},
        ],
        "coverage_summary": {"requirements": {"covered": 8, "total": 10}},
    }
    current = {
        "results": [
            {"case_id": "ADDED", "status": "passed", "elapsed_ms": 5},
            {"case_id": "CHANGED", "status": "failed", "elapsed_ms": 130},
        ],
        "coverage_summary": {"requirements": {"covered": 9, "total": 10}},
    }

    regression = test_reporting.compare_regression(current, baseline)
    kinds = {change["kind"] for change in regression["changes"]}
    assert {"added", "removed", "status", "performance_regression", "coverage"} <= kinds
    assert regression["summary"]["removed"] == 1


def test_report_contains_execution_metrics_structured_defects_and_authoritative_coverage():
    import test_reporting

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
    import test_reporting

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
    import test_reporting

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
    import test_reporting

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
    import test_reporting

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

    assert "[run#1](</D:/evidence folder/step.png>)" in report
