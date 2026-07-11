"""Tests for evidence-driven testcase, execution, report, and regression flow."""
import json
from unittest.mock import patch


def _observed_result(message="保存成功"):
    return {
        "ok": True,
        "target": {"text": "保存", "area": "toolbar"},
        "action": {"ok": True, "action": "click"},
        "signal": {
            "type": "message",
            "payload": {"message": message},
            "events": [{
                "type": "network", "url": "https://example.test/gateway", "method": "POST",
                "api_target": "scm.order.save", "status": 200,
                "packet": {"request": {"headers": {"Authorization": "secret"}, "post_data": '{"password":"secret","name":"order"}'},
                           "response": {"body": {"ok": True}}},
            }],
        },
        "before": None,
        "after": None,
    }


def test_evidence_to_case_execution_report_and_regression(monkeypatch, tmp_path):
    import config
    import flow_evidence
    import resource_store
    import test_execution
    import test_reporting
    import testcase_generation

    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    resource_store.clear_module()
    monkeypatch.setattr(flow_evidence, "_active_flow", None)
    monkeypatch.setattr(flow_evidence, "_last_flow", None)

    assert flow_evidence.start("采购入库", "保存单据")["ok"] is True
    page_state = flow_evidence.record_page_state("初始页面", {"ok": True, "actions": [{"text": "保存"}]})
    assert page_state["page_state_sequence"] == 1
    reference = flow_evidence.record_exploration(
        {"action": "click", "locator": "text:保存"}, _observed_result(), elapsed_ms=125,
        screenshot="evidence/step-1.png",
    )
    assert reference == {
        "flow_id": flow_evidence.status()["flow"]["flow_id"],
        "sequence": 1,
        "screenshot": "evidence/step-1.png",
    }
    stopped = flow_evidence.stop()
    assert stopped["ok"] is True

    loaded = flow_evidence.load(stopped["saved_to"])
    step = loaded["flow"]["steps"][0]
    assert step["network"][0]["request"]["headers"]["Authorization"] == "[REDACTED]"
    assert '"password":"[REDACTED]"' in step["network"][0]["request"]["post_data"]
    assert step["artifacts"]["screenshot"] == "evidence/step-1.png"
    assert step["artifacts"]["page_state_sequence"] == 1

    generated = testcase_generation.generate_verified_cases(loaded["flow"], {"module_pinyin": "CG"})
    assert len(generated["test_cases"]) == 1
    assert generated["test_cases"][0]["expected_result"] == "页面显示“保存成功”"
    assert generated["unverified_count"] == 2

    input_flow = dict(loaded["flow"])
    input_flow["steps"] = [dict(loaded["flow"]["steps"][0])]
    input_flow["steps"][0]["action"] = {"name": "input", "input": {"action": "input", "field_name": "订单号", "text": "PO20260711"}}
    input_cases = testcase_generation.generate_verified_cases(input_flow, {})["test_cases"]
    assert input_cases[0]["test_data"] == {"订单号": "PO20260711"}

    execution = test_execution.execute_cases(
        generated["test_cases"], lambda action, args: {
            "ok": action == "explore_action",
            "args": args,
            "signal": {
                "type": "message",
                "payload": {"message": "保存成功"},
                "events": [{"type": "network", "api_target": "scm.order.save", "status": 200}],
            },
        }
    )
    assert execution["results"][0]["status"] == "passed"

    baseline = {"results": [{"case_id": generated["test_cases"][0]["case_id"], "status": "failed", "elapsed_ms": 10}]}
    regression = test_reporting.compare_regression(execution, baseline)
    assert regression["changes"][0]["kind"] == "status"
    report = test_reporting.render_markdown(execution, generated["coverage_matrix"], regression)
    assert "自动化测试报告" in report
    assert "已验证覆盖场景" in report


def test_execution_marks_missing_recipe_as_skipped():
    import test_execution

    execution = test_execution.execute_cases([{"case_id": "I001"}], lambda *_: {"ok": True})
    assert execution["results"] == [{"case_id": "I001", "status": "skipped", "reason": "missing automation_recipe", "steps": []}]


def test_resource_store_reads_absolute_path_inside_resource_root(monkeypatch, tmp_path):
    import config
    import resource_store

    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    target = tmp_path / "result.json"
    target.write_text(json.dumps({"ok": True}), encoding="utf-8")
    assert resource_store.read_text_resource(str(target)) == '{"ok": true}'


def test_flow_stop_persists_cleanup_boundary_for_destructive_replay(monkeypatch, tmp_path):
    import config
    import flow_evidence
    import resource_store

    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    resource_store.clear_module()
    monkeypatch.setattr(flow_evidence, "_active_flow", None)
    monkeypatch.setattr(flow_evidence, "_last_flow", None)
    flow_evidence.start("工资明细", destructive=True)
    flow_evidence.record_exploration(
        {"action": "click", "locator": "text:添加"},
        {"action": {"ok": True, "action": "click"}, "signal": {"type": "none"}},
        elapsed_ms=1,
    )
    flow_evidence.record_exploration(
        {"action": "click", "locator": "text:删除"},
        {"action": {"ok": True, "action": "click"}, "signal": {"type": "message", "payload": {"message": "删除成功"}}},
        elapsed_ms=1,
    )

    stopped = flow_evidence.stop(cleanup_from_sequence=2)
    loaded = flow_evidence.load(stopped["saved_to"])["flow"]

    assert loaded["destructive"] is True
    assert loaded["cleanup_from_sequence"] == 2


def test_mcp_tools_chain_evidence_to_markdown_report(monkeypatch, tmp_path):
    import config
    import flow_evidence
    import resource_store
    import server

    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    resource_store.clear_module()
    monkeypatch.setattr(flow_evidence, "_active_flow", None)
    monkeypatch.setattr(flow_evidence, "_last_flow", None)

    server.flow_start("采购入库", capture_screenshots=False)
    flow_evidence.record_exploration(
        {"action": "click", "locator": "text:保存"}, _observed_result(), elapsed_ms=15,
    )
    flow_file = server.flow_stop()["saved_to"]

    generated = server.generate_test_cases_from_flow(flow_file, {"module_pinyin": "CG"}, "cases.json")
    assert generated["saved_to"]
    with patch.object(server, "explore_action", return_value={
        "ok": True,
        "signal": {
            "type": "message",
            "payload": {"message": "保存成功"},
            "events": [{"type": "network", "api_target": "scm.order.save", "status": 200}],
        },
    }):
        execution = server.run_test_cases(generated["saved_to"], "execution.json")
    assert execution["counts"] == {"passed": 1, "failed": 0, "xfailed": 0, "skipped": 0}

    report = server.generate_test_report(
        execution["saved_to"], coverage_file=generated["saved_to"], filename="report.md",
    )
    assert report["ok"] is True
    assert "自动化测试报告" in resource_store.read_text_resource(report["saved_to"])


def test_generate_report_merges_supplemental_xfailed_execution(monkeypatch, tmp_path):
    import config
    import resource_store
    import server

    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    current = tmp_path / "current.json"
    supplemental = tmp_path / "defect.json"
    current.write_text(json.dumps({
        "results": [{"case_id": "PASS", "status": "passed", "steps": []}],
        "module_info": {"module_name": "工资明细"},
    }, ensure_ascii=False), encoding="utf-8")
    supplemental.write_text(json.dumps({
        "results": [{"case_id": "D001", "status": "xfailed", "steps": []}],
    }, ensure_ascii=False), encoding="utf-8")

    report = server.generate_test_report(
        str(current), supplemental_execution_files=[str(supplemental)], filename="report.md",
    )
    markdown = resource_store.read_text_resource(report["saved_to"])

    assert "| 已知缺陷复现 | 1 |" in markdown
    assert "| 补充执行文件 | 1 |" in markdown
    assert "| D001 |" in markdown
