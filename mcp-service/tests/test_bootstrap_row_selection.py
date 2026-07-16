"""Unit tests for bootstrap row-selection hardening (no browser required)."""

from types import SimpleNamespace

from drissionpage_mcp.services import bootstrap_table


class _Wait:
    def __init__(self, shade=False):
        self._shade = shade

    def eles_loaded(self, *_args, **_kwargs):
        return self._shade

    def ele_deleted(self, *_args, **_kwargs):
        return True


class _FakeFrame:
    def __init__(self, shade=False, js_payload=None, ele=None):
        self.wait = _Wait(shade=shade)
        self._js_payload = js_payload
        self._ele = ele
        self.clicks = []

    def run_js(self, script):
        if "data-du-bt-select" in script and "removeAttribute" in script:
            return None
        return self._js_payload

    def ele(self, locator, timeout=1):
        return self._ele


class _FakeCheckbox:
    def __init__(self):
        self.clicked = []

    def wait(self):
        return self

    def clickable(self, timeout=1.0, raise_err=False):
        return True

    def click(self, by_js=False, timeout=2):
        self.clicked.append({"by_js": by_js, "timeout": timeout})


def test_row_selection_blocks_on_shade_by_default(monkeypatch):
    fr = _FakeFrame(shade=True)
    monkeypatch.setattr(bootstrap_table, "_get_frame", lambda: fr)
    result = bootstrap_table.click_bootstrap_row_selection(row=0)
    assert result["ok"] is False
    assert "遮罩" in result["reason"]


def test_row_selection_idempotent_when_already_selected(monkeypatch):
    fr = _FakeFrame(
        shade=False,
        js_payload='{"ok":true,"checked":true,"alreadySelected":true,"dataIndex":"0"}',
    )
    monkeypatch.setattr(bootstrap_table, "_get_frame", lambda: fr)
    result = bootstrap_table.click_bootstrap_row_selection(row=0)
    assert result == {
        "ok": True,
        "kind": "bootstrap",
        "table_index": 0,
        "already_selected": True,
        "select_all": False,
        "row": 0,
    }


def test_select_all_path_marks_and_clicks(monkeypatch):
    cb = _FakeCheckbox()
    # wait.clickable path uses cb.wait.clickable — attach nested wait
    cb.wait = SimpleNamespace(clickable=lambda timeout=1.0, raise_err=False: True)
    fr = _FakeFrame(
        shade=False,
        js_payload='{"ok":true,"selectAll":true,"checked":false,"alreadySelected":false}',
        ele=cb,
    )
    monkeypatch.setattr(bootstrap_table, "_get_frame", lambda: fr)
    result = bootstrap_table.click_bootstrap_row_selection(select_all=True)
    assert result["ok"] is True
    assert result["select_all"] is True
    assert result["already_selected"] is False
    assert cb.clicked
