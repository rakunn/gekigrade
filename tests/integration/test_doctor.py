from __future__ import annotations

import json
import plistlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import gekigrade.doctor as doctor_module
from gekigrade.doctor import build_doctor_report


def _write_ready_rawtherapee_bundle(
    root: Path, *, output_profile: Path, version: str = "5.13"
) -> Path:
    executable = root / "RawTherapee.app/Contents/MacOS/rawtherapee-cli"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    (executable.parent.parent / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleShortVersionString": version})
    )
    resources = executable.parent.parent / "Resources/share"
    dcp_directory = resources / "dcpprofiles"
    dcp_directory.mkdir(parents=True)
    (dcp_directory / "camera_model_aliases.json").write_text("{}\n", encoding="utf-8")
    (resources / "iccprofiles/input").mkdir(parents=True)
    output_directory = resources / "iccprofiles/output"
    output_directory.mkdir()
    shutil.copyfile(output_profile, output_directory / "RTv4_Large.icc")
    (resources / "camconst.json").write_text(json.dumps({"camera_constants": []}), encoding="utf-8")
    lensfun = resources / "lensfun"
    lensfun.mkdir()
    (lensfun / "minimal.xml").write_text("<lensdatabase></lensdatabase>\n", encoding="utf-8")
    return executable


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


def test_doctor_color_probe_uses_the_preparation_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_environments: list[dict[str, str] | None] = []
    real_run = subprocess.run

    def capture_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = args[0]
        if isinstance(command, list) and "-profile" in command:
            observed_environments.append(kwargs.get("env"))
        return real_run(*args, **kwargs)

    monkeypatch.setenv("MAGICK_CONFIGURE_PATH", "/caller/config")
    monkeypatch.setenv("MAGICK_CODER_MODULE_PATH", "/caller/coders")
    monkeypatch.setattr(subprocess, "run", capture_run)

    report = build_doctor_report()

    assert len(observed_environments) == 2
    assert all(
        environment == doctor_module.MAGICK_ENVIRONMENT for environment in observed_environments
    )
    assert report["color_probe"]["environment"] == doctor_module.MAGICK_ENVIRONMENT


def test_doctor_exiftool_probe_disables_configuration_and_uses_pinned_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_commands: list[list[str]] = []
    observed_environments: list[dict[str, str] | None] = []
    real_run = subprocess.run

    def capture_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = args[0]
        if isinstance(command, list) and command[0] == str(doctor_module.EXIFTOOL_CLI):
            observed_commands.append(command)
            observed_environments.append(kwargs.get("env"))
            return subprocess.CompletedProcess(command, 0, "13.55\n", "")
        return real_run(*args, **kwargs)

    monkeypatch.setenv("HOME", "/caller/home")
    monkeypatch.setenv("EXIFTOOL_HOME", "/caller/exiftool-home")
    monkeypatch.setattr(subprocess, "run", capture_run)

    report = build_doctor_report(run_color_probe=False)

    assert observed_commands == [[str(doctor_module.EXIFTOOL_CLI), "-config", "", "-ver"]]
    assert observed_environments == [
        {"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    ]
    assert report["tools"]["exiftool"]["available"] is True


def test_doctor_reports_raw_adapter_separately_from_jpeg_readiness() -> None:
    report = build_doctor_report()

    assert report["tools"]["rawtherapee"]["available"] is True
    assert report["tools"]["rawtherapee"]["version"] == "5.13"
    assert report["profiles"]["raw_development_pp3"]["sha256"]
    assert report["profiles"]["raw_development_pp3"]["matches_expected"] is True
    assert report["profiles"]["rawtherapee_output"]["sha256"]
    assert report["profiles"]["rawtherapee_output"]["valid"] is True
    assert report["profiles"]["lensfun_database"]["sha256"]
    assert report["profiles"]["lensfun_database"]["ready"] is True
    assert report["profiles"]["rawtherapee_camera_resources"]["ready"] is True
    assert report["ready_for_raw"] is True
    assert report["raw_status"] == "adapter-ready"


def test_doctor_rejects_a_modified_shipped_raw_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    modified_profile = tmp_path / "modified-neutral.pp3"
    modified_profile.write_text("[Version]\nAppVersion=5.13\n", encoding="utf-8")
    monkeypatch.setattr(doctor_module, "DEFAULT_RAW_PROFILE", modified_profile)

    report = build_doctor_report(run_color_probe=False)

    status = report["profiles"]["raw_development_pp3"]
    assert status["available"] is True
    assert status["matches_expected"] is False
    assert report["ready_for_raw"] is False
    assert report["raw_status"] == "not-ready"


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


def test_doctor_rejects_a_symlinked_rawtherapee_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target/RawTherapee.app/Contents/MacOS/rawtherapee-cli"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(0o755)
    linked = tmp_path / "linked/RawTherapee.app/Contents/MacOS/rawtherapee-cli"
    linked.parent.mkdir(parents=True)
    linked.symlink_to(target)
    plist = linked.parent.parent / "Info.plist"
    plist.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "5.13"}))
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_CLI", linked)
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_PLIST", plist)

    report = build_doctor_report(run_color_probe=False)

    assert report["tools"]["rawtherapee"]["available"] is False
    assert report["tools"]["rawtherapee"]["path"] is None
    assert report["tools"]["rawtherapee"]["version"] is None
    assert report["ready_for_raw"] is False
    assert report["raw_status"] == "not-ready"


def test_doctor_rejects_a_symlinked_rawtherapee_path_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _write_ready_rawtherapee_bundle(
        tmp_path / "target",
        output_profile=Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc"),
        version="5.12",
    )
    linked_contents = tmp_path / "linked/RawTherapee.app/Contents"
    linked_contents.mkdir(parents=True)
    (linked_contents / "MacOS").symlink_to(target.parent, target_is_directory=True)
    plist = linked_contents / "Info.plist"
    plist.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "5.13"}))
    linked_executable = linked_contents / "MacOS/rawtherapee-cli"
    assert linked_executable.is_symlink() is False
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_CLI", linked_executable)
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_PLIST", plist)

    report = build_doctor_report(run_color_probe=False)

    assert report["tools"]["rawtherapee"]["available"] is False
    assert report["tools"]["rawtherapee"]["version"] is None
    assert report["ready_for_raw"] is False
    assert report["raw_status"] == "not-ready"


def test_doctor_rejects_a_symlink_anywhere_in_the_rawtherapee_bundle(
    tmp_path: Path,
) -> None:
    executable = _write_ready_rawtherapee_bundle(
        tmp_path,
        output_profile=Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc"),
    )
    external = tmp_path / "external-resource"
    external.write_text("external", encoding="utf-8")
    unrelated = executable.parents[2] / "Contents/Resources/unrelated-link"
    unrelated.symlink_to(external)

    report = build_doctor_report(
        run_color_probe=False,
        rawtherapee_executable=executable,
    )

    assert report["tools"]["rawtherapee"]["available"] is False
    assert report["ready_for_raw"] is False
    assert report["raw_status"] == "not-ready"


def test_doctor_does_not_substitute_path_tools_for_prepare_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor_module, "EXIFTOOL_CLI", tmp_path / "missing-exiftool", raising=False)
    monkeypatch.setattr(
        doctor_module, "IMAGEMAGICK_CLI", tmp_path / "missing-magick", raising=False
    )

    report = build_doctor_report(run_color_probe=False)

    assert report["tools"]["exiftool"]["available"] is False
    assert report["tools"]["imagemagick"]["available"] is False
    assert report["ready_for_jpeg"] is False
    assert report["ready_for_raw"] is False


def test_doctor_rejects_a_symlinked_rawtherapee_output_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "RawTherapee.app/Contents/MacOS/rawtherapee-cli"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    plist = executable.parent.parent / "Info.plist"
    plist.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "5.13"}))
    external_profile = tmp_path / "external.icc"
    external_profile.write_bytes(b"test-profile")
    output_profile = executable.parent.parent / "Resources/share/iccprofiles/output/RTv4_Large.icc"
    output_profile.parent.mkdir(parents=True)
    output_profile.symlink_to(external_profile)
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_CLI", executable)
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_PLIST", plist)

    report = build_doctor_report(run_color_probe=False)

    assert report["profiles"]["rawtherapee_output"]["available"] is False
    assert report["profiles"]["rawtherapee_output"]["sha256"] is None
    assert report["ready_for_raw"] is False


def test_doctor_rejects_a_malformed_rawtherapee_output_profile(
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
    output_profile = resources / "iccprofiles/output/RTv4_Large.icc"
    output_profile.parent.mkdir()
    output_profile.write_bytes(b"not-an-icc-profile")
    (resources / "camconst.json").write_text(json.dumps({"camera_constants": []}), encoding="utf-8")
    lensfun = resources / "lensfun"
    lensfun.mkdir()
    (lensfun / "minimal.xml").write_text("<lensdatabase></lensdatabase>\n", encoding="utf-8")
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_CLI", executable)
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_PLIST", plist)

    report = build_doctor_report(run_color_probe=False)

    status = report["profiles"]["rawtherapee_output"]
    assert status["available"] is True
    assert status["valid"] is False
    assert status["error"]
    assert report["ready_for_raw"] is False
    assert report["raw_status"] == "not-ready"


def test_doctor_requires_an_rgb_display_output_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _write_ready_rawtherapee_bundle(
        tmp_path,
        output_profile=Path("/System/Library/ColorSync/Profiles/Generic CMYK Profile.icc"),
    )
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_CLI", executable)
    monkeypatch.setattr(doctor_module, "RAWTHERAPEE_PLIST", executable.parent.parent / "Info.plist")

    report = build_doctor_report(run_color_probe=False)

    status = report["profiles"]["rawtherapee_output"]
    assert status["available"] is True
    assert status["valid"] is False
    assert status["color_space"] == "CMYK"
    assert status["device_class"] == "prtr"
    assert "RGB display profile" in status["error"]
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
