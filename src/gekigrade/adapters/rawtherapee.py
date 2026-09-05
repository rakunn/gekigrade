from __future__ import annotations

import hashlib
import json
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

from PIL import Image, TiffImagePlugin, UnidentifiedImageError

from gekigrade.domain.jsonio import write_json

RAWTHERAPEE_CLI = Path("/Applications/RawTherapee.app/Contents/MacOS/rawtherapee-cli")
SUPPORTED_RAWTHERAPEE_VERSION = "5.13"
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


class CameraInputProfile(TypedDict):
    selection: str
    camera_make_model: str
    profile_key: str
    resolved_kind: str
    profile_path: str | None
    profile_sha256: str | None
    aliases_path: str
    aliases_sha256: str
    camera_constants_path: str
    camera_constants_sha256: str


@dataclass(frozen=True)
class RawDevelopmentResult:
    output_path: Path
    output_sha256: str
    source_sha256: str
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


def _find_profile(directory: Path, profile_key: str, suffixes: set[str]) -> Path | None:
    matches = sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and not path.is_symlink()
        and path.suffix.casefold() in suffixes
        and path.stem.casefold() == profile_key.casefold()
    )
    if len(matches) > 1:
        raise RawTherapeeError(f"multiple camera input profiles match {profile_key}")
    return matches[0] if matches else None


def inspect_camera_input_profile(
    metadata: dict[str, object], *, executable: Path = RAWTHERAPEE_CLI
) -> CameraInputProfile:
    camera_make_model = f"{metadata.get('Make', '')} {metadata.get('Model', '')}".strip()
    if not camera_make_model:
        raise RawTherapeeError("camera make and model are required to resolve the input profile")
    resources = executable.resolve(strict=True).parent.parent / "Resources/share"
    dcp_directory = resources / "dcpprofiles"
    icc_directory = resources / "iccprofiles/input"
    aliases_path = dcp_directory / "camera_model_aliases.json"
    camera_constants_path = resources / "camconst.json"
    for path in (aliases_path, camera_constants_path):
        if path.is_symlink() or not path.is_file():
            raise RawTherapeeError(f"RawTherapee camera resource is unavailable: {path}")
    if not dcp_directory.is_dir() or not icc_directory.is_dir():
        raise RawTherapeeError("RawTherapee camera profile directories are unavailable")
    try:
        aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RawTherapeeError(f"RawTherapee camera aliases cannot be parsed: {exc}") from exc
    if not isinstance(aliases, dict):
        raise RawTherapeeError("RawTherapee camera aliases must be a JSON object")
    profile_key = camera_make_model
    wanted = camera_make_model.casefold()
    for canonical, alias_values in aliases.items():
        if not isinstance(canonical, str) or not isinstance(alias_values, list):
            raise RawTherapeeError("RawTherapee camera aliases contain an invalid entry")
        candidates = [canonical, *(value for value in alias_values if isinstance(value, str))]
        if any(candidate.casefold() == wanted for candidate in candidates):
            profile_key = canonical
            break
    profile_path = _find_profile(dcp_directory, profile_key, {".dcp"})
    resolved_kind = "dcp"
    if profile_path is None:
        profile_path = _find_profile(icc_directory, profile_key, {".icc", ".icm"})
        resolved_kind = "icc" if profile_path is not None else "camera-matrix"
    return {
        "selection": "auto-matched-camera-profile",
        "camera_make_model": camera_make_model,
        "profile_key": profile_key,
        "resolved_kind": resolved_kind,
        "profile_path": str(profile_path) if profile_path is not None else None,
        "profile_sha256": _sha256(profile_path) if profile_path is not None else None,
        "aliases_path": str(aliases_path),
        "aliases_sha256": _sha256(aliases_path),
        "camera_constants_path": str(camera_constants_path),
        "camera_constants_sha256": _sha256(camera_constants_path),
    }


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
    installed_version = _tool_version(executable)
    if installed_version != SUPPORTED_RAWTHERAPEE_VERSION:
        found = installed_version or "unknown"
        raise RawTherapeeError(
            f"GekiGrade requires version {SUPPORTED_RAWTHERAPEE_VERSION}; found {found}"
        )

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
    try:
        with Image.open(target) as image:
            if not isinstance(image, TiffImagePlugin.TiffImageFile):
                raise OSError("decoded image is not a TIFF")
            bits_per_sample = image.tag_v2.get(258)
            samples_per_pixel = image.tag_v2.get(277)
            rgb_channels = image.mode == "RGB" and image.getbands() == ("R", "G", "B")
    except (OSError, SyntaxError, UnidentifiedImageError) as exc:
        target.unlink(missing_ok=True)
        raise RawTherapeeError(f"RawTherapee output TIFF cannot be decoded: {exc}") from exc
    bits = (bits_per_sample,) if isinstance(bits_per_sample, int) else tuple(bits_per_sample or ())
    if not bits or any(bit != 16 for bit in bits):
        target.unlink(missing_ok=True)
        raise RawTherapeeError("RawTherapee output TIFF must contain 16-bit samples")
    if not rgb_channels or samples_per_pixel != 3:
        target.unlink(missing_ok=True)
        raise RawTherapeeError("RawTherapee output TIFF must contain exactly three RGB channels")

    output_sha256 = _sha256(target)
    report["output_sha256"] = output_sha256
    write_json(report_path, report)
    return RawDevelopmentResult(
        output_path=target,
        output_sha256=output_sha256,
        source_sha256=before,
        profile_path=copied_profile,
        profile_sha256=profile_sha256,
        report_path=report_path,
    )
