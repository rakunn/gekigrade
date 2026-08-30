from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from gekigrade.domain.models import EditPlan
from gekigrade.pipeline.prepare import prepare_job


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_executable(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


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


def test_prepare_routes_arw_through_rawtherapee_into_the_working_contract(
    tmp_path: Path,
) -> None:
    source = tmp_path / "camera.ARW"
    pixels = np.zeros((120, 180, 3), dtype=np.uint8)
    pixels[:, :, 0] = 96
    pixels[:, :, 1] = 128
    pixels[:, :, 2] = 160
    Image.fromarray(pixels, mode="RGB").save(
        source,
        format="TIFF",
        compression="tiff_deflate",
        icc_profile=Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc").read_bytes(),
    )
    before = _sha256(source)
    exiftool = _write_executable(
        tmp_path / "fake-exiftool",
        """#!/usr/bin/env python3
import json
import sys

print(json.dumps([{
    "SourceFile": sys.argv[-1],
    "FileType": "ARW",
    "MIMEType": "image/x-sony-arw",
    "ImageWidth": 180,
    "ImageHeight": 120,
    "Orientation": 1,
    "Make": "SONY",
    "Model": "ILCE-TEST",
    "LensModel": "FE TEST",
    "ExposureTime": 0.008,
    "FNumber": 8.0,
    "ISO": 100,
    "FocalLength": 24.0,
    "DateTimeOriginal": "2026:08:27 17:50:37"
}]))
""",
    )
    rawtherapee = _write_executable(
        tmp_path / "fake-rawtherapee",
        """#!/usr/bin/env python3
import pathlib
import shutil
import sys

args = sys.argv[1:]
target = pathlib.Path(args[args.index("-o") + 1])
source = pathlib.Path(args[-1])
shutil.copyfile(source, target)
print("fake ARW developed")
""",
    )
    profile = tmp_path / "neutral.pp3"
    profile.write_text("[Version]\nAppVersion=5.13\nVersion=353\n", encoding="utf-8")
    lensfun = tmp_path / "mil-sony.xml"
    lensfun.write_text(
        """<lensdatabase>
<camera><maker>SONY</maker><model>ILCE-TEST</model></camera>
<lens><maker>Sony</maker><model>FE TEST</model></lens>
</lensdatabase>
""",
        encoding="utf-8",
    )
    job = tmp_path / "raw-job"

    prepare_job(
        source,
        job,
        exiftool_executable=exiftool,
        rawtherapee_executable=rawtherapee,
        raw_profile=profile,
        raw_output_profile=Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc"),
        lensfun_database=lensfun,
    )

    assert _sha256(source) == before
    metadata = json.loads((job / "source.json").read_text(encoding="utf-8"))
    assert metadata["format"] == "ARW"
    assert metadata["stored_dimensions"] == {"width": 180, "height": 120}
    assert metadata["oriented_dimensions"] == {"width": 180, "height": 120}
    assert metadata["capture_metadata"]["Model"] == "ILCE-TEST"
    development = metadata["raw_development"]
    assert development["engine"] == "RawTherapee"
    assert development["profile_sha256"] == _sha256(profile)
    assert development["intermediate_profile"]["embedded"] is True
    assert (
        development["intermediate_profile"]["sha256"]
        == development["expected_intermediate_profile_sha256"]
    )
    assert development["working_profile_sha256"]
    assert development["lens_correction"]["camera_match"] is True
    assert development["lens_correction"]["lens_match"] is True
    assert development["lens_correction"]["application_confirmed"] is False
    with Image.open(job / "intermediate/working.tif") as working:
        assert working.size == (180, 120)
        assert working.info.get("icc_profile")
    manifest = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"]["intermediate/rawtherapee/run.json"]["sha256"]
    assert manifest["artifacts"]["intermediate/rawtherapee/development.pp3"]["sha256"]
