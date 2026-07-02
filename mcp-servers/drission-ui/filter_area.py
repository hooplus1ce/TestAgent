"""筛选区操作：展开/切换模式、日期范围选择、字段矩阵扫描。

与 browser_session.py 解耦，专注于筛选区相关的 UI 自动化操作。
"""
import json
import logging
import time

import browser_session

logger = logging.getLogger("drission-ui")
# 复用 browser_session 全局锁：server 层 synchronized 已串行化所有工具调用，
# 此处 with _lock 是其重入（RLock 安全），无需独立锁；保留 _lock 名仅为最小改动。
_lock = browser_session._lock


def expand_filter_area(tab=None):
    """展开筛选区：优先切换为内联模式，并点击展开显示所有筛选字段。

    流程:
      0. 若已是内联模式且已展开 → 直接返回
      1. 点击 anticon-bars 打开“内联模式/弹窗模式”菜单，优先切换到“内联模式”
      2. 内联模式下若仍折叠 → 点击「展开▼」

    重要：不能在弹窗模式下直接点击「展开▼」，否则会触发“高级搜索”弹窗。
    """
    with _lock:
        tab = tab or browser_session.get_tab()
        fr = browser_session.get_active_frame(tab)
        if fr is None:
            return {"ok": False, "reason": "未找到活动 iframe"}
        try:
            try:
                fr.wait.eles_loaded('css:.page-query', timeout=5)
            except Exception:
                pass

            def _state():
                res = fr.run_js(r"""
                    var q=document.querySelector('.page-query');
                    if(!q)return JSON.stringify({hasQuery:false});
                    function visible(el){
                        if(!el)return false;
                        var s=getComputedStyle(el);
                        return s.display!=='none' && s.visibility!=='hidden' && el.offsetWidth>0 && el.offsetHeight>0;
                    }
                    var btns=[].slice.call(q.querySelectorAll('button'));
                    var expand=btns.find(function(b){return visible(b)&&b.textContent.replace(/\s+/g,'').indexOf('展开')>=0;});
                    var collapse=btns.find(function(b){return visible(b)&&b.textContent.replace(/\s+/g,'').indexOf('收起')>=0;});
                    var bars=btns.find(function(b){return visible(b)&&b.querySelector('i.anticon-bars');});
                    var remaining=q.querySelector('.legions-pro-quick-filter-remaining');
                    return JSON.stringify({
                        hasQuery:true,
                        hasExpand:!!expand,
                        hasCollapse:!!collapse,
                        hasBars:!!bars,
                        hasRemaining:!!remaining,
                        fieldText:q.textContent.trim().slice(0,120)
                    });
                """)
                return json.loads(res) if isinstance(res, str) else (res or {})

            st = _state()
            if st.get('hasRemaining') and st.get('hasCollapse'):
                return {"ok": True, "reason": "筛选区已是内联展开模式"}

            # Step 1: 优先切换到内联模式，避免点击「展开」触发高级搜索弹窗。
            if st.get('hasBars'):
                fr.run_js(r"""
                    var q=document.querySelector('.page-query');
                    function visible(el){
                        if(!el)return false;
                        var s=getComputedStyle(el);
                        return s.display!=='none' && s.visibility!=='hidden' && el.offsetWidth>0 && el.offsetHeight>0;
                    }
                    var btn=[].slice.call(q.querySelectorAll('button')).find(function(b){return visible(b)&&b.querySelector('i.anticon-bars');});
                    if(btn)btn.click();
                """)
                try:
                    fr.wait.ele_displayed('css:.ant-dropdown:not(.ant-dropdown-hidden)', timeout=3)
                except Exception:
                    pass

                mode_res = fr.run_js(r"""
                    var menus=[].slice.call(document.querySelectorAll('.ant-dropdown:not(.ant-dropdown-hidden)'));
                    var menu=menus.find(function(m){return m.textContent.indexOf('内联模式')>=0 && m.textContent.indexOf('弹窗模式')>=0;});
                    if(!menu)return JSON.stringify({ok:false, reason:'mode menu not found'});
                    var items=[].slice.call(menu.querySelectorAll('.ant-dropdown-menu-item'));
                    var inline=items.find(function(i){return i.textContent.trim().indexOf('内联模式')>=0;});
                    var selected=items.find(function(i){return i.className.indexOf('ant-dropdown-menu-item-selected')>=0;});
                    var selectedText=selected?selected.textContent.trim():'';
                    if(inline && selectedText.indexOf('内联模式')<0){inline.click();return JSON.stringify({ok:true, switched:true});}
                    document.body.click();
                    return JSON.stringify({ok:true, switched:false, selected:selectedText});
                """)
                try:
                    mode_res = json.loads(mode_res) if isinstance(mode_res, str) else mode_res
                except Exception:
                    mode_res = {}
                try:
                    fr.wait.ele_hidden('css:.ant-dropdown:not(.ant-dropdown-hidden)', timeout=3)
                except Exception:
                    pass

                st = _state()
                if st.get('hasRemaining') and st.get('hasCollapse'):
                    return {"ok": True, "reason": "已切换为内联展开模式"}

            # Step 2: 内联模式下展开剩余筛选项。
            if st.get('hasExpand'):
                fr.run_js(r"""
                    var q=document.querySelector('.page-query');
                    function visible(el){
                        if(!el)return false;
                        var s=getComputedStyle(el);
                        return s.display!=='none' && s.visibility!=='hidden' && el.offsetWidth>0 && el.offsetHeight>0;
                    }
                    var btn=[].slice.call(q.querySelectorAll('button')).find(function(b){return visible(b)&&b.textContent.replace(/\s+/g,'').indexOf('展开')>=0;});
                    if(btn)btn.click();
                """)
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
        fr = browser_session.get_active_frame(tab)
        if fr is None:
            return {"ok": False, "reason": "未找到活动 iframe"}
        try:
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
            # 智能等待：日历面板出现即就绪（ele 自带轮询，无需固定 sleep）
            cal = browser_session.get_tab().ele('css:.ant-calendar', timeout=5)
            if cal is None:
                return {"ok": False, "reason": "日历面板未弹出"}

            parts = start_date.split('/')
            target_year = int(parts[0])
            target_month = int(parts[1])

            # 双向翻页：按月份差正负选 prev/next 方向；上限 600 次（≈50 年）防异常死循环
            def _shown_ym():
                lp = cal.ele('css:.ant-calendar-range-left')
                ye = lp.ele('css:.ant-calendar-year-select')
                me = lp.ele('css:.ant-calendar-month-select')
                return int(ye.text.replace('年', '')), int(me.text.replace('月', ''))

            def _wait_ym_change(prev, deadline):
                """轮询直到左面板显示年/月与 prev 不同（或已等于目标），最多到 deadline。"""
                while time.time() < deadline:
                    try:
                        cur = _shown_ym()
                    except Exception:
                        cur = prev
                    if cur != prev:
                        return cur
                    time.sleep(0.05)
                return prev

            for _ in range(600):
                cur_year, cur_month = _shown_ym()
                if cur_year == target_year and cur_month == target_month:
                    break
                delta = (target_year * 12 + target_month) - (cur_year * 12 + cur_month)
                if delta == 0:
                    break
                btn_sel = 'css:.ant-calendar-next-month-btn' if delta > 0 else 'css:.ant-calendar-prev-month-btn'
                btn = cal.ele(btn_sel, timeout=1)
                if not btn:
                    return {"ok": False, "reason": f"未找到{'下一月' if delta > 0 else '上一月'}按钮"}
                btn.click()
                # 智能等待：显示的年/月变化后再决定是否继续翻（最多 1.5s）
                _wait_ym_change((cur_year, cur_month), time.time() + 1.5)

            start_cell = cal.ele(f'css:td[title="{start_date}"] .ant-calendar-date')
            if start_cell is None:
                return {"ok": False, "reason": f"未找到开始日期单元格: {start_date}"}
            start_cell.click()
            # 智能等待：开始日期被选中即视为已进入结束日期选择态（最多 3s）
            try:
                cal.wait.ele_displayed('css:.ant-calendar-selected-start-date', timeout=3)
            except Exception:
                pass

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
            var cols = document.querySelectorAll('.page-query .legions-pro-quick-filter-row > div[class*="ant-col-"]');
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
                if entry.get("hasOpCb"):
                    entry["operatorOptions"] = _collect_dropdown_options(fr, entry["col"], 1)
                if entry.get("hasValueCb"):
                    entry["options"] = _collect_dropdown_options(fr, entry["col"], 2)
                for k in ("col", "hasValueCb", "hasOpCb"):
                    entry.pop(k, None)
            return {"ok": True, "fields": fields}
        except Exception as e:
            logger.debug("scan_filter_fields 失败: %s", e)
            return {"ok": False, "reason": str(e)}


def _close_visible_dropdowns(fr, timeout=0.5):
    """快速关闭可见 Ant Design 下拉浮层。

    扫描下拉选项时不等待完整关闭动画；只做一次关闭动作 + 短确认，
    避免 slide-up-leave 动画导致每个下拉多等数秒。
    """
    fr.run_js(r"""
        document.activeElement && document.activeElement.blur && document.activeElement.blur();
        document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', code:'Escape', keyCode:27, which:27, bubbles:true}));
        document.body.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
        document.body.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
        document.body.click();
        document.querySelectorAll('.ant-select-dropdown, .ant-dropdown').forEach(function(d){
            d.style.removeProperty('display');
        });
    """)
    try:
        fr.wait.ele_hidden('css:.ant-select-dropdown:not(.ant-select-dropdown-hidden)', timeout=timeout)
    except Exception:
        pass
    try:
        fr.wait.ele_hidden('css:.ant-dropdown:not(.ant-dropdown-hidden)', timeout=timeout)
    except Exception:
        pass
    return True


def _collect_dropdown_options(fr, col_idx, sel_idx):
    """读取某字段列内某个 select 的下拉选项：清场 → 打开 → 智能等待 → 读取 → 关闭确认。

    col_idx 是 .page-query .legions-pro-quick-filter-row 的直接字段列索引；
    sel_idx: 0=字段名、1=操作符、2=值选择器。
    """
    _close_visible_dropdowns(fr, timeout=0.2)
    fr.run_js("document.querySelectorAll('.ant-select-dropdown, .ant-dropdown').forEach(function(d){d.style.removeProperty('display');});")

    open_js = (
        "var cols=document.querySelectorAll('.page-query .legions-pro-quick-filter-row > div[class*=\"ant-col-\"]');"
        "var sel=cols[%d]?cols[%d].querySelectorAll('.ant-select')[%d]:null;"
        "if(sel){var cb=sel.querySelector('[role=\"combobox\"]')||sel.querySelector('.ant-select-selection');"
        "if(cb){var r=cb.getBoundingClientRect();cb.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));cb.click();"
        "return JSON.stringify({ok:true,left:r.left,top:r.top,width:r.width,height:r.height});}}"
        "return JSON.stringify({ok:false});"
    ) % (col_idx, col_idx, sel_idx)
    opened = fr.run_js(open_js)
    try:
        opened = json.loads(opened) if isinstance(opened, str) else (opened or {})
    except Exception:
        opened = {"ok": False}
    if not opened.get("ok"):
        _close_visible_dropdowns(fr, timeout=0.2)
        return []

    try:
        fr.wait.ele_displayed('css:.ant-select-dropdown:not(.ant-select-dropdown-hidden)', timeout=1)
        fr.wait.ele_displayed('css:.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-dropdown-menu-item', timeout=0.8)
    except Exception:
        # 搜索型下拉可能暂无选项；后续读取为空即可。
        pass

    read_js = r"""
        var target = OPENED;
        function visible(el){
            var s=getComputedStyle(el);
            return s.display!=='none' && s.visibility!=='hidden' && el.offsetWidth>0 && el.offsetHeight>0;
        }
        var dropdowns=[].slice.call(document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden)')).filter(visible);
        var scored=dropdowns.map(function(d){
            var r=d.getBoundingClientRect();
            var dx=Math.abs(r.left-target.left);
            var dy=Math.abs(r.top-(target.top+target.height));
            return {d:d, score:dx+dy};
        }).sort(function(a,b){return a.score-b.score;});
        var d=scored.length?scored[0].d:null;
        var opts=[];
        if(d){
            var items=d.querySelectorAll('.ant-select-dropdown-menu-item, .ant-select-item-option');
            items.forEach(function(it){
                var t=it.textContent.trim();
                if(t&&t.length<60&&opts.indexOf(t)<0)opts.push(t);
            });
        }
        return JSON.stringify(opts);
    """.replace('OPENED', json.dumps(opened))
    res = fr.run_js(read_js)
    closed = _close_visible_dropdowns(fr, timeout=0.2)
    if not closed:
        logger.warning("下拉框未能确认关闭 col=%s sel=%s", col_idx, sel_idx)
    if isinstance(res, str):
        try:
            return json.loads(res)
        except Exception:
            return []
    return res or []

