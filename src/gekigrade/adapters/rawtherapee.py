from __future__ import annotations

import hashlib
import os
import plistlib
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from gekigrade.domain.jsonio import write_json

RAWTHERAPEE_CLI = Path("/Applications/RawTherapee.app/Contents/MacOS/rawtherapee-cli")
DEFAULT_RAW_PROFILE = Path(__file__).parent.parent / "raw_profiles/neutral-v1.pp3"
LENSFUN_DATABASE = Path(
    "/Applications/RawTherapee.app/Contents/Resources/share/lensfun/mil-sony.xml"
)
RAWTHERAPEE_OUTPUT_PROFILE = Path(
    "/Applications/RawTherapee.app/Contents/Resources/share/iccprofiles/output/RTv4_Large.icc"
)


class RawTherapeeError(RuntimeError):
    """Raised when deterministic RAW development cannot be completed safely."""


class LensfunSupport(TypedDict):
    database_path: str
    database_sha256: str | None
    camera_match: bool
    lens_match: bool
    requested: list[str]
    supported: list[str]
    all_requested_supported: bool
    application_confirmed: bool
    limitation: str


@dataclass(frozen=True)
class RawDevelopmentResult:
    output_path: Path
    output_sha256: str
    profile_path: Path
    profile_sha256: str
    report_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tool_version(executable: Path) -> str | None:
    plist = executable.parent.parent / "Info.plist"
    if not plist.is_file():
        return None
    try:
        with plist.open("rb") as stream:
            value = plistlib.load(stream).get("CFBundleShortVersionString")
    except (OSError, plistlib.InvalidFileException):
        return None
    return str(value) if value is not None else None


def _normalized_equipment_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def inspect_lensfun_support(
    metadata: dict[str, object], *, database: Path = LENSFUN_DATABASE
) -> LensfunSupport:
    base: LensfunSupport = {
        "database_path": str(database),
        "database_sha256": _sha256(database) if database.is_file() else None,
        "camera_match": False,
        "lens_match": False,
        "requested": ["distortion", "vignetting"],
        "supported": [],
        "all_requested_supported": False,
        "application_confirmed": False,
        "limitation": (
            "RawTherapee CLI does not report whether an automatic Lensfun correction was applied"
        ),
    }
    if not database.is_file():
        base["limitation"] = "Lensfun database is unavailable; correction support is unknown"
        return base
    try:
        root = ET.parse(database).getroot()
    except (OSError, ET.ParseError):
        base["limitation"] = "Lensfun database could not be parsed; correction support is unknown"
        return base

    wanted_make = _normalized_equipment_name(metadata.get("Make", ""))
    wanted_camera = _normalized_equipment_name(metadata.get("Model", ""))
    wanted_lens = _normalized_equipment_name(metadata.get("LensModel", ""))
    for camera in root.findall("camera"):
        make = _normalized_equipment_name(camera.findtext("maker", ""))
        model = _normalized_equipment_name(camera.findtext("model", ""))
        if wanted_make == make and wanted_camera == model:
            base["camera_match"] = True
            break
    for lens in root.findall("lens"):
        model = _normalized_equipment_name(lens.findtext("model", ""))
        if wanted_lens and wanted_lens == model:
            base["lens_match"] = True
            calibration = lens.find("calibration")
            if calibration is not None:
                base["supported"] = sorted(
                    {
                        child.tag
                        for child in calibration
                        if child.tag in {"distortion", "vignetting"}
                    }
                )
            base["all_requested_supported"] = set(base["requested"]).issubset(base["supported"])
            break
    return base


def _validate_inputs(
    source: Path, target: Path, work_directory: Path, profile: Path, executable: Path
) -> tuple[Path, Path, Path, Path, Path]:
    if source.is_symlink() or not source.is_file():
        raise ValueError("RAW source must be a regular, non-symlink file")
    if profile.is_symlink() or not profile.is_file():
        raise ValueError("RAW development profile must be a regular, non-symlink file")
    if not executable.is_file() or executable.stat().st_mode & 0o111 == 0:
        raise RawTherapeeError("RawTherapee CLI is unavailable; run `geki doctor`")

    resolved_source = source.resolve(strict=True)
    resolved_profile = profile.resolve(strict=True)
    resolved_executable = executable.resolve(strict=True)
    resolved_work = work_directory.resolve(strict=False)
    resolved_target = target.resolve(strict=False)
    if resolved_target == resolved_work or not resolved_target.is_relative_to(resolved_work):
        raise ValueError("RAW output must remain inside its isolated work directory")
    if resolved_source.is_relative_to(resolved_work):
        raise ValueError("RAW source must remain outside the writable work directory")
    if resolved_target.exists() or resolved_target.is_symlink():
        raise ValueError(f"RAW output already exists: {resolved_target}")
    return resolved_source, resolved_target, resolved_work, resolved_profile, resolved_executable


def develop_raw(
    source: Path,
    target: Path,
    *,
    work_directory: Path,
    profile: Path,
    executable: Path = RAWTHERAPEE_CLI,
    timeout_seconds: float = 600.0,
) -> RawDevelopmentResult:
    source, target, work_directory, profile, executable = _validate_inputs(
        source, target, work_directory, profile, executable
    )
    work_directory.mkdir(parents=True, exist_ok=False)
    settings = work_directory / "settings"
    cache = work_directory / "cache"
    settings.mkdir()
    cache.mkdir()
    copied_profile = work_directory / "development.pp3"
    shutil.copyfile(profile, copied_profile)
    profile_sha256 = _sha256(copied_profile)
    target.parent.mkdir(parents=True, exist_ok=True)

    arguments = [
        str(executable),
        "-o",
        str(target),
        "-q",
        "-p",
        str(copied_profile),
        "-tz",
        "-b16",
        "-Y",
        "-c",
        str(source),
    ]
    environment = os.environ.copy()
    environment["RT_SETTINGS"] = str(settings)
    environment["RT_CACHE"] = str(cache)
    before = _sha256(source)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
            stdin=subprocess.DEVNULL,
            env=environment,
        )
        returncode: int | None = completed.returncode
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        execution_error: str | None = None
    except (OSError, subprocess.TimeoutExpired) as exc:
        returncode = None
        stdout = ""
        stderr = ""
        execution_error = str(exc)

    after = _sha256(source)
    report_path = work_directory / "run.json"
    report = {
        "schema_version": "1.0.0",
        "tool": "RawTherapee",
        "tool_version": _tool_version(executable),
        "executable": str(executable),
        "executable_sha256": _sha256(executable),
        "arguments": arguments[1:],
        "environment": {"RT_SETTINGS": str(settings), "RT_CACHE": str(cache)},
        "duration_seconds": round(time.monotonic() - started, 6),
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "error": execution_error,
        "source_sha256_before": before,
        "source_sha256_after": after,
        "profile_sha256": profile_sha256,
    }
    write_json(report_path, report)

    if before != after:
        target.unlink(missing_ok=True)
        raise RawTherapeeError("source RAW changed while RawTherapee was running")
    if execution_error is not None:
        target.unlink(missing_ok=True)
        raise RawTherapeeError(f"RawTherapee could not complete: {execution_error}")
    if returncode != 0:
        target.unlink(missing_ok=True)
        detail = stderr or stdout or "unknown RawTherapee error"
        raise RawTherapeeError(f"RawTherapee failed: {detail}")
    if not target.is_file() or target.is_symlink():
        raise RawTherapeeError("RawTherapee did not create the expected TIFF")
    with target.open("rb") as stream:
        if stream.read(4) not in {b"II*\x00", b"MM\x00*"}:
            target.unlink(missing_ok=True)
            raise RawTherapeeError("RawTherapee output is not a TIFF")

    output_sha256 = _sha256(target)
    report["output_sha256"] = output_sha256
    write_json(report_path, report)
    return RawDevelopmentResult(
        output_path=target,
        output_sha256=output_sha256,
        profile_path=copied_profile,
        profile_sha256=profile_sha256,
        report_path=report_path,
    )
