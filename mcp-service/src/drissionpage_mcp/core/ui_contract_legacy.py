"""遗留 jQuery / Bootstrap 3 业务页 UI 契约。

覆盖 SCM Admin iframe 内的「昊链标准头端」类页面，例如账号管理、角色管理：
  jQuery 2.x + Bootstrap 3.3.x + AdminLTE + Bootstrap Table + layer.js + bootstrap-select。

选择器来自 2026-07-14 对账号管理真实页面的 DOM / 脚本探测。业务服务应引用本文件
常量，避免在 server.py 或 skill 中散落硬编码选择器。
"""

CONTRACT_NAME = "hoolinks-legacy-jq-bootstrap"
CONTRACT_VERSION = "2026.07.14.1"
FRAMEWORKS = {
    "base_library": "jQuery 2.x",
    "component_library": "Bootstrap 3.3.x",
    "admin_theme": "AdminLTE",
    "data_table": "Bootstrap Table",
    "dialog": "layer.js",
    "select": "bootstrap-select",
}

# ---- Bootstrap Table ----
BT_ROOT = ".bootstrap-table"
BT_CONTAINER = ".fixed-table-container"
BT_HEADER = ".fixed-table-header"
BT_BODY = ".fixed-table-body"
BT_TOOLBAR = ".fixed-table-toolbar"
BT_PAGINATION = ".fixed-table-pagination"
BT_COLUMNS_DROPDOWN = ".keep-open.btn-group"
BT_SEARCH_INPUT = ".fixed-table-toolbar .search input, .pull-right.search input, input.form-control[placeholder*='关键词']"
BT_REFRESH_BTN = 'button[name="refresh"], .fixed-table-toolbar button[name="refresh"]'
BT_PAGE_SIZE_BTN = ".page-list .dropdown-toggle, .fixed-table-pagination .btn-group.dropup .dropdown-toggle"
BT_SELECT_ALL = 'input[name="btSelectAll"]'
BT_SELECT_ITEM = 'input[name="btSelectItem"]'
BT_ROW = "tbody > tr[data-index], tbody > tr"
BT_TABLE = "table"

# ---- layer.js 弹层 ----
LAYER_ROOT = ".layui-layer"
LAYER_TITLE = ".layui-layer-title"
LAYER_CONTENT = ".layui-layer-content"
LAYER_BTN_AREA = ".layui-layer-btn"
LAYER_CLOSE = ".layui-layer-close, .layui-layer-setwin .layui-layer-close"
LAYER_MIN = ".layui-layer-min"
LAYER_MAX = ".layui-layer-max"
LAYER_SHADE = ".layui-layer-shade"
LAYER_MSG = ".layui-layer-msg, .layui-layer-dialog.layui-layer-msg"
LAYER_SAFE_LABELS = ("取消", "关闭", "返回", "否", "暂不", "知道了")

# ---- Bootstrap / 表单 ----
BS_BTN = ".btn, button.btn"
BS_PRIMARY = ".btn-primary"
BS_DEFAULT = ".btn-default"
BS_FORM_CONTROL = ".form-control"
BS_SELECT = ".bootstrap-select"
BS_SELECT_TOGGLE = ".bootstrap-select > button.dropdown-toggle"
BS_SELECT_MENU = ".bootstrap-select .dropdown-menu"
BS_SELECTPICKER = "select.selectpicker"
GLYPHICON = ".glyphicon"

# 工具栏业务按钮（账号管理等）
BTN_ADD = "button.btn-add, .btn-group .btn-add"
BTN_EDIT = "button.btn-edit, .btn-group .btn-edit"
BTN_PWD = "button.btn-pwd, .btn-group .btn-pwd"
BTN_SAVE = "button.btn-primary[type='submit'], button.btn-primary"
BTN_CANCEL = "button.btnClose, .btnClose, button.btn-default.btnClose"

# AdminLTE 壳特征（iframe 内旧页 body）
ADMINLTE_BODY = "body.skin-blue, body.sidebar-mini"

# 探测用选择器聚合
DETECT_SELECTORS = {
    "bootstrap_table": BT_ROOT + ", " + BT_CONTAINER,
    "layer": LAYER_ROOT,
    "bootstrap_select": BS_SELECT + ", " + BS_SELECTPICKER,
    "bootstrap_btn": ".btn-default, .btn-primary, .form-control",
    "adminlte": ADMINLTE_BODY,
    "jquery_file_upload": "input.fileInput, .fileinput-button",
}
