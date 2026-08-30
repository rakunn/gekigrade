from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gekigrade.adapters.rawtherapee import (
    RawTherapeeError,
    develop_raw,
    inspect_lensfun_support,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_executable(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_develop_raw_isolates_state_and_records_the_fixed_invocation(tmp_path: Path) -> None:
    source = tmp_path / "photo.arw"
    source.write_bytes(b"II*\x00source-pixels")
    before = _sha256(source)
    profile = tmp_path / "neutral.pp3"
    profile.write_text("[Version]\nAppVersion=5.13\nVersion=353\n", encoding="utf-8")
    executable = _write_executable(
        tmp_path / "fake-rawtherapee",
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

args = sys.argv[1:]
target = pathlib.Path(args[args.index("-o") + 1])
target.write_bytes(b"II*\\x00developed-pixels")
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
    assert report["tool_version"] is None


def test_develop_raw_surfaces_failure_and_does_not_admit_partial_output(tmp_path: Path) -> None:
    source = tmp_path / "photo.arw"
    source.write_bytes(b"II*\x00source-pixels")
    profile = tmp_path / "neutral.pp3"
    profile.write_text("[Version]\nAppVersion=5.13\nVersion=353\n", encoding="utf-8")
    executable = _write_executable(
        tmp_path / "failing-rawtherapee",
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
