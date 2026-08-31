from __future__ import annotations

import hashlib
import json
import plistlib
from pathlib import Path

import pytest
from PIL import Image

from gekigrade.adapters.rawtherapee import (
    RawTherapeeError,
    develop_raw,
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


def test_develop_raw_isolates_state_and_records_the_fixed_invocation(tmp_path: Path) -> None:
    source = tmp_path / "photo.arw"
    Image.new("I;16", (2, 2), 12345).save(source, format="TIFF")
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
    assert report["tool_version"] == "5.13"


def test_develop_raw_surfaces_failure_and_does_not_admit_partial_output(tmp_path: Path) -> None:
    source = tmp_path / "photo.arw"
    Image.new("I;16", (2, 2), 12345).save(source, format="TIFF")
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
    Image.new("I;16", (2, 2), 12345).save(source, format="TIFF")
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


def test_develop_raw_rejects_an_eight_bit_tiff_output(tmp_path: Path) -> None:
    source = tmp_path / "photo.arw"
    Image.new("L", (2, 2), 123).save(source, format="TIFF")
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


def test_lensfun_support_reports_matches_without_claiming_application(tmp_path: Path) -> None:
    database = tmp_path / "mil-sony.xml"
    database.write_text(
        """<lensdatabase>
<camera><maker>Sony</maker><model>ILCE-TEST</model><mount>Sony E</mount></camera>
<lens><maker>Sony</maker><model>FE 24-70mm f/2.8 GM II</model><mount>Sony E</mount>
<calibration><distortion model="ptlens" focal="24" a="0" b="0" c="0"/></calibration>
</lens>
</lensdatabase>
""",
        encoding="utf-8",
    )

    result = inspect_lensfun_support(
        {"Make": "SONY", "Model": "ILCE-TEST", "LensModel": "FE 24-70mm F2.8 GM II"},
        database=database,
    )

    assert result["database_sha256"] == _sha256(database)
    assert result["camera_match"] is True
    assert result["lens_match"] is True
    assert result["requested"] == ["distortion", "vignetting"]
    assert result["supported"] == ["distortion"]
    assert result["all_requested_supported"] is False
    assert result["application_confirmed"] is False
    assert "does not report" in result["limitation"]
