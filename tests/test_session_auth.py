"""session_auth.py 测试：会话刷新必须短超时、少重试。"""
from types import SimpleNamespace


class FakeWait:
    def __init__(self, doc_loaded=True):
        self.doc_loaded_result = doc_loaded
        self.load_start_calls = []
        self.doc_loaded_calls = []

    def load_start(self, timeout=None, raise_err=None):
        self.load_start_calls.append({"timeout": timeout, "raise_err": raise_err})
        return True

    def doc_loaded(self, timeout=None, raise_err=None):
        self.doc_loaded_calls.append({"timeout": timeout, "raise_err": raise_err})
        return self.doc_loaded_result


class FakeSetter:
    def __init__(self):
        self.cookie_batches = []

    def cookies(self, cookies):
        self.cookie_batches.append(cookies)


class FakeTab:
    def __init__(self, url, doc_loaded=True):
        self.url = url
        self.title = "SCM"
        self.wait = FakeWait(doc_loaded=doc_loaded)
        self.set = FakeSetter()
        self.get_calls = []
        self.cdp_calls = []
        self.refresh_calls = []
        self.clear_cache_calls = 0
        self.stop_loading_calls = 0
        self._is_loading = False

    def get(self, *args, **kwargs):
        self.get_calls.append({"args": args, "kwargs": kwargs})
        self.url = args[0]
        return SimpleNamespace(ok=True)

    def clear_cache(self):
        self.clear_cache_calls += 1

    def refresh(self, ignore_cache=False):
        self.refresh_calls.append({"ignore_cache": ignore_cache})

    def _run_cdp(self, name, **kwargs):
        self.cdp_calls.append({"name": name, "kwargs": kwargs})
        return {}

    def stop_loading(self):
        self.stop_loading_calls += 1


def _patch_login(monkeypatch, session_auth, tab):
    monkeypatch.setattr(session_auth.browser_session, "get_tab", lambda: tab)
    monkeypatch.setattr(
        session_auth,
        "get_login_auth",
        lambda: [{"name": "SESSION", "value": "s1"}],
    )


def test_login_ocr_skips_navigation_when_already_on_admin_host(monkeypatch):
    import session_auth

    tab = FakeTab("https://demo19-scm.hoolinks.com/scm-static/scm-admin/scm-admin/#/")
    _patch_login(monkeypatch, session_auth, tab)

    result = session_auth.login_ocr()

    assert result["ok"] is True
    assert result["navigation"]["skipped"] is True
    assert tab.get_calls == []
    assert tab.clear_cache_calls == 1
    assert tab.set.cookie_batches[0][0]["domain"] == session_auth.config.SCM_ACCESS_DOMAIN
    assert tab.refresh_calls == [{"ignore_cache": False}]
    assert tab.cdp_calls == []
    assert tab.wait.doc_loaded_calls[-1]["timeout"] == session_auth.config.REFRESH_LOAD_TIMEOUT


def test_login_ocr_uses_single_bounded_navigation_from_other_host(monkeypatch):
    import session_auth

    tab = FakeTab("chrome://newtab/")
    _patch_login(monkeypatch, session_auth, tab)

    result = session_auth.login_ocr()

    assert result["ok"] is True
    assert result["navigation"]["skipped"] is False
    assert len(tab.get_calls) == 1
    assert tab.get_calls[0]["args"] == (session_auth.SCM_ADMIN_URL,)
    assert tab.get_calls[0]["kwargs"]["retry"] == 0
    assert tab.get_calls[0]["kwargs"]["interval"] == 0
    assert tab.get_calls[0]["kwargs"]["timeout"] == session_auth.config.REFRESH_NAV_TIMEOUT


def test_bounded_reload_uses_drissionpage_refresh_and_stops_loading_when_load_timeout():
    import session_auth

    tab = FakeTab(
        "https://demo19-scm.hoolinks.com/scm-static/scm-admin/scm-admin/#/",
        doc_loaded=False,
    )

    loaded = session_auth._bounded_reload(tab)

    assert loaded is False
    assert tab.refresh_calls == [{"ignore_cache": False}]
    assert tab.stop_loading_calls == 1
    assert tab.wait.load_start_calls == []
    assert tab.wait.doc_loaded_calls == [
        {"timeout": session_auth.config.REFRESH_LOAD_TIMEOUT, "raise_err": False}
    ]
