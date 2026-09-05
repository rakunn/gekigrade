from __future__ import annotations

import hashlib
import inspect
import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import numpy as np
import OpenImageIO as oiio
import pytest
from PIL import Image

import gekigrade.doctor as doctor_module
import gekigrade.pipeline.prepare as prepare_module
from gekigrade.adapters.imagemagick import MAGICK_ENVIRONMENT, ProcessorIdentity
from gekigrade.adapters.imagemagick import make_preview as real_make_preview
from gekigrade.adapters.imagemagick import normalize_jpeg as real_normalize_jpeg
from gekigrade.adapters.imagemagick import normalize_profiled_tiff as real_normalize_profiled_tiff
from gekigrade.adapters.rawtherapee import DEFAULT_RAW_PROFILE, RawTherapeeError
from gekigrade.doctor import ACESCG_PROFILE, SRGB_PROFILE
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
    output_profiles = resources / "iccprofiles/output"
    output_profiles.mkdir()
    shutil.copyfile(
        "/System/Library/ColorSync/Profiles/sRGB Profile.icc",
        output_profiles / "RTv4_Large.icc",
    )
    (resources / "camconst.json").write_text('{"camera_constants": []}\n', encoding="utf-8")
    return executable


def _raw_test_dependencies(
    tmp_path: Path, *, orientation: int = 1
) -> tuple[Path, Path, Path, Path]:
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
    exiftool_source = """#!/usr/bin/env python3
import json
import sys

if sys.argv[1:] == ["-config", "", "-ver"]:
    print("13.55")
    raise SystemExit

print(json.dumps([{
    "SourceFile": sys.argv[-1],
    "FileType": "ARW",
    "MIMEType": "image/x-sony-arw",
    "ImageWidth": 180,
    "ImageHeight": 120,
    "Orientation": 1,
    "Make": "SONY",
    "Model": "ILCE-TEST",
    "LensMake": "Sony",
    "LensModel": "FE TEST",
    "ExposureTime": 0.008,
    "FNumber": 8.0,
    "ISO": 100,
    "FocalLength": 24.0,
    "DateTimeOriginal": "2026:08:27 17:50:37"
}]))
""".replace('"Orientation": 1', f'"Orientation": {orientation}')
    exiftool = _write_executable(
        tmp_path / "fake-exiftool",
        exiftool_source,
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
    lensfun = rawtherapee.parent.parent / "Resources/share/lensfun"
    lensfun.mkdir()
    (lensfun / "mil-sony.xml").write_text(
        """<lensdatabase>
<camera><maker>SONY</maker><model>ILCE-TEST</model><mount>Sony E</mount></camera>
<lens><maker>Sony</maker><model>FE TEST</model><mount>Sony E</mount></lens>
</lensdatabase>
""",
        encoding="utf-8",
    )
    return source, exiftool, rawtherapee, lensfun


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
    assert manifest["profiles"]["working"]["sha256"] == _sha256(ACESCG_PROFILE)
    assert manifest["profiles"]["output"]["sha256"] == _sha256(SRGB_PROFILE)
    assert (
        manifest["tools"]["profiles"]["acescg"]["sha256"]
        == manifest["profiles"]["working"]["sha256"]
    )
    assert (
        manifest["tools"]["profiles"]["srgb"]["sha256"] == manifest["profiles"]["output"]["sha256"]
    )


def test_exiftool_disables_user_configuration_and_uses_a_pinned_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = tmp_path / "observed.json"
    executable = _write_executable(
        tmp_path / "fake-exiftool",
        f"""#!/usr/bin/env python3
import json
import os
import pathlib
import sys

if "-ver" in sys.argv:
    print("13.55")
else:
    pathlib.Path({str(observed)!r}).write_text(json.dumps({{
        "arguments": sys.argv[1:],
        "environment": dict(os.environ),
    }}), encoding="utf-8")
    print("[{{}}]")
""",
    )
    monkeypatch.setenv("HOME", str(tmp_path / "hostile-home"))
    monkeypatch.setenv("EXIFTOOL_HOME", str(tmp_path / "hostile-exiftool-home"))

    _, identity = prepare_module._read_exiftool(tmp_path / "input.jpg", executable=executable)

    captured = json.loads(observed.read_text(encoding="utf-8"))
    assert captured["arguments"][:2] == ["-config", ""]
    assert all(
        captured["environment"].get(key) == value
        for key, value in prepare_module.EXIFTOOL_ENVIRONMENT.items()
    )
    assert "HOME" not in captured["environment"]
    assert "EXIFTOOL_HOME" not in captured["environment"]
    assert identity["configuration"] == "disabled"
    assert identity["environment"] == prepare_module.EXIFTOOL_ENVIRONMENT


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


def test_inspect_rejects_a_symlink_before_reading_its_signature(
    tagged_oriented_jpeg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked = tmp_path / "linked.jpg"
    linked.symlink_to(tagged_oriented_jpeg)
    original_open = cast(Any, Path.open)

    def reject_link_open(path: Path, *args: object, **kwargs: object) -> Any:
        if path == linked:
            raise AssertionError("source signature was read through a symlink")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_link_open)

    with pytest.raises(ValueError, match="regular, non-symlink"):
        inspect_photo(linked)


def test_prepare_rejects_a_symlink_before_reading_its_signature(
    tagged_oriented_jpeg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked = tmp_path / "linked.jpg"
    linked.symlink_to(tagged_oriented_jpeg)
    original_open = cast(Any, Path.open)

    def reject_link_open(path: Path, *args: object, **kwargs: object) -> Any:
        if path == linked:
            raise AssertionError("source signature was read through a symlink")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_link_open)

    with pytest.raises(ValueError, match="regular, non-symlink"):
        prepare_job(linked, tmp_path / "job")


def test_prepare_routes_arw_through_rawtherapee_into_the_working_contract(
    tmp_path: Path,
) -> None:
    source, exiftool, rawtherapee, lensfun = _raw_test_dependencies(tmp_path)
    before = _sha256(source)
    job = tmp_path / "raw-job"

    prepare_job(
        source,
        job,
        exiftool_executable=exiftool,
        rawtherapee_executable=rawtherapee,
        imagemagick_executable=Path("/opt/homebrew/bin/magick"),
    )

    assert _sha256(source) == before
    metadata = json.loads((job / "source.json").read_text(encoding="utf-8"))
    assert metadata["format"] == "ARW"
    assert metadata["stored_dimensions"] == {"width": 180, "height": 120}
    assert metadata["oriented_dimensions"] == {"width": 180, "height": 120}
    assert metadata["capture_metadata"]["Model"] == "ILCE-TEST"
    assert metadata["capture_metadata"]["LensMake"] == "Sony"
    metadata_reader = metadata["metadata_reader"]
    assert metadata_reader == {
        "name": "ExifTool",
        "path": str(exiftool.resolve()),
        "version": "13.55",
        "executable_sha256": _sha256(exiftool),
        "configuration": "disabled",
        "environment": prepare_module.EXIFTOOL_ENVIRONMENT,
    }
    normalization_tool = metadata["processing_tools"]["normalization"]
    assert normalization_tool["name"] == "ImageMagick"
    assert normalization_tool["path"] == str(Path("/opt/homebrew/bin/magick").resolve())
    assert normalization_tool["version"].startswith("Version: ImageMagick 7.1.1-47")
    assert normalization_tool["executable_sha256"] == _sha256(
        Path("/opt/homebrew/bin/magick").resolve()
    )
    assert normalization_tool["environment"] == MAGICK_ENVIRONMENT
    assert (
        normalization_tool["output_sha256"] == metadata["prepared_artifacts"]["working_tiff_sha256"]
    )
    preview_tool = metadata["processing_tools"]["preview"]
    assert preview_tool["environment"] == MAGICK_ENVIRONMENT
    assert preview_tool["output_sha256"] == metadata["prepared_artifacts"]["preview_jpeg_sha256"]
    assert metadata["prepared_artifacts"] == {
        "working_tiff_sha256": _sha256(job / "intermediate/working.tif"),
        "preview_jpeg_sha256": _sha256(job / "preview.jpg"),
    }
    development = metadata["raw_development"]
    assert development["engine"] == "RawTherapee"
    assert development["inspected_oriented_dimensions"] == {"width": 180, "height": 120}
    assert development["profile_sha256"] == _sha256(DEFAULT_RAW_PROFILE)
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
    expected_output_profile = (
        rawtherapee.parent.parent / "Resources/share/iccprofiles/output/RTv4_Large.icc"
    )
    assert development["expected_intermediate_profile_path"] == str(expected_output_profile)
    assert development["expected_intermediate_profile_sha256"] == _sha256(expected_output_profile)
    assert development["working_profile_sha256"]
    assert development["lens_correction"]["camera_match"] is True
    assert development["lens_correction"]["lens_match"] is True
    assert development["lens_correction"]["application_confirmed"] is False
    assert development["lens_correction"]["database_path"] == str(lensfun)
    assert [
        Path(item["path"]).name for item in development["lens_correction"]["database_files"]
    ] == ["mil-sony.xml"]
    with Image.open(job / "intermediate/working.tif") as working:
        assert working.size == (180, 120)
        assert working.info.get("icc_profile")
    manifest = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"]["intermediate/rawtherapee/run.json"]["sha256"]
    assert manifest["artifacts"]["intermediate/rawtherapee/development.pp3"]["sha256"]
    assert manifest["executed_tools"] == metadata["processing_tools"]
    assert (
        manifest["artifacts"]["intermediate/working.tif"]["sha256"]
        == metadata["prepared_artifacts"]["working_tiff_sha256"]
    )
    assert (
        manifest["artifacts"]["preview.jpg"]["sha256"]
        == metadata["prepared_artifacts"]["preview_jpeg_sha256"]
    )


def test_prepare_manifest_uses_the_selected_rawtherapee_output_profile_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)
    selected_profile = (
        rawtherapee.parent.parent / "Resources/share/iccprofiles/output/RTv4_Large.icc"
    )
    monkeypatch.setattr(
        doctor_module,
        "RAWTHERAPEE_CLI",
        tmp_path / "missing-default/RawTherapee.app/Contents/MacOS/rawtherapee-cli",
    )
    job = tmp_path / "raw-job"

    prepare_job(
        source,
        job,
        exiftool_executable=exiftool,
        rawtherapee_executable=rawtherapee,
    )

    manifest = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["tools"]["profiles"]["rawtherapee_output"] == {
        "available": True,
        "valid": True,
        "path": str(selected_profile),
        "sha256": _sha256(selected_profile),
        "color_space": "RGB",
        "device_class": "mntr",
        "error": None,
    }
    assert manifest["tools"]["tools"]["rawtherapee"] == {
        "name": "rawtherapee",
        "available": True,
        "path": str(rawtherapee),
        "version": "5.13",
        "install_hint": "Install with: brew install --cask rawtherapee",
        "error": None,
    }
    assert manifest["tools"]["profiles"]["rawtherapee_camera_resources"][
        "dcp_directory"
    ].startswith(str(rawtherapee.parent.parent))
    assert manifest["tools"]["profiles"]["lensfun_database"]["path"].startswith(
        str(rawtherapee.parent.parent)
    )
    assert manifest["tools"]["ready_for_raw"] is True
    assert manifest["tools"]["raw_status"] == "adapter-ready"


def test_raw_dimension_tolerance_accepts_the_verified_sony_border_crop() -> None:
    assert prepare_module._raw_dimensions_within_border_crop(
        {"width": 9728, "height": 6656}, {"width": 9556, "height": 6366}
    )


def test_prepare_does_not_expose_a_raw_profile_override() -> None:
    assert "raw_profile" not in inspect.signature(prepare_job).parameters
    assert "raw_output_profile" not in inspect.signature(prepare_job).parameters


def test_prepare_rejects_a_symlinked_rawtherapee_bundle_before_creating_job(
    tmp_path: Path,
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(tmp_path, target_is_directory=True)
    linked_executable = linked_root / rawtherapee.relative_to(tmp_path)
    job = tmp_path / "raw-job"

    with pytest.raises(RawTherapeeError, match="symlinked path"):
        prepare_job(
            source,
            job,
            exiftool_executable=exiftool,
            rawtherapee_executable=linked_executable,
        )
    assert not job.exists()


def test_prepare_rejects_a_modified_shipped_raw_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)
    modified_profile = tmp_path / "modified-neutral.pp3"
    modified_profile.write_text("[Version]\nAppVersion=5.13\n", encoding="utf-8")
    monkeypatch.setattr(prepare_module, "DEFAULT_RAW_PROFILE", modified_profile)

    with pytest.raises(RawTherapeeError, match="shipped RAW development profile"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job").exists()


@pytest.mark.parametrize("orientation", [2, 3, 4, 5, 6, 7, 8])
def test_prepare_rejects_raw_orientations_without_a_fully_verifiable_transform(
    tmp_path: Path, orientation: int
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path, orientation=orientation)

    with pytest.raises(ValueError, match=f"RAW EXIF orientation {orientation} is not"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job").exists()


def test_prepare_rejects_a_valid_working_tiff_replaced_after_imagemagick(
    tagged_oriented_jpeg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alternate = tmp_path / "alternate.jpg"
    alternate_exif = Image.Exif()
    alternate_exif[274] = 6
    Image.new("RGB", (320, 240), (12, 180, 72)).save(
        alternate,
        format="JPEG",
        exif=alternate_exif,
        icc_profile=SRGB_PROFILE.read_bytes(),
    )
    replacement = tmp_path / "replacement-working.tif"
    real_normalize_jpeg(alternate, replacement, has_profile=True)

    def normalize_then_replace(
        source: Path, working: Path, *, has_profile: bool, executable: Path
    ) -> ProcessorIdentity:
        result = real_normalize_jpeg(
            source, working, has_profile=has_profile, executable=executable
        )
        shutil.copyfile(replacement, working)
        return result

    monkeypatch.setattr(prepare_module, "normalize_jpeg", normalize_then_replace)

    with pytest.raises(RuntimeError, match="working TIFF does not match ImageMagick output"):
        prepare_job(tagged_oriented_jpeg, tmp_path / "jpeg-job")
    assert not (tmp_path / "jpeg-job/intermediate/working.tif").exists()
    assert not (tmp_path / "jpeg-job/preview.jpg").exists()
    assert not (tmp_path / "jpeg-job/manifest.json").exists()


def test_prepare_rejects_a_valid_preview_replaced_after_imagemagick(
    tagged_oriented_jpeg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def preview_then_replace(
        working: Path, preview: Path, *, executable: Path
    ) -> ProcessorIdentity:
        result = real_make_preview(working, preview, executable=executable)
        with Image.open(preview) as produced:
            replacement = produced.copy()
            profile = produced.info["icc_profile"]
        replacement.putpixel((0, 0), (255, 0, 255))
        replacement.save(preview, quality=92, subsampling=0, icc_profile=profile)
        return result

    monkeypatch.setattr(prepare_module, "make_preview", preview_then_replace)

    with pytest.raises(RuntimeError, match="preview JPEG does not match ImageMagick output"):
        prepare_job(tagged_oriented_jpeg, tmp_path / "jpeg-job")
    assert not (tmp_path / "jpeg-job/preview.jpg").exists()
    assert not (tmp_path / "jpeg-job/manifest.json").exists()


def test_prepare_rejects_a_raw_source_changed_after_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)

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
        )


def test_prepare_rechecks_the_raw_source_before_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)

    def make_preview_then_change_source(
        working: Path, preview: Path, *, executable: Path
    ) -> ProcessorIdentity:
        identity = real_make_preview(working, preview, executable=executable)
        with source.open("ab") as stream:
            stream.write(b"changed-after-development")
        return identity

    monkeypatch.setattr(prepare_module, "make_preview", make_preview_then_change_source)

    with pytest.raises(RawTherapeeError, match="changed during job preparation"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job/manifest.json").exists()


def test_prepare_removes_a_manifest_if_the_raw_changes_while_it_is_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)
    original_manifest = prepare_module._artifact_manifest

    def build_manifest_then_change_source(
        job: Path, metadata: dict[str, object], **profile_hashes: Any
    ) -> dict[str, object]:
        manifest = original_manifest(job, metadata, **profile_hashes)
        with source.open("ab") as stream:
            stream.write(b"changed-while-publishing-manifest")
        return manifest

    monkeypatch.setattr(prepare_module, "_artifact_manifest", build_manifest_then_change_source)

    with pytest.raises(RawTherapeeError, match="changed while publishing"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job/manifest.json").exists()


def test_raw_source_match_rejects_a_fifo_without_hashing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fifo = tmp_path / "source.ARW"
    os.mkfifo(fifo)
    original_open = os.open

    def guarded_open(path: Any, flags: int) -> int:
        assert flags & os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            assert flags & os.O_NOFOLLOW
        return original_open(path, flags)

    def reject_hash(_: object) -> str:
        raise AssertionError("FIFO must be rejected before hashing")

    monkeypatch.setattr(os, "open", guarded_open)
    monkeypatch.setattr(prepare_module, "_sha256_stream", reject_hash)

    assert prepare_module._raw_source_matches(fifo, "not-a-real-digest") is False


def test_inspect_rejects_an_oversized_raw_before_hashing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "oversized.ARW"
    source.write_bytes(b"II*\x00")
    with source.open("r+b") as stream:
        stream.truncate(prepare_module.MAX_SOURCE_BYTES + 1)

    def reject_hash(_: object) -> str:
        raise AssertionError("oversized RAW must be rejected before hashing")

    monkeypatch.setattr(prepare_module, "_sha256_stream", reject_hash)

    with pytest.raises(ValueError, match="ARW exceeds the 1 GiB safety limit"):
        inspect_photo(source, exiftool_executable=tmp_path / "unused-exiftool")


def test_prepare_removes_manifest_if_raw_disappears_while_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)
    original_manifest = prepare_module._artifact_manifest

    def build_manifest_then_remove_source(
        job: Path, metadata: dict[str, object], **profile_hashes: Any
    ) -> dict[str, object]:
        manifest = original_manifest(job, metadata, **profile_hashes)
        source.unlink()
        return manifest

    monkeypatch.setattr(prepare_module, "_artifact_manifest", build_manifest_then_remove_source)

    with pytest.raises(RawTherapeeError, match="changed while publishing"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job/manifest.json").exists()


def test_prepare_removes_manifest_if_copied_raw_profile_changes_while_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)
    original_manifest = prepare_module._artifact_manifest

    def build_manifest_then_change_profile(
        job: Path, metadata: dict[str, object], **profile_hashes: Any
    ) -> dict[str, object]:
        manifest = original_manifest(job, metadata, **profile_hashes)
        (job / "intermediate/rawtherapee/development.pp3").write_text(
            "changed-while-publishing-manifest", encoding="utf-8"
        )
        return manifest

    monkeypatch.setattr(prepare_module, "_artifact_manifest", build_manifest_then_change_profile)

    with pytest.raises(RawTherapeeError, match="profile changed while publishing"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job/manifest.json").exists()


def test_prepare_removes_manifest_if_raw_output_profile_changes_while_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)
    output_profile = rawtherapee.parent.parent / "Resources/share/iccprofiles/output/RTv4_Large.icc"
    original_manifest = prepare_module._artifact_manifest

    def change_profile_then_build_manifest(
        job: Path, metadata: dict[str, object], **profile_hashes: Any
    ) -> dict[str, object]:
        output_profile.write_bytes(b"changed-while-publishing-manifest")
        return original_manifest(job, metadata, **profile_hashes)

    monkeypatch.setattr(prepare_module, "_artifact_manifest", change_profile_then_build_manifest)

    with pytest.raises(RawTherapeeError, match="output ICC profile changed while publishing"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job/manifest.json").exists()


def test_prepare_removes_manifest_if_developed_tiff_changes_while_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)
    original_manifest = prepare_module._artifact_manifest

    def change_developed_then_build_manifest(
        job: Path, metadata: dict[str, object], **profile_hashes: Any
    ) -> dict[str, object]:
        (job / "intermediate/rawtherapee/developed.tif").write_bytes(
            b"changed-while-publishing-manifest"
        )
        return original_manifest(job, metadata, **profile_hashes)

    monkeypatch.setattr(prepare_module, "_artifact_manifest", change_developed_then_build_manifest)

    with pytest.raises(RawTherapeeError, match="developed TIFF changed while publishing"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job/manifest.json").exists()


@pytest.mark.parametrize("change", ["modify", "remove"])
def test_prepare_removes_manifest_if_raw_run_report_changes_while_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)
    original_manifest = prepare_module._artifact_manifest

    def change_report_then_build_manifest(
        job: Path, metadata: dict[str, object], **profile_hashes: Any
    ) -> dict[str, object]:
        report = job / "intermediate/rawtherapee/run.json"
        if change == "modify":
            report.write_text('{"forged": true}\n', encoding="utf-8")
        else:
            report.unlink()
        return original_manifest(job, metadata, **profile_hashes)

    monkeypatch.setattr(prepare_module, "_artifact_manifest", change_report_then_build_manifest)

    with pytest.raises(RawTherapeeError, match="run report changed while publishing"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job/manifest.json").exists()


@pytest.mark.parametrize(
    ("resource", "message"),
    [
        ("camera", "camera resources changed while publishing"),
        ("lensfun", "Lensfun database changed while publishing"),
    ],
)
def test_prepare_removes_manifest_if_raw_resources_change_while_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resource: str,
    message: str,
) -> None:
    source, exiftool, rawtherapee, lensfun = _raw_test_dependencies(tmp_path)
    aliases = rawtherapee.parent.parent / "Resources/share/dcpprofiles/camera_model_aliases.json"
    original_manifest = prepare_module._artifact_manifest

    def change_resource_then_build_manifest(
        job: Path, metadata: dict[str, object], **profile_hashes: Any
    ) -> dict[str, object]:
        if resource == "camera":
            aliases.write_text('{"UNRELATED": []}\n', encoding="utf-8")
        else:
            with (lensfun / "mil-sony.xml").open("a", encoding="utf-8") as stream:
                stream.write("\n<!-- changed while publishing -->\n")
        return original_manifest(job, metadata, **profile_hashes)

    monkeypatch.setattr(prepare_module, "_artifact_manifest", change_resource_then_build_manifest)

    with pytest.raises(RawTherapeeError, match=message):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job/manifest.json").exists()


def test_prepare_removes_manifest_if_camera_revalidation_raises_after_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)
    aliases = rawtherapee.parent.parent / "Resources/share/dcpprofiles/camera_model_aliases.json"
    original_manifest = prepare_module._artifact_manifest
    original_camera_inspection = cast(Any, prepare_module).inspect_camera_input_profile
    manifest_built = False

    def mark_manifest_built(
        job: Path, metadata: dict[str, object], **profile_hashes: Any
    ) -> dict[str, object]:
        nonlocal manifest_built
        manifest = original_manifest(job, metadata, **profile_hashes)
        manifest_built = True
        return manifest

    def remove_aliases_before_post_publication_inspection(
        capture_metadata: dict[str, object], *, executable: Path
    ) -> Any:
        if manifest_built:
            aliases.unlink(missing_ok=True)
        return original_camera_inspection(capture_metadata, executable=executable)

    monkeypatch.setattr(prepare_module, "_artifact_manifest", mark_manifest_built)
    monkeypatch.setattr(
        prepare_module,
        "inspect_camera_input_profile",
        remove_aliases_before_post_publication_inspection,
    )

    with pytest.raises(RawTherapeeError):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job/manifest.json").exists()


@pytest.mark.parametrize(
    ("profile_attribute", "system_profile"),
    [("ACESCG_PROFILE", ACESCG_PROFILE), ("SRGB_PROFILE", SRGB_PROFILE)],
)
def test_prepare_removes_manifest_if_validated_profile_changes_while_published(
    tagged_oriented_jpeg: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile_attribute: str,
    system_profile: Path,
) -> None:
    profile = tmp_path / f"{profile_attribute}.icc"
    profile.write_bytes(system_profile.read_bytes())
    monkeypatch.setattr(prepare_module, profile_attribute, profile)
    monkeypatch.setattr(f"gekigrade.adapters.imagemagick.{profile_attribute}", profile)
    original_manifest = prepare_module._artifact_manifest

    def build_manifest_then_change_profile(
        job: Path, metadata: dict[str, object], **profile_hashes: Any
    ) -> dict[str, object]:
        manifest = original_manifest(job, metadata, **profile_hashes)
        profile.write_bytes(b"changed-while-publishing-manifest")
        return manifest

    monkeypatch.setattr(prepare_module, "_artifact_manifest", build_manifest_then_change_profile)

    with pytest.raises(RuntimeError, match="color profile changed while publishing"):
        prepare_job(tagged_oriented_jpeg, tmp_path / "jpeg-job")
    assert not (tmp_path / "jpeg-job/manifest.json").exists()


def test_prepare_rejects_a_lensfun_database_changed_during_development(tmp_path: Path) -> None:
    source, exiftool, rawtherapee, lensfun = _raw_test_dependencies(tmp_path)
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
        )


def test_prepare_rejects_a_raw_output_profile_changed_during_development(tmp_path: Path) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)
    output_profile = rawtherapee.parent.parent / "Resources/share/iccprofiles/output/RTv4_Large.icc"
    _write_executable(
        rawtherapee,
        f"""#!/usr/bin/env python3
import pathlib
import shutil
import sys

args = sys.argv[1:]
shutil.copyfile(pathlib.Path(args[-1]), pathlib.Path(args[args.index("-o") + 1]))
with pathlib.Path({str(output_profile)!r}).open("ab") as stream:
    stream.write(b"changed during development")
""",
    )

    with pytest.raises(RawTherapeeError, match="output ICC profile changed"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )


def test_prepare_rejects_a_developed_tiff_replaced_during_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)
    replacement = tmp_path / "replacement.tif"
    Image.new("RGB", (180, 120), (8, 24, 48)).save(
        replacement,
        format="TIFF",
        icc_profile=Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc").read_bytes(),
    )

    def replace_then_normalize(
        developed: Path, working: Path, *, executable: Path
    ) -> ProcessorIdentity:
        shutil.copyfile(replacement, developed)
        return real_normalize_profiled_tiff(developed, working, executable=executable)

    monkeypatch.setattr(prepare_module, "normalize_profiled_tiff", replace_then_normalize)

    with pytest.raises(RawTherapeeError, match="developed TIFF changed"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )


def test_prepare_rejects_an_invalid_normalized_raw_working_tiff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)

    def write_eight_bit_working(
        developed: Path, working: Path, *, executable: Path
    ) -> ProcessorIdentity:
        del developed, executable
        Image.new("RGB", (180, 120), (32, 64, 96)).save(
            working,
            format="TIFF",
            icc_profile=ACESCG_PROFILE.read_bytes(),
        )
        return {
            "name": "ImageMagick",
            "path": "/test/magick",
            "version": "test",
            "executable_sha256": "a" * 64,
            "environment": MAGICK_ENVIRONMENT,
        }

    monkeypatch.setattr(prepare_module, "normalize_profiled_tiff", write_eight_bit_working)

    with pytest.raises(RuntimeError, match="working TIFF must contain 16-bit samples"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job/intermediate/working.tif").exists()


def test_prepare_rejects_a_working_tiff_changed_during_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)

    def make_preview_then_change_working(
        working: Path, preview: Path, *, executable: Path
    ) -> ProcessorIdentity:
        identity = real_make_preview(working, preview, executable=executable)
        with working.open("ab") as stream:
            stream.write(b"changed during preview")
        return identity

    monkeypatch.setattr(prepare_module, "make_preview", make_preview_then_change_working)

    with pytest.raises(RuntimeError, match="working TIFF changed during preview generation"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job/preview.jpg").exists()
    assert not (tmp_path / "raw-job/manifest.json").exists()


def test_prepare_rejects_jpeg_working_dimensions_that_differ_from_source(
    tagged_oriented_jpeg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_validate = prepare_module._validate_working_tiff

    def report_wrong_dimensions(working: Path) -> tuple[dict[str, int], str, str]:
        _, working_sha256, profile_sha256 = real_validate(working)
        return {"width": 200, "height": 200}, working_sha256, profile_sha256

    monkeypatch.setattr(prepare_module, "_validate_working_tiff", report_wrong_dimensions)

    with pytest.raises(RuntimeError, match="working TIFF dimensions do not match"):
        prepare_job(tagged_oriented_jpeg, tmp_path / "jpeg-job")
    assert not (tmp_path / "jpeg-job/preview.jpg").exists()
    assert not (tmp_path / "jpeg-job/manifest.json").exists()


def test_prepare_rejects_a_thumbnail_scale_raw_working_tiff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)
    real_validate = prepare_module._validate_working_tiff

    def report_thumbnail_dimensions(working: Path) -> tuple[dict[str, int], str, str]:
        _, working_sha256, profile_sha256 = real_validate(working)
        return {"width": 90, "height": 60}, working_sha256, profile_sha256

    monkeypatch.setattr(prepare_module, "_validate_working_tiff", report_thumbnail_dimensions)

    with pytest.raises(RuntimeError, match="outside the allowed border-crop tolerance"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job/intermediate/working.tif").exists()
    assert not (tmp_path / "raw-job/preview.jpg").exists()
    assert not (tmp_path / "raw-job/manifest.json").exists()


def test_prepare_binds_the_working_hash_to_the_validated_tiff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)
    real_validate = prepare_module._validate_working_tiff

    def validate_then_replace(working: Path) -> tuple[dict[str, int], str, str]:
        result = real_validate(working)
        Image.new("RGB", (180, 120), (32, 64, 96)).save(
            working,
            format="TIFF",
            icc_profile=ACESCG_PROFILE.read_bytes(),
        )
        return result

    monkeypatch.setattr(prepare_module, "_validate_working_tiff", validate_then_replace)

    with pytest.raises(RuntimeError, match="working TIFF changed before preview generation"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job/preview.jpg").exists()
    assert not (tmp_path / "raw-job/manifest.json").exists()


def test_prepare_rejects_a_preview_with_unexpected_dimensions(
    tagged_oriented_jpeg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def write_wrong_sized_preview(
        working: Path, preview: Path, *, executable: Path
    ) -> ProcessorIdentity:
        del working, executable
        Image.new("RGB", (100, 100), (32, 64, 96)).save(
            preview,
            format="JPEG",
            icc_profile=SRGB_PROFILE.read_bytes(),
        )
        return {
            "name": "ImageMagick",
            "path": "/test/magick",
            "version": "test",
            "executable_sha256": "a" * 64,
            "environment": MAGICK_ENVIRONMENT,
        }

    monkeypatch.setattr(prepare_module, "make_preview", write_wrong_sized_preview)

    with pytest.raises(RuntimeError, match="preview JPEG dimensions do not match"):
        prepare_job(tagged_oriented_jpeg, tmp_path / "jpeg-job")
    assert not (tmp_path / "jpeg-job/preview.jpg").exists()
    assert not (tmp_path / "jpeg-job/manifest.json").exists()


def test_prepare_rejects_a_preview_without_the_expected_srgb_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, exiftool, rawtherapee, _ = _raw_test_dependencies(tmp_path)

    def write_untagged_preview(
        working: Path, preview: Path, *, executable: Path
    ) -> ProcessorIdentity:
        del working, executable
        Image.new("RGB", (180, 120), (32, 64, 96)).save(preview, format="JPEG")
        return {
            "name": "ImageMagick",
            "path": "/test/magick",
            "version": "test",
            "executable_sha256": "a" * 64,
            "environment": MAGICK_ENVIRONMENT,
        }

    monkeypatch.setattr(prepare_module, "make_preview", write_untagged_preview)

    with pytest.raises(RuntimeError, match="preview JPEG must embed the expected sRGB profile"):
        prepare_job(
            source,
            tmp_path / "raw-job",
            exiftool_executable=exiftool,
            rawtherapee_executable=rawtherapee,
        )
    assert not (tmp_path / "raw-job/preview.jpg").exists()
    assert not (tmp_path / "raw-job/manifest.json").exists()


def test_inspect_rejects_a_raw_source_changed_during_metadata_read(tmp_path: Path) -> None:
    source, _, _, _ = _raw_test_dependencies(tmp_path)
    exiftool = _write_executable(
        tmp_path / "mutating-exiftool",
        """#!/usr/bin/env python3
import json
import pathlib
import sys

if sys.argv[1:] == ["-config", "", "-ver"]:
    print("13.55")
    raise SystemExit

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


def test_inspect_rejects_a_raw_source_replaced_by_symlink_during_metadata_read(
    tmp_path: Path,
) -> None:
    source, _, _, _ = _raw_test_dependencies(tmp_path)
    replacement = tmp_path / "replacement.ARW"
    shutil.copyfile(source, replacement)
    exiftool = _write_executable(
        tmp_path / "replacing-exiftool",
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys

if sys.argv[1:] == ["-config", "", "-ver"]:
    print("13.55")
    raise SystemExit

path = pathlib.Path(sys.argv[-1])
metadata = {{
    "SourceFile": str(path),
    "FileType": "ARW",
    "MIMEType": "image/x-sony-arw",
    "ImageWidth": 180,
    "ImageHeight": 120,
    "Orientation": 1,
    "Make": "SONY",
    "Model": "ILCE-TEST"
}}
path.unlink()
path.symlink_to(pathlib.Path({str(replacement)!r}))
print(json.dumps([metadata]))
""",
    )

    with pytest.raises(ValueError, match="changed during metadata inspection"):
        inspect_photo(source, exiftool_executable=exiftool)
    assert source.is_symlink()


def test_inspect_rejects_an_exiftool_binary_changed_during_metadata_read(tmp_path: Path) -> None:
    source, _, _, _ = _raw_test_dependencies(tmp_path)
    exiftool = _write_executable(
        tmp_path / "mutating-exiftool",
        """#!/usr/bin/env python3
import json
import pathlib
import sys

if sys.argv[1:] == ["-config", "", "-ver"]:
    print("13.55")
    raise SystemExit

metadata = {
    "SourceFile": sys.argv[-1],
    "FileType": "ARW",
    "MIMEType": "image/x-sony-arw",
    "ImageWidth": 180,
    "ImageHeight": 120,
    "Orientation": 1,
    "Make": "SONY",
    "Model": "ILCE-TEST"
}
with pathlib.Path(__file__).open("a", encoding="utf-8") as stream:
    stream.write("\\n# changed during metadata read\\n")
print(json.dumps([metadata]))
""",
    )

    with pytest.raises(RuntimeError, match="ExifTool executable changed during metadata"):
        inspect_photo(source, exiftool_executable=exiftool)
