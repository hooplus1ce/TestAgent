"""modal.py 测试：弹窗三级检测的分类与优先级逻辑。

mock target（tab/frame），不依赖真实浏览器。覆盖 _detect_in_target 的分支
（wrap 隐藏 / ghost 元素 / confirm vs interactive / notification / message）
与 detect_modal 的优先级（iframe 优先、top confirm 重命名为 system_confirm、
top 非 confirm 被忽略），以及 close_modal 的空场景。
"""
from unittest.mock import patch


class _FakeStates:
    def __init__(self, is_displayed=True):
        self.is_displayed = is_displayed


class _FakeEle:
    """模拟 DrissionPage 元素：text / states.is_displayed / ele() / eles() / run_js()。"""

    def __init__(self, text="", is_displayed=True, children=None, eles_list=None):
        self._text = text
        self.states = _FakeStates(is_displayed)
        self._children = children or {}
        self._eles_list = eles_list or {}

    @property
    def text(self):
        return self._text

    def ele(self, locator, timeout=0.3):
        return self._children.get(locator)

    def eles(self, locator, timeout=0.3):
        return self._eles_list.get(locator, [])

    def run_js(self, script):
        return None


class _FakeTarget:
    """模拟 tab/frame：ele() 按 locator 返回预设元素；run_js() 返回预设值。"""

    def __init__(self, elements=None, eles_map=None, run_js_return=None, run_js_raises=False):
        self._elements = elements or {}
        self._eles_map = eles_map or {}
        self._run_js_return = run_js_return
        self._run_js_raises = run_js_raises

    def ele(self, locator, timeout=0.3):
        return self._elements.get(locator)

    def eles(self, locator, timeout=0.3):
        return self._eles_map.get(locator, [])

    def run_js(self, script):
        if self._run_js_raises:
            raise RuntimeError("js error")
        return self._run_js_return


# ==================== _detect_in_target 分类逻辑 ====================

def test_no_modal_returns_none():
    """target 无任何弹窗元素 → none。"""
    import modal
    assert modal._detect_in_target(_FakeTarget()) == {"type": "none"}


def test_modal_wrap_hidden_returns_none():
    """ant-modal-content 存在但 ant-modal-wrap display:none → 判为已关闭。

    React 组件卸载不彻底时 ant-modal 残留但 wrap 已隐藏，这是最可靠的关闭判定。
    """
    import modal
    t = _FakeTarget(
        elements={"c:.ant-modal-content": _FakeEle(text="某弹窗")},
        run_js_return=True,  # wrap display:none
    )
    assert modal._detect_in_target(t) == {"type": "none"}


def test_modal_ghost_not_displayed_returns_none():
    """modal 存在但 is_displayed=False（ghost 元素，DP 缓存未 GC）→ 判为已关闭。"""
    import modal
    t = _FakeTarget(
        elements={"c:.ant-modal-content": _FakeEle(is_displayed=False)},
        run_js_return=False,  # wrap 可见，进入 is_displayed 检查
    )
    assert modal._detect_in_target(t) == {"type": "none"}


def test_confirm_modal_classified():
    """含 ant-confirm-body → type=confirm，携带 title/content/buttons/hasClose。"""
    import modal
    modal_el = _FakeEle(
        is_displayed=True,
        children={
            "c:.ant-modal-title": _FakeEle(text="确认删除"),
            "c:.ant-modal-body": _FakeEle(text="确定要删除吗？"),
            "c:.ant-modal-close": _FakeEle(),
            "c:.ant-confirm-body": _FakeEle(),
        },
        eles_list={"c:.ant-btn": [_FakeEle(text="取消"), _FakeEle(text="确定")]},
    )
    t = _FakeTarget(elements={"c:.ant-modal-content": modal_el}, run_js_return=False)
    info = modal._detect_in_target(t)
    assert info["type"] == "confirm"
    assert info["title"] == "确认删除"
    assert info["content"] == "确定要删除吗？"
    assert info["buttons"] == ["取消", "确定"]
    assert info["hasClose"] is True


def test_interactive_modal_when_no_confirm_body():
    """modal 无 ant-confirm-body → type=interactive。"""
    import modal
    modal_el = _FakeEle(
        is_displayed=True,
        children={
            "c:.ant-modal-title": _FakeEle(text="编辑"),
            "c:.ant-modal-body": _FakeEle(text="表单"),
        },
    )
    t = _FakeTarget(elements={"c:.ant-modal-content": modal_el}, run_js_return=False)
    info = modal._detect_in_target(t)
    assert info["type"] == "interactive"
    assert info["title"] == "编辑"


def test_notification_detected():
    """ant-notification-notice → type=notification，message 取 message 字段。"""
    import modal
    notif = _FakeEle(children={
        "c:.ant-notification-notice-message": _FakeEle(text="保存成功"),
        "c:.ant-notification-notice-description": _FakeEle(text="详情"),
    })
    t = _FakeTarget(elements={"c:.ant-notification-notice": notif})
    info = modal._detect_in_target(t)
    assert info["type"] == "notification"
    assert info["message"] == "保存成功"


def test_notification_falls_back_to_description():
    """message 元素缺失时，notification 的 message 取 description。"""
    import modal
    notif = _FakeEle(children={
        "c:.ant-notification-notice-description": _FakeEle(text="仅描述"),
    })
    t = _FakeTarget(elements={"c:.ant-notification-notice": notif})
    info = modal._detect_in_target(t)
    assert info["type"] == "notification"
    assert info["message"] == "仅描述"


def test_message_notice_detected():
    """ant-message-notice → type=message。"""
    import modal
    msg = _FakeEle(children={"c:.ant-message-notice-content": _FakeEle(text="操作成功")})
    t = _FakeTarget(elements={"c:.ant-message-notice": msg})
    info = modal._detect_in_target(t)
    assert info["type"] == "message"
    assert info["message"] == "操作成功"


# ==================== detect_modal 优先级 ====================

def test_detect_modal_iframe_scoped():
    """iframe 内有业务弹窗 → scope=iframe，不查 top。"""
    import modal
    iframe_t = _FakeTarget(elements={
        "c:.ant-message-notice": _FakeEle(children={
            "c:.ant-message-notice-content": _FakeEle(text="iframe内消息"),
        }),
    })
    with patch.object(modal.browser_session, "get_tab_ro", return_value=_FakeTarget()), \
         patch.object(modal.browser_session, "get_active_frame_ro", return_value=iframe_t):
        info = modal.detect_modal()
    assert info["type"] == "message"
    assert info["scope"] == "iframe"


def test_detect_modal_top_confirm_renamed():
    """iframe 无弹窗、top 有 confirm → 重命名为 system_confirm，scope=top。"""
    import modal
    top_modal = _FakeEle(is_displayed=True, children={"c:.ant-confirm-body": _FakeEle()})
    top_t = _FakeTarget(elements={"c:.ant-modal-content": top_modal}, run_js_return=False)
    with patch.object(modal.browser_session, "get_tab_ro", return_value=top_t), \
         patch.object(modal.browser_session, "get_active_frame_ro", return_value=None):
        info = modal.detect_modal()
    assert info["type"] == "system_confirm"
    assert info["scope"] == "top"


def test_detect_modal_none_when_clean():
    """iframe 与 top 均无弹窗 → none，含 waited。"""
    import modal
    with patch.object(modal.browser_session, "get_tab_ro", return_value=_FakeTarget()), \
         patch.object(modal.browser_session, "get_active_frame_ro", return_value=None):
        info = modal.detect_modal()
    assert info["type"] == "none"
    assert "waited" in info


def test_detect_modal_top_notification_ignored():
    """top 层的 notification/message 不算系统确认弹窗，不作为 top 返回（只有 confirm 才拦截）。"""
    import modal
    top_t = _FakeTarget(elements={
        "c:.ant-notification-notice": _FakeEle(children={
            "c:.ant-notification-notice-message": _FakeEle(text="顶部通知"),
        }),
    })
    with patch.object(modal.browser_session, "get_tab_ro", return_value=top_t), \
         patch.object(modal.browser_session, "get_active_frame_ro", return_value=None):
        info = modal.detect_modal()
    assert info["type"] == "none"


# ==================== close_modal ====================

def test_close_modal_nothing_to_close():
    """无弹窗时返回 ok=True, closed=[], errors=[]。"""
    import modal
    with patch.object(modal.browser_session, "get_tab", return_value=_FakeTarget()), \
         patch.object(modal.browser_session, "get_active_frame", return_value=None):
        result = modal.close_modal()
    assert result["ok"] is True
    assert result["closed"] == []
    assert result["errors"] == []
