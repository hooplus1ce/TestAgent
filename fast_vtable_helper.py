import time

from DrissionPage.items import ChromiumElement, ChromiumFrame


class FastVTableHelper:
    """
    针对当前系统深度优化定制的 VTable 极速助手
    不再使用低效的树遍历，直接通过绝对路径秒级获取实例。
    """

    fast_bind_js = """
            const el = document.querySelector('.vtable');
            if (!el || !el.parentElement) return false;

            const parent = el.parentElement;
            // 动态识别当前页面的随机 Fiber Key
            const fk = Object.keys(parent).find(k => k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance'));
            if (!fk) return false;

            try {
                // 执行绝对路径直达！
                const instance = parent[fk].return.return.return.return.stateNode.vtableInstance;
                if (instance && typeof instance.getCellRect === 'function') {
                    window._vtable = instance; // 注入到全局，供后续所有API秒级调用
                    return true;
                }
            } catch(e) {}
            return false;
        """

    def __init__(
        self,
    ):
        self.iframe: ChromiumFrame
        self.vtable_ele: ChromiumElement

    def vtable_connection(self, iframe: ChromiumFrame):
        """核心极速定位：利用已知的绝对路径，1毫秒内绑定实例"""
        self.iframe = iframe
        self.vtable_ele = iframe.ele(".vtable")
        # 完美复刻你测出来的绝对路径，仅将随机的 FiberKey 做动态提取
        return iframe.run_js(self.fast_bind_js)

    def get_vtable_overview(self):
        """获取 vtable 实例"""
        return self.iframe.run_js(
            "return {rowCount: window._vtable.rowCount, colCount: window._vtable.colCount, dataCount: window._vtable.recordsCount};"
        )

    # ==================== 1. 数据获取 API (瞬间返回) ====================

    def get_all_records(self):
        """获取表格内全量的原始 JSON 数据"""
        return self.iframe.run_js("return window._vtable.records || [];")

    def get_cell_value(self, col, row):
        """获取指定单元格的文本值"""
        return self.iframe.run_js(f"return window._vtable.getCellValue({col}, {row});")

    # ==================== 2. 高级交互 API (精准物理模拟) ====================
    def get_cell_viewport_location(self, col, row, scroll_to=True):
        """计算单元格在浏览器视口中的绝对几何坐标 (X, Y)"""
        if scroll_to:
            self.iframe.run_js(
                f"window._vtable.scrollToCell({{ col: {col}, row: {row} }});"
            )
            time.sleep(0.1)  # 给滚动动画留一点定格时间

        # 核心坐标换算逻辑：结合滚动偏移 + 冻结行列补偿
        js_get_rect = f"""
            let inst = window._vtable;
            let rect = inst.getCellRect({col}, {row});
            if (!rect) return null;

            let x1 = rect.bounds ? rect.bounds.x1 : (rect.x1 !== undefined ? rect.x1 : rect.left);
            let y1 = rect.bounds ? rect.bounds.y1 : (rect.y1 !== undefined ? rect.y1 : rect.top);
            let x2 = rect.bounds ? rect.bounds.x2 : (rect.x2 !== undefined ? rect.x2 : rect.right);
            let y2 = rect.bounds ? rect.bounds.y2 : (rect.y2 !== undefined ? rect.y2 : rect.bottom);

            let scrollLeft = {col} < (inst.frozenColCount || 0) ? 0 : (inst.scrollLeft || 0);
            let scrollTop = {row} < (inst.frozenRowCount || 0) ? 0 : (inst.scrollTop || 0);

            return {{ x: (x1 + x2) / 2 - scrollLeft, y: (y1 + y2) / 2 - scrollTop }};
        """
        cell_offset = self.iframe.run_js(js_get_rect)
        if not cell_offset:
            raise RuntimeError(f"无法获取单元格 ({col}, {row}) 的矩形边界")

        # 加上 canvas 在大浏览器窗口下的起点，DrissionPage 自动处理 iframe 嵌套偏差
        canvas_x, canvas_y = self.vtable_ele.rect.viewport_location
        return int(canvas_x + cell_offset["x"]), int(canvas_y + cell_offset["y"])

    def get_cell_viewport_location_relative(self, col, row, scroll_to=True):
        """
        【最稳健版】获取单元格在浏览器视口中的绝对几何坐标 (X, Y)
        """
        if scroll_to:
            # 1. 强行滚动到该单元格，确保它被渲染并呈现在可视区内
            self.iframe.run_js(
                f"window._vtable.scrollToCell({{ col: {col}, row: {row} }});"
            )
            time.sleep(0.15)  # 给滚动动画留出定格缓冲时间

        # 2. 直接调用 getCellRelativeRect，让 VTable 引擎帮你处理一切滚动和冻结列的数学计算
        js_get_relative = f"""
                let inst = window._vtable;
                let rect = inst.getCellRelativeRect({col}, {row});
                if (!rect) return null;

                // 直接计算单元格在当前 Canvas 内部的中心点 X 和 Y
                return {{
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2
                }};
            """
        cell_offset = self.iframe.run_js(js_get_relative)
        if not cell_offset:
            raise RuntimeError(f"无法获取单元格 ({col}, {row}) 的相对可视边界")

        # 3. 加上 Canvas 元素本身在整个大浏览器窗口下的起点坐标
        # DrissionPage 会自动帮你加上 iframe 的偏移量，实现真正的物理定位
        canvas_x, canvas_y = self.vtable_ele.rect.viewport_location

        return int(canvas_x + cell_offset["x"]), int(canvas_y + cell_offset["y"])

    def click_cell(self, col, row, double=False):
        """单/双击指定的单元格"""
        x, y = self.get_cell_viewport_location(col, row)
        actions = self.iframe.actions.move_to((x, y))
        if double:
            actions.click().wait(0.05).click()
        else:
            actions.click()

    def drag_cells(self, start_col, start_row, end_col, end_row):
        """从一个单元格拖拽框选到另一个单元格"""
        # 滚动并计算终点、起点坐标
        self.iframe.run_js(
            f"window._vtable.scrollToCell({{ col: {end_col}, row: {end_row} }});"
        )
        self.iframe.run_js(
            f"window._vtable.scrollToCell({{ col: {start_col}, row: {start_row} }});"
        )
        time.sleep(0.15)

        start_x, start_y = self.get_cell_viewport_location(
            start_col, start_row, scroll_to=False
        )
        end_x, end_y = self.get_cell_viewport_location(
            end_col, end_row, scroll_to=False
        )

        # 执行物理动作链框选
        self.iframe.actions.move_to((start_x, start_y)).hold().wait(0.1).move_to((
            end_x,
            end_y,
        )).wait(0.1).release()
