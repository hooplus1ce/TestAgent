"""Focused tests for reproducible execution and trustworthy reporting."""
from __future__ import annotations

import json

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


def test_filter_recipe_quality_gate_rejects_stale_partial_match_pattern():
    import test_execution

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

    assert any("未使用 query_filter" in item for item in reasons)
    assert any("不能证明全量结果匹配" in item for item in reasons)


def test_full_column_assertions_require_every_visible_row_to_match():
    import test_execution

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
    import server

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
    import server

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
    assert "加载遮罩" in result["reason"]


def test_formal_recipe_rejects_js_and_non_vtable_coordinate_clicks():
    import server

    server._recipe_context.native_actions_only = True
    try:
        js_click = server._run_recipe_action("explore_action", {
            "action": "click", "locator": "text:保存", "by_js": True,
        })
        coordinate_click = server._run_recipe_action("explore_action", {
            "action": "click_xy", "x": 100, "y": 100,
        })
    finally:
        server._recipe_context.native_actions_only = False

    assert js_click["ok"] is False and "禁止 by_js" in js_click["reason"]
    assert coordinate_click["ok"] is False and "禁止普通坐标点击" in coordinate_click["reason"]


def test_formal_recipe_uses_drission_element_click_and_multi_click(monkeypatch):
    import server

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
    import observe

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
    import config
    import flow_evidence
    import server
    import test_execution

    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    case_file = tmp_path / "cases.json"
    case_file.write_text(json.dumps({
        "module_info": {},
        "test_cases": [{"case_id": "NATIVE", "automation_recipe": [{"action": "click", "args": {"locator": "text:保存"}}]}],
    }, ensure_ascii=False), encoding="utf-8")
    seen = []
    monkeypatch.setattr(server, "_browser_ready_gate", lambda *_: seen.append(server._recipe_requires_native_actions()) or {"ok": True})
    monkeypatch.setattr(flow_evidence, "is_active", lambda: False)
    monkeypatch.setattr(flow_evidence, "start", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(flow_evidence, "stop", lambda: {"ok": True})
    monkeypatch.setattr(flow_evidence, "sanitize", lambda payload: payload)
    monkeypatch.setattr(test_execution, "execute_cases", lambda *_args, **_kwargs: {
        "results": [], "started_at": "", "finished_at": "",
    })

    result = server.run_test_cases(str(case_file), "native.json")

    assert result["ok"] is True
    assert seen == [True]
    assert server._recipe_requires_native_actions() is False


def test_report_bundle_copies_execution_json_and_screenshots(monkeypatch, tmp_path):
    import config
    import server

    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    screenshot = tmp_path / "source.png"
    screenshot.write_bytes(b"png")
    execution = tmp_path / "execution.json"
    execution.write_text(json.dumps({
        "results": [{
            "case_id": "BUNDLE", "status": "passed", "steps": [],
            "evidence_refs": [{"flow_id": "run", "sequence": 1, "screenshot": str(screenshot)}],
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
