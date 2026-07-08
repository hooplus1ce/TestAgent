"""browser_session.py 测试：list_tabs / _pick_tab / get_active_frame（mock Chromium）
及 _ensure_display_env 跨平台/headless 守卫。"""
import os

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _restore_display_env():
    """恢复 DISPLAY/XAUTHORITY，避免 _ensure_display_env 测试污染全局环境。"""
    saved = {k: os.environ.get(k) for k in ("DISPLAY", "XAUTHORITY")}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_list_tabs_no_browser():
    """未 connect 时返回空列表。"""
    import browser_session
    with patch.object(browser_session, "_browser", None):
        assert browser_session.list_tabs() == []


def test_list_tabs_with_mock_browser():
    """mock _browser.tab_ids 返回 tab 信息。"""
    import browser_session

    mock_tab1 = MagicMock()
    mock_tab1.url = "https://demo19-scm.hoolinks.com/page1"
    mock_tab1.title = "页面1"
    mock_tab2 = MagicMock()
    mock_tab2.url = "https://other.com/page2"
    mock_tab2.title = "Other"

    mock_browser = MagicMock()
    mock_browser.tab_ids = ["t1", "t2"]
    mock_browser.get_tab = MagicMock(side_effect=[mock_tab1, mock_tab2])

    with patch.object(browser_session, "_browser", mock_browser), \
         patch.object(browser_session, "_lock"):
        tabs = browser_session.list_tabs()

    assert len(tabs) == 2
    assert tabs[0]["url"] == "https://demo19-scm.hoolinks.com/page1"
    assert tabs[0]["title"] == "页面1"
    assert tabs[1]["url"] == "https://other.com/page2"


def test_list_tabs_browser_error():
    """_browser 抛异常时返回空列表，不向上传播。"""
    import browser_session

    mock_browser = MagicMock()
    mock_browser.tab_ids = MagicMock(side_effect=RuntimeError("disconnected"))

    with patch.object(browser_session, "_browser", mock_browser), \
         patch.object(browser_session, "_lock"):
        tabs = browser_session.list_tabs()

    assert tabs == []


def test_pick_tab_prefers_hoolinks_url():
    """_pick_tab 优先选 url 含 hoolinks 的 tab。"""
    import browser_session

    hoolinks_tab = MagicMock()
    hoolinks_tab.url = "https://demo19-scm.hoolinks.com/admin"
    other_tab = MagicMock()
    other_tab.url = "https://google.com"

    mock_browser = MagicMock()
    # get_tab(url=hoolinks) 返回 hoolinks_tab
    mock_browser.get_tab = MagicMock(return_value=hoolinks_tab)

    result = browser_session._pick_tab(mock_browser, "诺贝科技")
    assert result is hoolinks_tab


def test_pick_tab_fallback_to_latest():
    """所有探活都失败时回退到 latest_tab。"""
    import browser_session

    mock_browser = MagicMock()
    mock_browser.get_tab = MagicMock(side_effect=Exception("fail"))
    mock_browser.tab_ids = MagicMock(side_effect=Exception("fail"))
    mock_browser.latest_tab = "latest"

    result = browser_session._pick_tab(mock_browser, "诺贝科技")
    assert result == "latest"


def test_get_active_frame_returns_none_on_error():
    """get_frame 抛异常时返回 None。"""
    import browser_session

    mock_tab = MagicMock()
    mock_tab.get_frame = MagicMock(side_effect=RuntimeError("no frame"))

    with patch.object(browser_session, "get_tab", return_value=mock_tab), \
         patch.object(browser_session, "_lock"):
        result = browser_session.get_active_frame(mock_tab)

    assert result is None


def test_get_browser_returns_browser():
    """get_browser 返回 _browser 实例。"""
    import browser_session

    mock_browser = MagicMock()
    with patch.object(browser_session, "_browser", mock_browser), \
         patch.object(browser_session, "_lock"):
        result = browser_session.get_browser()
    assert result is mock_browser


def test_find_passes_timeout_to_clickable_wait():
    """clickable 等待应使用调用方 timeout，避免退回 DrissionPage 默认长等待。"""
    import browser_session

    class FakeWait:
        def __init__(self):
            self.kwargs = None

        def clickable(self, **kwargs):
            self.kwargs = kwargs
            return True

    class FakeStates:
        is_clickable = True

    fake_ele = MagicMock()
    fake_ele.wait = FakeWait()
    fake_ele.states = FakeStates()
    fake_tab = MagicMock()
    fake_tab.ele.return_value = fake_ele

    with patch.object(browser_session, "get_tab", return_value=fake_tab), \
         patch.object(browser_session, "get_active_frame", return_value=None), \
         patch.object(browser_session, "_lock"):
        result = browser_session.find("#search", in_frame=True, timeout=1.25)

    assert result is fake_ele
    assert fake_ele.wait.kwargs == {"timeout": 1.25, "wait_stop": False}


# ==================== _ensure_display_env 跨平台/headless 守卫 ====================

def test_ensure_display_skipped_when_headless():
    """headless 模式：跳过探测，不设 DISPLAY。"""
    import browser_session, config
    with patch.object(config, "HEADLESS", True), \
         patch.dict("os.environ", {}, clear=False), \
         patch.object(browser_session.os, "getuid", side_effect=AssertionError("不应调用 getuid")):
        browser_session.os.environ.pop("DISPLAY", None)
        browser_session._ensure_display_env()
        assert "DISPLAY" not in browser_session.os.environ


def test_ensure_display_skipped_on_windows():
    """非 Linux（Windows）：跳过探测，且不触碰 os.getuid（Windows 无此函数）。"""
    import browser_session, config
    with patch.object(config, "HEADLESS", False), \
         patch.object(browser_session.sys, "platform", "win32"), \
         patch.object(browser_session.os, "getuid", side_effect=AssertionError("Windows 不应调用 getuid")):
        browser_session.os.environ.pop("DISPLAY", None)
        browser_session._ensure_display_env()  # 不应抛异常
        assert "DISPLAY" not in browser_session.os.environ


def test_ensure_display_sets_on_linux_headed():
    """Linux + 有头 + 无 DISPLAY：探测补齐 DISPLAY=:0。"""
    import browser_session, config
    with patch.object(config, "HEADLESS", False), \
         patch.object(browser_session.sys, "platform", "linux"), \
         patch.object(browser_session.os, "listdir", return_value=[]):
        browser_session.os.environ.pop("DISPLAY", None)
        browser_session._ensure_display_env()
        assert browser_session.os.environ.get("DISPLAY") == ":0"


def test_ensure_display_preserves_existing():
    """已有 DISPLAY（真图形会话）：不覆盖。"""
    import browser_session, config
    with patch.object(config, "HEADLESS", False), \
         patch.object(browser_session.sys, "platform", "linux"):
        browser_session.os.environ["DISPLAY"] = ":99"
        browser_session._ensure_display_env()
        assert browser_session.os.environ["DISPLAY"] == ":99"


def test_ensure_display_finds_xauthority():
    """Linux 有头：从 /run/user/<uid> 探测到 .mutter-Xwaylandauth.* 并设 XAUTHORITY。"""
    import browser_session, config
    with patch.object(config, "HEADLESS", False), \
         patch.object(browser_session.sys, "platform", "linux"), \
         patch.object(browser_session.os, "getuid", return_value=1000), \
         patch.object(browser_session.os, "listdir", return_value=[".mutter-Xwaylandauth.ABC123"]):
        browser_session.os.environ.pop("DISPLAY", None)
        browser_session.os.environ.pop("XAUTHORITY", None)
        browser_session._ensure_display_env()
        assert browser_session.os.environ.get("XAUTHORITY", "").endswith(".mutter-Xwaylandauth.ABC123")
