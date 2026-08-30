from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from PIL import Image

from gekigrade.domain.models import EditPlan
from gekigrade.pipeline.prepare import prepare_job


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_prepare_builds_a_complete_oriented_profiled_job_without_touching_source(
    tagged_oriented_jpeg: Path, tmp_path: Path
) -> None:
    before = _sha256(tagged_oriented_jpeg)
    job = tmp_path / "job"

    prepare_job(tagged_oriented_jpeg, job)

    assert _sha256(tagged_oriented_jpeg) == before
    required = (
        "source.json",
        "manifest.json",
        "analysis.json",
        "preview.jpg",
        "intermediate/working.tif",
        "crops/candidates.json",
        "crops/contact-sheet.jpg",
        "plans/example-plan.json",
        "looks.json",
        "edit-plan.schema.json",
    )
    for relative in required:
        assert (job / relative).is_file(), relative

    with Image.open(job / "preview.jpg") as preview:
        assert preview.size == (240, 320)
        assert preview.info.get("icc_profile")
    with Image.open(job / "intermediate/working.tif") as working:
        assert working.size == (240, 320)
        assert working.info.get("icc_profile")
    identify = subprocess.run(
        [
            "/opt/homebrew/bin/magick",
            "identify",
            "-format",
            "%z %[profiles]",
            str(job / "intermediate/working.tif"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert identify.stdout == "16 icc"

    source = json.loads((job / "source.json").read_text(encoding="utf-8"))
    assert source["source_sha256"] == before
    assert source["stored_dimensions"] == {"width": 320, "height": 240}
    assert source["oriented_dimensions"] == {"width": 240, "height": 320}
    assert source["exif_orientation"] == 6
    assert source["icc_profile"]["embedded"] is True

    plan = EditPlan.model_validate_json((job / "plans/example-plan.json").read_text())
    assert plan.source_sha256 == before
    assert len(plan.candidates) == 3

    manifest = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_sha256"] == before
    assert manifest["artifacts"]["preview.jpg"]["sha256"]
    assert manifest["profiles"]["working"]["sha256"]


def test_prepare_rejects_non_jpeg_before_creating_job(tmp_path: Path) -> None:
    source = tmp_path / "not-an-image.jpg"
    source.write_text("not a JPEG", encoding="utf-8")
    job = tmp_path / "job"

    try:
        prepare_job(source, job)
    except ValueError as exc:
        assert "JPEG" in str(exc)
    else:
        raise AssertionError("invalid JPEG was accepted")

    assert not job.exists()
