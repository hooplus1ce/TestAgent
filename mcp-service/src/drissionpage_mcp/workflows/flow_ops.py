"""Public flow-evidence orchestration used by MCP components and server re-exports."""

from __future__ import annotations

from ..resources import resource_store
from ..services import page_model
from . import flow_evidence


def flow_start(
    module: str,
    flow_name: str = "exploration",
    capture_screenshots: bool = True,
    scenario_type: str = "功能测试",
    risk_type: str = "正常路径",
    destructive: bool = False,
    cleanup_strategy: str = "",
) -> dict:
    result = flow_evidence.start(
        module,
        flow_name,
        capture_screenshots,
        scenario_type=scenario_type,
        risk_type=risk_type,
        destructive=destructive,
        cleanup_strategy=cleanup_strategy,
    )
    if result.get("ok"):
        resource_store.set_module(module)
    return result


def flow_status() -> dict:
    return flow_evidence.status()


def flow_capture_page_state(
    label: str = "initial",
    include_filters: bool = True,
    include_tables: bool = True,
    max_table_rows: int = 30,
) -> dict:
    if not flow_evidence.is_active():
        return {"ok": False, "reason": "no active evidence flow; call flow_start first"}
    page_state = page_model.capture_page_model(
        include_filters=include_filters,
        include_tables=include_tables,
        include_table_data=True,
        max_table_rows=max_table_rows,
        max_elements=200,
    )
    reference = flow_evidence.record_page_state(label, page_state)
    if isinstance(reference, dict) and reference.get("ok") is False:
        return reference
    return {
        "ok": bool(page_state.get("ok")),
        "reference": reference,
        "page_state": page_state,
    }


def flow_stop(filename: str = None, cleanup_from_sequence: int = None) -> dict:
    return flow_evidence.stop(filename, cleanup_from_sequence=cleanup_from_sequence)
