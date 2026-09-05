from __future__ import annotations

import hashlib
import json
import plistlib
import subprocess
from pathlib import Path

import numpy as np
import OpenImageIO as oiio
import pytest
from PIL import Image

import gekigrade.pipeline.prepare as prepare_module
from gekigrade.adapters.imagemagick import make_preview as real_make_preview
from gekigrade.adapters.rawtherapee import RawTherapeeError
from gekigrade.domain.models import EditPlan
from gekigrade.domain.paths import create_job_directory as real_create_job_directory
from gekigrade.pipeline.prepare import inspect_photo, prepare_job


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_executable(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


def _write_rawtherapee_app(root: Path, source: str) -> Path:
    executable = _write_executable(root / "RawTherapee.app/Contents/MacOS/rawtherapee-cli", source)
    (executable.parent.parent / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleShortVersionString": "5.13"})
    )
    resources = executable.parent.parent / "Resources/share"
    dcp_directory = resources / "dcpprofiles"
    dcp_directory.mkdir(parents=True)
    (dcp_directory / "camera_model_aliases.json").write_text("{}\n", encoding="utf-8")
    (dcp_directory / "SONY ILCE-TEST.dcp").write_bytes(b"synthetic-test-dcp")
    (resources / "iccprofiles/input").mkdir(parents=True)
    (resources / "camconst.json").write_text('{"camera_constants": []}\n', encoding="utf-8")
    return executable


def _raw_test_dependencies(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    source = tmp_path / "camera.ARW"
    srgb_profile = Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc")
    unprofiled_source = tmp_path / "camera-unprofiled.tif"
    buffer = oiio.ImageBuf(oiio.ImageSpec(180, 120, 3, oiio.UINT16))
    pixels = np.empty((120, 180, 3), dtype=np.uint16)
    pixels[:, :, 0] = 24000
    pixels[:, :, 1] = 32000
    pixels[:, :, 2] = 40000
    assert buffer.set_pixels(oiio.ROI(0, 180, 0, 120, 0, 1, 0, 3), pixels)
    assert buffer.write(str(unprofiled_source)), buffer.geterror()
    subprocess.run(
        [
            "/opt/homebrew/bin/magick",
            str(unprofiled_source),
            "-profile",
            str(srgb_profile),
            "-depth",
            "16",
            "-define",
            "tiff:bits-per-sample=16",
            f"TIFF:{source}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
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
    rawtherapee = _write_rawtherapee_app(
        tmp_path,
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
    lensfun = tmp_path / "lensfun"
    lensfun.mkdir()
    (lensfun / "mil-sony.xml").write_text(
        """<lensdatabase>
<camera><maker>SONY</maker><model>ILCE-TEST</model><mount>Sony E</mount></camera>
<lens><maker>Sony</maker><model>FE TEST</model><mount>Sony E</mount></lens>
</lensdatabase>
""",
        encoding="utf-8",
    )
    return source, exiftool, rawtherapee, profile, lensfun


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
    source, exiftool, rawtherapee, profile, lensfun = _raw_test_dependencies(tmp_path)
    before = _sha256(source)
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
    camera_input = development["camera_input_profile"]
    expected_dcp = rawtherapee.parent.parent / "Resources/share/dcpprofiles/SONY ILCE-TEST.dcp"
    assert camera_input["selection"] == "auto-matched-camera-profile"
    assert camera_input["profile_key"] == "SONY ILCE-TEST"
    assert camera_input["resolved_kind"] == "dcp"
    assert camera_input["profile_path"] == str(expected_dcp)
    assert camera_input["profile_sha256"] == _sha256(expected_dcp)
    assert camera_input["camera_constants_sha256"] == _sha256(
        rawtherapee.parent.parent / "Resources/share/camconst.json"
    )
    assert development["intermediate_profile"]["embedded"] is True
    assert (
        development["intermediate_profile"]["sha256"]
        == development["expected_intermediate_profile_sha256"]
    )
    assert development["working_profile_sha256"]
    assert development["lens_correction"]["camera_match"] is True
    assert development["lens_correction"]["lens_match"] is True
    assert development["lens_correction"]["application_confirmed"] is False
    assert [
        Path(item["path"]).name for item in development["lens_correction"]["database_files"]
    ] == ["mil-sony.xml"]
    with Image.open(job / "intermediate/working.tif") as working:
        assert working.size == (180, 120)
        assert working.info.get("icc_profile")
    manifest = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"]["intermediate/rawtherapee/run.json"]["sha256"]
    assert manifest["artifacts"]["intermediate/rawtherapee/development.pp3"]["sha256"]


def test_prepare_rejects_a_raw_source_changed_after_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, profile, lensfun = _raw_test_dependencies(tmp_path)

    def create_job_then_change_source(source_path: Path, output_path: Path) -> Path:
        job = real_create_job_directory(source_path, output_path)
        with source_path.open("ab") as stream:
            stream.write(b"changed-after-inspection")
        return job

    monkeypatch.setattr(prepare_module, "create_job_directory", create_job_then_change_source)

    with pytest.raises(RawTherapeeError, match="changed after inspection"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
            raw_profile=profile,
            raw_output_profile=Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc"),
            lensfun_database=lensfun,
        )


def test_prepare_rechecks_the_raw_source_before_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, profile, lensfun = _raw_test_dependencies(tmp_path)

    def make_preview_then_change_source(working: Path, preview: Path) -> None:
        real_make_preview(working, preview)
        with source.open("ab") as stream:
            stream.write(b"changed-after-development")

    monkeypatch.setattr(prepare_module, "make_preview", make_preview_then_change_source)

    with pytest.raises(RawTherapeeError, match="changed during job preparation"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
            raw_profile=profile,
            raw_output_profile=Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc"),
            lensfun_database=lensfun,
        )


def test_prepare_rejects_a_lensfun_database_changed_during_development(tmp_path: Path) -> None:
    source, exiftool, rawtherapee, profile, lensfun = _raw_test_dependencies(tmp_path)
    lensfun_file = lensfun / "mil-sony.xml"
    _write_executable(
        rawtherapee,
        f"""#!/usr/bin/env python3
import pathlib
import shutil
import sys

args = sys.argv[1:]
shutil.copyfile(pathlib.Path(args[-1]), pathlib.Path(args[args.index("-o") + 1]))
with pathlib.Path({str(lensfun_file)!r}).open("a", encoding="utf-8") as stream:
    stream.write("\\n<!-- changed during development -->\\n")
""",
    )

    with pytest.raises(RawTherapeeError, match="Lensfun database changed"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
            raw_profile=profile,
            raw_output_profile=Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc"),
            lensfun_database=lensfun,
        )


def test_inspect_rejects_a_raw_source_changed_during_metadata_read(tmp_path: Path) -> None:
    source, _, _, _, _ = _raw_test_dependencies(tmp_path)
    exiftool = _write_executable(
        tmp_path / "mutating-exiftool",
        """#!/usr/bin/env python3
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[-1])
metadata = {
    "SourceFile": str(path),
    "FileType": "ARW",
    "MIMEType": "image/x-sony-arw",
    "ImageWidth": 180,
    "ImageHeight": 120,
    "Orientation": 1,
    "Make": "SONY",
    "Model": "ILCE-TEST"
}
with path.open("ab") as stream:
    stream.write(b"changed-during-exiftool")
print(json.dumps([metadata]))
""",
    )

    with pytest.raises(ValueError, match="changed during metadata inspection"):
        inspect_photo(source, exiftool_executable=exiftool)
