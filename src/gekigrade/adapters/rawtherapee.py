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
from typing import BinaryIO, TypedDict

from PIL import Image, TiffImagePlugin, UnidentifiedImageError

from gekigrade.domain.jsonio import write_json

RAWTHERAPEE_CLI = Path("/Applications/RawTherapee.app/Contents/MacOS/rawtherapee-cli")
SUPPORTED_RAWTHERAPEE_VERSION = "5.13"
DEFAULT_RAW_PROFILE = Path(__file__).parent.parent / "raw_profiles/neutral-v1.pp3"
LENSFUN_DATABASE = Path("/Applications/RawTherapee.app/Contents/Resources/share/lensfun")
RAWTHERAPEE_OUTPUT_PROFILE = Path(
    "/Applications/RawTherapee.app/Contents/Resources/share/iccprofiles/output/RTv4_Large.icc"
)


class RawTherapeeError(RuntimeError):
    """Raised when deterministic RAW development cannot be completed safely."""


class FingerprintedFile(TypedDict):
    path: str
    sha256: str


class LensfunSupport(TypedDict):
    database_path: str
    database_sha256: str | None
    database_files: list[FingerprintedFile]
    camera_match: bool
    camera_mounts: list[str]
    lens_match: bool
    lens_maker: str | None
    lens_mounts: list[str]
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


class ResourceStatus(TypedDict):
    available: bool
    ready: bool
    path: str
    sha256: str | None
    files: list[FingerprintedFile]
    error: str | None


class CameraResourceStatus(TypedDict):
    available: bool
    ready: bool
    dcp_directory: str
    input_icc_directory: str
    aliases_path: str
    aliases_sha256: str | None
    camera_constants_path: str
    camera_constants_sha256: str | None
    error: str | None


@dataclass(frozen=True)
class RawDevelopmentResult:
    output_path: Path
    output_sha256: str
    source_sha256: str
    profile_path: Path
    profile_sha256: str
    report_path: Path


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return _sha256_stream(stream)


def _sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def _file_identity(status: os.stat_result) -> tuple[int, int, int, int, int]:
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns)


def _validate_developed_tiff(target: Path) -> str:
    if target.is_symlink() or not target.is_file():
        raise RawTherapeeError("RawTherapee did not create a regular, non-symlink TIFF")
    try:
        with target.open("rb") as stream:
            opened_identity = _file_identity(os.fstat(stream.fileno()))
            if stream.read(4) not in {b"II*\x00", b"MM\x00*"}:
                raise RawTherapeeError("RawTherapee output is not a TIFF")
            stream.seek(0)
            output_sha256 = _sha256_stream(stream)
            stream.seek(0)
            with Image.open(stream) as image:
                if not isinstance(image, TiffImagePlugin.TiffImageFile):
                    raise OSError("decoded image is not a TIFF")
                bits_per_sample = image.tag_v2.get(258)
                samples_per_pixel = image.tag_v2.get(277)
                rgb_channels = image.mode == "RGB" and image.getbands() == ("R", "G", "B")
            closed_identity = _file_identity(os.fstat(stream.fileno()))
        path_identity = _file_identity(target.stat())
    except RawTherapeeError:
        target.unlink(missing_ok=True)
        raise
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as exc:
        target.unlink(missing_ok=True)
        raise RawTherapeeError(f"RawTherapee output TIFF cannot be decoded: {exc}") from exc
    if (
        target.is_symlink()
        or opened_identity != closed_identity
        or path_identity != opened_identity
    ):
        target.unlink(missing_ok=True)
        raise RawTherapeeError("RawTherapee output changed during output validation")
    bits = (bits_per_sample,) if isinstance(bits_per_sample, int) else tuple(bits_per_sample or ())
    if not bits or any(bit != 16 for bit in bits):
        target.unlink(missing_ok=True)
        raise RawTherapeeError("RawTherapee output TIFF must contain 16-bit samples")
    if not rgb_channels or samples_per_pixel != 3:
        target.unlink(missing_ok=True)
        raise RawTherapeeError("RawTherapee output TIFF must contain exactly three RGB channels")
    return output_sha256


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


def _camera_resource_paths(executable: Path) -> tuple[Path, Path, Path, Path]:
    resources = executable.resolve(strict=True).parent.parent / "Resources/share"
    dcp_directory = resources / "dcpprofiles"
    icc_directory = resources / "iccprofiles/input"
    aliases_path = dcp_directory / "camera_model_aliases.json"
    camera_constants_path = resources / "camconst.json"
    return dcp_directory, icc_directory, aliases_path, camera_constants_path


def lensfun_database_for_executable(executable: Path = RAWTHERAPEE_CLI) -> Path:
    return executable.resolve(strict=False).parent.parent / "Resources/share/lensfun"


def rawtherapee_output_profile_for_executable(
    executable: Path = RAWTHERAPEE_CLI,
) -> Path:
    return (
        executable.resolve(strict=False).parent.parent
        / "Resources/share/iccprofiles/output/RTv4_Large.icc"
    )


def _load_json_object(path: Path, label: str) -> dict[object, object]:
    if path.is_symlink() or not path.is_file():
        raise RawTherapeeError(f"RawTherapee {label} is unavailable: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RawTherapeeError(f"RawTherapee {label} cannot be parsed: {exc}") from exc
    if not isinstance(value, dict):
        raise RawTherapeeError(f"RawTherapee {label} must be a JSON object")
    return value


def _load_camera_aliases(path: Path) -> dict[str, list[str]]:
    value = _load_json_object(path, "camera aliases")
    aliases: dict[str, list[str]] = {}
    for canonical, alias_values in value.items():
        if (
            not isinstance(canonical, str)
            or not isinstance(alias_values, list)
            or not all(isinstance(alias, str) for alias in alias_values)
        ):
            raise RawTherapeeError("RawTherapee camera aliases contain an invalid entry")
        aliases[canonical] = alias_values
    return aliases


def _strip_json_comments(value: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(value):
        character = value[index]
        if in_string:
            result.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            result.append(character)
            index += 1
            continue
        next_character = value[index + 1] if index + 1 < len(value) else ""
        if character == "/" and next_character == "/":
            result.extend((" ", " "))
            index += 2
            while index < len(value) and value[index] not in "\r\n":
                result.append(" ")
                index += 1
            continue
        if character == "/" and next_character == "*":
            result.extend((" ", " "))
            index += 2
            while index + 1 < len(value) and value[index : index + 2] != "*/":
                result.append(value[index] if value[index] in "\r\n" else " ")
                index += 1
            if index + 1 >= len(value):
                raise RawTherapeeError(
                    "RawTherapee camera constants contain an unterminated comment"
                )
            result.extend((" ", " "))
            index += 2
            continue
        result.append(character)
        index += 1
    return "".join(result)


def _load_camera_constants(path: Path) -> list[dict[object, object]]:
    if path.is_symlink() or not path.is_file():
        raise RawTherapeeError(f"RawTherapee camera constants are unavailable: {path}")
    try:
        value = json.loads(_strip_json_comments(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RawTherapeeError(f"RawTherapee camera constants cannot be parsed: {exc}") from exc
    if not isinstance(value, dict):
        raise RawTherapeeError("RawTherapee camera constants must be a JSON object")
    constants = value.get("camera_constants")
    if not isinstance(constants, list) or not all(isinstance(item, dict) for item in constants):
        raise RawTherapeeError(
            "RawTherapee camera constants must contain a camera_constants object list"
        )
    return constants


def inspect_camera_resources(*, executable: Path = RAWTHERAPEE_CLI) -> CameraResourceStatus:
    try:
        dcp_directory, icc_directory, aliases_path, camera_constants_path = _camera_resource_paths(
            executable
        )
    except (OSError, RuntimeError) as exc:
        unresolved = executable.resolve(strict=False).parent.parent / "Resources/share"
        return {
            "available": False,
            "ready": False,
            "dcp_directory": str(unresolved / "dcpprofiles"),
            "input_icc_directory": str(unresolved / "iccprofiles/input"),
            "aliases_path": str(unresolved / "dcpprofiles/camera_model_aliases.json"),
            "aliases_sha256": None,
            "camera_constants_path": str(unresolved / "camconst.json"),
            "camera_constants_sha256": None,
            "error": f"RawTherapee executable cannot be resolved: {exc}",
        }
    base: CameraResourceStatus = {
        "available": False,
        "ready": False,
        "dcp_directory": str(dcp_directory),
        "input_icc_directory": str(icc_directory),
        "aliases_path": str(aliases_path),
        "aliases_sha256": None,
        "camera_constants_path": str(camera_constants_path),
        "camera_constants_sha256": None,
        "error": None,
    }
    if (
        dcp_directory.is_symlink()
        or not dcp_directory.is_dir()
        or icc_directory.is_symlink()
        or not icc_directory.is_dir()
    ):
        base["error"] = "RawTherapee camera profile directories are unavailable"
        return base
    base["available"] = aliases_path.is_file() and camera_constants_path.is_file()
    try:
        _load_camera_aliases(aliases_path)
        _load_camera_constants(camera_constants_path)
        base["aliases_sha256"] = _sha256(aliases_path)
        base["camera_constants_sha256"] = _sha256(camera_constants_path)
    except (OSError, RawTherapeeError) as exc:
        base["error"] = str(exc)
        return base
    base["ready"] = True
    return base


def inspect_camera_input_profile(
    metadata: dict[str, object], *, executable: Path = RAWTHERAPEE_CLI
) -> CameraInputProfile:
    camera_make_model = f"{metadata.get('Make', '')} {metadata.get('Model', '')}".strip()
    if not camera_make_model:
        raise RawTherapeeError("camera make and model are required to resolve the input profile")
    dcp_directory, icc_directory, aliases_path, camera_constants_path = _camera_resource_paths(
        executable
    )
    if (
        dcp_directory.is_symlink()
        or not dcp_directory.is_dir()
        or icc_directory.is_symlink()
        or not icc_directory.is_dir()
    ):
        raise RawTherapeeError("RawTherapee camera profile directories are unavailable")
    aliases = _load_camera_aliases(aliases_path)
    _load_camera_constants(camera_constants_path)
    profile_key = camera_make_model
    wanted = camera_make_model.casefold()
    for canonical, alias_values in aliases.items():
        candidates = [canonical, *alias_values]
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


def _lensfun_files(database: Path) -> tuple[Path, list[Path]]:
    if database.is_symlink() or not database.is_dir():
        raise RawTherapeeError("Lensfun database directory is unavailable")
    root = database.resolve(strict=True)
    candidates = sorted(root.rglob("*.xml"))
    if not candidates:
        raise RawTherapeeError("Lensfun database contains no XML files")
    if any(path.is_symlink() or not path.is_file() for path in candidates):
        raise RawTherapeeError("Lensfun database contains an unsafe XML path")
    return root, candidates


def _collection_fingerprint(root: Path, paths: list[Path]) -> tuple[str, list[FingerprintedFile]]:
    digest = hashlib.sha256()
    files: list[FingerprintedFile] = []
    for path in paths:
        file_sha256 = _sha256(path)
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_sha256.encode("ascii"))
        digest.update(b"\n")
        files.append({"path": str(path), "sha256": file_sha256})
    return digest.hexdigest(), files


def inspect_lensfun_database(*, database: Path = LENSFUN_DATABASE) -> ResourceStatus:
    base: ResourceStatus = {
        "available": database.is_dir() and not database.is_symlink(),
        "ready": False,
        "path": str(database),
        "sha256": None,
        "files": [],
        "error": None,
    }
    try:
        root, paths = _lensfun_files(database)
        database_sha256, files = _collection_fingerprint(root, paths)
        for path in paths:
            ET.parse(path)
    except (OSError, ET.ParseError, RawTherapeeError) as exc:
        base["error"] = str(exc)
        return base
    base["path"] = str(root)
    base["sha256"] = database_sha256
    base["files"] = files
    base["ready"] = True
    return base


def inspect_lensfun_support(
    metadata: dict[str, object], *, database: Path = LENSFUN_DATABASE
) -> LensfunSupport:
    database_status = inspect_lensfun_database(database=database)
    base: LensfunSupport = {
        "database_path": database_status["path"],
        "database_sha256": database_status["sha256"],
        "database_files": database_status["files"],
        "camera_match": False,
        "camera_mounts": [],
        "lens_match": False,
        "lens_maker": None,
        "lens_mounts": [],
        "requested": ["distortion", "vignetting"],
        "supported": [],
        "all_requested_supported": False,
        "application_confirmed": False,
        "limitation": (
            "RawTherapee CLI does not report whether an automatic Lensfun correction was applied"
        ),
    }
    if not database_status["ready"]:
        detail = database_status["error"] or "unknown database error"
        base["limitation"] = f"Lensfun database is unavailable: {detail}"
        return base

    wanted_make = _normalized_equipment_name(metadata.get("Make", ""))
    wanted_camera = _normalized_equipment_name(metadata.get("Model", ""))
    wanted_lens_make = _normalized_equipment_name(metadata.get("LensMake", ""))
    wanted_lens = _normalized_equipment_name(metadata.get("LensModel", ""))
    roots = [ET.parse(item["path"]).getroot() for item in database_status["files"]]
    camera_mounts: set[str] = set()
    for root in roots:
        for camera in root.findall("camera"):
            make = _normalized_equipment_name(camera.findtext("maker", ""))
            model = _normalized_equipment_name(camera.findtext("model", ""))
            if wanted_make == make and wanted_camera == model:
                base["camera_match"] = True
                camera_mounts.update(
                    mount.text.strip()
                    for mount in camera.findall("mount")
                    if mount.text and mount.text.strip()
                )
    base["camera_mounts"] = sorted(camera_mounts)
    normalized_camera_mounts = {
        _normalized_equipment_name(mount) for mount in base["camera_mounts"]
    }
    if not normalized_camera_mounts:
        return base
    lens_matches: list[tuple[ET.Element, str, list[str]]] = []
    for root in roots:
        for lens in root.findall("lens"):
            maker = lens.findtext("maker", "").strip()
            model = _normalized_equipment_name(lens.findtext("model", ""))
            lens_mounts = sorted(
                {
                    mount.text.strip()
                    for mount in lens.findall("mount")
                    if mount.text and mount.text.strip()
                }
            )
            normalized_lens_mounts = {_normalized_equipment_name(mount) for mount in lens_mounts}
            if (
                wanted_lens
                and wanted_lens == model
                and normalized_camera_mounts.intersection(normalized_lens_mounts)
            ):
                lens_matches.append((lens, maker, lens_mounts))
    if wanted_lens_make:
        lens_matches = [
            match
            for match in lens_matches
            if _normalized_equipment_name(match[1]) == wanted_lens_make
        ]
    elif len({_normalized_equipment_name(match[1]) for match in lens_matches}) > 1:
        base["limitation"] = "Lensfun lens match is ambiguous across makers; LensMake is required"
        return base
    if len(lens_matches) > 1:
        base["limitation"] = "Lensfun lens match is ambiguous across duplicate entries"
        return base
    if not lens_matches:
        return base
    lens, maker, lens_mounts = lens_matches[0]
    base["lens_match"] = True
    base["lens_maker"] = maker
    base["lens_mounts"] = lens_mounts
    calibration = lens.find("calibration")
    if calibration is not None:
        base["supported"] = sorted(
            {child.tag for child in calibration if child.tag in {"distortion", "vignetting"}}
        )
    base["all_requested_supported"] = set(base["requested"]).issubset(base["supported"])
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
    tool_version = _tool_version(executable)
    if tool_version != SUPPORTED_RAWTHERAPEE_VERSION:
        found = tool_version or "unknown"
        raise RawTherapeeError(
            f"GekiGrade requires version {SUPPORTED_RAWTHERAPEE_VERSION}; found {found}"
        )
    executable_sha256 = _sha256(executable)
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
    tool_version_after = _tool_version(executable)
    try:
        executable_sha256_after = _sha256(executable)
    except OSError:
        executable_sha256_after = None
    try:
        profile_sha256_after = _sha256(copied_profile)
    except OSError:
        profile_sha256_after = None
    report_path = work_directory / "run.json"
    report = {
        "schema_version": "1.0.0",
        "tool": "RawTherapee",
        "tool_version": tool_version,
        "tool_version_after": tool_version_after,
        "executable": str(executable),
        "executable_sha256": executable_sha256,
        "executable_sha256_after": executable_sha256_after,
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
        "profile_sha256_after": profile_sha256_after,
    }
    write_json(report_path, report)

    if before != after:
        target.unlink(missing_ok=True)
        raise RawTherapeeError("source RAW changed while RawTherapee was running")
    if tool_version_after != tool_version or executable_sha256_after != executable_sha256:
        target.unlink(missing_ok=True)
        raise RawTherapeeError("RawTherapee executable changed while it was running")
    if profile_sha256_after != profile_sha256:
        target.unlink(missing_ok=True)
        raise RawTherapeeError("RAW development profile changed while RawTherapee was running")
    if execution_error is not None:
        target.unlink(missing_ok=True)
        raise RawTherapeeError(f"RawTherapee could not complete: {execution_error}")
    if returncode != 0:
        target.unlink(missing_ok=True)
        detail = stderr or stdout or "unknown RawTherapee error"
        raise RawTherapeeError(f"RawTherapee failed: {detail}")
    output_sha256 = _validate_developed_tiff(target)
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
