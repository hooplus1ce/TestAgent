"""Workflow tools via FileSystemProvider."""

import asyncio

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.tools import tool

from drissionpage_mcp.components._sync import with_read, with_write
from drissionpage_mcp.workflows import flow_ops, recipe_execution


@tool(name="flow_start")
def flow_start(
    module: str,
    flow_name: str = "exploration",
    capture_screenshots: bool = True,
    scenario_type: str = "功能测试",
    risk_type: str = "正常路径",
    destructive: bool = False,
    cleanup_strategy: str = "",
    screenshot_policy: str = "on_failure",
) -> dict:
    """开始记录真实业务流证据。"""
    return with_write(
        flow_ops.flow_start,
        module,
        flow_name=flow_name,
        capture_screenshots=capture_screenshots,
        scenario_type=scenario_type,
        risk_type=risk_type,
        destructive=destructive,
        cleanup_strategy=cleanup_strategy,
        screenshot_policy=screenshot_policy,
    )


@tool(name="flow_status")
def flow_status() -> dict:
    """返回当前或最近一次业务流证据的状态与步骤数量。"""
    return with_read(flow_ops.flow_status)


@tool(name="flow_capture_page_state")
def flow_capture_page_state(
    label: str = "initial",
    include_filters: bool = True,
    include_tables: bool = True,
    max_table_rows: int = 30,
) -> dict:
    """采集当前 iframe 页面资产并写入活动业务流证据。"""
    return with_write(
        flow_ops.flow_capture_page_state,
        label=label,
        include_filters=include_filters,
        include_tables=include_tables,
        max_table_rows=max_table_rows,
    )


@tool(name="flow_stop")
def flow_stop(filename: str = None, cleanup_from_sequence: int = None) -> dict:
    """结束并保存业务流。"""
    return with_write(
        flow_ops.flow_stop,
        filename,
        cleanup_from_sequence=cleanup_from_sequence,
    )


@tool(name="generate_test_cases_from_flow")
def generate_test_cases_from_flow(
    flow_file: str,
    module_info: dict = None,
    filename: str = None,
) -> dict:
    """从业务流证据生成 automation_recipe 测试用例。"""
    return with_write(
        recipe_execution.generate_test_cases_from_flow,
        flow_file,
        module_info=module_info,
        filename=filename,
    )


@tool(name="combine_test_case_files")
def combine_test_case_files(
    case_files: list[str],
    filename: str = None,
    module_info: dict = None,
    exclude_case_ids: list[str] = None,
    exclude_known_defects: bool = False,
) -> dict:
    """合并多个用例文件为统一执行清单。"""
    return with_write(
        recipe_execution.combine_test_case_files,
        case_files,
        filename=filename,
        module_info=module_info,
        exclude_case_ids=exclude_case_ids,
        exclude_known_defects=exclude_known_defects,
    )


@tool(name="run_test_cases")
async def run_test_cases(
    case_file: str,
    filename: str = None,
    ctx: Context = CurrentContext(),
) -> dict:
    """回放 automation_recipe，并在批量执行期间向 Agent 报告进度。"""
    await ctx.report_progress(0, 100, "正在校验测试用例并准备浏览器回放")
    await ctx.info("run_test_cases started", logger_name="drissionpage-mcp.workflow")
    result = await asyncio.to_thread(
        recipe_execution.run_test_cases, case_file, filename,
    )
    if result.get("ok"):
        counts = result.get("counts", {})
        message = "回放完成：通过 %s，失败 %s" % (
            counts.get("passed", 0),
            counts.get("failed", 0),
        )
    else:
        message = "回放未完成：%s" % result.get("reason", "未知原因")
    await ctx.report_progress(100, 100, message)
    await ctx.info(message, logger_name="drissionpage-mcp.workflow")
    return result


@tool(name="generate_test_report")
def generate_test_report(
    execution_file: str,
    coverage_file: str = None,
    baseline_file: str = None,
    filename: str = None,
    defects_file: str = None,
    supplemental_execution_files: list[str] = None,
) -> dict:
    """生成 Markdown 测试报告。"""
    return with_write(
        recipe_execution.generate_test_report,
        execution_file,
        coverage_file=coverage_file,
        baseline_file=baseline_file,
        filename=filename,
        defects_file=defects_file,
        supplemental_execution_files=supplemental_execution_files,
    )


@tool(name="compare_regression_report")
def compare_regression_report(execution_file: str, baseline_file: str) -> dict:
    """比较当前执行结果与历史基线。"""
    return with_read(
        recipe_execution.compare_regression_report,
        execution_file,
        baseline_file,
    )
