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
        if callable(self._run_js_return):
            return self._run_js_return(script)
        if isinstance(self._run_js_return, list):
            return self._run_js_return.pop(0) if self._run_js_return else None
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
        run_js_return='{"type":"none"}',  # wrap display:none
    )
    assert modal._detect_in_target(t) == {"type": "none"}


def test_modal_ghost_not_displayed_returns_none():
    """modal 存在但 is_displayed=False（ghost 元素，DP 缓存未 GC）→ 判为已关闭。"""
    import modal
    t = _FakeTarget(
        elements={"c:.ant-modal-content": _FakeEle(is_displayed=False)},
        run_js_return='{"type":"none"}',
    )
    assert modal._detect_in_target(t) == {"type": "none"}


def test_confirm_modal_classified():
    """含 ant-confirm-body → type=confirm，携带 title/content/buttons/hasClose。"""
    import modal
    t = _FakeTarget(run_js_return=(
        '{"type":"confirm","title":"确认删除","content":"确定要删除吗？",'
        '"buttons":["取消","确定"],"hasClose":true}'
    ))
    info = modal._detect_in_target(t)
    assert info["type"] == "confirm"
    assert info["title"] == "确认删除"
    assert info["content"] == "确定要删除吗？"
    assert info["buttons"] == ["取消", "确定"]
    assert info["hasClose"] is True


def test_interactive_modal_when_no_confirm_body():
    """modal 无 ant-confirm-body → type=interactive。"""
    import modal
    t = _FakeTarget(run_js_return=(
        '{"type":"interactive","title":"编辑","content":"表单",'
        '"buttons":[],"hasClose":false}'
    ))
    info = modal._detect_in_target(t)
    assert info["type"] == "interactive"
    assert info["title"] == "编辑"


def test_notification_detected():
    """ant-notification-notice → type=notification，message 取 message 字段。"""
    import modal
    t = _FakeTarget(run_js_return='{"type":"notification","message":"保存成功"}')
    info = modal._detect_in_target(t)
    assert info["type"] == "notification"
    assert info["message"] == "保存成功"


def test_notification_falls_back_to_description():
    """message 元素缺失时，notification 的 message 取 description。"""
    import modal
    t = _FakeTarget(run_js_return='{"type":"notification","message":"仅描述"}')
    info = modal._detect_in_target(t)
    assert info["type"] == "notification"
    assert info["message"] == "仅描述"


def test_message_notice_detected():
    """ant-message-notice → type=message。"""
    import modal
    t = _FakeTarget(run_js_return='{"type":"message","message":"操作成功"}')
    info = modal._detect_in_target(t)
    assert info["type"] == "message"
    assert info["message"] == "操作成功"


# ==================== detect_modal 优先级 ====================

def test_detect_modal_iframe_scoped():
    """iframe 内有业务弹窗 → scope=iframe，不查 top。"""
    import modal
    iframe_t = _FakeTarget(run_js_return='{"type":"message","message":"iframe内消息"}')
    with patch.object(modal.browser_session, "get_tab_ro", return_value=_FakeTarget()), \
         patch.object(modal.browser_session, "get_active_frame_ro", return_value=iframe_t):
        info = modal.detect_modal()
    assert info["type"] == "message"
    assert info["scope"] == "iframe"


def test_detect_modal_top_confirm_renamed():
    """iframe 无弹窗、top 有 confirm → 重命名为 system_confirm，scope=top。"""
    import modal
    top_t = _FakeTarget(run_js_return=(
        '{"type":"confirm","title":"","content":"","buttons":[],"hasClose":false}'
    ))
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


def test_detect_modal_top_notification_scoped():
    """top 层 notification/message 也作为可观察反馈返回。"""
    import modal
    top_t = _FakeTarget(run_js_return='{"type":"notification","message":"顶部通知"}')
    with patch.object(modal.browser_session, "get_tab_ro", return_value=top_t), \
         patch.object(modal.browser_session, "get_active_frame_ro", return_value=None):
        info = modal.detect_modal()
    assert info["type"] == "notification"
    assert info["scope"] == "top"


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


def test_close_modal_hidden_modal_treated_as_closed():
    """隐藏残留 modal（如 display:none）不应尝试点击关闭，也不应报错。"""
    import modal
    hidden_modal = _FakeEle(children={})
    top_t = _FakeTarget(
        elements={"c:.ant-modal-content": hidden_modal},
        run_js_return=['[]', '{"visible":false}'],
    )

    with patch.object(modal.browser_session, "get_tab", return_value=top_t), \
         patch.object(modal.browser_session, "get_active_frame", return_value=None):
        result = modal.close_modal()

    assert result["ok"] is True
    assert result["closed"] == []
    assert result["errors"] == []


def test_clear_transient_overlays_scans_iframe_and_top():
    """点击前清理只用 DrissionPage 元素定位和原生关闭按钮。"""
    import modal

    class _Wait:
        def clickable(self, **_kwargs):
            return True

    class _Close:
        wait = _Wait()

        def click(self, **_kwargs):
            return True

    class _Notice:
        states = _FakeStates(True)

        def __init__(self, text, close_locator):
            self.text = text
            self._close_locator = close_locator

        def ele(self, locator, timeout=0.2):
            return _Close() if locator == self._close_locator else None

    class _WaitTarget:
        def __init__(self, notification=None, message=None):
            self.notification = notification
            self.message = message
            self.wait = type("Wait", (), {"ele_hidden": lambda *_args, **_kwargs: True})()

        def eles(self, locator, timeout=0.2):
            if locator == 'c:.ant-notification-notice':
                return [self.notification] if self.notification else []
            if locator == 'c:.ant-message-notice':
                return [self.message] if self.message else []
            return []

    iframe_t = _WaitTarget(notification=_Notice("请勾选记录", 'c:.ant-notification-notice-close'))
    top_t = _WaitTarget(message=_Notice("处理中", 'c:.ant-message-close,.ant-message-notice-close'))

    with patch.object(modal.browser_session, "get_tab", return_value=top_t), \
         patch.object(modal.browser_session, "get_active_frame", return_value=iframe_t):
        result = modal.clear_transient_overlays()

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["closed"] == [
        {"type": "notification", "message": "请勾选记录", "scope": "iframe"},
        {"type": "message", "message": "处理中", "scope": "top"},
    ]
