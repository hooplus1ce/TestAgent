"""筛选区操作：展开/切换模式、日期范围选择、字段矩阵扫描。

与 browser_session.py 解耦，专注于筛选区相关的 UI 自动化操作。
"""
import json
import logging
import threading

import browser_session

logger = logging.getLogger("drission-ui")
_lock = threading.RLock()


def expand_filter_area(tab=None):
    """展开筛选区：将弹窗模式切换为内联模式，并点击展开显示所有筛选字段。

    流程:
      0. 先检测当前模式：若已是内联模式且已展开 → 直接返回，不操作
      1. 非内联模式 → 点击 anticon-bars → 切「内联模式」
      2. 折叠状态 → 点击「展开▼」
    """
    with _lock:
        tab = tab or browser_session.get_tab()
        fr = browser_session.get_active_frame(tab)
        if fr is None:
            return {"ok": False, "reason": "未找到活动 iframe"}
        try:
            # 等待筛选区基础元素渲染就绪再执行 JS 检测
            try:
                fr.wait.ele_loaded('css:button', timeout=5)
            except Exception:
                pass  # 即使个别按钮未 load，也继续尝试 JS 检测
            logger.debug("filter area buttons loaded, running JS detection")
            # Step 0: 用 JS 检测展开/收起按钮（DrissionPage 不支持 :has-text 伪类）
            btn_state = fr.run_js("""
                var btns=document.querySelectorAll('button');
                for(var i=0;i<btns.length;i++){
                    var t=btns[i].textContent.trim().replace(/\\s+/g,'');
                    if(t.indexOf('展开')>=0&&btns[i].offsetParent!==null)
                        return JSON.stringify({type:'expand'});
                    if(t.indexOf('收起')>=0&&btns[i].offsetParent!==null)
                        return JSON.stringify({type:'collapse'});
                }
                return JSON.stringify({type:'none'});
            """)
            if isinstance(btn_state, str):
                import json as _json
                btn_state = _json.loads(btn_state)
            if btn_state and btn_state.get('type') == 'expand':
                # 点击「展开」按钮（用 JS 点击，避免 CSS selector 问题）
                fr.run_js("""
                    var btns=document.querySelectorAll('button');
                    for(var i=0;i<btns.length;i++){
                        var t=btns[i].textContent.trim().replace(/\\s+/g,'');
                        if(t.indexOf('展开')>=0){btns[i].click();break;}
                    }
                """)
                import time; time.sleep(1)
                return {"ok": True, "reason": "筛选区已展开"}
            elif btn_state and btn_state.get('type') == 'collapse':
                return {"ok": True, "reason": "筛选区已展开"}

            # Step 1: 非内联模式 → 点击 anticon-bars
            mode_btn = fr.ele('css:button.ant-dropdown-trigger i.anticon-bars', timeout=3)
            if mode_btn:
                mode_btn.parent().click()
                import time
                time.sleep(0.5)

                selected = fr.ele('css:.ant-dropdown-menu-item-selected', timeout=2)
                if selected and "弹窗" in selected.text:
                    inline_item = fr.ele('css:.ant-dropdown-menu-item:not(.ant-dropdown-menu-item-selected)', timeout=2)
                    if inline_item and "内联" in inline_item.text:
                        inline_item.click()
                        import time
                        time.sleep(1)

                # 关闭下拉（在 iframe 内执行，确保点到 dropdown）
                fr.run_js("document.body.click()")
                import time
                time.sleep(0.3)

            # Step 2: 用 JS 查找并点击「展开▼」
            js_expand = fr.run_js("""
                var btns=document.querySelectorAll('button');
                for(var i=0;i<btns.length;i++){
                    var t=btns[i].textContent.trim().replace(/\\s+/g,'');
                    if(t.indexOf('展开')>=0){btns[i].click();return JSON.stringify({found:true});}
                }
                return JSON.stringify({found:false});
            """)
            if isinstance(js_expand, str):
                import json as _json; js_expand = _json.loads(js_expand)
            if js_expand and js_expand.get('found'):
                import time; time.sleep(1)
                return {"ok": True, "reason": "已切换内联模式并展开筛选区"}

            # 检测「收起」状态
            js_collapse = fr.run_js("""
                var btns=document.querySelectorAll('button');
                for(var i=0;i<btns.length;i++){
                    var t=btns[i].textContent.trim().replace(/\\s+/g,'');
                    if(t.indexOf('收起')>=0)return JSON.stringify({found:true});
                }
                return JSON.stringify({found:false});
            """)
            if isinstance(js_collapse, str):
                import json as _json; js_collapse = _json.loads(js_collapse)
            if js_collapse and js_collapse.get('found'):
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
        fr = browser_session.get_active_frame(tab)
        if fr is None:
            return {"ok": False, "reason": "未找到活动 iframe"}
        try:
            import time
            rows = fr.eles('css:.legions-pro-quick-filter .ant-row')
            target_row = None
            for row in rows:
                text = row.text
                if field_name in text and row.ele('css:.ant-calendar-picker', timeout=1):
                    target_row = row
                    break
            if target_row is None:
                return {"ok": False, "reason": f"未找到字段「{field_name}」的日期选择器"}

            picker_input = target_row.ele('css:.ant-calendar-picker-input')
            picker_input.click()
            time.sleep(0.6)

            cal = browser_session.get_tab().ele('css:.ant-calendar', timeout=3)
            if cal is None:
                return {"ok": False, "reason": "日历面板未弹出"}

            parts = start_date.split('/')
            target_year = int(parts[0])
            target_month = int(parts[1])

            for _ in range(24):
                left_panel = cal.ele('css:.ant-calendar-range-left')
                year_el = left_panel.ele('css:.ant-calendar-year-select')
                month_el = left_panel.ele('css:.ant-calendar-month-select')
                cur_year = int(year_el.text.replace('年', ''))
                cur_month = int(month_el.text.replace('月', ''))
                if cur_year == target_year and cur_month == target_month:
                    break
                prev_btn = cal.ele('css:.ant-calendar-prev-month-btn')
                prev_btn.click()
                time.sleep(0.1)

            start_cell = cal.ele(f'css:td[title="{start_date}"] .ant-calendar-date')
            if start_cell is None:
                return {"ok": False, "reason": f"未找到开始日期单元格: {start_date}"}
            start_cell.click()
            time.sleep(0.6)

            end_cell = cal.ele(f'css:td[title="{end_date}"] .ant-calendar-date')
            if end_cell is None:
                return {"ok": False, "reason": f"未找到结束日期单元格: {end_date}"}
            end_cell.click()

            inputs = target_row.eles('css:input.ant-calendar-range-picker-input')
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
    对每个下拉字段自动点击展开并获取待选项。
    """
    with _lock:
        tab = tab or browser_session.get_tab()
        fr = browser_session.get_active_frame(tab)
        if fr is None:
            return {"ok": False, "reason": "未找到活动 iframe"}
        try:
            js = r"""
                var rows = document.querySelectorAll('.legions-pro-quick-filter .ant-row');
                var result = [];
                var seenField = {};

                for (var ri = 0; ri < rows.length; ri++) {
                    var row = rows[ri];
                    var selects = row.querySelectorAll('.ant-select');
                    var inputs = row.querySelectorAll('input.ant-input-sm');
                    var datePickers = row.querySelectorAll('.ant-calendar-picker');

                    for (var si = 0; si < selects.length; si += 3) {
                        var fieldEl = selects[si];
                        var opEl = selects[si + 1];
                        var valEl = selects[si + 2];

                        var fieldName = fieldEl ? fieldEl.textContent.trim() : '';
                        if (!fieldName || seenField[fieldName]) continue;
                        seenField[fieldName] = true;

                        var entry = {
                            field: fieldName,
                            operator: opEl ? opEl.textContent.trim() : '',
                            inputType: 'unknown',
                            options: []
                        };

                        if (valEl) {
                            var searchInput = valEl.querySelector('.ant-select-search__field');
                            if (searchInput) {
                                var hasSelect = valEl.querySelector('.ant-select-selection');
                                entry.inputType = hasSelect ? 'searchable-dropdown' : 'dropdown';
                            }
                            // 打开值下拉获取选项
                            try {
                                var cb = valEl.querySelector('[role="combobox"]');
                                if (cb) {
                                    cb.click();
                                    var tw = Date.now(); while (Date.now() - tw < 600) {}
                                    var items = document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option');
                                    items.forEach(function(it) {
                                        var t = it.textContent.trim();
                                        if (t && t.length < 30 && entry.options.indexOf(t) < 0) entry.options.push(t);
                                    });
                                    document.body.click();
                                    var tw2 = Date.now(); while (Date.now() - tw2 < 300) {}
                                }
                            } catch(e) {}
                        }

                        if (inputs.length > 0) entry.inputType = 'text-input';
                        if (datePickers.length > 0) entry.inputType = 'date-range';

                        // 操作符下拉选项
                        if (opEl) {
                            try {
                                var opCb = opEl.querySelector('[role="combobox"]');
                                if (opCb) {
                                    opCb.click();
                                    var tw = Date.now(); while (Date.now() - tw < 500) {}
                                    var opItems = document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option');
                                    var opList = [];
                                    opItems.forEach(function(it) {
                                        var t = it.textContent.trim();
                                        if (t && t.length < 15 && opList.indexOf(t) < 0) opList.push(t);
                                    });
                                    if (opList.length > 0) entry.operatorOptions = opList;
                                    document.body.click();
                                    var tw2 = Date.now(); while (Date.now() - tw2 < 300) {}
                                }
                            } catch(e) {}
                        }

                        result.push(entry);
                    }
                }
                return JSON.stringify(result);
            """
            res = fr.run_js(js)
            if isinstance(res, str):
                return {"ok": True, "fields": json.loads(res)}
            return {"ok": False, "reason": "扫描返回为空"}
        except Exception as e:
            logger.debug("scan_filter_fields 失败: %s", e)
            return {"ok": False, "reason": str(e)}
