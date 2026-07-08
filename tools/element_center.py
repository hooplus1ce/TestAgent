"""get_element_center — 获取元素在顶层视口的中心点坐标。

用法:
    from tools.element_center import get_element_center

    el = tab.ele('.ant-modal-close')
    center = get_element_center(el)
    # → {"cx": 1721.0, "cy": 199.0, "_space": "top-viewport"}

    click_xy(x=center['cx'], y=center['cy'])
"""


def get_element_center(el):
    """获取元素在顶层视口（top-viewport）的中心点坐标。

    Args:
        el: DrissionPage ChromiumElement 对象

    Returns:
        dict: {"cx": float, "cy": float, "_space": "top-viewport"}
              坐标可直接传给 click_xy(x=cx, y=cy)
    """
    # 方案1: DrissionPage 原生 click_point（已含 iframe 偏移）
    try:
        cp = el.rect.click_point
        return {"cx": round(float(cp[0]), 1), "cy": round(float(cp[1]), 1),
                "_space": "top-viewport"}
    except Exception:
        pass

    # 方案2: 从 location + size 计算几何中心
    try:
        loc = el.rect.location
        sz = el.rect.size
        cx = loc[0] + sz[0] / 2
        cy = loc[1] + sz[1] / 2
        return {"cx": round(cx, 1), "cy": round(cy, 1),
                "_space": "top-viewport"}
    except Exception:
        pass

    # 方案3: JS getBoundingClientRect 兜底
    try:
        js = (
            "var r = this.getBoundingClientRect();"
            "return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2});"
        )
        res = el.run_js(js)
        if isinstance(res, str):
            import json
            data = json.loads(res)
        else:
            data = res
        if data and 'x' in data:
            cx, cy = data['x'], data['y']
            # 叠加 iframe 偏移
            try:
                owner = el.owner
                off_js = (
                    "var ifr = window.frameElement;"
                    "if (!ifr) return '{\"x\":0,\"y\":0}';"
                    "var r = ifr.getBoundingClientRect();"
                    "return JSON.stringify({x: r.left, y: r.top});"
                )
                off_res = owner.run_js(off_js)
                if isinstance(off_res, str):
                    offset = json.loads(off_res)
                else:
                    offset = off_res
                if offset:
                    cx += offset.get('x', 0)
                    cy += offset.get('y', 0)
            except Exception:
                pass
            return {"cx": round(cx, 1), "cy": round(cy, 1),
                    "_space": "top-viewport"}
    except Exception:
        pass

    return {"error": "无法获取元素坐标"}
