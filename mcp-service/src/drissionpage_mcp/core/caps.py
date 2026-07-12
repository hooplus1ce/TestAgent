"""能力分组（Capability Tiers）

按场景分组暴露工具，
避免一次性加载全部工具造成上下文浪费。

使用方式：
  export DRISSIONPAGE_MCP_CAPS=core,vtable,filter  # 启用指定分组
  export DRISSIONPAGE_MCP_CAPS=all                 # 启用所有分组

分组说明：
  core      - 核心自动化（默认）：连接、导航、通用交互
  vtable    - Canvas 表格操作
  filter    - 筛选区操作
  observe   - 观察/弹窗检测
  network   - 网络监听
  storage   - 存储/浏览器上下文
  roles     - 多账号角色会话与审批回归切换
  devtools  - 调试/高级功能
"""
import os


# 能力分组定义
CAP_GROUPS = {
    "core": [
        # 连接与会话
        "connect", "refresh_session", "check_session",
        # 导航与 frame
        "enter_module", "get_active_frame",
        # 通用 DOM 原语
        "click", "click_xy", "input", "set_field_value", "insert_text", "hover", "set_date",
        # 页面理解
        "scan_page_elements", "find_elements", "find_batch", "dom_tree",
        "capture_page_model", "scan_toolbar_actions", "scan_form_fields",
        "observe_snapshot", "scan_pagination",
        # 调试
        "screenshot", "close_modal", "get_element_coords",
        # 浏览器管理
        "browser_tabs",
        # 滚动操作
        "browser_scroll",
        # 按键操作
        "browser_press_key",
        # 元素状态查询
        "browser_get_element_state",
        # 新增：caps 管理
        "browser_list_caps",
    ],
    "vtable": [
        # 统一表格 facade
        "scan_table", "get_table_values", "find_vtable_row", "count_vtable_rows",
        "get_vtable_row_values", "get_table_data",
        "get_all_table_data", "scan_action_availability_by_selection",
        "get_vtable_cell_render_info", "get_vtable_cell_icons",
        "vtable_action", "click_table_cell", "hover_table_cell", "resize_table_column",
    ],
    "filter": [
        # 筛选区
        "scan_filter_fields", "select_date_range", "select_option",
    ],
    "observe": [
        # 观察器
        "observe_snapshot", "observe_start", "observe_wait", "explore_action",
    ],
    "network": [
        # 网络监听
        "listen_start", "listen_wait", "listen_stop",
        "listen_ws_start", "listen_ws_wait",
        "network_record_start", "network_record_stop", "network_record_export",
    ],
    "workflow": [
        # 真实证据、用例生成、执行、报告与回归
        "flow_start", "flow_status", "flow_capture_page_state", "flow_stop",
        "generate_test_cases_from_flow", "run_test_cases",
        "combine_test_case_files",
        "generate_test_report", "compare_regression_report",
    ],
    "storage": [
        # 存储/上下文
        "new_context", "switch_context", "close_context", "list_contexts",
        "set_permission",
    ],
    "roles": [
        # 多账号角色会话（每个角色独立 BrowserContext）
        "role_session_open", "role_session_login", "role_session_activate",
        "role_session_list", "role_session_close",
    ],
    "devtools": [
        # 调试/高级功能
        "run_js", "mouse_trail", "download_by_browser",
        "browser_console_messages",
        # PDF 导出
        "browser_save_pdf",
    ],
}


def get_enabled_caps() -> set[str]:
    """从环境变量获取启用的能力分组"""
    caps_env = os.environ.get("DRISSIONPAGE_MCP_CAPS") or os.environ.get("DRISSION_UI_CAPS", "")
    if not caps_env:
        # 默认暴露全部工具；需要裁剪时显式设置 DRISSIONPAGE_MCP_CAPS=core,vtable 等。
        return set(CAP_GROUPS.keys())
    if caps_env.strip().lower() == "all":
        return set(CAP_GROUPS.keys())
    return {c.strip() for c in caps_env.split(",") if c.strip()}


ENABLED_CAPS = get_enabled_caps()


def is_tool_enabled(tool_name: str) -> bool:
    """检查工具是否在启用的分组中"""
    if tool_name == "browser_list_caps":
        return True
    for cap, tools in CAP_GROUPS.items():
        if cap in ENABLED_CAPS and tool_name in tools:
            return True
    return False


def get_tool_group(tool_name: str) -> str | None:
    """获取工具所属的分组"""
    for cap, tools in CAP_GROUPS.items():
        if tool_name in tools:
            return cap
    return None


def list_caps() -> dict:
    """列出所有分组及其包含的工具"""
    return {
        "enabled": sorted(ENABLED_CAPS),
        "available": {
            cap: tools for cap, tools in CAP_GROUPS.items()
        },
    }


# 未分类的工具（内部使用，不暴露）
_INTERNAL_TOOLS = {
    "expand_filter_area", "reset_to_initial", "dom_overview",
    "find_static", "get_frame", "mount_vtable", "scan_vtable_columns",
    "get_column_values", "get_cell_rect", "scroll_to_cell", "click_cell",
    "resize_column", "detect_modal", "detect_notification",
    "detect_message", "detect_url_change", "detect_tab_change",
    "scan_modal", "scan_drawer", "scan_floats",
    "scan_html_table", "get_html_table_values", "click_html_table_cell",
    "hover_html_table_cell", "get_html_table_data",
}
