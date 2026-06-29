"""browser_session.py 测试：list_tabs / _pick_tab / get_active_frame（mock Chromium）。"""
from unittest.mock import MagicMock, patch


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
