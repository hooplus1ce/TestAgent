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
    from drissionpage_mcp.core import config
    from drissionpage_mcp.workflows import flow_evidence
    from drissionpage_mcp.resources import resource_store
    from drissionpage_mcp.workflows import test_execution
    from drissionpage_mcp.workflows import test_reporting
    from drissionpage_mcp.workflows import testcase_generation
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    resource_store.clear_module()
    monkeypatch.setattr(flow_evidence, "_active_flow", None)
    monkeypatch.setattr(flow_evidence, "_last_flow", None)

    assert flow_evidence.start("采购入库", "保存单据", destructive=True)["ok"] is True
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
    cleanup_result = _observed_result("恢复成功")
    cleanup_result["target"]["text"] = "恢复测试数据"
    flow_evidence.record_exploration(
        {"action": "click", "locator": "text:恢复测试数据"}, cleanup_result, elapsed_ms=20,
    )
    stopped = flow_evidence.stop(cleanup_from_sequence=2)
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
    assert generated["unverified_count"] == sum(
        row["status"] != "已验证" for row in generated["coverage_matrix"]
    )

    input_flow = dict(loaded["flow"])
    input_flow["steps"] = [dict(loaded["flow"]["steps"][0])]
    input_flow["destructive"] = False
    input_flow.pop("cleanup_from_sequence", None)
    input_flow["steps"][0]["action"] = {"name": "input", "input": {"action": "input", "field_name": "订单号", "text": "PO20260711"}}
    input_cases = testcase_generation.generate_verified_cases(input_flow, {})["test_cases"]
    assert input_cases[0]["test_data"] == {"订单号": "PO20260711"}

    def replay(_action, args):
        message = "恢复成功" if "恢复" in str(args.get("locator", "")) else "保存成功"
        return {
            "ok": True, "args": args,
            "signal": {
                "type": "message", "payload": {"message": message},
                "events": [{"type": "network", "api_target": "scm.order.save", "status": 200}],
            },
        }

    execution = test_execution.execute_cases(generated["test_cases"], replay)
    assert execution["results"][0]["status"] == "passed"

    baseline = {"results": [{"case_id": generated["test_cases"][0]["case_id"], "status": "failed", "elapsed_ms": 10}]}
    regression = test_reporting.compare_regression(execution, baseline)
    assert regression["changes"][0]["kind"] == "status"
    report = test_reporting.render_markdown(execution, generated["coverage_matrix"], regression)
    assert "自动化测试报告" in report
    assert "已验证覆盖场景" in report


def test_execution_marks_missing_recipe_as_skipped():
    from drissionpage_mcp.workflows import test_execution
    execution = test_execution.execute_cases([{"case_id": "I001"}], lambda *_: {"ok": True})
    assert execution["results"] == [{"case_id": "I001", "case_title": "", "status": "skipped", "reason": "missing automation_recipe", "steps": []}]


def test_resource_store_reads_absolute_path_inside_resource_root(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp.resources import resource_store
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    target = tmp_path / "result.json"
    target.write_text(json.dumps({"ok": True}), encoding="utf-8")
    assert resource_store.read_text_resource(str(target)) == '{"ok": true}'


def test_flow_stop_persists_cleanup_boundary_for_destructive_replay(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp.workflows import flow_evidence
    from drissionpage_mcp.resources import resource_store
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
    from drissionpage_mcp.core import config
    from drissionpage_mcp.workflows import flow_evidence
    from drissionpage_mcp.resources import resource_store
    from drissionpage_mcp import server
    from drissionpage_mcp.workflows import recipe_execution
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    resource_store.clear_module()
    monkeypatch.setattr(flow_evidence, "_active_flow", None)
    monkeypatch.setattr(flow_evidence, "_last_flow", None)

    server.flow_start("采购入库", capture_screenshots=False, destructive=True)
    flow_evidence.record_exploration(
        {"action": "click", "locator": "text:保存"}, _observed_result(), elapsed_ms=15,
    )
    cleanup_result = _observed_result("恢复成功")
    cleanup_result["target"]["text"] = "恢复测试数据"
    flow_evidence.record_exploration(
        {"action": "click", "locator": "text:恢复测试数据"}, cleanup_result, elapsed_ms=10,
    )
    flow_file = server.flow_stop(cleanup_from_sequence=2)["saved_to"]

    generated = server.generate_test_cases_from_flow(
        flow_file, {"module_pinyin": "CG", "api_key": "secret"}, "cases.json",
    )
    assert generated["saved_to"]
    assert generated["module_info"]["api_key"] == "[REDACTED]"

    def replay_action(**kwargs):
        message = "恢复成功" if "恢复" in str(kwargs.get("locator", "")) else "保存成功"
        return {
            "ok": True,
            "signal": {
                "type": "message", "payload": {"message": message},
                "events": [{"type": "network", "api_target": "scm.order.save", "status": 200}],
            },
        }

    network_result = {"type": "network", "status": 200,
                      "packet": {"status": 200, "body": {"ok": True}}}
    with patch.object(recipe_execution, "_browser_ready_gate", return_value={"ok": True}), \
            patch.object(recipe_execution.table_facade, "pre_click_cleanup", return_value={"errors": []}), \
            patch.object(server.filter_area, "reset_filter_area", return_value={"ok": True}), \
            patch.object(server.observe, "observe_start", return_value={"ok": True}), \
            patch.object(server.observe, "observe_wait", return_value=network_result), \
            patch.object(server.browser_session, "get_active_frame", return_value=object()), \
            patch.object(recipe_execution, "_wait_query_table", return_value=(True, "vtable")), \
            patch.object(recipe_execution, "get_active_frame", return_value={"ok": True}), \
            patch.object(recipe_execution.interaction, "explore_action", side_effect=replay_action):
        execution = server.run_test_cases(generated["saved_to"], "execution.json")
    assert execution["counts"] == {"passed": 1, "failed": 0, "xfailed": 0, "skipped": 0}

    report = server.generate_test_report(
        execution["saved_to"], coverage_file=generated["saved_to"], filename="report.md",
    )
    assert report["ok"] is True
    assert "自动化测试报告" in resource_store.read_text_resource(report["saved_to"])


def test_generate_report_merges_supplemental_xfailed_execution(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp.resources import resource_store
    from drissionpage_mcp import server
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


def test_flow_sanitize_redacts_authorization_userinfo_and_nested_json():
    from drissionpage_mcp.workflows import flow_evidence
    sanitized = flow_evidence.sanitize({
        "auth_url": "https://example.test/a?authorization=Basic%20abc&name=order",
        "url": "https://user:pass@example.test/a?token=abc#session=xyz",
        "message": "Authorization: Basic abc123\nCookie: sid=secret; session=xyz; see https://u:p@example.test/a",
        "body": '{"password":"secret","ok":true}',
    })

    assert sanitized["url"] == "https://[REDACTED]@example.test/a?token=%5BREDACTED%5D#[REDACTED]"
    assert sanitized["auth_url"].endswith("authorization=%5BREDACTED%5D&name=order")
    assert "abc123" not in sanitized["message"]
    assert "secret" not in sanitized["message"]
    assert "u:p" not in sanitized["message"]
    assert sanitized["body"] == '{"password":"[REDACTED]","ok":true}'
    artifact = flow_evidence.sanitize_artifact({"rows": list(range(2_500))})
    assert len(artifact["rows"]) == 2_500


def test_flow_recording_enforces_page_and_step_limits(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp.workflows import flow_evidence
    from drissionpage_mcp.resources import resource_store
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    monkeypatch.setattr(flow_evidence, "_MAX_PAGE_STATES", 1)
    monkeypatch.setattr(flow_evidence, "_MAX_STEPS", 1)
    monkeypatch.setattr(flow_evidence, "_active_flow", None)
    monkeypatch.setattr(flow_evidence, "_last_flow", None)
    resource_store.clear_module()
    assert flow_evidence.start("上限验证", capture_screenshots=False)["ok"] is True

    assert flow_evidence.record_page_state("one", {"ok": True})["page_state_sequence"] == 1
    page_limit = flow_evidence.record_page_state("two", {"ok": True})
    first_step = flow_evidence.record_exploration(
        {"action": "click", "locator": "text:查询"},
        {"action": {"ok": True, "action": "click"}, "signal": {"type": "none"}},
        elapsed_ms=1,
    )
    step_limit = flow_evidence.record_exploration(
        {"action": "click", "locator": "text:查询"},
        {"action": {"ok": True, "action": "click"}, "signal": {"type": "none"}},
        elapsed_ms=1,
    )

    assert page_limit == {"ok": False, "reason": "flow exceeds 1 page states"}
    assert first_step["sequence"] == 1
    assert step_limit == {"ok": False, "reason": "flow exceeds 1 steps"}
    assert flow_evidence.stop()["ok"] is True


def test_flow_preserves_distinct_raw_network_packets(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp.workflows import flow_evidence
    from drissionpage_mcp.resources import resource_store
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    monkeypatch.setattr(flow_evidence, "_active_flow", None)
    monkeypatch.setattr(flow_evidence, "_last_flow", None)
    resource_store.clear_module()
    assert flow_evidence.start("网络证据", capture_screenshots=False)["ok"] is True

    reference = flow_evidence.record_exploration(
        {"action": "click", "locator": "text:查询"},
        {
            "ok": True,
            "observe_start": {"ok": True},
            "action": {"ok": True, "action": "click"},
            "signal": {
                "type": "message", "payload": {"message": "完成"},
                "events": [
                    {"type": "network", "url": "/gateway", "status": 200,
                     "response": {"body": {"page": 1}}},
                    {"type": "network", "url": "/gateway", "status": 200,
                     "response": {"body": {"page": 2}}},
                ],
            },
        },
        elapsed_ms=3,
    )
    stopped = flow_evidence.stop()
    networks = flow_evidence.load(stopped["saved_to"])["flow"]["steps"][0]["network"]

    assert reference["sequence"] == 1
    assert len(networks) == 2
    assert [item["response"]["body"]["page"] for item in networks] == [1, 2]


def test_flow_omits_account_json_network_noise(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp.resources import resource_store
    from drissionpage_mcp.workflows import flow_evidence

    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    monkeypatch.setattr(flow_evidence, "_active_flow", None)
    monkeypatch.setattr(flow_evidence, "_last_flow", None)
    resource_store.clear_module()
    assert flow_evidence.start("网络过滤", capture_screenshots=False)["ok"] is True

    flow_evidence.record_exploration(
        {"action": "click", "locator": "text:查询"},
        {
            "ok": True,
            "observe_start": {"ok": True},
            "action": {"ok": True, "action": "click"},
            "signal": {
                "type": "network",
                "url": "https://scm.example.com//main/api/v1/account.json",
                "api_target": "https://scm.example.com//main/api/v1/account.json",
                "events": [{
                    "type": "network",
                    "url": "https://example.test/gateway",
                    "api_target": "scm.order.list",
                    "status": 200,
                }],
            },
        },
        elapsed_ms=1,
    )
    stopped = flow_evidence.stop()
    networks = flow_evidence.load(stopped["saved_to"])["flow"]["steps"][0]["network"]

    assert networks == [{"url": "https://example.test/gateway", "api_target": "scm.order.list", "status": 200}]


def test_flow_marks_observer_start_failure_and_rejects_fractional_cleanup(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config
    from drissionpage_mcp.workflows import flow_evidence
    from drissionpage_mcp.resources import resource_store
    monkeypatch.setattr(config, "SHOT_DIR", str(tmp_path))
    monkeypatch.setattr(flow_evidence, "_active_flow", None)
    monkeypatch.setattr(flow_evidence, "_last_flow", None)
    resource_store.clear_module()
    assert flow_evidence.start("观察失败", capture_screenshots=False)["ok"] is True
    assert flow_evidence.record_page_state("bad", "not-an-object") == {
        "ok": False, "reason": "page_model must be an object",
    }
    flow_evidence.record_exploration(
        {"action": "click", "locator": "text:查询"},
        {
            "ok": True,
            "observe_start": {"ok": False, "reason": "listener unavailable"},
            "action": {"ok": True, "action": "click"},
            "signal": {"type": "none"},
        },
        elapsed_ms=1,
    )

    assert flow_evidence.status()["flow"]["failed_step_count"] == 1
    assert flow_evidence.stop(cleanup_from_sequence=1.5) == {
        "ok": False, "reason": "cleanup_from_sequence must be an integer",
    }
    stopped = flow_evidence.stop(cleanup_from_sequence=1)
    step = flow_evidence.load(stopped["saved_to"])["flow"]["steps"][0]
    assert step["outcome"] == "failed"
    assert step["error"] == "listener unavailable"
