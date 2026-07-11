"""筛选区操作：展开/切换模式、日期范围选择、字段矩阵扫描。

与 browser_session.py 解耦，专注于筛选区相关的 UI 自动化操作。
"""
import json
import logging
import time

import browser_session

logger = logging.getLogger("drissionpage-mcp")
# 复用 browser_session 全局锁：server 层 synchronized 已串行化所有工具调用，
# 此处 with _lock 是其重入（RLock 安全），无需独立锁；保留 _lock 名仅为最小改动。
_lock = browser_session._lock


def _native_click(element, timeout: float = 3) -> None:
    """Click through DrissionPage; tolerate minimal test doubles without JS fallback."""
    waiter = getattr(element, "wait", None)
    if waiter is not None:
        waiter.clickable(timeout=timeout, wait_stop=True, raise_err=False)
    try:
        element.click(by_js=False, wait_stop=True)
    except TypeError:
        element.click()


def expand_filter_area(tab=None):
    """展开筛选区：优先切换为内联模式，并点击展开显示所有筛选字段。"""
    with _lock:
        tab = tab or browser_session.get_tab()
        fr = browser_session.get_active_frame(tab)
        if fr is None:
            return {"ok": False, "reason": "未找到活动 iframe"}
        try:
            try:
                fr.wait.eles_loaded('c:.page-query', timeout=5)
            except Exception:
                pass

            def _state():
                query = browser_session.ele_with_fallback(
                    fr,
                    'css:.page-query',
                    'xpath://div[contains(@class, "page-query")]',
                    timeout=1.0
                )
                if not query:
                    return {"hasQuery": False}
                
                btns = browser_session.eles_with_fallback(
                    query,
                    'css:button',
                    'xpath:.//button'
                )
                
                expand = None
                collapse = None
                bars = None
                
                for b in btns:
                    if b.states.is_displayed:
                        text = (b.text or "").replace(" ", "")
                        if "展开" in text:
                            expand = b
                        elif "收起" in text:
                            collapse = b
                        else:
                            bars_icon = browser_session.ele_with_fallback(
                                b,
                                'css:i.anticon-bars',
                                'xpath:.//i[contains(@class, "anticon-bars")]',
                                timeout=0.1
                            )
                            if bars_icon:
                                bars = b
                                
                remaining = browser_session.ele_with_fallback(
                    query,
                    'css:.legions-pro-quick-filter-remaining',
                    'xpath:.//*[contains(@class, "legions-pro-quick-filter-remaining")]',
                    timeout=0.1
                )
                
                return {
                    "hasQuery": True,
                    "hasExpand": expand is not None,
                    "hasCollapse": collapse is not None,
                    "hasBars": bars is not None,
                    "hasRemaining": remaining is not None,
                    "fieldText": (query.text or "").strip()[:120]
                }

            st = _state()
            if st.get('hasRemaining') and st.get('hasCollapse'):
                return {"ok": True, "reason": "筛选区已是内联展开模式"}

            # Step 1: 优先切换到内联模式，避免点击「展开」触发高级搜索弹窗。
            if st.get('hasBars'):
                query = browser_session.ele_with_fallback(
                    fr,
                    'css:.page-query',
                    'xpath://div[contains(@class, "page-query")]'
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
                        bars_btn.click()
                        
                try:
                    fr.wait.ele_displayed('c:.ant-dropdown:not(.ant-dropdown-hidden)', timeout=3)
                except Exception:
                    pass

                dropdowns = browser_session.eles_with_fallback(
                    fr,
                    'css:.ant-dropdown:not(.ant-dropdown-hidden)',
                    'xpath://*[contains(@class, "ant-dropdown") and not(contains(@class, "ant-dropdown-hidden"))]'
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
                        'css:.ant-dropdown-menu-item',
                        'xpath:.//*[contains(@class, "ant-dropdown-menu-item")]'
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
                        inline_item.click()
                    else:
                        fr.click()
                        
                try:
                    fr.wait.ele_hidden('c:.ant-dropdown:not(.ant-dropdown-hidden)', timeout=3)
                except Exception:
                    pass

                st = _state()
                if st.get('hasRemaining') and st.get('hasCollapse'):
                    return {"ok": True, "reason": "已切换为内联展开模式"}

            # Step 2: 内联模式下展开剩余筛选项。
            if st.get('hasExpand'):
                query = browser_session.ele_with_fallback(
                    fr,
                    'css:.page-query',
                    'xpath://div[contains(@class, "page-query")]'
                )
                if query:
                    btns = browser_session.eles_with_fallback(query, 'css:button', 'xpath:.//button')
                    expand_btn = None
                    for b in btns:
                        if b.states.is_displayed and '展开' in (b.text or "").replace(" ", ""):
                            expand_btn = b
                            break
                    if expand_btn:
                        expand_btn.click()
                        
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
    """选择 Ant Design RangePicker 日期范围。

    Args:
        field_name: 筛选字段名称，如「领料时间」「发料时间」「创建时间」
        start_date: 开始日期，格式 "yyyy/MM/dd"，如 "2026/05/01"
        end_date: 结束日期，格式 "yyyy/MM/dd"，如 "2026/05/31"
        tab: 浏览器 tab，默认取当前
    """
    with _lock:
        tab = tab or browser_session.get_tab()
        expand_result = expand_filter_area(tab)
        if not expand_result.get("ok"):
            return expand_result
        fr = browser_session.get_active_frame(tab)
        if fr is None:
            return {"ok": False, "reason": "未找到活动 iframe"}
        try:
            rows = fr.eles('c:.legions-pro-query-item', timeout=1) or []
            if not rows:
                rows = fr.eles('c:.legions-pro-quick-filter .ant-row', timeout=1)
            target_row = None
            for row in rows:
                text = row.text
                if field_name in text and row.ele('c:.ant-calendar-picker', timeout=1):
                    target_row = row
                    break
            if target_row is None:
                return {"ok": False, "reason": f"未找到字段「{field_name}」的日期选择器"}

            picker_input = target_row.ele('c:.ant-calendar-picker-input')
            _native_click(picker_input, timeout=3)
            # 智能等待：日历面板出现即就绪（ele 自带轮询，无需固定 sleep）
            cal = fr.ele('c:.ant-calendar', timeout=5)
            if cal is None:
                return {"ok": False, "reason": "日历面板未弹出"}

            parts = start_date.split('/')
            target_year = int(parts[0])
            target_month = int(parts[1])

            # 双向翻页：按月份差正负选 prev/next 方向；上限 600 次（≈50 年）防异常死循环
            def _shown_ym():
                lp = cal.ele('c:.ant-calendar-range-left')
                ye = lp.ele('c:.ant-calendar-year-select')
                me = lp.ele('c:.ant-calendar-month-select')
                return int(ye.text.replace('年', '')), int(me.text.replace('月', ''))

            for _ in range(600):
                cur_year, cur_month = _shown_ym()
                if cur_year == target_year and cur_month == target_month:
                    break
                delta = (target_year * 12 + target_month) - (cur_year * 12 + cur_month)
                if delta == 0:
                    break
                btn_sel = 'c:.ant-calendar-next-month-btn' if delta > 0 else 'c:.ant-calendar-prev-month-btn'
                btn = cal.ele(btn_sel, timeout=1)
                if not btn:
                    return {"ok": False, "reason": f"未找到{'下一月' if delta > 0 else '上一月'}按钮"}
                _native_click(btn, timeout=1.5)
                # DrissionPage 原生 click 会等待页面停止变化；不使用固定延时。
                cal.wait.stop_moving(timeout=1.5, raise_err=False)

            start_cell = cal.ele(f'c:td[title="{start_date}"] .ant-calendar-date')
            if start_cell is None:
                return {"ok": False, "reason": f"未找到开始日期单元格: {start_date}"}
            _native_click(start_cell, timeout=3)
            # 智能等待：开始日期被选中即视为已进入结束日期选择态（最多 3s）
            try:
                cal.wait.ele_displayed('c:.ant-calendar-selected-start-date', timeout=3)
            except Exception:
                pass

            end_cell = cal.ele(f'c:td[title="{end_date}"] .ant-calendar-date')
            if end_cell is None:
                return {"ok": False, "reason": f"未找到结束日期单元格: {end_date}"}
            _native_click(end_cell, timeout=3)
            frame_waiter = getattr(fr, "wait", None)
            if frame_waiter is not None:
                frame_waiter.ele_hidden('c:.ant-calendar-picker-container .ant-calendar', timeout=3, raise_err=False)

            inputs = target_row.eles('c:input.ant-calendar-range-picker-input')
            return {
                "ok": True,
                "startValue": inputs[0].attr('value') if inputs else '',
                "endValue": inputs[1].attr('value') if len(inputs) > 1 else ''
            }

        except Exception as e:
            logger.debug("select_date_range 失败: %s", e)
            return {"ok": False, "reason": str(e)}


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
        fr = browser_session.get_active_frame(tab)
        if fr is None:
            return {"ok": False, "reason": "未找到活动 iframe"}
        try:
            struct_js = r"""
            function selText(sel){
                var s=sel.querySelector('.ant-select-selection__rendered');
                return s?s.textContent.trim():'';
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
            # 扫描完成后清理所有下拉
            _close_visible_dropdowns(fr, timeout=0.2)
            return {"ok": True, "fields": fields}
        except Exception as e:
            logger.debug("scan_filter_fields 失败: %s", e)
            return {"ok": False, "reason": str(e)}


def reset_filter_area(tab=None) -> dict:
    """点击筛选区的「重置」按钮清除所有筛选条件，再点击「查询」刷新表格数据。
    不刷新 iframe，远快于 reset_to_initial 的页面刷新。
    """
    tab = tab or browser_session.get_tab()
    fr = browser_session.get_active_frame(tab)
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}
    try:
        query = browser_session.ele_with_fallback(
            fr,
            'css:.page-query',
            'xpath://div[contains(@class, "page-query")]',
            timeout=3.0
        )
        if not query:
            return {"ok": False, "reason": "未找到筛选区 .page-query"}

        btns = browser_session.eles_with_fallback(query, 'css:button', 'xpath:.//button')
        reset_btn = None
        search_btn = None
        for b in btns:
            if not b.states.is_displayed:
                continue
            text = (b.text or "").replace(" ", "")
            if "重置" in text:
                reset_btn = b
            elif "查询" in text:
                search_btn = b

        if reset_btn:
            _native_click(reset_btn, timeout=3)
        else:
            return {"ok": False, "reason": "未找到重置按钮"}

        if search_btn:
            _native_click(search_btn, timeout=3)

        return {"ok": True, "reset_clicked": reset_btn is not None, "search_clicked": search_btn is not None}
    except Exception as e:
        logger.debug("reset_filter_area 失败: %s", e)
        return {"ok": False, "reason": str(e)}


def submit_filter_area(tab=None) -> dict:
    """点击当前内联筛选区的「查询」按钮。

    网络等待不在这里完成；调用方必须在点击前建立监听，以免错过短请求。
    """
    tab = tab or browser_session.get_tab()
    fr = browser_session.get_active_frame(tab)
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe"}
    try:
        query = browser_session.ele_with_fallback(
            fr, 'css:.page-query',
            'xpath://div[contains(@class, "page-query")]', timeout=3.0,
        )
        if not query:
            return {"ok": False, "reason": "未找到筛选区 .page-query"}
        for button in browser_session.eles_with_fallback(query, 'css:button', 'xpath:.//button'):
            if button.states.is_displayed and "查询" in (button.text or "").replace(" ", ""):
                _native_click(button, timeout=3)
                return {"ok": True, "clicked": "查询"}
        return {"ok": False, "reason": "未找到查询按钮"}
    except Exception as exc:
        logger.debug("submit_filter_area 失败: %s", exc)
        return {"ok": False, "reason": str(exc)}


def _close_visible_dropdowns(fr, timeout=0.5):
    """快速关闭可见 Ant Design 下拉浮层。

    扫描下拉选项时不等待完整关闭动画；只做一次关闭动作 + 短确认，
    避免 slide-up-leave 动画导致每个下拉多等数秒。
    """
    try:
        fr.actions.key_down('Escape').key_up('Escape')
    except Exception:
        pass
    try:
        fr.wait.ele_hidden('c:.ant-select-dropdown:not(.ant-select-dropdown-hidden)', timeout=timeout)
    except Exception:
        pass
    try:
        fr.wait.ele_hidden('c:.ant-dropdown:not(.ant-dropdown-hidden)', timeout=timeout)
    except Exception:
        pass
    return True


def _collect_dropdown_options(fr, col_idx, sel_idx):
    """读取某字段列内某个 select 的下拉选项：打开 → 智能等待 → 读取。

    col_idx 是字段列索引；sel_idx: 0=字段名、1=操作符、2=值选择器。
    """
    try:
        fr.actions.key_down('Escape').key_up('Escape')
    except Exception:
        pass
    try:
        fr.wait.ele_hidden('c:.ant-select-dropdown:not(.ant-select-dropdown-hidden)', timeout=1, raise_err=False)
    except Exception:
        pass

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
        opener.click()

        try:
            fr.wait.ele_displayed('c:.ant-select-dropdown:not(.ant-select-dropdown-hidden)', timeout=1)
            fr.wait.ele_displayed('c:.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-dropdown-menu-item', timeout=0.8)
        except Exception:
            pass

        dropdowns = browser_session.eles_with_fallback(
            fr,
            'css:.ant-select-dropdown:not(.ant-select-dropdown-hidden)',
            'xpath://*[contains(@class, "ant-select-dropdown") and not(contains(@class, "ant-select-dropdown-hidden"))]'
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
            'css:.ant-select-item, li, .ant-select-item-option',
            'xpath:.//*[contains(@class, "ant-select-item") or local-name()="li" or contains(@class, "ant-select-item-option")]'
        )
        return [(it.text or "").strip() for it in items]
    except Exception as e:
        logger.debug("_collect_dropdown_options 失败: %s", e)
        return []
