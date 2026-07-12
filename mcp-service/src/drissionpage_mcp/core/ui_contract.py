"""诺贝 SCM 固定前端组件契约。

本服务不是通用网页驱动器：前端已固定为 Ant Design 3、Legions Pro Quick Filter
与 VisActor VTable。这里集中保存跨模块复用的稳定 DOM 契约，业务组件升级时先修改本
文件，再运行各能力分组的契约测试；功能模块不得另写一套同义选择器。

选择器来自 2026-07-12 对“上工记录”“工资明细”真实页面的表单、筛选区、弹窗、
日期面板、下拉菜单、HTML Table 与 VTable Canvas 采集。优先使用精确组件 class；
XPath 只作为 DrissionPage 定位失败时的兼容路径。
"""

# 该版本随 DOM 契约变化递增，便于页面模型、执行证据和测试报告定位兼容范围。
CONTRACT_NAME = "nuobei-scm-fixed-ui"
CONTRACT_VERSION = "2026.07.12.1"
FRAMEWORKS = {
    "component_library": "Ant Design 3",
    "quick_filter": "Legions Pro Quick Filter",
    "canvas_table": "VisActor VTable",
}

# 顶层工作区：只有 aria-hidden=false 的 tabpanel 才是当前业务页。
ACTIVE_FRAME = 'c:[role="tabpanel"][aria-hidden="false"] iframe'
ACTIVE_FRAME_XPATH = (
    'xpath://div[@role="tabpanel" and not(@aria-hidden="true")]//iframe'
)
ACTIVE_TAB_TRIGGER = 'css:.ant-tabs-tab-active .ant-dropdown-trigger'
ACTIVE_TAB_TRIGGER_XPATH = (
    'xpath://div[contains(@class,"ant-tabs-tab-active")]'
    '//*[contains(@class,"ant-dropdown-trigger")]'
)

# Legions Pro 快捷筛选：首屏条件与 remaining 区共同组成一个逻辑筛选区。
FILTER_ROOT = 'c:.page-query'
FILTER_ROOT_CSS = 'css:.page-query'
FILTER_ROOT_XPATH = 'xpath://div[contains(@class,"page-query")]'
FILTER_REMAINING_CSS = 'css:.legions-pro-quick-filter-remaining'
FILTER_REMAINING_XPATH = (
    'xpath:.//*[contains(@class,"legions-pro-quick-filter-remaining")]'
)
FILTER_ROW = 'c:.legions-pro-query-item'
FILTER_ROW_FALLBACK = 'c:.legions-pro-quick-filter .ant-row'
FILTER_INLINE_MENU = 'c:.ant-dropdown:not(.ant-dropdown-hidden)'
FILTER_INLINE_MENU_CSS = 'css:.ant-dropdown:not(.ant-dropdown-hidden)'
FILTER_INLINE_MENU_XPATH = (
    'xpath://*[contains(@class,"ant-dropdown") '
    'and not(contains(@class,"ant-dropdown-hidden"))]'
)
FILTER_INLINE_ITEM_CSS = 'css:.ant-dropdown-menu-item'
FILTER_INLINE_ITEM_XPATH = (
    'xpath:.//*[contains(@class,"ant-dropdown-menu-item")]'
)
FILTER_COLUMNS_CSS = 'css:.legions-pro-quick-filter-row > div[class*="ant-col-"]'
FILTER_COLUMNS_XPATH = (
    'xpath://div[contains(@class,"legions-pro-quick-filter-row")]'
    '/div[contains(@class,"ant-col-")]'
)
FILTER_SELECT_CSS = 'css:.ant-select'
FILTER_SELECT_XPATH = 'xpath:.//*[contains(@class,"ant-select")]'
FILTER_SELECT_TRIGGER_CSS = (
    'css:[role="combobox"], .ant-select-selection, .ant-select-selector'
)
FILTER_SELECT_TRIGGER_XPATH = (
    'xpath:.//*[@role="combobox"] | '
    './/*[contains(@class,"ant-select-selection")] | '
    './/*[contains(@class,"ant-select-selector")]'
)
FILTER_SELECT_OPEN = 'c:.ant-select-dropdown:not(.ant-select-dropdown-hidden)'
FILTER_SELECT_OPEN_CSS = 'css:.ant-select-dropdown:not(.ant-select-dropdown-hidden)'
FILTER_SELECT_OPEN_XPATH = (
    'xpath://*[contains(@class,"ant-select-dropdown") '
    'and not(contains(@class,"ant-select-dropdown-hidden"))]'
)
FILTER_DATE_PICKER = 'c:.ant-calendar-picker'
FILTER_DATE_INPUT = 'c:.ant-calendar-picker-input'
FILTER_DATE_CALENDAR = 'c:.ant-calendar'

# Ant Design 表单。语义字段工具应先定位 FORM_ITEM，再在内部找具体控件，避免同名字段串位。
FORM_ITEM = '.ant-form-item'
FORM_LABEL = '.ant-form-item-label label,.ant-form-item-label'
FORM_CONTROL = (
    'input:not([type="hidden"]),textarea,.ant-select,.ant-calendar-picker,.ant-picker,'
    '.ant-input-number,.ant-checkbox-wrapper,.ant-radio-group,.ant-switch,[role="combobox"]'
)
TEXT_CONTROL = 'input:not([type="hidden"]),textarea,.ant-input-number-input,[contenteditable="true"]'
DATE_RANGE_INPUT = 'input.ant-calendar-range-picker-input'
BUTTON = 'button,.ant-btn,[role="button"],a[href]'

# Ant Design 浮层。可见性必须另外以 states.is_displayed 或边界框判断，不能仅凭 DOM 存在。
MODAL = '.ant-modal'
MODAL_CONTENT = '.ant-modal-content'
MODAL_WRAP = '.ant-modal-wrap'
MODAL_CLOSE = '.ant-modal-close'
MODAL_TITLE = '.ant-modal-title'
MODAL_BODY = '.ant-modal-body'
CONFIRM_BODY = '.ant-confirm-body'
CONFIRM_WRAPPER_CSS = 'css:.ant-modal-content .ant-confirm-body-wrapper'
CONFIRM_WRAPPER_XPATH = (
    'xpath://*[contains(@class,"ant-modal-content")]'
    '//*[contains(@class,"ant-confirm-body-wrapper")]'
)
CONFIRM_BODY_CSS = 'css:.ant-confirm-body'
CONFIRM_BODY_XPATH = 'xpath:.//*[contains(@class,"ant-confirm-body")]'
NOTIFICATION = '.ant-notification-notice'
MESSAGE = '.ant-message-notice'
DRAWER = '.ant-drawer'
DRAWER_TITLE = '.ant-drawer-title'
DRAWER_BODY = '.ant-drawer-body'
DRAWER_CLOSE = '.ant-drawer-close'
POPOVER = '.ant-popover'
TOOLTIP = '.ant-tooltip'
DROPDOWN = '.ant-dropdown'
SELECT_DROPDOWN = '.ant-select-dropdown'
DATE_PICKER_OVERLAY = '.ant-calendar-picker-container'
DATE_CALENDAR = '.ant-calendar'
OVERLAY_CLOSE = (
    '.ant-modal-close,.ant-drawer-close,.ant-notification-notice-close,'
    '.ant-message-notice-close'
)

# VTable 浮层和画布。ROOT_FALLBACK 仅用于旧页面缺少精确 .vtable class 的场景。
VTABLE_ROOT = '.vtable'
VTABLE_ROOT_FALLBACK = '[class*="vtable"]'
VTABLE_LOADING = (
    "xpath://div[@class='page-content']//div[contains(@class,'vtable-loading')]"
)
VTABLE_FILTER_MENU = '.vtable-filter-menu'
VTABLE_TOOLTIP = '.vtable__bubble-tooltip-element'
VTABLE_MENU = '.vtable__menu-element'

# Ant Design 原生 HTML 表格与分页。
HTML_TABLE_WRAPPER = '.ant-table-wrapper'
HTML_TABLE = '.ant-table'
PAGINATION = '.ant-pagination'

# 页面模型只扫描可交互控件；Canvas 内元素由 VTable facade 单独建模。
INTERACTIVE_CONTROLS = (
    'button,a[href],input,select,textarea,'
    '[role=button],[role=menuitem],[role=tab],[role=checkbox],'
    '[role=switch],[role=link],[onclick],.el-button,.ant-btn,[class*=btn]'
)

# 页面快照使用组件根，MutationObserver 使用最早挂载的内容节点；二者用途不同，不能合并。
FLOAT_ROOTS = (
    MODAL,
    DRAWER,
    POPOVER,
    TOOLTIP,
    DROPDOWN,
    SELECT_DROPDOWN,
    NOTIFICATION,
    MESSAGE,
    VTABLE_FILTER_MENU,
    VTABLE_TOOLTIP,
    VTABLE_MENU,
    DATE_PICKER_OVERLAY,
    DATE_CALENDAR,
)

OBSERVABLE_OVERLAYS = (
    MODAL_CONTENT,
    DRAWER,
    POPOVER,
    TOOLTIP,
    DROPDOWN,
    SELECT_DROPDOWN,
    VTABLE_FILTER_MENU,
    VTABLE_TOOLTIP,
    VTABLE_MENU,
    DATE_PICKER_OVERLAY,
    DATE_CALENDAR,
    NOTIFICATION,
    MESSAGE,
)
