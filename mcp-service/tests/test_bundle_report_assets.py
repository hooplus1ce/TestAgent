"""Exercise the real _bundle_report_assets copy path (tempfile + shutil)."""

from __future__ import annotations

import os
from pathlib import Path

from drissionpage_mcp.core import config
from drissionpage_mcp.workflows import recipe_execution


# Minimal valid 1x1 PNG that satisfies recipe_execution.image_kind checks.
_MIN_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01"  # width 1
    b"\x00\x00\x00\x01"  # height 1
    b"\x08\x02\x00\x00\x00"
    b"\x90wS\xde"  # IHDR CRC (not validated by image_kind)
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_bundle_report_assets_copies_png_from_shot_dir_via_tempfile(tmp_path, monkeypatch):
    """Regression: missing ``import tempfile`` would NameError inside copy_ref."""
    shot_dir = tmp_path / "shots"
    shot_dir.mkdir()
    project_root = tmp_path / "project"
    project_root.mkdir()
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    monkeypatch.setattr(config, "SHOT_DIR", str(shot_dir))
    monkeypatch.setattr(config, "PROJECT_ROOT", str(project_root))

    png_name = "evidence_shot.png"
    source = shot_dir / png_name
    source.write_bytes(_MIN_PNG)
    assert source.is_file()

    execution = {
        "ok": True,
        "results": [
            {
                "case_id": "T-1",
                "evidence_refs": [
                    {"screenshot": png_name, "label": "after-click"},
                ],
            }
        ],
    }
    # Relative path so resolve_screenshot joins SHOT_DIR.
    execution_file = str(shot_dir / "execution.json")
    Path(execution_file).write_text("{}", encoding="utf-8")

    result = recipe_execution._bundle_report_assets(
        execution, execution_file, str(bundle_dir),
    )

    assert "copied" in result
    assert result["copied"], "expected at least one copied asset"
    assets_dir = Path(result["assets_dir"])
    assert assets_dir.is_dir()
    relative = result["copied"][0]
    assert relative.startswith("assets/")
    dest = bundle_dir / relative
    assert dest.is_file()
    assert dest.read_bytes() == _MIN_PNG
    # Execution snapshot rewritten with updated screenshot path.
    bundled = result["execution"]
    ref = bundled["results"][0]["evidence_refs"][0]
    assert ref["screenshot"] == relative
    assert ref.get("source_screenshot") == png_name
    assert not ref.get("screenshot_missing")
    assert Path(result["execution_copy"]).is_file()
