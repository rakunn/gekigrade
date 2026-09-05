from __future__ import annotations

import hashlib
import json
import plistlib
import shutil
from pathlib import Path

import numpy as np
import OpenImageIO as oiio
import pytest
from PIL import Image

from gekigrade.adapters.rawtherapee import (
    RawTherapeeError,
    develop_raw,
    inspect_camera_input_profile,
    inspect_lensfun_support,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_executable(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


def _write_rawtherapee_app(root: Path, version: str, source: str) -> Path:
    executable = _write_executable(root / "RawTherapee.app/Contents/MacOS/rawtherapee-cli", source)
    (executable.parent.parent / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleShortVersionString": version})
    )
    return executable


def _write_uint16_tiff(path: Path, *, channels: int = 3) -> None:
    buffer = oiio.ImageBuf(oiio.ImageSpec(2, 2, channels, oiio.UINT16))
    pixels = np.full((2, 2, channels), 32768, dtype=np.uint16)
    assert buffer.set_pixels(oiio.ROI(0, 2, 0, 2, 0, 1, 0, channels), pixels)
    assert buffer.write(str(path), fileformat="tiff"), buffer.geterror()


def test_develop_raw_isolates_state_and_records_the_fixed_invocation(tmp_path: Path) -> None:
    source = tmp_path / "photo.arw"
    _write_uint16_tiff(source)
    before = _sha256(source)
    profile = tmp_path / "neutral.pp3"
    profile.write_text("[Version]\nAppVersion=5.13\nVersion=353\n", encoding="utf-8")
    executable = _write_rawtherapee_app(
        tmp_path,
        "5.13",
        """#!/usr/bin/env python3
import json
import os
import pathlib
import shutil
import sys

args = sys.argv[1:]
target = pathlib.Path(args[args.index("-o") + 1])
shutil.copyfile(pathlib.Path(args[-1]), target)
target.with_suffix(".invocation.json").write_text(json.dumps({
    "args": args,
    "settings": os.environ.get("RT_SETTINGS"),
    "cache": os.environ.get("RT_CACHE"),
}), encoding="utf-8")
print("processed deterministically")
""",
    )
    work = tmp_path / "rawtherapee"
    target = work / "developed.tif"

    result = develop_raw(
        source,
        target,
        work_directory=work,
        profile=profile,
        executable=executable,
    )

    assert _sha256(source) == before
    invocation = json.loads(target.with_suffix(".invocation.json").read_text(encoding="utf-8"))
    copied_profile = work / "development.pp3"
    assert invocation["args"] == [
        "-o",
        str(target),
        "-q",
        "-p",
        str(copied_profile),
        "-tz",
        "-b16",
        "-Y",
        "-c",
        str(source.resolve()),
    ]
    assert invocation["settings"] == str(work / "settings")
    assert invocation["cache"] == str(work / "cache")
    assert copied_profile.read_bytes() == profile.read_bytes()
    assert result.output_sha256 == _sha256(target)
    assert result.profile_sha256 == _sha256(profile)
    report = json.loads((work / "run.json").read_text(encoding="utf-8"))
    assert report["returncode"] == 0
    assert report["stdout"] == "processed deterministically"
    assert report["source_sha256_before"] == before
    assert report["source_sha256_after"] == before
    assert report["executable_sha256"] == _sha256(executable)
    assert report["executable_sha256_after"] == _sha256(executable)
    assert report["tool_version"] == "5.13"
    assert report["tool_version_after"] == "5.13"
    assert report["profile_sha256_after"] == _sha256(profile)


def test_develop_raw_surfaces_failure_and_does_not_admit_partial_output(tmp_path: Path) -> None:
    source = tmp_path / "photo.arw"
    _write_uint16_tiff(source)
    profile = tmp_path / "neutral.pp3"
    profile.write_text("[Version]\nAppVersion=5.13\nVersion=353\n", encoding="utf-8")
    executable = _write_rawtherapee_app(
        tmp_path,
        "5.13",
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
pathlib.Path(args[args.index("-o") + 1]).write_bytes(b"partial")
print("unsupported camera data", file=sys.stderr)
raise SystemExit(7)
""",
    )
    work = tmp_path / "rawtherapee"
    target = work / "developed.tif"

    with pytest.raises(RawTherapeeError, match="unsupported camera data"):
        develop_raw(
            source,
            target,
            work_directory=work,
            profile=profile,
            executable=executable,
        )

    assert not target.exists()
    report = json.loads((work / "run.json").read_text(encoding="utf-8"))
    assert report["returncode"] == 7
    assert report["stderr"] == "unsupported camera data"


def test_develop_raw_rejects_an_unpinned_rawtherapee_version(tmp_path: Path) -> None:
    source = tmp_path / "photo.arw"
    _write_uint16_tiff(source)
    profile = tmp_path / "neutral.pp3"
    profile.write_text("[Version]\nAppVersion=5.13\nVersion=353\n", encoding="utf-8")
    executable = _write_rawtherapee_app(
        tmp_path,
        "5.12",
        """#!/usr/bin/env python3
import pathlib
import shutil
import sys

args = sys.argv[1:]
shutil.copyfile(pathlib.Path(args[-1]), pathlib.Path(args[args.index("-o") + 1]))
""",
    )

    with pytest.raises(RawTherapeeError, match=r"requires version 5\.13; found 5\.12"):
        develop_raw(
            source,
            tmp_path / "rawtherapee/developed.tif",
            work_directory=tmp_path / "rawtherapee",
            profile=profile,
            executable=executable,
        )

    assert not (tmp_path / "rawtherapee").exists()


def test_develop_raw_rejects_an_executable_changed_during_the_run(tmp_path: Path) -> None:
    source = tmp_path / "photo.arw"
    _write_uint16_tiff(source)
    profile = tmp_path / "neutral.pp3"
    profile.write_text("[Version]\nAppVersion=5.13\nVersion=353\n", encoding="utf-8")
    executable = _write_rawtherapee_app(
        tmp_path,
        "5.13",
        """#!/usr/bin/env python3
import pathlib
import shutil
import sys

args = sys.argv[1:]
shutil.copyfile(pathlib.Path(args[-1]), pathlib.Path(args[args.index("-o") + 1]))
with pathlib.Path(sys.argv[0]).open("a", encoding="utf-8") as stream:
    stream.write("# replaced during run\\n")
""",
    )
    work = tmp_path / "rawtherapee"
    target = work / "developed.tif"

    with pytest.raises(RawTherapeeError, match="executable changed"):
        develop_raw(
            source,
            target,
            work_directory=work,
            profile=profile,
            executable=executable,
        )

    assert not target.exists()


def test_develop_raw_revalidates_the_version_immediately_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "photo.arw"
    _write_uint16_tiff(source)
    profile = tmp_path / "neutral.pp3"
    profile.write_text("[Version]\nAppVersion=5.13\nVersion=353\n", encoding="utf-8")
    executable = _write_rawtherapee_app(
        tmp_path,
        "5.13",
        """#!/usr/bin/env python3
import pathlib
import shutil
import sys

args = sys.argv[1:]
shutil.copyfile(pathlib.Path(args[-1]), pathlib.Path(args[args.index("-o") + 1]))
""",
    )
    plist = executable.parent.parent / "Info.plist"
    original_copyfile = shutil.copyfile

    def copy_profile_then_upgrade(source_path: Path, target_path: Path) -> None:
        original_copyfile(source_path, target_path)
        if target_path.name == "development.pp3":
            plist.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "5.12"}))

    monkeypatch.setattr(shutil, "copyfile", copy_profile_then_upgrade)
    work = tmp_path / "rawtherapee"
    target = work / "developed.tif"

    with pytest.raises(RawTherapeeError, match=r"requires version 5\.13; found 5\.12"):
        develop_raw(
            source,
            target,
            work_directory=work,
            profile=profile,
            executable=executable,
        )

    assert not target.exists()


def test_develop_raw_rejects_a_copied_profile_changed_during_the_run(tmp_path: Path) -> None:
    source = tmp_path / "photo.arw"
    _write_uint16_tiff(source)
    profile = tmp_path / "neutral.pp3"
    profile.write_text("[Version]\nAppVersion=5.13\nVersion=353\n", encoding="utf-8")
    executable = _write_rawtherapee_app(
        tmp_path,
        "5.13",
        """#!/usr/bin/env python3
import pathlib
import shutil
import sys

args = sys.argv[1:]
shutil.copyfile(pathlib.Path(args[-1]), pathlib.Path(args[args.index("-o") + 1]))
with pathlib.Path(args[args.index("-p") + 1]).open("a", encoding="utf-8") as stream:
    stream.write("# changed during run\\n")
""",
    )
    work = tmp_path / "rawtherapee"
    target = work / "developed.tif"

    with pytest.raises(RawTherapeeError, match="development profile changed"):
        develop_raw(
            source,
            target,
            work_directory=work,
            profile=profile,
            executable=executable,
        )

    assert not target.exists()


def test_develop_raw_rejects_an_eight_bit_tiff_output(tmp_path: Path) -> None:
    source = tmp_path / "photo.arw"
    Image.new("RGB", (2, 2), (123, 123, 123)).save(source, format="TIFF")
    profile = tmp_path / "neutral.pp3"
    profile.write_text("[Version]\nAppVersion=5.13\nVersion=353\n", encoding="utf-8")
    executable = _write_rawtherapee_app(
        tmp_path,
        "5.13",
        """#!/usr/bin/env python3
import pathlib
import shutil
import sys

args = sys.argv[1:]
shutil.copyfile(pathlib.Path(args[-1]), pathlib.Path(args[args.index("-o") + 1]))
""",
    )
    work = tmp_path / "rawtherapee"
    target = work / "developed.tif"

    with pytest.raises(RawTherapeeError, match="16-bit samples"):
        develop_raw(
            source,
            target,
            work_directory=work,
            profile=profile,
            executable=executable,
        )

    assert not target.exists()


def test_develop_raw_rejects_a_sixteen_bit_grayscale_tiff_output(tmp_path: Path) -> None:
    source = tmp_path / "photo.arw"
    _write_uint16_tiff(source, channels=1)
    profile = tmp_path / "neutral.pp3"
    profile.write_text("[Version]\nAppVersion=5.13\nVersion=353\n", encoding="utf-8")
    executable = _write_rawtherapee_app(
        tmp_path,
        "5.13",
        """#!/usr/bin/env python3
import pathlib
import shutil
import sys

args = sys.argv[1:]
shutil.copyfile(pathlib.Path(args[-1]), pathlib.Path(args[args.index("-o") + 1]))
""",
    )
    work = tmp_path / "rawtherapee"
    target = work / "developed.tif"

    with pytest.raises(RawTherapeeError, match="three RGB channels"):
        develop_raw(
            source,
            target,
            work_directory=work,
            profile=profile,
            executable=executable,
        )

    assert not target.exists()


def test_camera_input_profile_records_matrix_fallback_resources(tmp_path: Path) -> None:
    executable = _write_rawtherapee_app(tmp_path, "5.13", "#!/bin/sh\nexit 0\n")
    resources = executable.parent.parent / "Resources/share"
    dcp_directory = resources / "dcpprofiles"
    dcp_directory.mkdir(parents=True)
    aliases = dcp_directory / "camera_model_aliases.json"
    aliases.write_text("{}\n", encoding="utf-8")
    (resources / "iccprofiles/input").mkdir(parents=True)
    camera_constants = resources / "camconst.json"
    camera_constants.write_text('{"camera_constants": []}\n', encoding="utf-8")

    result = inspect_camera_input_profile(
        {"Make": "SONY", "Model": "ILCE-7RM5"}, executable=executable
    )

    assert result["profile_key"] == "SONY ILCE-7RM5"
    assert result["resolved_kind"] == "camera-matrix"
    assert result["profile_path"] is None
    assert result["profile_sha256"] is None
    assert result["aliases_sha256"] == _sha256(aliases)
    assert result["camera_constants_sha256"] == _sha256(camera_constants)


def test_camera_input_profile_rejects_malformed_camera_constants(tmp_path: Path) -> None:
    executable = _write_rawtherapee_app(tmp_path, "5.13", "#!/bin/sh\nexit 0\n")
    resources = executable.parent.parent / "Resources/share"
    dcp_directory = resources / "dcpprofiles"
    dcp_directory.mkdir(parents=True)
    (dcp_directory / "camera_model_aliases.json").write_text("{}\n", encoding="utf-8")
    (resources / "iccprofiles/input").mkdir(parents=True)
    (resources / "camconst.json").write_text("not-json", encoding="utf-8")

    with pytest.raises(RawTherapeeError, match="camera constants cannot be parsed"):
        inspect_camera_input_profile({"Make": "SONY", "Model": "ILCE-7RM5"}, executable=executable)


def test_lensfun_support_reports_matches_without_claiming_application(tmp_path: Path) -> None:
    database = tmp_path / "lensfun"
    database.mkdir()
    wrong_mount_database = database / "mil-canon.xml"
    wrong_mount_database.write_text(
        """<lensdatabase>
<lens><maker>Sigma</maker><model>24-70mm F2.8 DG DN II Art</model><mount>Canon RF</mount>
<calibration>
<vignetting model="pa" focal="24" aperture="2.8" distance="10" k1="0" k2="0" k3="0"/>
</calibration>
</lens>
</lensdatabase>
""",
        encoding="utf-8",
    )
    camera_database = database / "mil-sony.xml"
    camera_database.write_text(
        """<lensdatabase>
<camera><maker>Sony</maker><model>ILCE-TEST</model><mount>Sony E</mount></camera>
</lensdatabase>
""",
        encoding="utf-8",
    )
    lens_database = database / "mil-sigma.xml"
    lens_database.write_text(
        """<lensdatabase>
<lens><maker>Sigma</maker><model>24-70mm F2.8 DG DN II Art</model><mount>Sony E</mount>
<calibration><distortion model="ptlens" focal="24" a="0" b="0" c="0"/></calibration>
</lens>
</lensdatabase>
""",
        encoding="utf-8",
    )

    result = inspect_lensfun_support(
        {
            "Make": "SONY",
            "Model": "ILCE-TEST",
            "LensModel": "24-70mm F2.8 DG DN II Art",
        },
        database=database,
    )

    assert result["database_sha256"]
    assert {Path(item["path"]).name for item in result["database_files"]} == {
        "mil-canon.xml",
        "mil-sony.xml",
        "mil-sigma.xml",
    }
    assert {item["sha256"] for item in result["database_files"]} == {
        _sha256(wrong_mount_database),
        _sha256(camera_database),
        _sha256(lens_database),
    }
    assert result["camera_match"] is True
    assert result["camera_mounts"] == ["Sony E"]
    assert result["lens_match"] is True
    assert result["lens_mounts"] == ["Sony E"]
    assert result["requested"] == ["distortion", "vignetting"]
    assert result["supported"] == ["distortion"]
    assert result["all_requested_supported"] is False
    assert result["application_confirmed"] is False
    assert "does not report" in result["limitation"]
