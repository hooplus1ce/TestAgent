from drissionpage_mcp import server
from drissionpage_mcp.workflows import test_execution


def _role_recipe() -> dict:
    return {
        "case_id": "APPROVAL-ROLE-001",
        "case_title": "申请人与主管顺序审批",
        "automation_recipe": {
            "setup": [
                {"action": "role_session_start", "args": {"role_id": "requester"}},
                {"action": "role_session_start", "args": {"role_id": "dept_manager"}},
            ],
            "steps": [
                {"action": "role_session_activate", "args": {"role_id": "requester"}},
                {"action": "role_session_activate", "args": {"role_id": "dept_manager"}},
                {
                    "action": "scan_table",
                    "assertions": [{
                        "path": "actor",
                        "operator": "equals",
                        "value": "dept_manager",
                    }],
                },
            ],
            "cleanup": [
                {"action": "role_session_close", "args": {"role_id": "dept_manager"}},
                {"action": "role_session_close", "args": {"role_id": "requester"}},
            ],
        },
    }


def test_role_recipe_requires_activation_before_business_actions():
    case = _role_recipe()
    case["automation_recipe"]["steps"].pop(0)
    case["automation_recipe"]["steps"].pop(0)

    reasons = test_execution.weak_recipe_reasons(case)

    assert "角色回归用例必须在首个业务操作前执行 role_session_activate" in reasons
    assert test_execution.uses_role_session_actions(case) is True


def test_role_recipe_rejects_proxy_arguments_before_evidence_is_written():
    case = _role_recipe()
    case["automation_recipe"]["setup"][0]["action"] = "role_session_open"
    case["automation_recipe"]["setup"][0]["args"]["proxy"] = "http://user:password@example.test:8080"

    reasons = test_execution.weak_recipe_reasons(case)

    assert "role_session_open 配方不支持 proxy；请通过服务配置提供代理" in reasons


def test_recipe_dispatches_role_session_actions(monkeypatch):
    from drissionpage_mcp.workflows import recipe_execution

    calls = []

    def action(name):
        def run(**kwargs):
            calls.append((name, kwargs))
            return {"ok": True, "action": name, **kwargs}
        return run

    for name in (
        "role_session_start",
        "role_session_open",
        "role_session_login",
        "role_session_activate",
        "role_session_list",
        "role_session_close",
    ):
        monkeypatch.setattr(recipe_execution, name, action(name))
        monkeypatch.setattr(server, name, action(name))

    assert recipe_execution._run_recipe_action("role_session_start", {"role_id": "requester"})["ok"] is True
    assert recipe_execution._run_recipe_action("role_session_activate", {"role_id": "dept_manager"})["ok"] is True
    assert recipe_execution._run_recipe_action("role_session_list", {})["ok"] is True
    assert recipe_execution._run_recipe_action("role_session_close", {"role_id": "requester"})["ok"] is True
    assert [name for name, _ in calls] == [
        "role_session_start",
        "role_session_activate",
        "role_session_list",
        "role_session_close",
    ]


def test_run_test_cases_executes_role_recipe_without_default_session_gate(monkeypatch):
    from drissionpage_mcp.workflows import recipe_execution, flow_evidence
    from drissionpage_mcp.resources import resource_store

    payload = {"module_info": {"module_name": "审批中心"}, "test_cases": [_role_recipe()]}
    calls = []
    flow_active = {"value": False}

    monkeypatch.setattr(recipe_execution, "_read_json_resource", lambda _path: (payload, None))
    monkeypatch.setattr(
        recipe_execution,
        "_browser_connection_gate",
        lambda: calls.append("connection_gate") or {"ok": True, "mode": "roles"},
    )
    monkeypatch.setattr(
        recipe_execution,
        "_browser_ready_gate",
        lambda _module: (_ for _ in ()).throw(AssertionError("default gate must not run")),
    )
    monkeypatch.setattr(
        recipe_execution.table_facade,
        "pre_click_cleanup",
        lambda _clean: (_ for _ in ()).throw(AssertionError("default cleanup must not run")),
    )
    monkeypatch.setattr(recipe_execution, "_resolve_artifact_path", lambda *args: "/tmp/role-execution.json")
    monkeypatch.setattr(resource_store, "_resolve_existing_path", lambda _path: "/tmp/role-cases.json")
    monkeypatch.setattr(resource_store, "write_json_atomic", lambda *_args: None)
    monkeypatch.setattr(flow_evidence, "is_active", lambda: flow_active["value"])
    monkeypatch.setattr(
        flow_evidence,
        "start",
        lambda *args, **kwargs: flow_active.update(value=True) or {"ok": True},
    )
    monkeypatch.setattr(
        flow_evidence,
        "stop",
        lambda: flow_active.update(value=False) or {"ok": True},
    )
    monkeypatch.setattr(flow_evidence, "wants_screenshot", lambda *_args: False)
    monkeypatch.setattr(flow_evidence, "record_exploration", lambda *args, **kwargs: {"sequence": 1})

    def role_action(name):
        def run(role_id=None, **kwargs):
            calls.append("%s:%s" % (name, role_id or ""))
            return {"ok": True, "role_id": role_id}
        return run

    for name in (
        "role_session_start",
        "role_session_activate",
        "role_session_close",
    ):
        monkeypatch.setattr(recipe_execution, name, role_action(name))
    monkeypatch.setattr(
        recipe_execution.table_facade,
        "scan_table",
        lambda **kwargs: calls.append("scan_table") or {"ok": True, "actor": "dept_manager"},
    )

    result = recipe_execution.run_test_cases("role-cases.json", "role-execution.json")

    assert result["ok"] is True
    assert result["counts"] == {"passed": 1, "failed": 0, "xfailed": 0, "skipped": 0}
    assert result["execution"]["role_mode"] is True
    assert calls == [
        "connection_gate",
        "role_session_start:requester",
        "role_session_start:dept_manager",
        "role_session_activate:requester",
        "role_session_activate:dept_manager",
        "scan_table",
        "role_session_close:dept_manager",
        "role_session_close:requester",
    ]
