import os
import urllib.parse


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


def test_list_resources_returns_relative_file_index(monkeypatch, tmp_path):
    import resource_store

    monkeypatch.setattr(resource_store.config, "SHOT_DIR", str(tmp_path))
    resource_store.resolve_path("page-model.json")
    evidence = tmp_path / "page-model.json"
    evidence.write_text('{"ok": true}', encoding="utf-8")

    result = resource_store.list_resources()

    assert result["ok"] is True
    assert result["base_dir"] == os.path.abspath(str(tmp_path))
    assert result["files"][0]["path"] == "page-model.json"
    assert result["files"][0]["uri"] == "drissionpage-mcp://resources/page-model.json"
    assert result["files"][0]["size"] > 0


def test_read_text_resource_stays_under_shot_dir(monkeypatch, tmp_path):
    import resource_store

    monkeypatch.setattr(resource_store.config, "SHOT_DIR", str(tmp_path))
    (tmp_path / "dom.yml").write_text("tag: body", encoding="utf-8")

    assert resource_store.read_text_resource("dom.yml") == "tag: body"

    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    try:
        resource_store.read_text_resource("../outside.txt")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("path traversal should be sanitized under SHOT_DIR")


def test_read_text_resource_accepts_url_encoded_nested_path(monkeypatch, tmp_path):
    import resource_store

    monkeypatch.setattr(resource_store.config, "SHOT_DIR", str(tmp_path))
    nested = tmp_path / "生产动态表"
    nested.mkdir()
    (nested / "dom.yml").write_text("tag: body", encoding="utf-8")

    encoded = urllib.parse.quote("生产动态表/dom.yml", safe="")

    assert resource_store.read_text_resource(encoded) == "tag: body"
