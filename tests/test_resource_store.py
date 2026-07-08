import os


def test_simple_filename_is_saved_under_active_module(monkeypatch, tmp_path):
    import resource_store

    monkeypatch.setattr(resource_store.config, "SHOT_DIR", str(tmp_path))
    resource_store.set_module("采购入库 明细")

    path = resource_store.resolve_path("page-model.json")

    assert path == os.path.join(str(tmp_path), "采购入库_明细", "page-model.json")


def test_explicit_relative_path_is_respected(monkeypatch, tmp_path):
    import resource_store

    monkeypatch.setattr(resource_store.config, "SHOT_DIR", str(tmp_path))
    resource_store.set_module("采购入库")

    path = resource_store.resolve_path("WMS/采购入库/page-model.json")

    assert path == os.path.join(str(tmp_path), "WMS", "采购入库", "page-model.json")


def test_category_is_used_for_default_names(monkeypatch, tmp_path):
    import resource_store

    monkeypatch.setattr(resource_store.config, "SHOT_DIR", str(tmp_path))
    resource_store.set_module("采购入库")

    path = resource_store.resolve_path(default_name="shot.png", category="screenshots")

    assert path == os.path.join(str(tmp_path), "采购入库", "screenshots", "shot.png")
