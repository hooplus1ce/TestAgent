"""Focused tests for scenario-level, evidence-driven testcase generation."""

from drissionpage_mcp.workflows import testcase_generation

def _step(sequence, action, args, observation=None, element=None):
    return {
        "sequence": sequence,
        "action": {"name": action, "input": {"action": action, **args}},
        "element": element or {},
        "observation": observation or {"type": "none", "events": []},
        "network": [],
        "artifacts": {"screenshot": "evidence/step-%d.png" % sequence},
        "outcome": "passed",
        "error": "",
    }


def test_one_flow_generates_one_ordered_business_case_with_real_assertions():
    flow = {
        "flow_id": "flow-query-order",
        "module": "采购订单",
        "flow_name": "按订单号查询采购订单",
        "page_states": [{
            "sequence": 1,
            "label": "initial",
            "page_model": {
                "ok": True,
                "filters": {"ok": True, "fields": [{
                    "field": "订单号", "valueMode": "free-text",
                    "operatorOptions": ["包含", "等于"],
                }]},
                "actions": {"ok": True, "actions": [
                    {"text": "查询", "area": "filter"},
                    {"text": "导出", "area": "toolbar"},
                ]},
                "tables": {"ok": True, "scan": {"columns": [
                    {"title": "订单号", "bodyBehavior": "none"},
                    {"title": "订单状态", "bodyBehavior": "none"},
                ]}},
                "table_data": {"ok": True, "count": 2, "columns": [
                    {"title": "订单号"}, {"title": "订单状态"},
                ]},
                "modals": {"ok": True, "overlays": [{
                    "type": "interactive", "title": "高级筛选", "buttons": ["确定", "取消"],
                }]},
                "interfaces": {"items": [{
                    "api_target": "scm.order.query", "method": "POST", "status": 200,
                    "body": {"success": True, "total": 2},
                }]},
            },
        }],
        "steps": [
            _step(1, "input", {"locator": "css:#order-no", "field_name": "订单号", "text": "PO20260711001"}, element={"label": "订单号", "area": "filter"}),
            _step(2, "click", {"locator": "text:查询"}, observation={
                "type": "network", "url": "https://example.test/gateway?orderQuery=",
                "api_target": "scm.order.query", "status": 200,
                "packet": {"url": "https://example.test/gateway?orderQuery=",
                           "api_target": "scm.order.query", "status": 200,
                           "body": {"success": True, "data": {"total": 2}}},
                "events": [{"type": "network", "api_target": "scm.order.query", "status": 200}],
            }, element={"text": "查询", "area": "filter"}),
        ],
    }

    generated = testcase_generation.generate_verified_cases(flow, {"case_id_start": 7})

    assert generated["ok"] is True
    assert generated["quality_gates"]["passed"] is True
    assert len(generated["test_cases"]) == 1
    case = generated["test_cases"][0]
    assert case["case_id"] == "F007"
    assert case["test_steps"] == [
        "在“订单号”输入“PO20260711001”",
        "点击“查询”",
    ]
    assert case["test_data"] == {"订单号": "PO20260711001"}
    assert case["automation_recipe"].keys() == {"setup", "steps", "cleanup"}
    assert [item["args"]["action"] for item in case["automation_recipe"]["steps"]] == ["input", "click"]
    assert case["automation_recipe"]["steps"][0]["assertions"] == []
    assertions = case["automation_recipe"]["steps"][1]["assertions"]
    assert case["automation_recipe"]["steps"][1]["args"]["signals"] == ["network"]
    assert case["automation_recipe"]["steps"][1]["args"]["listen_targets"] == "orderQuery"
    assert {item["path"] for item in assertions} == {
        "signal.api_target", "signal.status", "signal.packet.body",
    }
    body_assertion = next(item for item in assertions if item["path"] == "signal.packet.body")
    assert body_assertion["value"] == {"success": True, "data": {"total": 2}}
    assert all(set(item) == {"path", "operator", "value", "description"} for item in assertions)
    assert "状态码 200" in case["expected_result"]
    assert "响应体包含" in case["expected_result"]
    assert len(case["evidence_refs"]) == 2


def test_coverage_is_derived_from_page_assets_and_risk_types():
    flow = {
        "flow_id": "flow-assets",
        "module": "制造排产",
        "flow_name": "查询制令单",
        "page_states": [{
            "sequence": 1,
            "page_model": {
                "filters": {"fields": [
                    {"field": "制令单号", "valueMode": "free-text"},
                    {"field": "创建日期", "valueMode": "date-range"},
                ]},
                "actions": {"actions": [{"text": "查询", "area": "filter"}]},
                "tables": {"scan": {"columns": [{"title": "制令单号"}]}},
                "table_data": {"count": 1, "columns": [{"title": "制令单号"}]},
                "modals": {"overlays": []},
                "interfaces": {"items": [{"api_target": "mom.order.list", "status": 200}]},
            },
        }],
        "steps": [_step(1, "click", {"locator": "text:查询"}, observation={
            "type": "message", "payload": {"message": "查询完成"}, "events": [],
        }, element={"text": "查询", "area": "filter"})],
    }

    generated = testcase_generation.generate_verified_cases(flow, {})

    assert generated["coverage_summary"]["asset_counts"] == {
        "filter": 2, "action": 1, "table_column": 1, "interface": 1,
    }
    rows = generated["coverage_matrix"]
    assert {row["asset_type"] for row in rows} == {"requirement", "filter", "action", "table_column", "interface"}
    assert any(row["risk"] == "组合条件" and row["status"] == "待验证" for row in rows)
    assert any(row["risk"] == "边界值" and "非法日期范围" in row["scenario"] for row in rows)
    assert any(row["risk"] == "异常路径" and row["asset_type"] == "interface" for row in rows)
    assert generated["coverage_summary"]["total"] == len(rows)
    assert generated["unverified_count"] == sum(row["status"] != "已验证" for row in rows)
    summary = generated["coverage_summary"]
    assert summary["requirements"]["label"] == "需求场景覆盖"
    assert summary["requirements"]["total"] == len(rows)
    assert summary["requirements"]["verified"] == summary["verified"]
    assert summary["risks"]["total"] == len({row["risk"] for row in rows if row["risk"]})
    assert summary["risks"]["label"] == "风险维度覆盖"
    assert summary["risks"]["items"]["异常路径"]["scenario_total"] >= 1
    assert summary["assets"]["total"] == 5
    assert summary["assets"]["label"] == "页面资产覆盖"
    assert summary["assets"]["verified"] >= 2


def test_formal_case_is_rejected_without_real_business_assertion():
    flow = {
        "flow_id": "flow-no-feedback",
        "module": "工作台",
        "flow_name": "编辑工作台",
        "page_states": [{
            "sequence": 1,
            "page_model": {"actions": [{"text": "编辑我的工作台"}]},
        }],
        "steps": [_step(1, "click", {"locator": "text:编辑我的工作台"}, observation={
            "type": "none", "events": [],
        }, element={"text": "编辑我的工作台"})],
    }

    generated = testcase_generation.generate_verified_cases(flow, {})

    assert generated["test_cases"] == []
    assert generated["quality_gates"]["passed"] is False
    assert "包含真实业务断言" in generated["quality_gates"]["failures"]
    assert generated["coverage_summary"]["asset_counts"] == {"action": 1}


def test_core_form_flow_uses_stable_add_prefix_and_configured_counter():
    flow = {
        "flow_id": "flow-create",
        "module": "采购订单",
        "flow_name": "新增采购订单",
        "destructive": True,
        "cleanup_from_sequence": 3,
        "page_states": [],
        "steps": [
            _step(1, "input", {"locator": "css:#name", "field_name": "供应商", "text": "真实供应商A"}, element={"label": "供应商"}),
            _step(2, "click", {"locator": "text:保存"}, observation={
                "type": "message", "payload": {"message": "保存成功"}, "events": [],
            }, element={"text": "保存"}),
            _step(3, "click", {"locator": "text:恢复测试数据"}, observation={
                "type": "message", "payload": {"message": "恢复成功"}, "events": [],
            }, element={"text": "恢复测试数据"}),
        ],
    }

    generated = testcase_generation.generate_verified_cases(flow, {"case_id_start": {"A": 12, "default": 1}})

    assert generated["test_cases"][0]["case_id"] == "A012"
    assert generated["test_cases"][0]["expected_result"] == "页面显示“保存成功”"
    assert generated["quality_gates"]["checks"][-1]["passed"] is True
    assert generated["quality_gates"]["passed"] is True


def test_destructive_flow_requires_and_separates_cleanup_commands():
    flow = {
        "flow_id": "flow-delete",
        "module": "采购订单",
        "flow_name": "删除采购订单",
        "destructive": True,
        "cleanup_from_sequence": 3,
        "page_states": [{
            "sequence": 1,
            "page_model": {"actions": [{"text": "删除"}, {"text": "恢复测试数据"}]},
        }],
        "steps": [
            _step(1, "click", {"locator": "text:删除"}, observation={
                "type": "confirm", "payload": {"title": "确认删除"}, "events": [],
            }, element={"text": "删除"}),
            _step(2, "click", {"locator": "text:确定"}, observation={
                "type": "message", "payload": {"message": "删除成功"}, "events": [],
            }, element={"text": "确定"}),
            _step(3, "click", {"locator": "text:恢复测试数据"}, observation={
                "type": "message", "payload": {"message": "恢复成功"}, "events": [],
            }, element={"text": "恢复测试数据"}),
        ],
    }

    generated = testcase_generation.generate_verified_cases(flow, {})

    assert generated["quality_gates"]["passed"] is True
    case = generated["test_cases"][0]
    assert len(case["automation_recipe"]["steps"]) == 2
    assert len(case["automation_recipe"]["cleanup"]) == 1
    assert case["test_steps"] == ["点击“删除”", "点击“确定”"]
    assert "删除成功" in case["expected_result"]
    assert "恢复成功" not in case["expected_result"]
    assert case["automation_recipe"]["cleanup"][0]["evidence_sequence"] == 3

    without_cleanup = {**flow, "cleanup_from_sequence": None}
    rejected = testcase_generation.generate_verified_cases(without_cleanup, {})
    assert rejected["test_cases"] == []
    assert "破坏性场景具备清理步骤" in rejected["quality_gates"]["failures"]


def test_structured_vtable_reads_generate_dynamic_direct_recipe_assertions():
    tag = "E2E-SALARY-20260711"
    flow = {
        "flow_id": "flow-structured-row",
        "module": "工资明细",
        "flow_name": "新增工资明细数据复验",
        "page_states": [],
        "steps": [
            _step(1, "find_vtable_row", {
                "column_title": "备注", "value": tag, "save_as": "salary_row",
            }, observation={"type": "structured_result", "payload": {
                "ok": True, "column_title": "备注", "value": tag, "row": 1,
            }}),
            _step(2, "get_vtable_row_values", {
                "key_column": "备注", "key_value": tag,
                "column_titles": ["备注", "生产数量", "金额"],
            }, observation={"type": "structured_result", "payload": {
                "ok": True, "values": {"备注": tag, "生产数量": "2", "金额": "7"},
            }}),
            _step(3, "click_table_cell", {
                "row": {"$ref": "salary_row.row"}, "col": 0, "kind": "vtable",
            }),
        ],
    }

    generated = testcase_generation.generate_verified_cases(flow, {})

    assert generated["quality_gates"]["passed"] is True
    commands = generated["test_cases"][0]["automation_recipe"]["steps"]
    assert [item["action"] for item in commands] == [
        "find_vtable_row", "get_vtable_row_values", "click_table_cell",
    ]
    assert commands[0]["args"]["save_as"] == "salary_row"
    assert commands[2]["args"]["row"] == {"$ref": "salary_row.row"}
    assert generated["test_cases"][0]["test_data"] == {"备注": tag}
    assert {item["path"] for item in commands[1]["assertions"]} == {
        "values.备注", "values.生产数量", "values.金额",
    }
    assert "目标业务记录的“金额”为“7”" in generated["test_cases"][0]["expected_result"]


def test_secondary_validation_notification_becomes_replay_assertion():
    flow = {
        "flow_id": "flow-required-validation",
        "module": "工资明细",
        "flow_name": "总工时必填校验",
        "page_states": [],
        "steps": [_step(1, "click", {"locator": "text:确定"}, observation={
            "type": "interactive",
            "payload": {"title": "添加工资明细"},
            "events": [{"type": "notification", "message": "总工时不能为空"}],
        }, element={"text": "确定"})],
    }

    generated = testcase_generation.generate_verified_cases(flow, {})
    assertions = generated["test_cases"][0]["automation_recipe"]["steps"][0]["assertions"]

    assert generated["test_cases"][0]["automation_recipe"]["steps"][0]["args"]["signals"] == [
        "message", "notification",
    ]
    assert any(item["path"] == "signal.events" and item["value"] == {
        "type": "notification", "message": "总工时不能为空",
    } for item in assertions)
    assert "页面显示“总工时不能为空”" in generated["test_cases"][0]["expected_result"]


def test_modal_assertion_is_not_suppressed_by_secondary_account_poll():
    flow = {
        "flow_id": "flow-modal-with-noise",
        "module": "工资明细",
        "flow_name": "打开编辑弹窗",
        "page_states": [],
        "steps": [_step(1, "click", {"locator": "text:编辑"}, observation={
            "type": "interactive",
            "payload": {"title": "编辑工资明细"},
            "events": [{
                "type": "network",
                "url": "https://example.test/main/api/v1/account.json",
                "status": 200,
            }],
        }, element={"text": "编辑", "area": "page"})],
    }

    generated = testcase_generation.generate_verified_cases(flow, {})

    assertions = generated["test_cases"][0]["automation_recipe"]["steps"][0]["assertions"]
    assert assertions == [{
        "path": "signal.payload.title", "operator": "equals",
        "value": "编辑工资明细", "description": "页面显示标题为“编辑工资明细”的弹窗",
    }]


def test_modal_field_does_not_verify_same_named_page_filter():
    flow = {
        "flow_id": "flow-modal-field-scope",
        "module": "工资明细",
        "flow_name": "编辑备注",
        "page_states": [{
            "sequence": 1,
            "page_model": {"filters": {"fields": [{"field": "备注", "valueMode": "free-text"}]}},
        }],
        "steps": [
            _step(1, "input", {
                "field_name": "备注", "text": "弹窗内备注",
            }, element={"label": "备注", "scope": "modal"}),
            _step(2, "click", {"locator": "text:确定"}, observation={
                "type": "message", "payload": {"message": "修改成功"}, "events": [],
            }, element={"text": "确定", "area": "modal"}),
        ],
    }

    generated = testcase_generation.generate_verified_cases(flow, {})

    filter_rows = [
        row for row in generated["coverage_matrix"]
        if row["asset_type"] == "filter" and row["function"] == "备注"
    ]
    assert filter_rows
    assert all(row["status"] == "待验证" for row in filter_rows)


def test_cleanup_only_assertion_does_not_verify_business_flow_coverage():
    flow = {
        "flow_id": "flow-cleanup-only",
        "module": "采购订单",
        "flow_name": "删除后恢复",
        "destructive": True,
        "cleanup_from_sequence": 2,
        "page_states": [],
        "steps": [
            _step(1, "click", {"locator": "text:删除"}, element={"text": "删除", "area": "page"}),
            _step(2, "click", {"locator": "text:恢复"}, observation={
                "type": "message", "payload": {"message": "恢复成功"}, "events": [],
            }, element={"text": "恢复", "area": "page"}),
        ],
    }

    generated = testcase_generation.generate_verified_cases(flow, {})

    assert generated["test_cases"] == []
    requirement_rows = [row for row in generated["coverage_matrix"] if row["asset_type"] == "requirement"]
    assert requirement_rows and all(row["status"] == "待验证" for row in requirement_rows)


def test_edit_restore_case_is_core_interaction_with_visible_cleanup_and_fixture_gate():
    flow = {
        "flow_id": "flow-edit-restore",
        "module": "工资明细",
        "flow_name": "编辑工资明细并恢复原值",
        "destructive": True,
        "cleanup_from_sequence": 3,
        "include_cleanup_in_case": True,
        "verify_fixture_in_setup": True,
        "page_states": [],
        "steps": [
            _step(1, "click", {"locator": "text:编辑"}, observation={
                "type": "interactive", "payload": {"title": "编辑工资明细"}, "events": [],
            }, element={"text": "编辑", "area": "page"}),
            _step(2, "click", {"locator": "text:确定"}, observation={
                "type": "message", "payload": {"message": "修改成功"}, "events": [],
            }, element={"text": "确定", "area": "modal"}),
            _step(3, "click", {"locator": "text:编辑"}, observation={
                "type": "interactive", "payload": {"title": "编辑工资明细"}, "events": [],
            }, element={"text": "编辑", "area": "page"}),
            _step(4, "get_vtable_row_values", {
                "key_column": "备注", "key_value": "FIXTURE-1", "column_titles": ["单价", "金额"],
            }, observation={
                "type": "structured_result", "payload": {"values": {"单价": "3.5", "金额": "7"}},
            }),
        ],
    }

    case = testcase_generation.generate_verified_cases(flow, {})["test_cases"][0]

    assert case["case_id"] == "I001"
    assert case["priority"] == "中级"
    assert len(case["test_steps"]) == 4
    assert case["test_data"] == {"备注": "FIXTURE-1"}
    assert "目标业务记录的“单价”为“3.5”" in case["expected_result"]
    assert case["automation_recipe"]["setup"][0]["action"] == "get_vtable_row_values"
    assert "自动化执行前会核验其初始值" in case["preconditions"][-1]


def test_step_and_network_evidence_add_real_assets_without_repeated_page_snapshot():
    flow = {
        "flow_id": "flow-delete-defect",
        "module": "工资明细",
        "flow_name": "删除系统异常缺陷复现",
        "scenario_type": "异常测试",
        "risk_type": "异常路径+数据一致性",
        "known_defect": {"defect_id": "SALARY-DEL-001", "status": "open"},
        "case_prefix": "D",
        "destructive": True,
        "cleanup_from_sequence": 2,
        "page_states": [],
        "steps": [
            _step(1, "click", {"locator": "text:删除"}, observation={
                "type": "network", "url": "https://gateway.test/api?batchRemove=",
                "api_target": "salary.batchRemove", "status": 200,
                "packet": {
                    "url": "https://gateway.test/api?batchRemove=",
                    "api_target": "salary.batchRemove", "status": 200,
                    "body": {"ok": False, "status": -1, "msg": "系统异常!"},
                },
                "events": [],
            }, element={"text": "删除", "area": "page"}),
            _step(2, "click", {"locator": "text:恢复测试数据"}, observation={
                "type": "message", "payload": {"message": "恢复完成"}, "events": [],
            }, element={"text": "恢复测试数据", "area": "page"}),
        ],
    }
    flow["steps"][0]["network"] = [{
        "url": "https://example.test/main/api/v1/account.json", "status": 200,
        "body": {"ok": True},
    }]

    generated = testcase_generation.generate_verified_cases(flow, {})

    assets = {(item["asset_type"], item["name"]) for item in generated["asset_inventory"]}
    assert {("action", "删除"), ("interface", "salary.batchRemove")} <= assets
    assert generated["coverage_summary"]["assets"]["verified"] >= 2
    assert generated["test_cases"][0]["case_id"] == "D001"
    assert generated["test_cases"][0]["known_defect"]["defect_id"] == "SALARY-DEL-001"
    assert any(
        row["asset_type"] == "action" and row["function"] == "删除"
        and row["risk"] == "异常路径" and row["status"] == "已验证"
        for row in generated["coverage_matrix"]
    )
    assert any(
        row["asset_type"] == "interface" and row["risk"] == "异常路径"
        and row["status"] == "已验证"
        for row in generated["coverage_matrix"]
    )


def test_merge_generated_suites_deduplicates_assets_and_promotes_verified_coverage():
    asset = {"asset_type": "action", "name": "编辑", "evidence_refs": [{"flow_id": "one"}]}
    pending = {
        "coverage_id": "ACT-001", "asset_type": "action", "function": "编辑",
        "scenario": "编辑并校验金额", "risk": "数据一致性", "status": "待验证",
    }
    verified = {**pending, "coverage_id": "ACT-009", "status": "已验证"}
    def merge_case(case_id, coverage_ref):
        return {
            "case_id": case_id,
            "coverage_refs": [coverage_ref],
            "automation_recipe": {"steps": [{
                "action": "get_table_values",
                "args": {"column_title": "金额"},
                "assertions": [{
                    "path": "values", "operator": "all_equals", "value": "7",
                }],
            }]},
        }
    payloads = [
        {
            "module_info": {"module_pinyin": "GZMX"},
            "asset_inventory": [asset], "coverage_matrix": [pending],
            "quality_gates": {"passed": True},
            "test_cases": [merge_case("F001", "ACT-001")],
        },
        {
            "asset_inventory": [{**asset, "evidence_refs": [{"flow_id": "two"}]}],
            "coverage_matrix": [verified], "quality_gates": {"passed": True},
            "test_cases": [merge_case("A001", "ACT-009")],
        },
    ]

    merged = testcase_generation.merge_generated_suites(payloads)

    assert merged["ok"] is True
    assert [case["case_id"] for case in merged["test_cases"]] == ["F001", "A001"]
    assert len(merged["asset_inventory"]) == 1
    assert len(merged["asset_inventory"][0]["evidence_refs"]) == 2
    assert merged["coverage_matrix"][0]["status"] == "已验证"
    assert [case["coverage_refs"] for case in merged["test_cases"]] == [
        ["SUITE-001"], ["SUITE-001"],
    ]
    assert merged["coverage_summary"]["requirements"]["verified"] == 1


def test_listener_target_uses_environment_independent_path_without_query():
    assert testcase_generation._listener_target_from_url(
        "https://demo.example.test/api/gateway"
    ) == "/api/gateway"


def test_network_subset_keeps_stable_business_rows_and_skips_volatile_ids():
    assert testcase_generation._stable_body_subset({
        "ok": True,
        "data": [
            {"id": 99, "order_no": "PO-001", "amount": 7},
            {"id": 100, "order_no": "PO-002", "amount": 9},
        ],
    }) == {
        "ok": True,
        "data": [
            {"order_no": "PO-001", "amount": 7},
            {"order_no": "PO-002", "amount": 9},
        ],
    }


def test_gateway_operation_ignores_tenant_parameter_order():
    url = "https://example.test/gateway?tenant=x&orderQuery="
    assert testcase_generation._listener_target_from_url(url) == "orderQuery"
    assert testcase_generation._network_identity(url) == ("contains", "orderQuery")


def test_merge_can_keep_known_defect_coverage_without_formal_case():
    payload = {
        "asset_inventory": [],
        "coverage_matrix": [{
            "coverage_id": "AST-001", "asset_type": "requirement",
            "function": "删除缺陷", "scenario": "复现系统异常",
            "risk": "异常路径", "status": "已验证",
        }],
        "quality_gates": {"passed": True},
        "test_cases": [{
            "case_id": "I001", "coverage_refs": ["AST-001"],
            "known_defect": {"defect_id": "SALARY-DEL-001"},
        }],
    }

    merged = testcase_generation.merge_generated_suites(
        [payload], exclude_known_defects=True,
    )

    assert merged["test_cases"] == []
    assert merged["coverage_summary"]["requirements"]["verified"] == 1
    assert merged["excluded_cases"] == [{"case_id": "I001", "reason": "known_defect"}]


def test_generation_rejects_undeclared_save_without_cleanup():
    flow = {
        "flow_id": "unsafe-save", "module": "采购订单", "flow_name": "保存订单",
        "page_states": [],
        "steps": [_step(1, "click", {"locator": "text:保存"}, observation={
            "type": "message", "payload": {"message": "保存成功"}, "events": [],
        }, element={"text": "保存"})],
    }

    generated = testcase_generation.generate_verified_cases(flow, {})

    assert generated["test_cases"] == []
    assert "破坏性操作已显式声明" in generated["quality_gates"]["failures"]
    assert "破坏性场景具备清理步骤" in generated["quality_gates"]["failures"]


def test_merge_rejects_module_conflicts_duplicate_coverage_ids_and_untrusted_cases():
    row_a = {
        "coverage_id": "DUP", "asset_type": "action", "function": "查询",
        "scenario": "按单号查询", "risk": "正常路径", "status": "已验证",
    }
    row_b = {
        "coverage_id": "DUP", "asset_type": "filter", "function": "订单号",
        "scenario": "订单号等于", "risk": "等价类", "status": "已验证",
    }
    payload = {
        "module_info": {"module_level2": "采购订单"},
        "asset_inventory": [], "coverage_matrix": [row_a, row_b],
        "quality_gates": {"passed": True},
        "test_cases": [{"case_id": "BAD", "coverage_refs": ["DUP"]}],
    }
    other_module = {
        "module_info": {"module_level2": "销售订单"},
        "asset_inventory": [], "coverage_matrix": [],
        "quality_gates": {"passed": True}, "test_cases": [],
    }

    merged = testcase_generation.merge_generated_suites([payload, other_module])

    failures = merged["quality_gates"]["failures"]
    assert merged["ok"] is False
    assert any("module_info conflicts" in failure for failure in failures)
    assert any("duplicate coverage ids" in failure for failure in failures)
    assert any("automation_recipe" in failure for failure in failures)


def test_merge_rejects_non_string_coverage_ids_and_refs_without_crashing():
    payload = {
        "module_info": {},
        "asset_inventory": [1],
        "coverage_matrix": [{
            "coverage_id": [], "asset_type": "action", "function": "查询",
            "scenario": "按单号查询", "risk": "正常路径", "status": "已验证",
        }],
        "quality_gates": {"passed": "true", "failures": 1},
        "test_cases": [{
            "case_id": "BAD-REF", "coverage_refs": [{}],
            "automation_recipe": {"steps": [{
                "action": "get_table_values",
                "assertions": [{"path": "values", "operator": "all_equals", "value": "A"}],
            }]},
        }],
    }

    merged = testcase_generation.merge_generated_suites([payload])

    assert merged["ok"] is False
    assert any("coverage_id_must_be_string" in failure
               for failure in merged["quality_gates"]["failures"])
    assert any("coverage_ref_must_be_string" in failure
               for failure in merged["quality_gates"]["failures"])
    assert any("invalid assets" in failure
               for failure in merged["quality_gates"]["failures"])
    assert any("source_1: 1" in failure
               for failure in merged["quality_gates"]["failures"])


def test_generic_form_fields_keep_scope_and_only_used_scope_is_verified():
    flow = {
        "flow_id": "scoped-fields", "module": "工资明细", "flow_name": "编辑备注",
        "page_states": [{
            "sequence": 1,
            "page_model": {"fields": {"fields": [
                {"label": "备注", "type": "input", "area": "page"},
                {"label": "备注", "type": "input", "area": "modal", "required": True},
            ]}},
        }],
        "steps": [
            _step(1, "input", {"field_name": "备注", "text": "弹窗值"},
                  element={"label": "备注", "scope": "modal"}),
            _step(2, "click", {"locator": "text:确定"}, observation={
                "type": "message", "payload": {"message": "修改成功"}, "events": [],
            }, element={"text": "确定", "area": "modal"}),
        ],
    }

    generated = testcase_generation.generate_verified_cases(flow, {})
    fields = [item for item in generated["asset_inventory"] if item["asset_type"] == "field"]
    normal_rows = [
        row for row in generated["coverage_matrix"]
        if row["asset_type"] == "field" and row["risk"] == "正常路径"
    ]

    assert len(fields) == 2
    assert {(row["area"], row["status"]) for row in normal_rows} == {
        ("page", "待验证"), ("modal", "已验证"),
    }
    assert any(row["risk"] == "必填校验" and row["status"] == "待验证"
               for row in generated["coverage_matrix"])


def test_persisted_raw_response_and_stable_url_assertions_are_retained():
    network_step = _step(1, "click", {"locator": "text:查询"}, observation={
        "type": "message", "payload": {"message": "查询完成"}, "events": [],
    }, element={"text": "查询", "area": "page"})
    network_step["network"] = [{
        "url": "https://gateway.test/query", "status": 200,
        "response": {"body": {"success": True, "total": 3}},
    }]
    network_flow = {
        "flow_id": "raw-network", "module": "订单", "flow_name": "查询订单",
        "page_states": [], "steps": [network_step],
    }
    generated = testcase_generation.generate_verified_cases(network_flow, {})
    persisted = [
        item for item in generated["test_cases"][0]["business_assertions"]
        if item.get("source") == "persisted_network"
    ]
    interface = next(item for item in generated["asset_inventory"]
                     if item["asset_type"] == "interface")

    assert persisted[0]["value"] == {"success": True, "total": 3}
    assert interface["metadata"]["body"] == {"success": True, "total": 3}

    url_flow = {
        "flow_id": "stable-url", "module": "订单", "flow_name": "打开详情",
        "page_states": [],
        "steps": [_step(1, "click", {"locator": "text:详情"}, observation={
            "type": "url_change",
            "url": "https://demo.example.test/scm/#/orders/detail?id=volatile",
            "events": [],
        }, element={"text": "详情", "area": "page"})],
    }
    assertion = testcase_generation.generate_verified_cases(
        url_flow, {},
    )["test_cases"][0]["automation_recipe"]["steps"][0]["assertions"][0]
    assert assertion == {
        "path": "signal.url", "operator": "contains",
        "value": "/scm/#/orders/detail",
        "description": "页面地址匹配“/scm/#/orders/detail”",
    }


def test_structured_column_and_render_results_generate_business_assertions():
    column_flow = {
        "flow_id": "uniform-column", "module": "订单", "flow_name": "校验状态列",
        "page_states": [],
        "steps": [_step(1, "get_table_values", {"column_title": "状态"}, observation={
            "type": "structured_result", "payload": {
                "ok": True, "values": ["已完成", "已完成"],
            },
        })],
    }
    column_case = testcase_generation.generate_verified_cases(column_flow, {})["test_cases"][0]
    assert column_case["automation_recipe"]["steps"][0]["assertions"] == [{
        "path": "values", "operator": "all_equals", "value": "已完成",
        "description": "“状态”全部业务记录均为“已完成”",
    }]

    render_flow = {
        "flow_id": "render-cell", "module": "订单", "flow_name": "校验状态样式",
        "page_states": [],
        "steps": [_step(1, "get_vtable_cell_render_info", {
            "row": 1, "column_title": "状态",
        }, observation={
            "type": "structured_result", "payload": {
                "ok": True, "text": "已完成", "fontColor": "#008000",
            },
        })],
    }
    render_case = testcase_generation.generate_verified_cases(render_flow, {})["test_cases"][0]
    assert {item["path"] for item in render_case["automation_recipe"]["steps"][0]["assertions"]} == {
        "text", "fontColor",
    }


def test_generation_rejects_ambiguous_step_sequence_and_defect_metadata():
    flow = {
        "flow_id": "bad-sequence", "module": "订单", "flow_name": "查询订单",
        "known_defect": {}, "page_states": [],
        "steps": [
            _step(1, "click", {"locator": "text:查询"}),
            _step(3, "click", {"locator": "text:确定"}, observation={
                "type": "message", "payload": {"message": "完成"}, "events": [],
            }),
        ],
    }
    generated = testcase_generation.generate_verified_cases(flow, {})

    assert generated["test_cases"] == []
    assert "证据步骤序号唯一连续" in generated["quality_gates"]["failures"]
    assert "已知缺陷元数据完整" in generated["quality_gates"]["failures"]


def test_merge_preserves_module_defaults_scopes_metadata_and_trimmed_exclusions():
    def payload(area, case_id):
        row = {
            "coverage_id": "ROW", "asset_type": "field", "function": "备注",
            "area": area, "scenario": "输入备注", "risk": "正常路径", "status": "已验证",
        }
        return {
            "module_info": {"module_pinyin": "GZMX", "module_name": "工资明细"},
            "asset_inventory": [{
                "asset_type": "field", "name": "备注",
                "metadata": {"area": area, "required": area == "modal", "row_count": 1},
                "evidence_refs": [{"flow_id": area}],
            }],
            "coverage_matrix": [row], "quality_gates": {"passed": True},
            "test_cases": [{
                "case_id": case_id, "coverage_refs": ["ROW"],
                "automation_recipe": {"steps": [{
                    "action": "get_table_values", "args": {"column_title": "备注"},
                    "assertions": [{"path": "values", "operator": "all_equals", "value": "A"}],
                }]},
            }],
        }

    merged = testcase_generation.merge_generated_suites(
        [payload("page", "F001"), payload("modal", "F002")],
        module_info={"author": "Tester"}, exclude_case_ids=[" F002 "],
    )

    assert merged["ok"] is True
    assert merged["module_info"] == {
        "module_pinyin": "GZMX", "module_name": "工资明细", "author": "Tester",
    }
    assert len(merged["asset_inventory"]) == 2
    assert len(merged["coverage_matrix"]) == 2
    assert [case["case_id"] for case in merged["test_cases"]] == ["F001"]
    assert merged["excluded_cases"] == [{"case_id": "F002", "reason": "excluded_case_id"}]
