import time

from drissionpage_mcp.services import interaction
from drissionpage_mcp.workflows import flow_evidence, testcase_generation


def test_default_observation_is_short_and_only_snapshots_a_feedback_signal(monkeypatch):
    observed = {}

    monkeypatch.setattr(interaction.browser_session, "get_tab", lambda: object())
    monkeypatch.setattr(interaction.table_facade, "pre_click_cleanup", lambda _enabled: None)
    monkeypatch.setattr(interaction.table_facade, "attach_cleanup", lambda result, _cleanup: result)
    monkeypatch.setattr(
        interaction,
        "_resolve_and_click",
        lambda *_args, **_kwargs: {"ok": True, "locator": "css:#save"},
    )
    monkeypatch.setattr(interaction.observe, "observe_start", lambda **_kwargs: {"ok": True})

    def wait(**kwargs):
        observed.update(kwargs)
        return {"type": "message", "events": []}

    monkeypatch.setattr(interaction.observe, "observe_wait", wait)
    monkeypatch.setattr(
        interaction.observe,
        "observe_snapshot",
        lambda **_kwargs: {"ok": True, "type": "snapshot", "count": 1},
    )
    monkeypatch.setattr(flow_evidence, "wants_screenshot", lambda _result: False)
    monkeypatch.setattr(flow_evidence, "record_exploration", lambda *_args, **_kwargs: None)

    result = interaction.explore_action(locator="css:#save", timeout=8)

    assert result["ok"] is True
    assert observed["timeout"] <= 1.5
    assert observed["include_snapshot"] is False
    assert result["signal"]["snapshot_after"]["count"] == 1
    assert {"resolve_target_ms", "action_ms", "observe_wait_ms", "total_ms"} <= set(
        result["timings"]
    )


def test_text_click_fallbacks_share_one_deadline(monkeypatch):
    calls = []

    monkeypatch.setattr(interaction.recipe_context, "requires_native_actions", lambda: False)

    def missing_element(_locator, **kwargs):
        calls.append(kwargs["timeout"])
        time.sleep(min(kwargs["timeout"], 0.03))
        return None

    monkeypatch.setattr(interaction.browser_session, "find", missing_element)
    monkeypatch.setattr(interaction, "_click_text_by_js", lambda *_args, **_kwargs: {"ok": False})

    started = time.monotonic()
    result = interaction._resolve_and_click("text:missing target", timeout=0.05)
    elapsed = time.monotonic() - started

    assert result["ok"] is False
    assert calls
    assert max(calls) <= 0.05
    assert elapsed < 0.15


def test_evidence_screenshot_policy_captures_only_failures_by_default(monkeypatch):
    monkeypatch.setattr(
        flow_evidence,
        "_active_flow",
        {"capture_screenshots": True, "screenshot_policy": "on_failure"},
    )

    assert flow_evidence.wants_screenshot({"ok": True}) is False
    assert flow_evidence.wants_screenshot({"ok": False}) is True


def test_recipe_generation_uses_direct_actions_without_feedback_contract():
    flow = {}
    steps = [
        {
            "sequence": 1,
            "action": {
                "name": "click",
                "input": {"action": "click", "locator": "css:#query"},
            },
            "observation": {},
        },
        {
            "sequence": 2,
            "action": {
                "name": "input",
                "input": {"action": "input", "field_name": "单据编号", "text": "PO-1"},
            },
            "observation": {},
        },
        {
            "sequence": 3,
            "action": {
                "name": "click",
                "input": {
                    "action": "click",
                    "locator": "css:#submit",
                    "signals": ["network"],
                },
            },
            "observation": {},
        },
    ]

    recipe, _ = testcase_generation._build_recipe(flow, steps)
    first, second, third = recipe["steps"]

    assert first["action"] == "click"
    assert first["args"] == {"locator": "css:#query"}
    assert second["action"] == "set_field_value"
    assert second["args"] == {"field_name": "单据编号", "value": "PO-1"}
    assert third["action"] == "explore_action"
    assert third["args"]["action"] == "click"


def test_wait_spec_locator_uses_targeted_wait_and_returns_compact_evidence(monkeypatch):
    class _States:
        is_displayed = True
        is_enabled = True

    class _Element:
        states = _States()
        tag = "button"
        text = "订单已提交"

    observer_started = []
    monkeypatch.setattr(interaction.browser_session, "get_tab", lambda: object())
    monkeypatch.setattr(interaction.browser_session, "find", lambda *_args, **_kwargs: _Element())
    monkeypatch.setattr(interaction.table_facade, "pre_click_cleanup", lambda _enabled: None)
    monkeypatch.setattr(interaction.table_facade, "attach_cleanup", lambda result, _cleanup: result)
    monkeypatch.setattr(interaction, "_resolve_and_click", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(
        interaction.observe,
        "observe_start",
        lambda **_kwargs: observer_started.append(True) or {"ok": True},
    )
    monkeypatch.setattr(flow_evidence, "wants_screenshot", lambda _result: False)
    monkeypatch.setattr(flow_evidence, "record_exploration", lambda *_args, **_kwargs: None)

    result = interaction.explore_action(
        locator="css:#submit",
        timeout=2,
        wait_spec={
            "kind": "locator",
            "locator": "css:.submit-result",
            "state": "visible",
            "text_contains": "已提交",
            "evidence": "summary",
        },
    )

    assert result["ok"] is True
    assert result["signal"] == {
        "type": "locator",
        "matched": True,
        "locator": "css:.submit-result",
        "state": "visible",
        "tag": "button",
        "text": "订单已提交",
        "elapsedMs": result["timings"]["wait_spec_ms"],
    }
    assert observer_started == []


def test_response_wait_result_omits_network_bodies_by_default():
    spec, error = interaction._normalize_wait_spec(
        {"kind": "response", "targets": "saveOrder", "status": 200}, 3,
    )
    assert error is None

    result = interaction._wait_for_response_spec(
        spec,
        {
            "type": "network",
            "elapsedMs": 42,
            "packet": {
                "url": "https://example.test/gateway/saveOrder",
                "method": "POST",
                "api_target": "saveOrder",
                "status": 200,
                "post_data": {"large": "request body"},
                "body": {"large": "response body"},
                "headers": {"x": "value"},
            },
        },
    )

    assert result == {
        "type": "response",
        "matched": True,
        "url": "https://example.test/gateway/saveOrder",
        "method": "POST",
        "api_target": "saveOrder",
        "status": 200,
        "elapsedMs": 42,
    }


def test_legacy_network_signal_is_compacted_before_returning_to_the_agent():
    result = interaction._compact_network_signal(
        {
            "type": "network",
            "elapsedMs": 21,
            "packet": {
                "url": "https://example.test/gateway/query",
                "method": "POST",
                "api_target": "query",
                "status": 200,
                "post_data": "request-body",
                "body": "response-body",
                "headers": {"content-type": "application/json"},
            },
        }
    )

    assert result == {
        "type": "network",
        "url": "https://example.test/gateway/query",
        "method": "POST",
        "api_target": "query",
        "status": 200,
        "elapsedMs": 21,
    }


def test_url_wait_matches_only_the_requested_location(monkeypatch):
    class _Tab:
        url = "https://example.test/scm-admin/orders/detail/42"

    monkeypatch.setattr(interaction.browser_session, "get_tab", lambda: _Tab())
    monkeypatch.setattr(interaction.browser_session, "get_active_frame_ro", lambda *_args, **_kwargs: None)
    spec, error = interaction._normalize_wait_spec(
        {"kind": "url", "contains": "/orders/detail/"}, 2,
    )
    assert error is None

    result = interaction._wait_for_url_spec(spec, time.monotonic() + 1)

    assert result == {
        "type": "url",
        "matched": True,
        "url": "https://example.test/scm-admin/orders/detail/42",
    }


def test_table_wait_returns_matched_values_without_returning_the_full_table(monkeypatch):
    monkeypatch.setattr(
        interaction.table_facade,
        "query_table",
        lambda **_kwargs: {"ok": True, "kind": "vtable", "values": ["草稿", "已提交", "已提交"]},
    )
    spec, error = interaction._normalize_wait_spec(
        {
            "kind": "table",
            "column_title": "单据状态",
            "value": "已提交",
            "expected_count": 2,
        },
        2,
    )
    assert error is None

    result = interaction._wait_for_table_spec(spec, time.monotonic() + 1)

    assert result == {
        "type": "table",
        "matched": True,
        "column_title": "单据状态",
        "match_count": 2,
        "sample_values": ["已提交", "已提交"],
        "table_kind": "vtable",
    }
