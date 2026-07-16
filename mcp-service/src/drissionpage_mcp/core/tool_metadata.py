"""工具元数据：集中管理工具的分组、注解、超时等元信息。

通过 FastMCP 原生
Transform 机制实现标签注入和可见性控制。

使用方式：
  export DRISSIONPAGE_MCP_PROFILE=full                # 默认：暴露完整工具面
  export DRISSIONPAGE_MCP_PROFILE=enterprise          # 企业测试主路径
  export DRISSIONPAGE_MCP_CAPS=core,vtable,filter     # 启用指定分组
  export DRISSIONPAGE_MCP_CAPS=all                    # 启用所有分组
"""
import os
from collections.abc import Sequence

from fastmcp.server.transforms import Transform, GetToolNext, VersionSpec
from fastmcp.tools.tool import Tool

from . import config


# ==================== 能力分组定义 ====================
# 分组说明：
#   core      - 核心自动化：连接、导航、通用交互
#   vtable    - Canvas 表格操作（含统一表格 facade）
#   legacy    - 遗留 jQuery/Bootstrap Table / layer 页面族
#   filter    - 筛选区操作
#   observe   - 观察/弹窗检测
#   network   - 网络监听
#   workflow  - 证据生成、用例生成、执行、报告与回归
#   storage   - 存储/浏览器上下文
#   roles     - 多账号角色会话与审批回归切换
#   devtools  - 调试/高级功能
CAP_GROUPS = {
    "core": [
        "connect", "refresh_session", "check_session", "set_target_env",
        "enter_module", "get_active_frame", "detect_page_family", "activate_tool_groups",
        "scan_layer_content",
        "select_row_open_layer",
        "click", "click_xy", "input", "set_field_value", "insert_text", "hover", "set_date",
        "scan_page_elements", "find_elements", "find_batch", "dom_tree",
        "capture_page_model", "scan_toolbar_actions", "scan_form_fields",
        "observe_snapshot", "scan_pagination",
        "screenshot", "close_modal", "get_element_coords",
        "browser_tabs",
        "browser_scroll",
        "browser_press_key",
        "browser_get_element_state",
        "browser_list_caps",
        "detect_layer_msg",
    ],
    "vtable": [
        "query_table", "inspect_table_cell", "table_action",
        "scan_table", "get_table_values", "find_vtable_row", "count_vtable_rows",
        "get_vtable_row_values", "get_table_data",
        "get_all_table_data", "scan_action_availability_by_selection",
        "get_vtable_cell_render_info", "get_vtable_cell_icons",
        "vtable_action", "click_table_cell", "hover_table_cell", "resize_table_column",
        "reorder_vtable_column",
    ],
    "legacy": [
        "detect_page_family", "activate_tool_groups",
        "scan_layer_content", "scan_form_fields", "select_option", "set_field_value",
        "select_row_open_layer",
        "scan_table", "get_table_values", "get_table_data", "get_all_table_data",
        "click_table_cell", "query_table", "table_action",
        "observe_snapshot", "close_modal", "capture_page_model",
        "detect_layer_msg",
    ],
    "filter": [
        "scan_filter_fields", "select_option",
    ],
    "observe": [
        "observe_snapshot", "observe_start", "observe_wait", "explore_action",
    ],
    "network": [
        "network_trace_start", "network_trace_stop",
        "listen_start", "listen_wait", "listen_stop",
        "listen_ws_start", "listen_ws_wait",
        "network_record_start", "network_record_stop", "network_record_export",
    ],
    "workflow": [
        "flow_start", "flow_status", "flow_capture_page_state", "flow_stop",
        "generate_test_cases_from_flow", "run_test_cases",
        "combine_test_case_files",
        "generate_test_report", "compare_regression_report",
    ],
    "storage": [
        "new_context", "switch_context", "close_context", "list_contexts",
        "set_permission",
    ],
    "roles": [
        "role_session_start",
        "role_session_open", "role_session_login", "role_session_activate",
        "role_session_list", "role_session_close",
    ],
    "devtools": [
        "run_js", "mouse_trail",
        "download_by_browser", "click_to_download", "click_to_upload",
        "browser_console_messages",
        "browser_save_pdf",
    ],
}


# ==================== 企业 Profile 工具集 ====================
ENTERPRISE_TOOLS = {
    "connect", "browser_tabs", "check_session", "refresh_session", "set_target_env",
    "enter_module", "get_active_frame", "detect_page_family", "activate_tool_groups",
    "capture_page_model", "scan_filter_fields", "scan_form_fields",
    "scan_layer_content", "select_option", "set_field_value",
    "select_row_open_layer",
    "find_elements",
    "observe_snapshot", "explore_action", "close_modal", "screenshot",
    "query_table", "inspect_table_cell", "table_action",
    "network_trace_start", "network_trace_stop",
    "flow_start", "flow_status", "flow_capture_page_state", "flow_stop",
    "generate_test_cases_from_flow", "run_test_cases",
    "generate_test_report", "compare_regression_report",
    "role_session_start", "role_session_activate", "role_session_list",
    "role_session_close",
    "detect_layer_msg",
}


# ==================== 注解相关集合 ====================
# 只读工具：不改变页面状态
READ_ONLY_TOOLS = {
    "browser_get_element_state",
    "browser_list_caps",
    "check_session",
    "detect_page_family",
    "scan_layer_content",
    "find_batch",
    "find_elements",
    "get_active_frame",
    "get_element_coords",
    "list_contexts",
    "observe_snapshot",
    "query_table",
    "inspect_table_cell",
    "role_session_list",
    "scan_form_fields",
    "scan_pagination",
    "scan_toolbar_actions",
    "flow_status",
    "detect_layer_msg",
}

# 叠加性工具：会修改页面状态但可逆
ADDITIVE_TOOLS = {
    "browser_console_messages",
    "browser_save_pdf",
    "capture_page_model",
    "connect",
    "dom_tree",
    "network_record_export",
    "network_record_start",
    "network_record_stop",
    "network_trace_start",
    "network_trace_stop",
    "role_session_activate",
    "role_session_login",
    "role_session_open",
    "role_session_start",
    "listen_start",
    "listen_stop",
    "listen_wait",
    "listen_ws_start",
    "listen_ws_wait",
    "observe_start",
    "observe_wait",
    "scan_page_elements",
    "scan_table",
    "screenshot",
    "select_option",
    "scan_filter_fields",
    "new_context",
    "switch_context",
    "generate_test_cases_from_flow",
    "generate_test_report",
    "compare_regression_report",
}

# 幂等写工具：多次调用效果相同
IDEMPOTENT_WRITE_TOOLS = {
    "check_session",
    "connect",
    "get_active_frame",
    "role_session_activate",
    "browser_list_caps",
}

# 工具超时覆盖
TOOL_TIMEOUTS = {
    "listen_wait": config.WAIT_TOOL_TIMEOUT,
    "listen_ws_wait": config.WAIT_TOOL_TIMEOUT,
    "observe_wait": config.WAIT_TOOL_TIMEOUT,
}


# ==================== 运行时状态 ====================
PROFILES = {"enterprise", "full"}


def get_enabled_profile() -> str:
    """Return the configured profile, falling back to the full development surface."""
    profile = os.environ.get("DRISSIONPAGE_MCP_PROFILE", "full").strip().lower()
    return profile if profile in PROFILES else "full"


def _get_enabled_caps() -> set[str]:
    caps_env = os.environ.get("DRISSIONPAGE_MCP_CAPS", "")
    if not caps_env:
        return set(CAP_GROUPS.keys())
    if caps_env.strip().lower() == "all":
        return set(CAP_GROUPS.keys())
    return {c.strip() for c in caps_env.split(",") if c.strip()}


ENABLED_PROFILE = get_enabled_profile()
ENABLED_CAPS = _get_enabled_caps()


# ==================== 工具→分组逆向索引 ====================
_TOOL_CAP_MAP: dict[str, set[str]] = {}
for _cap, _tools in CAP_GROUPS.items():
    for _tool in _tools:
        _TOOL_CAP_MAP.setdefault(_tool, set()).add(_cap)


# ==================== 公开辅助函数 ====================
def list_caps() -> dict:
    """列出当前分组、Profile 和公开工具目录。"""
    return {
        "profile": ENABLED_PROFILE,
        "enabled": sorted(ENABLED_CAPS),
        "exposed_tools": sorted({
            tool for tools in CAP_GROUPS.values() for tool in tools
            if _is_tool_visible_by_config(tool)
        }),
        "available": {
            cap: tools for cap, tools in CAP_GROUPS.items()
        },
    }


def _is_tool_visible_by_config(tool_name: str) -> bool:
    """根据环境配置判断工具是否该被暴露（仅用于状态报告，不由 FastMCP 执行过滤）。"""
    if ENABLED_PROFILE == "enterprise" and tool_name not in ENTERPRISE_TOOLS:
        return False
    for cap, tools in CAP_GROUPS.items():
        if cap in ENABLED_CAPS and tool_name in tools:
            return True
    return False


def is_component_needed(tool_name: str) -> bool:
    """检查某工具在当前 profile/caps 下是否可能被需要（用于惰性加载组件 Provider）。"""
    if tool_name not in _TOOL_CAP_MAP:
        return False
    if ENABLED_PROFILE == "enterprise":
        return tool_name in ENTERPRISE_TOOLS
    caps_for_tool = _TOOL_CAP_MAP[tool_name]
    return bool(caps_for_tool & ENABLED_CAPS)


def get_tool_caps(tool_name: str) -> set[str]:
    """获取工具所属的能力分组。"""
    return _TOOL_CAP_MAP.get(tool_name, set())


# ==================== FastMCP Transform ====================
class ToolMetadataTransform(Transform):
    """在工具列出和查找时注入 cap:*/profile:enterprise/level:* 标签及 annotations。

    与 mcp.enable(tags=...)/mcp.disable(tags=...) 配合使用，
    替代旧 _cap_aware_tool 的注册时注入方式。
    """

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._enhance(t) for t in tools]

    async def get_tool(self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None) -> Tool | None:
        tool = await call_next(name, version=version)
        return self._enhance(tool) if tool else None

    @staticmethod
    def _build_annotations(name: str):
        from mcp.types import ToolAnnotations

        if name in READ_ONLY_TOOLS:
            return ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
                title=None,
            )
        if name in ADDITIVE_TOOLS:
            return ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=name in IDEMPOTENT_WRITE_TOOLS,
                openWorldHint=True,
                title=None,
            )
        return ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=name in IDEMPOTENT_WRITE_TOOLS,
            openWorldHint=True,
            title=None,
        )

    @staticmethod
    def _build_tags(name: str, annotations, existing_tags: set[str]) -> set[str]:
        """Keep component-specific domains while owning capability/risk metadata here."""
        managed_prefixes = ("cap:", "profile:", "level:", "risk:")
        caps_set = _TOOL_CAP_MAP.get(name, set())
        tags = {
            tag for tag in existing_tags
            if not tag.startswith(managed_prefixes)
        }
        for cap in caps_set:
            tags.add(f"cap:{cap}")
        if name in ENTERPRISE_TOOLS:
            tags.update({"profile:enterprise", "level:facade"})
        elif caps_set & {"devtools"}:
            tags.add("level:primitive")
        else:
            tags.add("level:advanced")
        if annotations.readOnlyHint:
            tags.add("risk:read")
        elif annotations.destructiveHint:
            tags.add("risk:destructive")
        else:
            tags.add("risk:write")
        return tags

    @staticmethod
    def _enhance(tool: Tool) -> Tool:
        name = tool.name
        annotations = tool.annotations or ToolMetadataTransform._build_annotations(name)
        tags = ToolMetadataTransform._build_tags(name, annotations, set(tool.tags))
        update = {"tags": tags, "annotations": annotations}
        if name in TOOL_TIMEOUTS:
            update["timeout"] = TOOL_TIMEOUTS[name]
        return tool.model_copy(update=update)


# ==================== 可见性配置 ====================
def configure_visibility(mcp):
    """根据环境变量配置工具可见性。

    在 FastMCP 服务器初始化后调用。使用原生 enable()/disable()
    替代旧 _cap_aware_tool 的注册时过滤。
    """
    profile = os.environ.get("DRISSIONPAGE_MCP_PROFILE", "full").strip().lower()
    if profile not in PROFILES:
        profile = "full"
    caps_env = os.environ.get("DRISSIONPAGE_MCP_CAPS", "").strip()

    if profile == "enterprise":
        mcp.enable(tags={"profile:enterprise"}, only=True)
        return

    if caps_env and caps_env.lower() != "all":
        requested = {c.strip() for c in caps_env.split(",") if c.strip()}
        all_cap_tags = {f"cap:{c}" for c in CAP_GROUPS}
        mcp.disable(tags=all_cap_tags, components={"tool"})
        if requested:
            mcp.enable(tags={f"cap:{c}" for c in requested}, components={"tool"})
