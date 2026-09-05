from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TypedDict

from PIL import Image, TiffImagePlugin, UnidentifiedImageError

from gekigrade.domain.jsonio import write_json

RAWTHERAPEE_CLI = Path("/Applications/RawTherapee.app/Contents/MacOS/rawtherapee-cli")
SUPPORTED_RAWTHERAPEE_VERSION = "5.13"
DEFAULT_RAW_PROFILE = Path(__file__).parent.parent / "raw_profiles/neutral-v1.pp3"
EXPECTED_DEFAULT_RAW_PROFILE_SHA256 = (
    "514d46ae454b127728fb6a9f81791c605f4bb9614de934fcb0c9b86becd5c817"
)
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
    report_sha256: str


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


def path_has_symlink(path: Path) -> bool:
    current = path.absolute()
    while True:
        if current.is_symlink():
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def rawtherapee_bundle_has_symlink(executable: Path) -> bool:
    try:
        bundle = executable.parents[2]
        return bundle.is_symlink() or any(path.is_symlink() for path in bundle.rglob("*"))
    except (IndexError, OSError):
        return True


def _bundle_fingerprint(bundle: Path) -> str:
    try:
        root_before = bundle.lstat()
        if bundle.is_symlink() or not stat.S_ISDIR(root_before.st_mode):
            raise RawTherapeeError("RawTherapee application bundle is not a safe directory")
        paths = sorted(bundle.rglob("*"), key=lambda path: path.relative_to(bundle).as_posix())
        digest = hashlib.sha256()
        identities: dict[str, tuple[int, int, int, int, int]] = {}
        for path in paths:
            relative = path.relative_to(bundle).as_posix()
            status_before = path.lstat()
            identity_before = _file_identity(status_before)
            if stat.S_ISLNK(status_before.st_mode):
                raise RawTherapeeError("RawTherapee application bundle contains symlinks")
            mode = stat.S_IMODE(status_before.st_mode)
            if stat.S_ISDIR(status_before.st_mode):
                record = f"directory\0{relative}\0{mode:o}\n"
            elif stat.S_ISREG(status_before.st_mode):
                file_sha256 = _stable_source_sha256(path)
                if file_sha256 is None or _file_identity(path.lstat()) != identity_before:
                    raise RawTherapeeError(
                        "RawTherapee application bundle changed while it was fingerprinted"
                    )
                record = f"file\0{relative}\0{mode:o}\0{status_before.st_size}\0{file_sha256}\n"
            else:
                raise RawTherapeeError(
                    "RawTherapee application bundle contains a non-regular entry"
                )
            identities[relative] = identity_before
            digest.update(record.encode("utf-8"))

        paths_after = sorted(
            bundle.rglob("*"), key=lambda path: path.relative_to(bundle).as_posix()
        )
        if [path.relative_to(bundle).as_posix() for path in paths_after] != list(identities):
            raise RawTherapeeError(
                "RawTherapee application bundle changed while it was fingerprinted"
            )
        for path in paths_after:
            relative = path.relative_to(bundle).as_posix()
            if _file_identity(path.lstat()) != identities[relative]:
                raise RawTherapeeError(
                    "RawTherapee application bundle changed while it was fingerprinted"
                )
        if _file_identity(bundle.lstat()) != _file_identity(root_before):
            raise RawTherapeeError(
                "RawTherapee application bundle changed while it was fingerprinted"
            )
    except RawTherapeeError:
        raise
    except OSError as exc:
        raise RawTherapeeError(
            f"RawTherapee application bundle could not be fingerprinted: {exc}"
        ) from exc
    return digest.hexdigest()


def _stable_source_sha256(path: Path) -> str | None:
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened_status = os.fstat(stream.fileno())
            opened_identity = _file_identity(opened_status)
            if not stat.S_ISREG(opened_status.st_mode):
                return None
            digest = _sha256_stream(stream)
            closed_identity = _file_identity(os.fstat(stream.fileno()))
        path_status = path.lstat()
    except OSError:
        return None
    if (
        not stat.S_ISREG(path_status.st_mode)
        or opened_identity != closed_identity
        or _file_identity(path_status) != opened_identity
    ):
        return None
    return digest


def _copy_regular_file_exclusive(
    source: Path, destination: Path, *, label: str, mode: int = 0o600
) -> str:
    source_flags = os.O_RDONLY | os.O_NONBLOCK
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
        destination_flags |= os.O_NOFOLLOW
    try:
        source_descriptor = os.open(source, source_flags)
    except OSError as exc:
        raise RawTherapeeError(f"{label} could not be opened safely") from exc
    try:
        destination_descriptor = os.open(destination, destination_flags, mode)
    except OSError as exc:
        os.close(source_descriptor)
        raise RawTherapeeError(f"{label} destination could not be created safely") from exc
    try:
        with (
            os.fdopen(source_descriptor, "rb") as source_stream,
            os.fdopen(destination_descriptor, "wb") as destination_stream,
        ):
            opened_status = os.fstat(source_stream.fileno())
            opened_identity = _file_identity(opened_status)
            if not stat.S_ISREG(opened_status.st_mode):
                raise RawTherapeeError(f"{label} source is not a regular file")
            shutil.copyfileobj(source_stream, destination_stream)
            source_identity_after = _file_identity(os.fstat(source_stream.fileno()))
        source_path_status = source.lstat()
        if (
            not stat.S_ISREG(source_path_status.st_mode)
            or opened_identity != source_identity_after
            or _file_identity(source_path_status) != opened_identity
        ):
            raise RawTherapeeError(f"{label} source changed while it was copied")
        copied_sha256 = _stable_source_sha256(destination)
        if copied_sha256 is None:
            raise RawTherapeeError(f"copied {label} is not a stable regular file")
        return copied_sha256
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _copy_profile_exclusive(source: Path, destination: Path) -> str:
    return _copy_regular_file_exclusive(source, destination, label="RAW development profile")


def _write_run_report(report_path: Path, report: Mapping[str, object], target: Path) -> None:
    try:
        write_json(report_path, report)
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise RawTherapeeError("RawTherapee run report could not be written safely") from exc


def _snapshot_runtime_bundle(executable: Path) -> tuple[Path, Path, str, str]:
    bundle = executable.parents[2]
    if rawtherapee_bundle_has_symlink(executable):
        raise RawTherapeeError("RawTherapee application bundle contains symlinks")
    selected_bundle_sha256 = _bundle_fingerprint(bundle)
    runtime_root = Path(
        tempfile.mkdtemp(prefix="gekigrade-rawtherapee-runtime-", dir="/private/tmp")
    )
    relative_executable = executable.relative_to(bundle)
    clone = runtime_root / bundle.name
    try:
        completed = subprocess.run(
            ["/bin/cp", "-cR", str(bundle), str(clone)],
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
            env={"LC_ALL": "C", "PATH": "/bin:/usr/bin"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        shutil.rmtree(runtime_root, ignore_errors=True)
        raise RawTherapeeError(
            f"RawTherapee runtime bundle could not be snapshotted: {exc}"
        ) from exc
    strategy = "apfs-clone"
    if completed.returncode != 0:
        clone = runtime_root / f"copy-{bundle.name}"
        try:
            copied = subprocess.run(
                ["/bin/cp", "-R", str(bundle), str(clone)],
                capture_output=True,
                check=False,
                text=True,
                timeout=120,
                stdin=subprocess.DEVNULL,
                env={"LC_ALL": "C", "PATH": "/bin:/usr/bin"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            shutil.rmtree(runtime_root, ignore_errors=True)
            raise RawTherapeeError(
                f"RawTherapee runtime bundle could not be snapshotted: {exc}"
            ) from exc
        if copied.returncode != 0:
            shutil.rmtree(runtime_root, ignore_errors=True)
            detail = copied.stderr.strip() or completed.stderr.strip() or "copy failed"
            raise RawTherapeeError(f"RawTherapee runtime bundle could not be snapshotted: {detail}")
        strategy = "full-copy"
    runtime_executable = clone / relative_executable
    if (
        clone.is_symlink()
        or any(path.is_symlink() for path in clone.rglob("*"))
        or path_has_symlink(runtime_executable)
        or not runtime_executable.is_file()
        or runtime_executable.stat().st_mode & 0o111 == 0
    ):
        shutil.rmtree(runtime_root, ignore_errors=True)
        raise RawTherapeeError("RawTherapee runtime snapshot has no safe executable")
    try:
        runtime_bundle_sha256 = _bundle_fingerprint(clone)
        selected_bundle_sha256_after = _bundle_fingerprint(bundle)
    except Exception:
        shutil.rmtree(runtime_root, ignore_errors=True)
        raise
    if (
        runtime_bundle_sha256 != selected_bundle_sha256
        or selected_bundle_sha256_after != selected_bundle_sha256
    ):
        shutil.rmtree(runtime_root, ignore_errors=True)
        raise RawTherapeeError(
            "RawTherapee runtime snapshot does not match the selected application bundle"
        )
    return runtime_root, runtime_executable, strategy, selected_bundle_sha256


def _resource_fingerprint(executable: Path, metadata: dict[str, object]) -> dict[str, object]:
    camera_resources = inspect_camera_resources(executable=executable)
    if not camera_resources["ready"]:
        raise RawTherapeeError("RawTherapee runtime camera resources are not ready")
    camera_input = inspect_camera_input_profile(metadata, executable=executable)
    lensfun = inspect_lensfun_database(database=lensfun_database_for_executable(executable))
    if not lensfun["ready"]:
        raise RawTherapeeError("RawTherapee runtime Lensfun database is not ready")
    output_profile_sha256 = _stable_source_sha256(
        rawtherapee_output_profile_for_executable(executable)
    )
    if output_profile_sha256 is None:
        raise RawTherapeeError("RawTherapee runtime output profile is not stable")
    return {
        "camera_aliases_sha256": camera_resources["aliases_sha256"],
        "camera_constants_sha256": camera_resources["camera_constants_sha256"],
        "camera_profile_key": camera_input["profile_key"],
        "camera_profile_kind": camera_input["resolved_kind"],
        "camera_profile_sha256": camera_input["profile_sha256"],
        "lensfun_database_sha256": lensfun["sha256"],
        "output_profile_sha256": output_profile_sha256,
    }


def _expected_resource_fingerprint(
    camera_input: CameraInputProfile,
    camera_resources: CameraResourceStatus,
    lensfun: ResourceStatus,
    output_profile_sha256: str,
) -> dict[str, object]:
    return {
        "camera_aliases_sha256": camera_resources["aliases_sha256"],
        "camera_constants_sha256": camera_resources["camera_constants_sha256"],
        "camera_profile_key": camera_input["profile_key"],
        "camera_profile_kind": camera_input["resolved_kind"],
        "camera_profile_sha256": camera_input["profile_sha256"],
        "lensfun_database_sha256": lensfun["sha256"],
        "output_profile_sha256": output_profile_sha256,
    }


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
    matching_profile_keys: list[str] = []
    for canonical, alias_values in aliases.items():
        candidates = [canonical, *alias_values]
        if any(candidate.casefold() == wanted for candidate in candidates):
            matching_profile_keys.append(canonical)
    if len(matching_profile_keys) > 1:
        raise RawTherapeeError(f"multiple camera alias mappings match {camera_make_model}")
    if matching_profile_keys:
        profile_key = matching_profile_keys[0]
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
    camera_matches: list[ET.Element] = []
    for root in roots:
        for camera in root.findall("camera"):
            make = _normalized_equipment_name(camera.findtext("maker", ""))
            model = _normalized_equipment_name(camera.findtext("model", ""))
            if wanted_make == make and wanted_camera == model:
                camera_matches.append(camera)
    if len(camera_matches) > 1:
        base["limitation"] = "Lensfun camera match is ambiguous across duplicate entries"
        return base
    if not camera_matches:
        return base
    base["camera_match"] = True
    base["camera_mounts"] = sorted(
        {
            mount.text.strip()
            for mount in camera_matches[0].findall("mount")
            if mount.text and mount.text.strip()
        }
    )
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
    if path_has_symlink(executable):
        raise RawTherapeeError("RawTherapee CLI has a symlinked path component")
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
    capture_metadata: dict[str, object] | None = None,
    expected_camera_input_profile: CameraInputProfile | None = None,
    expected_camera_resources: CameraResourceStatus | None = None,
    expected_lensfun_database: ResourceStatus | None = None,
    expected_output_profile_sha256: str | None = None,
) -> RawDevelopmentResult:
    source, target, work_directory, profile, executable = _validate_inputs(
        source, target, work_directory, profile, executable
    )
    work_directory.mkdir(parents=True, exist_ok=False)
    settings = work_directory / "settings"
    cache = work_directory / "cache"
    temporary = work_directory / "tmp"
    settings.mkdir()
    cache.mkdir()
    temporary.mkdir()
    copied_profile = work_directory / "development.pp3"
    profile_sha256 = _copy_profile_exclusive(profile, copied_profile)
    source_snapshot = work_directory / f"source-snapshot{source.suffix}"
    before = _copy_regular_file_exclusive(
        source,
        source_snapshot,
        label="source RAW snapshot",
        mode=0o400,
    )
    selected_tool_version = _tool_version(executable)
    selected_executable_sha256 = _sha256(executable)
    try:
        (
            runtime_root,
            runtime_executable,
            runtime_snapshot_strategy,
            bundle_sha256,
        ) = _snapshot_runtime_bundle(executable)
    except Exception:
        source_snapshot.unlink(missing_ok=True)
        raise
    expected_resource_inputs = (
        expected_camera_input_profile,
        expected_camera_resources,
        expected_lensfun_database,
        expected_output_profile_sha256,
    )
    if capture_metadata is None and any(item is not None for item in expected_resource_inputs):
        source_snapshot.unlink(missing_ok=True)
        shutil.rmtree(runtime_root, ignore_errors=True)
        raise ValueError("capture metadata is required with expected runtime resources")
    if capture_metadata is not None and any(item is None for item in expected_resource_inputs):
        source_snapshot.unlink(missing_ok=True)
        shutil.rmtree(runtime_root, ignore_errors=True)
        raise ValueError("all expected RawTherapee runtime resources are required")
    target.parent.mkdir(parents=True, exist_ok=True)

    arguments = [
        str(runtime_executable),
        "-o",
        str(target),
        "-q",
        "-p",
        str(copied_profile),
        "-tz",
        "-b16",
        "-c",
        str(source_snapshot),
    ]
    environment = {
        "LC_ALL": "C",
        "OMP_DYNAMIC": "FALSE",
        "OMP_NUM_THREADS": "1",
        "OMP_SCHEDULE": "static",
        "PATH": os.defpath,
        "RT_CACHE": str(cache),
        "RT_SETTINGS": str(settings),
        "TMPDIR": str(temporary),
    }
    try:
        if _stable_source_sha256(source) != before:
            raise RawTherapeeError("source RAW changed while its execution snapshot was created")
        tool_version = _tool_version(runtime_executable)
        if tool_version != SUPPORTED_RAWTHERAPEE_VERSION:
            found = tool_version or "unknown"
            raise RawTherapeeError(
                f"GekiGrade requires version {SUPPORTED_RAWTHERAPEE_VERSION}; found {found}"
            )
        executable_sha256 = _sha256(runtime_executable)
        if selected_tool_version != tool_version or selected_executable_sha256 != executable_sha256:
            raise RawTherapeeError("RawTherapee runtime executable does not match the selection")
        expected_runtime_resources: dict[str, object] | None = None
        runtime_resources_before: dict[str, object] | None = None
        if capture_metadata is not None:
            assert expected_camera_input_profile is not None
            assert expected_camera_resources is not None
            assert expected_lensfun_database is not None
            assert expected_output_profile_sha256 is not None
            expected_runtime_resources = _expected_resource_fingerprint(
                expected_camera_input_profile,
                expected_camera_resources,
                expected_lensfun_database,
                expected_output_profile_sha256,
            )
            runtime_resources_before = _resource_fingerprint(runtime_executable, capture_metadata)
            if runtime_resources_before != expected_runtime_resources:
                raise RawTherapeeError(
                    "RawTherapee runtime resources do not match the accepted fingerprints"
                )
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

        after = _stable_source_sha256(source)
        source_snapshot_after = _stable_source_sha256(source_snapshot)
        tool_version_after = _tool_version(runtime_executable)
        try:
            executable_sha256_after = _sha256(runtime_executable)
        except OSError:
            executable_sha256_after = None
        selected_tool_version_after = _tool_version(executable)
        try:
            selected_executable_sha256_after = _sha256(executable)
        except OSError:
            selected_executable_sha256_after = None
        profile_sha256_after = _stable_source_sha256(copied_profile)
        runtime_resources_after = (
            _resource_fingerprint(runtime_executable, capture_metadata)
            if capture_metadata is not None
            else None
        )
        runtime_bundle_sha256_after = _bundle_fingerprint(runtime_executable.parents[2])
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        source_snapshot.unlink(missing_ok=True)
        shutil.rmtree(runtime_root, ignore_errors=True)
    report_path = work_directory / "run.json"
    report = {
        "schema_version": "1.0.0",
        "tool": "RawTherapee",
        "tool_version": tool_version,
        "tool_version_after": tool_version_after,
        "executable": str(executable),
        "runtime_executable": str(runtime_executable),
        "runtime_snapshot_strategy": runtime_snapshot_strategy,
        "bundle_sha256": bundle_sha256,
        "runtime_bundle_sha256_after": runtime_bundle_sha256_after,
        "executable_sha256": executable_sha256,
        "executable_sha256_after": executable_sha256_after,
        "selected_executable_sha256": selected_executable_sha256,
        "selected_executable_sha256_after": selected_executable_sha256_after,
        "arguments": arguments[1:],
        "environment": environment,
        "duration_seconds": round(time.monotonic() - started, 6),
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "error": execution_error,
        "source_sha256_before": before,
        "source_sha256_after": after,
        "source_snapshot_sha256_before": before,
        "source_snapshot_sha256_after": source_snapshot_after,
        "profile_sha256": profile_sha256,
        "profile_sha256_after": profile_sha256_after,
        "runtime_resources_before": runtime_resources_before,
        "runtime_resources_after": runtime_resources_after,
    }
    _write_run_report(report_path, report, target)

    if before != after:
        target.unlink(missing_ok=True)
        raise RawTherapeeError("source RAW changed while RawTherapee was running")
    if source_snapshot_after != before:
        target.unlink(missing_ok=True)
        raise RawTherapeeError(
            "source RAW execution snapshot changed while RawTherapee was running"
        )
    if tool_version_after != tool_version or executable_sha256_after != executable_sha256:
        target.unlink(missing_ok=True)
        raise RawTherapeeError("RawTherapee executable changed while it was running")
    if (
        selected_tool_version_after != selected_tool_version
        or selected_executable_sha256_after != selected_executable_sha256
    ):
        target.unlink(missing_ok=True)
        raise RawTherapeeError("selected RawTherapee executable changed while it was running")
    if runtime_resources_after != runtime_resources_before:
        target.unlink(missing_ok=True)
        raise RawTherapeeError("RawTherapee runtime resources changed while it was running")
    if runtime_bundle_sha256_after != bundle_sha256:
        target.unlink(missing_ok=True)
        raise RawTherapeeError("RawTherapee runtime bundle changed while it was running")
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
    _write_run_report(report_path, report, target)
    report_sha256 = _stable_source_sha256(report_path)
    if report_sha256 is None:
        target.unlink(missing_ok=True)
        raise RawTherapeeError("RawTherapee run report is not a stable regular file")
    return RawDevelopmentResult(
        output_path=target,
        output_sha256=output_sha256,
        source_sha256=before,
        profile_path=copied_profile,
        profile_sha256=profile_sha256,
        report_path=report_path,
        report_sha256=report_sha256,
    )
