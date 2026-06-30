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
                btn_state = json.loads(btn_state)
            if btn_state and btn_state.get('type') == 'expand':
                # 点击「展开」按钮（用 JS 点击，避免 CSS selector 问题）
                fr.run_js("""
                    var btns=document.querySelectorAll('button');
                    for(var i=0;i<btns.length;i++){
                        var t=btns[i].textContent.trim().replace(/\\s+/g,'');
                        if(t.indexOf('展开')>=0){btns[i].click();break;}
                    }
                """)
                # 智能等待：展开完成后「收起」按钮出现即视为就绪（最多 3s）
                try:
                    fr.wait.ele_displayed('text:收起', timeout=3)
                except Exception:
                    pass
                return {"ok": True, "reason": "筛选区已展开"}
            elif btn_state and btn_state.get('type') == 'collapse':
                return {"ok": True, "reason": "筛选区已展开"}

            # Step 1: 非内联模式 → 点击 anticon-bars
            mode_btn = fr.ele('css:button.ant-dropdown-trigger i.anticon-bars', timeout=3)
            if mode_btn:
                mode_btn.parent().click()
                # 智能等待：下拉菜单出现即就绪（最多 3s）
                try:
                    fr.wait.ele_displayed('css:.ant-dropdown-menu', timeout=3)
                except Exception:
                    pass

                selected = fr.ele('css:.ant-dropdown-menu-item-selected', timeout=2)
                if selected and "弹窗" in selected.text:
                    inline_item = fr.ele('css:.ant-dropdown-menu-item:not(.ant-dropdown-menu-item-selected)', timeout=2)
                    if inline_item and "内联" in inline_item.text:
                        inline_item.click()
                        # 智能等待：选中级联通常自动收起下拉（最多 3s）
                        try:
                            fr.wait.ele_hidden('css:.ant-dropdown-menu', timeout=3)
                        except Exception:
                            pass

                # 兜底关闭下拉并等待其隐藏
                fr.run_js("document.body.click()")
                try:
                    fr.wait.ele_hidden('css:.ant-dropdown-menu', timeout=3)
                except Exception:
                    pass

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
                js_expand = json.loads(js_expand)
            if js_expand and js_expand.get('found'):
                # 智能等待：展开完成后「收起」按钮出现即视为就绪（最多 3s）
                try:
                    fr.wait.ele_displayed('text:收起', timeout=3)
                except Exception:
                    pass
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
                js_collapse = json.loads(js_collapse)
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
    分两阶段：先 JS 扫描结构（不打开下拉），再对每个下拉字段用「打开→等待→读取并关闭」
    两步法获取选项——避免在单次 run_js 内用 while 忙等待阻塞页面事件循环（会让下拉
    展开动画冻死、读不到正确选项）。分组按 .ant-row 内部语义识别，遇非字段 select
    前进 1 步重新对齐，而非盲跳 +3。
    """
    with _lock:
        tab = tab or browser_session.get_tab()
        fr = browser_session.get_active_frame(tab)
        if fr is None:
            return {"ok": False, "reason": "未找到活动 iframe"}
        try:
            # ---- 阶段 1：结构扫描（按 .ant-row 内部分组，不打开任何下拉）----
            struct_js = r"""
            var OP = ['等于','不等于','包含','不包含','大于','小于','大于等于','小于等于','为空','不为空','之间','不属于','属于','开头是','结尾是','在...之间'];
            function isOp(t){ t=(t||'').trim(); for(var i=0;i<OP.length;i++) if(t.indexOf(OP[i])>=0) return true; return false; }
            function selText(sel){ var s=sel.querySelector('.ant-select-selection__rendered'); return s?s.textContent.trim():''; }
            var rows = document.querySelectorAll('.legions-pro-quick-filter .ant-row');
            var out = [], seen = {};
            for (var ri = 0; ri < rows.length; ri++) {
                var row = rows[ri];
                var selects = row.querySelectorAll('.ant-select');
                var inputs = row.querySelectorAll('input.ant-input-sm');
                var datePickers = row.querySelectorAll('.ant-calendar-picker');
                var si = 0;
                while (si < selects.length) {
                    var fName = selText(selects[si]);
                    // 字段组起点：文本非空、非操作符、未见过；否则前进一步重新对齐（不盲跳 +3）
                    if (!fName || isOp(fName) || seen[fName]) { si++; continue; }
                    seen[fName] = true;
                    var opEl = selects[si+1] || null;
                    var valEl = selects[si+2] || null;
                    var opText = opEl ? selText(opEl) : '';
                    var entry = { row: ri, field: fName, operator: opText, inputType: 'unknown',
                                  options: [], hasValueCb: false, hasOpCb: false,
                                  valIdx: valEl ? si+2 : -1, opIdx: opEl ? si+1 : -1 };
                    if (valEl) {
                        var searchInput = valEl.querySelector('.ant-select-search__field');
                        if (searchInput) {
                            entry.inputType = valEl.querySelector('.ant-select-selection') ? 'searchable-dropdown' : 'dropdown';
                        }
                        if (valEl.querySelector('[role="combobox"]')) entry.hasValueCb = true;
                    }
                    if (inputs.length > 0 && !valEl) entry.inputType = 'text-input';
                    if (datePickers.length > 0) entry.inputType = 'date-range';
                    if (opEl && opEl.querySelector('[role="combobox"]')) entry.hasOpCb = true;
                    out.push(entry);
                    si += valEl ? 3 : (opEl ? 2 : 1);
                }
            }
            return JSON.stringify(out);
            """
            res = fr.run_js(struct_js)
            fields = json.loads(res) if isinstance(res, str) else res
            if not isinstance(fields, list):
                return {"ok": False, "reason": "结构扫描返回非列表"}

            # ---- 阶段 2：逐个下拉用两步法获取选项（不在 JS 内忙等待）----
            for entry in fields:
                if entry.get("hasValueCb") and entry.get("valIdx", -1) >= 0:
                    entry["options"] = _collect_dropdown_options(fr, entry["row"], entry["valIdx"])
                if entry.get("hasOpCb") and entry.get("opIdx", -1) >= 0:
                    entry["operatorOptions"] = _collect_dropdown_options(fr, entry["row"], entry["opIdx"])
                for k in ("row", "valIdx", "opIdx", "hasValueCb", "hasOpCb"):
                    entry.pop(k, None)
            return {"ok": True, "fields": fields}
        except Exception as e:
            logger.debug("scan_filter_fields 失败: %s", e)
            return {"ok": False, "reason": str(e)}


def _collect_dropdown_options(fr, row_idx, sel_idx):
    """两步法读取某行某 select 的下拉选项：JS 打开 → 智能等待 → JS 读取并关闭。

    避免在单次 run_js 内用 while 忙等待阻塞页面事件循环（会让下拉展开动画冻死、
    读不到正确选项）。row_idx/sel_idx 均为整数，%d 格式化无注入风险。
    """
    open_js = (
        "var rows=document.querySelectorAll('.legions-pro-quick-filter .ant-row');"
        "var sel=rows[%d]?rows[%d].querySelectorAll('.ant-select')[%d]:null;"
        "if(sel){var cb=sel.querySelector('[role=\"combobox\"]');if(cb){cb.click();return JSON.stringify({ok:true});}}"
        "return JSON.stringify({ok:false});"
    ) % (row_idx, row_idx, sel_idx)
    fr.run_js(open_js)
    # 智能等待：下拉面板出现即就绪（不阻塞页面事件循环）
    try:
        fr.wait.ele_displayed('css:.ant-select-dropdown:not(.ant-select-dropdown-hidden)', timeout=3)
    except Exception:
        pass
    # 读取可见下拉选项并关闭（读取时面板仍可见，关闭后返回）
    read_js = (
        "var items=document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option');"
        "var opts=[];items.forEach(function(it){var t=it.textContent.trim();if(t&&t.length<30&&opts.indexOf(t)<0)opts.push(t);});"
        "document.body.click();return JSON.stringify(opts);"
    )
    res = fr.run_js(read_js)
    # 智能等待：下拉面板隐藏即关闭完成，避免残留干扰下一个下拉
    try:
        fr.wait.ele_hidden('css:.ant-select-dropdown:not(.ant-select-dropdown-hidden)', timeout=3)
    except Exception:
        pass
    if isinstance(res, str):
        try:
            return json.loads(res)
        except Exception:
            return []
    return res or []
