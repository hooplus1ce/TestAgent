"""Unit tests for VTable instance lifecycle and render stability (no browser)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from drissionpage_mcp.services import vtable


class _FakeFrame:
    def __init__(self, responses=None):
        self.url = "https://example.test/module"
        self._responses = list(responses or [])
        self.scripts = []

    def run_js(self, script):
        self.scripts.append(script)
        if not self._responses:
            return None
        value = self._responses.pop(0)
        return value


def test_frame_key_changes_with_tab_and_url(monkeypatch):
    fr = SimpleNamespace(url="https://a/x")
    monkeypatch.setattr(vtable, "_frame", lambda: fr)
    monkeypatch.setattr(vtable.browser_session, "get_active_tab_name", lambda: "模块A")
    key_a = vtable._frame_key()
    monkeypatch.setattr(vtable.browser_session, "get_active_tab_name", lambda: "模块B")
    key_b = vtable._frame_key()
    assert key_a != key_b
    assert "模块A" in key_a and "https://a/x" in key_a


def test_ensure_vtable_remounts_when_frame_key_changes(monkeypatch):
    mounts = []

    def fake_mount(force=False):
        mounts.append(force)
        vtable._instance_state["frame_key"] = vtable._frame_key()
        vtable._instance_state["mount_token"] = "tok-%d" % len(mounts)
        return {"ok": True, "mountToken": vtable._instance_state["mount_token"]}

    monkeypatch.setattr(vtable, "mount_vtable", fake_mount)
    monkeypatch.setattr(vtable, "_validate_mounted", lambda fr=None: {"valid": True, "mountToken": "old"})
    monkeypatch.setattr(
        vtable,
        "_frame",
        lambda: SimpleNamespace(url="https://example/m1"),
    )
    monkeypatch.setattr(vtable.browser_session, "get_active_tab_name", lambda: "M1")
    vtable._instance_state.update({"frame_key": None, "mount_token": None, "last_valid_at": 0})

    assert vtable._ensure_vtable() is True
    assert mounts == [True]

    # Same frame: valid → no remount
    assert vtable._ensure_vtable() is True
    assert mounts == [True]

    # Module switch forces remount
    monkeypatch.setattr(vtable.browser_session, "get_active_tab_name", lambda: "M2")
    assert vtable._ensure_vtable() is True
    assert mounts == [True, True]


def test_invalidate_vtable_clears_python_and_page_cache(monkeypatch):
    fr = _FakeFrame(responses=[json.dumps({"ok": True})])
    monkeypatch.setattr(vtable, "_frame", lambda: fr)
    monkeypatch.setattr(
        vtable.browser_session,
        "load_js",
        lambda name: "/*%s*/" % name,
    )
    vtable._instance_state.update({
        "frame_key": "old|url",
        "mount_token": "abc",
        "last_valid_at": 123.0,
    })
    result = vtable.invalidate_vtable(reason="test")
    assert result["ok"] is True
    assert vtable._instance_state["frame_key"] is None
    assert vtable._instance_state["mount_token"] is None
    assert any("invalidateMountedVTable" in s for s in fr.scripts)


def test_wait_root_geometry_stable_requires_consecutive_samples(monkeypatch):
    # Two different samples then three identical → stable
    geos = [
        json.dumps({"x": 1, "y": 2, "w": 100, "h": 50}),
        json.dumps({"x": 2, "y": 2, "w": 100, "h": 50}),
        json.dumps({"x": 2, "y": 2, "w": 100, "h": 50}),
        json.dumps({"x": 2, "y": 2, "w": 100, "h": 50}),
    ]
    fr = _FakeFrame(responses=geos)
    monkeypatch.setattr(vtable, "_frame", lambda: fr)
    monkeypatch.setattr(vtable.time, "sleep", lambda *_a, **_k: None)
    assert vtable._wait_root_geometry_stable(timeout=1.0, samples=3, interval=0.0) is True


def test_wait_for_render_stable_requires_geometry(monkeypatch):
    monkeypatch.setattr(vtable, "_ensure_vtable", lambda force=False: True)
    monkeypatch.setattr(vtable, "is_loading_complete", lambda timeout=20: True)
    monkeypatch.setattr(vtable, "_wait_root_geometry_stable", lambda **kwargs: True)
    assert vtable.wait_for_render_stable(timeout=1)["ok"] is True

    monkeypatch.setattr(vtable, "_wait_root_geometry_stable", lambda **kwargs: False)
    failed = vtable.wait_for_render_stable(timeout=1)
    assert failed["ok"] is False
    assert "几何" in failed["reason"]


def test_mount_vtable_updates_instance_state(monkeypatch):
    fr = SimpleNamespace(url="https://example/m")
    monkeypatch.setattr(vtable, "_frame", lambda: fr)
    monkeypatch.setattr(vtable.browser_session, "get_active_tab_name", lambda: "Tab")
    monkeypatch.setattr(
        vtable,
        "_run",
        lambda js_file, call: {"ok": True, "mountToken": "t1", "reused": False, "levels": 2},
    )
    vtable._instance_state.update({"frame_key": None, "mount_token": None})
    res = vtable.mount_vtable(force=True)
    assert res["ok"] is True
    assert vtable._instance_state["mount_token"] == "t1"
    assert vtable._instance_state["frame_key"] == "Tab|https://example/m"
