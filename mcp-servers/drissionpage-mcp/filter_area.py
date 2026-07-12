"""筛选区操作：展开/切换模式、日期范围选择、字段矩阵扫描。

与 browser_session.py 解耦，专注于筛选区相关的 UI 自动化操作。
"""
import json
from datetime import datetime
import logging
from DrissionPage.common import Keys

import browser_session
import ui_contract

logger = logging.getLogger("drissionpage-mcp")
# 复用 browser_session 全局锁：server 层 synchronized 已串行化所有工具调用，
# 此处 with _lock 是其重入（RLock 安全），无需独立锁；保留 _lock 名仅为最小改动。
_lock = browser_session._lock

def _get_frame(tab):
    """定位路径优先使用无副作用 frame 查询，失败才同步模块名称。"""
    frame = browser_session.get_active_frame_ro(tab)
    return frame if frame is not None else browser_session.get_active_frame(tab)


def _native_click(element, timeout: float = 3, wait_stop: bool = True) -> None:
    """通过 DrissionPage 4.2 原生 click 自带的可点击与遮挡等待执行动作。"""
    try:
        element.click(by_js=False, timeout=timeout, wait_stop=wait_stop)
    except TypeError:
        # 保持最小测试替身和旧稳定版兼容；不回退 JavaScript 点击。
        element.click()


def expand_filter_area(tab=None):
    """展开筛选区：优先切换为内联模式，并点击展开显示所有筛选字段。"""
    with _lock:
        tab = tab or browser_session.get_tab()
        fr = _get_frame(tab)
        if fr is None:
            return {"ok": False, "reason": "未找到活动 iframe"}
        try:
            try:
                fr.wait.eles_loaded(ui_contract.FILTER_ROOT, timeout=5)
            except Exception:
                pass

            def _state():
                query = browser_session.ele_with_fallback(
                    fr,
                    ui_contract.FILTER_ROOT_CSS,
                    ui_contract.FILTER_ROOT_XPATH,
                    timeout=1.0,
                )
                if not query:
                    return {"hasQuery": False}
                buttons = browser_session.eles_with_fallback(
                    query, 'css:button', 'xpath:.//button'
                )
                expand = None
                collapse = None
                for button in buttons:
                    if not button.states.is_displayed:
                        continue
                    text = (button.text or "").replace(" ", "")
                    if "展开" in text:
                        expand = button
                    elif "收起" in text:
                        collapse = button
                remaining = browser_session.ele_with_fallback(
                    query,
                    ui_contract.FILTER_REMAINING_CSS,
                    ui_contract.FILTER_REMAINING_XPATH,
                    timeout=0.1,
                )
                bars = None
                if not (remaining and collapse):
                    for button in buttons:
                        if button.states.is_displayed and browser_session.ele_with_fallback(
                            button,
                            'css:i.anticon-bars',
                            'xpath:.//i[contains(@class, "anticon-bars")]',
                            timeout=0.1,
                        ):
                            bars = button
                            break
                return {
                    "hasQuery": True,
                    "hasExpand": expand is not None,
                    "hasCollapse": collapse is not None,
                    "hasBars": bars is not None,
                    "hasRemaining": remaining is not None,
                    "fieldText": (query.text or "").strip()[:120],
                }

            st = _state()
            if not st.get("hasQuery"):
                return {"ok": False, "reason": "未找到筛选区 %s" % ui_contract.FILTER_ROOT}
            if st.get('hasRemaining') and st.get('hasCollapse'):
                return {"ok": True, "reason": "筛选区已是内联展开模式"}

            # Step 1: 优先切换到内联模式，避免点击「展开」触发高级搜索弹窗。
            if st.get('hasBars'):
                query = browser_session.ele_with_fallback(
                    fr,
                    ui_contract.FILTER_ROOT_CSS,
                    ui_contract.FILTER_ROOT_XPATH,
                )
                if query:
                    btns = browser_session.eles_with_fallback(query, 'css:button', 'xpath:.//button')
                    bars_btn = None
                    for b in btns:
                        if b.states.is_displayed:
                            if browser_session.ele_with_fallback(
                                b,
                                'css:i.anticon-bars',
                                'xpath:.//i[contains(@class, "anticon-bars")]',
                                timeout=0.1
                            ):
                                bars_btn = b
                                break
                    if bars_btn:
                        _native_click(bars_btn, timeout=2, wait_stop=False)
                        
                try:
                    fr.wait.ele_displayed(ui_contract.FILTER_INLINE_MENU, timeout=3)
                except Exception:
                    pass

                dropdowns = browser_session.eles_with_fallback(
                    fr,
                    ui_contract.FILTER_INLINE_MENU_CSS,
                    ui_contract.FILTER_INLINE_MENU_XPATH,
                )
                
                menu = None
                for m in dropdowns:
                    if m.states.is_displayed:
                        text = m.text or ""
                        if '内联模式' in text and '弹窗模式' in text:
                            menu = m
                            break
                
                if menu:
                    items = browser_session.eles_with_fallback(
                        menu,
                        ui_contract.FILTER_INLINE_ITEM_CSS,
                        ui_contract.FILTER_INLINE_ITEM_XPATH,
                    )
                    inline_item = None
                    selected_item = None
                    for item in items:
                        item_text = (item.text or "").strip()
                        if '内联模式' in item_text:
                            inline_item = item
                        if 'ant-dropdown-menu-item-selected' in (item.attrs.get('class') or ""):
                            selected_item = item
                    
                    selected_text = (selected_item.text or "").strip() if selected_item else ""
                    if inline_item and '内联模式' not in selected_text:
                        _native_click(inline_item, timeout=2, wait_stop=False)
                    else:
                        _close_visible_dropdowns(fr, timeout=0.3)
                        
                try:
                    fr.wait.ele_hidden(ui_contract.FILTER_INLINE_MENU, timeout=3)
                except Exception:
                    pass

                st = _state()
                if st.get('hasRemaining') and st.get('hasCollapse'):
                    return {"ok": True, "reason": "已切换为内联展开模式"}

            # Step 2: 内联模式下展开剩余筛选项。
            if st.get('hasExpand'):
                query = browser_session.ele_with_fallback(
                    fr,
                    ui_contract.FILTER_ROOT_CSS,
                    ui_contract.FILTER_ROOT_XPATH,
                )
                if query:
                    btns = browser_session.eles_with_fallback(query, 'css:button', 'xpath:.//button')
                    expand_btn = None
                    for b in btns:
                        if b.states.is_displayed and '展开' in (b.text or "").replace(" ", ""):
                            expand_btn = b
                            break
                    if expand_btn:
                        _native_click(expand_btn, timeout=2, wait_stop=False)
                        
                try:
                    fr.wait.ele_displayed('text:收起', timeout=3)
                except Exception:
                    pass
                st = _state()
                if st.get('hasRemaining') or st.get('hasCollapse'):
                    return {"ok": True, "reason": "筛选区已以内联模式展开"}

            if st.get('hasCollapse'):
                return {"ok": True, "reason": "筛选区已展开"}
            return {"ok": True, "reason": "无展开/收起按钮，跳过"}

        except Exception as e:
            logger.debug("expand_filter_area 失败: %s", e)
            return {"ok": False, "reason": str(e)}

def select_date_range(field_name: str, start_date: str, end_date: str, tab=None):
    """选择 Ant Design RangePicker 日期范围，支持跨任意月份并校验最终值。"""
    try:
        start = datetime.strptime(str(start_date).replace("-", "/"), "%Y/%m/%d").date()
        end = datetime.strptime(str(end_date).replace("-", "/"), "%Y/%m/%d").date()
    except (TypeError, ValueError):
        return {"ok": False, "reason": "日期格式必须为 yyyy/MM/dd 或 yyyy-MM-dd"}
    if start > end:
        return {"ok": False, "reason": "开始日期不能晚于结束日期"}
    start_iso, end_iso = start.isoformat(), end.isoformat()

    with _lock:
        tab = tab or browser_session.get_tab()
        expanded = expand_filter_area(tab)
        if not expanded.get("ok"):
            return expanded
        frame = _get_frame(tab)
        if frame is None:
            return {"ok": False, "reason": "未找到活动 iframe"}
        try:
            rows = frame.eles(ui_contract.FILTER_ROW, timeout=1) or []
            if not rows:
                rows = frame.eles(ui_contract.FILTER_ROW_FALLBACK, timeout=1) or []
            target_row = None
            for row in rows:
                if field_name not in (row.text or ""):
                    continue
                if row.ele(ui_contract.FILTER_DATE_PICKER, timeout=0.5):
                    target_row = row
                    break
            if target_row is None:
                return {"ok": False, "reason": "未找到字段「%s」的日期选择器" % field_name}

            picker_input = target_row.ele(ui_contract.FILTER_DATE_INPUT, timeout=1)
            if picker_input is None:
                return {"ok": False, "reason": "日期输入框不存在: %s" % field_name}
            _native_click(picker_input, timeout=3)
            calendar = frame.ele(ui_contract.FILTER_DATE_CALENDAR, timeout=5)
            if calendar is None:
                return {"ok": False, "reason": "日历面板未弹出"}

            def shown_left_month():
                panel = calendar.ele('c:.ant-calendar-range-left', timeout=0.5) or calendar
                year_element = panel.ele('c:.ant-calendar-year-select', timeout=0.5)
                month_element = panel.ele('c:.ant-calendar-month-select', timeout=0.5)
                year = int("".join(char for char in str(year_element.text or "") if char.isdigit()))
                month = int("".join(char for char in str(month_element.text or "") if char.isdigit()))
                return year, month

            def active_date_cell(value, target_date):
                titles = list(dict.fromkeys((value, target_date.strftime("%Y/%m/%d"))))
                selectors = []
                for title in titles:
                    selectors.extend((
                        ('c:td[title="%s"]:not(.ant-calendar-last-month-cell)'
                         ':not(.ant-calendar-next-month-btn-day)'
                         ':not(.ant-calendar-next-month-cell) .ant-calendar-date' % title),
                        'c:td[title="%s"] .ant-calendar-date' % title,
                    ))
                for _ in range(600):
                    cell = None
                    for selector in selectors:
                        try:
                            cell = calendar.ele(selector, timeout=0.15)
                        except Exception:
                            cell = None
                        if cell:
                            return cell
                    current_year, current_month = shown_left_month()
                    current_index = current_year * 12 + current_month
                    target_index = target_date.year * 12 + target_date.month
                    try:
                        right_panel = calendar.ele('c:.ant-calendar-range-right', timeout=0)
                    except Exception:
                        right_panel = None
                    visible_span = 1 if right_panel else 0
                    delta = target_index - current_index
                    if 0 <= delta <= visible_span:
                        return None
                    forward = delta > visible_span
                    button_selector = (
                        'c:.ant-calendar-next-month-btn'
                        if forward else 'c:.ant-calendar-prev-month-btn'
                    )
                    try:
                        button = calendar.ele(button_selector, timeout=0.5)
                    except Exception:
                        button = None
                    if button is None:
                        return None
                    previous_month = (current_year, current_month)
                    _native_click(button, timeout=1.5)
                    calendar.wait.stop_moving(timeout=1.5, raise_err=False)
                    if shown_left_month() == previous_month:
                        return None
                return None

            start_cell = active_date_cell(start_iso, start)
            if start_cell is None:
                return {"ok": False, "reason": "未找到开始日期单元格: %s" % start_iso}
            _native_click(start_cell, timeout=3)
            try:
                calendar.ele('c:.ant-calendar-selected-start-date', timeout=3)
            except Exception:
                pass

            end_cell = active_date_cell(end_iso, end)
            if end_cell is None:
                return {"ok": False, "reason": "未找到结束日期单元格: %s" % end_iso}
            _native_click(end_cell, timeout=3)
            frame.wait.ele_hidden(
                ui_contract.FILTER_DATE_CALENDAR, timeout=3, raise_err=False
            )

            inputs = target_row.eles('c:input.ant-calendar-range-picker-input') or []
            actual_start = inputs[0].attr('value') if inputs else ''
            actual_end = inputs[1].attr('value') if len(inputs) > 1 else ''
            normalized_start = str(actual_start or "").replace("/", "-")
            normalized_end = str(actual_end or "").replace("/", "-")
            if normalized_start != start.isoformat() or normalized_end != end.isoformat():
                return {
                    "ok": False,
                    "reason": "日期范围写入后校验失败",
                    "startValue": actual_start,
                    "endValue": actual_end,
                }
            return {"ok": True, "startValue": actual_start, "endValue": actual_end}
        except Exception as exc:
            logger.debug("select_date_range 失败: %s", exc)
            return {"ok": False, "reason": str(exc)}


def scan_filter_fields(tab=None):
    """扫描筛选区所有字段，返回字段名/操作符/输入类型/下拉选项的完整矩阵。

    展开筛选区（expand_filter_area）后调用此函数，确保所有字段暴露在 DOM 中。
    内联模式下必须只扫描 .legions-pro-quick-filter-row 的直接字段列，不能扫描
    所有嵌套 .ant-row，否则会被字段内部布局和日期控件干扰导致错位。

    每个筛选字段为三段式控件：字段名下拉 / 操作符下拉 / 值控件。
    - operatorOptions：第二段操作符下拉框的全部可选操作符，必须与 field 对应。
    - valueMode=free-text：第三段是文本框，可自由输入。
    - valueMode=date-range：第三段是日期范围。
    - valueMode=must-select-option：第三段是下拉框，只能选择 options 中已有内容，不能任意填写。
    """
    with _lock:
        tab = tab or browser_session.get_tab()
        expand_result = expand_filter_area(tab)
        if not expand_result.get("ok"):
            return expand_result
        fr = _get_frame(tab)
        if fr is None:
            return {"ok": False, "reason": "未找到活动 iframe"}
        try:
            struct_js = r"""
            function selText(sel){
                var s=sel.querySelector('.ant-select-selection__rendered,.ant-select-selection-selected-value,.ant-select-selector');
                return s?(s.textContent||'').trim():'';
            }
            function isActionCol(col){
                var t=(col.textContent||'').trim().replace(/\s+/g,'');
                return !col.querySelector('.ant-select') || t.indexOf('重置')>=0 || t.indexOf('收起')>=0 || t.indexOf('展开')>=0;
            }
            var cols = document.querySelectorAll('.legions-pro-quick-filter-row > div[class*="ant-col-"]');
            var out = [], seen = {};
            for (var i = 0; i < cols.length; i++) {
                var col = cols[i];
                if (isActionCol(col)) continue;
                var selects = col.querySelectorAll('.ant-select');
                if (selects.length < 2) continue;
                var field = selText(selects[0]);
                var operator = selText(selects[1]);
                if (!field || !operator || seen[field]) continue;
                seen[field] = true;

                var entry = {
                    col: i,
                    field: field,
                    operator: operator,
                    inputType: 'unknown',
                    options: [],
                    operatorOptions: [],
                    hasValueCb: false,
                    hasOpCb: false
                };
                var valueSelect = selects[2] || null;
                var datePicker = col.querySelector('.ant-calendar-picker');
                var textInput = col.querySelector('input.ant-input-sm');
                var rangeInputs = col.querySelectorAll('input.ant-calendar-range-picker-input');

                if (datePicker || rangeInputs.length >= 2) {
                    entry.inputType = 'date-range';
                    entry.valueMode = 'date-range';
                    entry.value = {
                        start: rangeInputs[0] ? rangeInputs[0].value : '',
                        end: rangeInputs[1] ? rangeInputs[1].value : ''
                    };
                } else if (valueSelect) {
                    entry.inputType = valueSelect.querySelector('.ant-select-search__field') ? 'searchable-dropdown' : 'dropdown';
                    entry.valueMode = 'must-select-option';
                    entry.hasValueCb = !!valueSelect.querySelector('[role="combobox"]');
                } else if (textInput) {
                    entry.inputType = 'text-input';
                    entry.valueMode = 'free-text';
                    entry.value = textInput.value || '';
                } else {
                    entry.valueMode = 'unknown';
                }
                entry.hasOpCb = !!selects[1].querySelector('[role="combobox"]');
                out.push(entry);
            }
            return JSON.stringify(out);
            """
            res = fr.run_js(struct_js)
            fields = json.loads(res) if isinstance(res, str) else res
            if not isinstance(fields, list):
                return {"ok": False, "reason": "结构扫描返回非列表"}

            for entry in fields:
                # 总是尝试读取操作符下拉选项（不管 hasOpCb）
                op_opts = _collect_dropdown_options(fr, entry["col"], 1)
                if op_opts:
                    entry["operatorOptions"] = op_opts
                if entry.get("hasValueCb"):
                    entry["options"] = _collect_dropdown_options(fr, entry["col"], 2)
                for k in ("col", "hasValueCb", "hasOpCb"):
                    entry.pop(k, None)
            return {"ok": True, "fields": fields}
        except Exception as e:
            logger.debug("scan_filter_fields 失败: %s", e)
            return {"ok": False, "reason": str(e)}
        finally:
            _close_visible_dropdowns(fr, timeout=0.08)


def _select_filter_option(fr, select_element, option_text: str, timeout: float = 5.0) -> dict:
    """Select one option from a quick-filter Ant Select with native element actions."""
    _close_visible_dropdowns(fr, timeout=0.5)
    opener = browser_session.ele_with_fallback(
        select_element,
        'css:[role="combobox"], .ant-select-selection, .ant-select-selector',
        'xpath:.//*[@role="combobox"] | .//*[contains(@class, "ant-select-selection")] | .//*[contains(@class, "ant-select-selector")]',
        timeout=0.5,
    ) or select_element
    target_mid = opener.rect.viewport_midpoint
    _native_click(opener, timeout=timeout, wait_stop=False)
    try:
        fr.wait.ele_displayed(
            ui_contract.FILTER_SELECT_OPEN,
            timeout=timeout, raise_err=False,
        )
    except Exception:
        pass
    dropdowns = browser_session.eles_with_fallback(
        fr,
        ui_contract.FILTER_SELECT_OPEN_CSS,
        ui_contract.FILTER_SELECT_OPEN_XPATH,
    )
    visible = [item for item in dropdowns if item.states.is_displayed]
    if not visible:
        return {"ok": False, "reason": "筛选下拉未打开"}
    dropdown = min(
        visible,
        key=lambda item: abs(item.rect.viewport_midpoint[0] - target_mid[0])
        + abs(item.rect.viewport_midpoint[1] - target_mid[1]),
    )
    options = browser_session.eles_with_fallback(
        dropdown,
        'css:.ant-select-dropdown-menu-item, .ant-select-item-option, li',
        'xpath:.//*[contains(@class, "ant-select-dropdown-menu-item") or contains(@class, "ant-select-item-option") or local-name()="li"]',
    )
    enabled = [
        item for item in options
        if item.states.is_displayed and item.states.is_enabled
        and item.attr("aria-disabled") != "true"
    ]
    expected = str(option_text or "").strip()
    selected = next((item for item in enabled if (item.text or "").strip() == expected), None)
    if selected is None:
        _close_visible_dropdowns(fr, timeout=0.2)
        return {
            "ok": False, "reason": "筛选选项不存在: %s" % expected,
            "available": [(item.text or "").strip() for item in enabled[:50]],
        }
    selected_text = (selected.text or "").strip()
    _native_click(selected, timeout=timeout, wait_stop=False)
    return {"ok": True, "selected": selected_text}


def _quick_filter_field_column(fr, field_name: str):
    columns = browser_session.eles_with_fallback(
        fr,
        'css:.legions-pro-quick-filter-row > div[class*="ant-col-"]',
        'xpath://div[contains(@class, "legions-pro-quick-filter-row")]/div[contains(@class, "ant-col-")]',
    )
    wanted = str(field_name or "").strip()
    for column in columns:
        try:
            selects = column.eles('css:.ant-select', timeout=0.2)
        except Exception:
            selects = []
        if not selects:
            try:
                selects = column.eles('xpath:.//*[contains(@class, "ant-select")]', timeout=0.2)
            except Exception:
                selects = []
        if len(selects) < 2:
            continue
        rendered = browser_session.ele_with_fallback(
            selects[0],
            'css:.ant-select-selection__rendered, .ant-select-selection-selected-value, .ant-select-selector',
            'xpath:.//*[contains(@class, "ant-select-selection__rendered") or contains(@class, "ant-select-selection-selected-value") or contains(@class, "ant-select-selector")]',
            timeout=0.2,
        )
        if rendered and (rendered.text or "").strip() == wanted:
            return column, selects
    return None, []


def set_filter_condition(field_name: str, operator: str, value=None,
                         timeout: float = 5.0, tab=None,
                         ensure_expanded: bool = True) -> dict:
    """Set one quick-filter field/operator/value tuple through native UI actions."""
    with _lock:
        tab = tab or browser_session.get_tab()
        if ensure_expanded:
            expanded = expand_filter_area(tab)
            if not expanded.get("ok"):
                return expanded
        fr = _get_frame(tab)
        if fr is None:
            return {"ok": False, "reason": "未找到活动 iframe"}
        column, selects = _quick_filter_field_column(fr, field_name)
        if column is None:
            return {"ok": False, "reason": "未找到筛选字段: %s" % field_name}
        compact_operator = str(operator or "").strip().replace(" ", "")
        current_operator = str(selects[1].text or "").strip().replace(" ", "")
        operator_changed = current_operator != compact_operator
        if operator_changed:
            selected_operator = _select_filter_option(fr, selects[1], operator, timeout)
            if not selected_operator.get("ok"):
                return selected_operator
        if compact_operator in {"为空", "不为空", "empty", "not_empty", "is_empty", "is_not_empty"}:
            return {"ok": True, "field": field_name, "operator": operator,
                    "value": None, "operator_changed": operator_changed}

        if compact_operator in {"范围", "介于", "range", "between"}:
            if isinstance(value, dict):
                start, end = value.get("start"), value.get("end")
            elif isinstance(value, (list, tuple)) and len(value) == 2:
                start, end = value
            else:
                return {"ok": False, "reason": "范围筛选值必须包含 start/end"}
            result = select_date_range(field_name, str(start), str(end), tab)
            if result.get("ok"):
                result.update({"field": field_name, "operator": operator, "value": value,
                               "operator_changed": operator_changed})
            return result

        if operator_changed:
            # Operator changes may re-render the value control; resolve it again before use.
            column, selects = _quick_filter_field_column(fr, field_name)
            if column is None:
                return {"ok": False, "reason": "操作符切换后筛选字段失效: %s" % field_name}
        if len(selects) >= 3:
            values = list(value) if isinstance(value, (list, tuple, set)) else [value]
            selection = browser_session.ele_with_fallback(
                selects[2],
                'css:.ant-select-selection--multiple',
                'xpath:.//*[contains(@class, "ant-select-selection--multiple")]',
                timeout=0.1,
            )
            if len(values) > 1 and selection is None:
                return {"ok": False, "reason": "筛选值控件不支持多选: %s" % field_name}
            selected_values = []
            for item in values:
                selected = _select_filter_option(fr, selects[2], str(item), timeout)
                if not selected.get("ok"):
                    return selected
                selected_values.append(selected.get("selected"))
            return {
                "ok": True, "field": field_name, "operator": operator,
                "value": value, "selected_values": selected_values,
                "operator_changed": operator_changed,
            }

        inputs = browser_session.eles_with_fallback(
            column,
            'css:input.ant-input-sm:not(.ant-calendar-range-picker-input)',
            'xpath:.//input[contains(@class, "ant-input-sm") and not(contains(@class, "ant-calendar-range-picker-input"))]',
        )
        inputs = [item for item in inputs if item.states.is_displayed]
        if not inputs:
            return {"ok": False, "reason": "筛选字段缺少值控件: %s" % field_name}
        if isinstance(value, (list, tuple, set)):
            return {"ok": False, "reason": "文本筛选字段不接受列表值: %s" % field_name}
        try:
            inputs[0].wait.clickable(timeout=timeout, wait_stop=True, raise_err=False)
            inputs[0].input("" if value is None else str(value), clear=True, by_js=False)
        except TypeError:
            inputs[0].input("" if value is None else str(value), clear=True)
        return {"ok": True, "field": field_name, "operator": operator,
                "value": value, "operator_changed": operator_changed}


def _is_filter_search_button(button) -> bool:
    text = (button.text or "").replace(" ", "")
    if "查询" in text:
        return True
    for attribute in ("title", "aria-label"):
        if "查询" in str(button.attr(attribute) or ""):
            return True
    return bool(browser_session.ele_with_fallback(
        button,
        'css:.anticon-search',
        'xpath:.//*[contains(@class, "anticon-search")]',
        timeout=0.1,
    ))

def reset_filter_area(tab=None, submit: bool = True) -> dict:
    """Clear filter conditions and optionally submit the cleared query."""
    tab = tab or browser_session.get_tab()
    fr = _get_frame(tab)
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}
    try:
        query = browser_session.ele_with_fallback(
            fr,
            ui_contract.FILTER_ROOT_CSS,
            ui_contract.FILTER_ROOT_XPATH,
            timeout=3.0,
        )
        if not query:
            return {"ok": False, "reason": "未找到筛选区 .page-query"}

        buttons = browser_session.eles_with_fallback(query, 'css:button', 'xpath:.//button')
        reset_button = None
        search_button = None
        for button in buttons:
            if not button.states.is_displayed:
                continue
            if "重置" in (button.text or "").replace(" ", ""):
                reset_button = button
            elif submit and _is_filter_search_button(button):
                search_button = button

        if not reset_button:
            return {"ok": False, "reason": "未找到重置按钮"}
        _native_click(reset_button, timeout=3)
        if submit:
            if not search_button:
                return {"ok": False, "reason": "未找到查询按钮"}
            _native_click(search_button, timeout=3)
        return {
            "ok": True, "reset_clicked": True,
            "search_clicked": bool(submit), "query_deferred": not submit,
        }
    except Exception as exc:
        logger.debug("reset_filter_area 失败: %s", exc)
        return {"ok": False, "reason": str(exc)}


def submit_filter_area(tab=None) -> dict:
    """点击当前内联筛选区的「查询」按钮。

    网络等待不在这里完成；调用方必须在点击前建立监听，以免错过短请求。
    """
    tab = tab or browser_session.get_tab()
    fr = _get_frame(tab)
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}
    try:
        query = browser_session.ele_with_fallback(
            fr, ui_contract.FILTER_ROOT_CSS,
            ui_contract.FILTER_ROOT_XPATH, timeout=3.0,
        )
        if not query:
            return {"ok": False, "reason": "未找到筛选区 .page-query"}
        for button in browser_session.eles_with_fallback(query, 'css:button', 'xpath:.//button'):
            if button.states.is_displayed and _is_filter_search_button(button):
                _native_click(button, timeout=3)
                return {"ok": True, "clicked": "查询"}
        return {"ok": False, "reason": "未找到查询按钮"}
    except Exception as exc:
        logger.debug("submit_filter_area 失败: %s", exc)
        return {"ok": False, "reason": str(exc)}


def _close_visible_dropdowns(fr, timeout=0.5):
    """仅在确有可见下拉时发送 Escape，并等待具体元素隐藏。"""
    visible = []
    for locator in (
        ui_contract.FILTER_SELECT_OPEN,
        'c:.ant-dropdown:not(.ant-dropdown-hidden)',
    ):
        try:
            visible.extend(
                element for element in (fr.eles(locator, timeout=0) or [])
                if element.states.is_displayed
            )
        except Exception:
            pass
    if not visible:
        return False
    try:
        fr.actions.key_down(Keys.ESCAPE).key_up(Keys.ESCAPE)
    except Exception:
        return False
    for element in visible:
        try:
            element.wait.hidden(timeout=timeout, raise_err=False)
        except Exception:
            pass
    return True


def _collect_dropdown_options(fr, col_idx, sel_idx):
    """读取某字段列内某个 select 的下拉选项：打开 → 智能等待 → 读取。

    col_idx 是字段列索引；sel_idx: 0=字段名、1=操作符、2=值选择器。
    """
    _close_visible_dropdowns(fr, timeout=0.08)
    if col_idx < 0 or sel_idx < 0:
        return []

    try:
        cols = browser_session.eles_with_fallback(
            fr,
            'css:.legions-pro-quick-filter-row > div[class*="ant-col-"]',
            'xpath://div[contains(@class, "legions-pro-quick-filter-row")]/div[contains(@class, "ant-col-")]'
        )
        if col_idx >= len(cols):
            return []
        col = cols[col_idx]
        selects = browser_session.eles_with_fallback(col, 'css:.ant-select', 'xpath:.//*[contains(@class, "ant-select")]')
        if sel_idx >= len(selects):
            return []
        sel = selects[sel_idx]

        opener = browser_session.ele_with_fallback(
            sel,
            'css:[role="combobox"], .ant-select-selection, .ant-select-selector',
            'xpath:.//*[@role="combobox"] | .//*[contains(@class, "ant-select-selection")] | .//*[contains(@class, "ant-select-selector")]',
            timeout=0.5
        ) or sel
        
        target_mid = opener.rect.viewport_midpoint
        _native_click(opener, timeout=1, wait_stop=False)

        try:
            fr.wait.ele_displayed(ui_contract.FILTER_SELECT_OPEN, timeout=1, raise_err=False)
        except Exception:
            pass

        dropdowns = browser_session.eles_with_fallback(
            fr,
            ui_contract.FILTER_SELECT_OPEN_CSS,
            ui_contract.FILTER_SELECT_OPEN_XPATH,
        )
        active_dropdowns = [d for d in dropdowns if d.states.is_displayed]
        if not active_dropdowns:
            return []

        closest_dropdown = None
        min_dist = float('inf')
        for d in active_dropdowns:
            d_mid = d.rect.viewport_midpoint
            dist = abs(d_mid[0] - target_mid[0]) + abs(d_mid[1] - target_mid[1])
            if dist < min_dist:
                min_dist = dist
                closest_dropdown = d

        if not closest_dropdown:
            return []

        items = browser_session.eles_with_fallback(
            closest_dropdown,
            'css:.ant-select-dropdown-menu-item, .ant-select-item-option, li[role="option"]',
            'xpath:.//*[contains(@class, "ant-select-dropdown-menu-item") or contains(@class, "ant-select-item-option") or (local-name()="li" and @role="option")]'
        )
        values = [
            (item.text or "").strip() for item in items
            if item.states.is_displayed
            and item.states.is_enabled
            and item.attr("aria-disabled") != "true"
            and (item.text or "").strip()
        ]
        return list(dict.fromkeys(values))
    except Exception as e:
        logger.debug("_collect_dropdown_options 失败: %s", e)
        return []
