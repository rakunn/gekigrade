from __future__ import annotations

import json
import plistlib
from pathlib import Path

import pytest

import gekigrade.doctor as doctor_module
from gekigrade.doctor import build_doctor_report


def test_doctor_confirms_milestone_one_runtime() -> None:
    report = build_doctor_report()

    assert report["ready_for_jpeg"] is True
    assert report["python"]["version"].startswith("3.12.")
    assert report["python"]["openimageio"] == "3.1.16.0"
    assert report["python"]["opencolorio"] == "2.5.2"
    assert report["tools"]["exiftool"]["available"] is True
    assert report["tools"]["imagemagick"]["available"] is True
    assert report["profiles"]["acescg"]["sha256"]
    assert report["profiles"]["srgb"]["sha256"]
    assert report["color_probe"]["repeat_tiff_file_hash_equal"] is True
    assert report["color_probe"]["working_profile_embedded"] is True
    assert report["color_probe"]["ocio_roundtrip_rmse"] < 0.00001


def test_doctor_reports_raw_adapter_separately_from_jpeg_readiness() -> None:
    report = build_doctor_report()

    assert report["tools"]["rawtherapee"]["available"] is True
    assert report["tools"]["rawtherapee"]["version"] == "5.13"
    assert report["profiles"]["raw_development_pp3"]["sha256"]
    assert report["profiles"]["rawtherapee_output"]["sha256"]
    assert report["profiles"]["lensfun_database"]["sha256"]
    assert report["profiles"]["lensfun_database"]["ready"] is True
    assert report["profiles"]["rawtherapee_camera_resources"]["ready"] is True
    assert report["ready_for_raw"] is True
    assert report["raw_status"] == "adapter-ready"


def test_doctor_does_not_mark_an_unpinned_rawtherapee_version_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "RawTherapee.app/Contents/MacOS/rawtherapee-cli"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    plist = executable.parent.parent / "Info.plist"
    plist.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "5.12"}))
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_CLI", executable)
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_PLIST", plist)

    report = build_doctor_report(run_color_probe=False)

    assert report["tools"]["rawtherapee"]["version"] == "5.12"
    assert report["ready_for_raw"] is False
    assert report["raw_status"] == "not-ready"


def test_doctor_reports_malformed_rawtherapee_metadata_as_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "RawTherapee.app/Contents/MacOS/rawtherapee-cli"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    plist = executable.parent.parent / "Info.plist"
    plist.write_text("not-a-plist", encoding="utf-8")
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_CLI", executable)
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_PLIST", plist)

    report = build_doctor_report(run_color_probe=False)

    assert report["tools"]["rawtherapee"]["version"] is None
    assert report["ready_for_raw"] is False
    assert report["raw_status"] == "not-ready"


def test_doctor_requires_parseable_camera_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "RawTherapee.app/Contents/MacOS/rawtherapee-cli"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    plist = executable.parent.parent / "Info.plist"
    plist.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "5.13"}))
    resources = executable.parent.parent / "Resources/share"
    dcp_directory = resources / "dcpprofiles"
    dcp_directory.mkdir(parents=True)
    (dcp_directory / "camera_model_aliases.json").write_text("not-json", encoding="utf-8")
    (resources / "iccprofiles/input").mkdir(parents=True)
    (resources / "camconst.json").write_text(json.dumps({"camera_constants": []}), encoding="utf-8")
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_CLI", executable)
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_PLIST", plist)

    report = build_doctor_report(run_color_probe=False)

    assert report["profiles"]["rawtherapee_camera_resources"]["ready"] is False
    assert "aliases" in report["profiles"]["rawtherapee_camera_resources"]["error"]
    assert report["ready_for_raw"] is False


def test_doctor_rejects_malformed_camera_constants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "RawTherapee.app/Contents/MacOS/rawtherapee-cli"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    plist = executable.parent.parent / "Info.plist"
    plist.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "5.13"}))
    resources = executable.parent.parent / "Resources/share"
    dcp_directory = resources / "dcpprofiles"
    dcp_directory.mkdir(parents=True)
    (dcp_directory / "camera_model_aliases.json").write_text("{}\n", encoding="utf-8")
    (resources / "iccprofiles/input").mkdir(parents=True)
    (resources / "camconst.json").write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_CLI", executable)
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_PLIST", plist)

    report = build_doctor_report(run_color_probe=False)

    assert report["profiles"]["rawtherapee_camera_resources"]["ready"] is False
    assert "camera constants" in report["profiles"]["rawtherapee_camera_resources"]["error"]
    assert report["ready_for_raw"] is False
