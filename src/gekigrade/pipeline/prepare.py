from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any, BinaryIO, cast

import numpy as np
from PIL import Image, ImageDraw, TiffImagePlugin, UnidentifiedImageError

from gekigrade.adapters.imagemagick import (
    MAGICK,
    make_preview,
    normalize_jpeg,
    normalize_profiled_tiff,
    preview_dimensions,
)
from gekigrade.adapters.rawtherapee import (
    DEFAULT_RAW_PROFILE,
    EXPECTED_DEFAULT_RAW_PROFILE_SHA256,
    RAWTHERAPEE_CLI,
    RawTherapeeError,
    develop_raw,
    inspect_camera_input_profile,
    inspect_lensfun_support,
    lensfun_database_for_executable,
    rawtherapee_output_profile_for_executable,
)
from gekigrade.analysis.metrics import analyze_srgb
from gekigrade.doctor import (
    ACESCG_PROFILE,
    EXIFTOOL_CLI,
    SRGB_PROFILE,
    build_doctor_report,
    sha256_file,
)
from gekigrade.doctor import EXIFTOOL_ENVIRONMENT as DOCTOR_EXIFTOOL_ENVIRONMENT
from gekigrade.domain.jsonio import write_json
from gekigrade.domain.models import EditPlan
from gekigrade.domain.paths import create_job_directory
from gekigrade.geometry.crops import generate_crop_candidates
from gekigrade.grading.looks import looks_as_json

MAX_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_PIXEL_COUNT = 200_000_000
RAW_MIN_DIMENSION_RETENTION_PERCENT = 95
EXIFTOOL = EXIFTOOL_CLI
EXIFTOOL_ENVIRONMENT = DOCTOR_EXIFTOOL_ENVIRONMENT


def _inspect_jpeg(
    path: Path, *, exiftool_executable: Path = EXIFTOOL
) -> tuple[dict[str, Any], bytes | None]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("source must be a regular, non-symlink JPEG")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("JPEG exceeds the 1 GiB safety limit")
    with path.open("rb") as stream:
        if stream.read(3) != b"\xff\xd8\xff":
            raise ValueError("source does not have a valid JPEG signature")
    try:
        with Image.open(path) as image:
            if image.format != "JPEG":
                raise ValueError("source decoder did not identify JPEG")
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            if width * height > MAX_PIXEL_COUNT:
                raise ValueError("JPEG exceeds the 200 megapixel safety limit")
            orientation = int(image.getexif().get(274, 1))
            profile = image.info.get("icc_profile")
            mode = image.mode
    except (OSError, SyntaxError) as exc:
        raise ValueError(f"JPEG cannot be decoded safely: {exc}") from exc
    if mode == "CMYK" and not profile:
        raise ValueError("unprofiled CMYK JPEG is ambiguous and is not accepted")
    oriented_width, oriented_height = (
        (height, width) if orientation in {5, 6, 7, 8} else (width, height)
    )
    metadata, metadata_reader = _read_exiftool(path, executable=exiftool_executable)
    result = {
        "schema_version": "1.0.0",
        "source_path": str(path.resolve()),
        "source_sha256": sha256_file(path),
        "format": "JPEG",
        "stored_dimensions": {"width": width, "height": height},
        "oriented_dimensions": {"width": oriented_width, "height": oriented_height},
        "exif_orientation": orientation,
        "color_mode": mode,
        "icc_profile": {
            "embedded": bool(profile),
            "byte_length": len(profile) if profile else 0,
            "assumption": None if profile else "untagged RGB JPEG is interpreted as sRGB",
        },
        "capture_metadata": metadata,
        "metadata_reader": metadata_reader,
        "warnings": [] if profile else ["JPEG has no embedded ICC profile; sRGB was assumed"],
    }
    return result, profile


def inspect_jpeg(path: Path) -> dict[str, Any]:
    result, _ = _inspect_jpeg(path)
    return result


def _exiftool_identity(executable: Path) -> dict[str, Any]:
    if not executable.is_file() or executable.stat().st_mode & 0o111 == 0:
        raise RuntimeError("ExifTool is unavailable; run `geki doctor`")
    resolved = executable.resolve(strict=True)
    executable_sha256 = sha256_file(resolved)
    version_result = subprocess.run(
        [str(resolved), "-config", "", "-ver"],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
        stdin=subprocess.DEVNULL,
        env=EXIFTOOL_ENVIRONMENT,
    )
    if version_result.returncode != 0:
        raise RuntimeError(f"ExifTool version check failed: {version_result.stderr.strip()}")
    version = version_result.stdout.strip()
    if not version:
        raise RuntimeError("ExifTool version check returned no version")
    if sha256_file(resolved) != executable_sha256:
        raise RuntimeError("ExifTool executable changed during version inspection")
    return {
        "name": "ExifTool",
        "path": str(resolved),
        "version": version,
        "executable_sha256": executable_sha256,
        "configuration": "disabled",
        "environment": dict(EXIFTOOL_ENVIRONMENT),
    }


def _read_exiftool(
    path: Path, *, executable: Path = EXIFTOOL
) -> tuple[dict[str, Any], dict[str, Any]]:
    identity = _exiftool_identity(executable)
    result = subprocess.run(
        [
            identity["path"],
            "-config",
            "",
            "-json",
            "-n",
            "-FileType",
            "-MIMEType",
            "-ImageWidth",
            "-ImageHeight",
            "-Orientation",
            "-Make",
            "-Model",
            "-LensMake",
            "-LensModel",
            "-ExposureTime",
            "-FNumber",
            "-ISO",
            "-FocalLength",
            "-DateTimeOriginal",
            str(path),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
        stdin=subprocess.DEVNULL,
        env=EXIFTOOL_ENVIRONMENT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ExifTool failed: {result.stderr.strip()}")
    if _exiftool_identity(Path(identity["path"])) != identity:
        raise RuntimeError("ExifTool executable changed during metadata inspection")
    records: object = json.loads(result.stdout)
    if not isinstance(records, list) or not records or not isinstance(records[0], dict):
        return {}, identity
    record = cast(dict[str, Any], records[0])
    record.pop("SourceFile", None)
    return record, identity


def _inspect_raw(path: Path, *, exiftool_executable: Path = EXIFTOOL) -> dict[str, Any]:
    if _source_signature(path) not in {b"II*\x00", b"MM\x00*"}:
        raise ValueError("source does not have a TIFF-based RAW signature")
    source_snapshot = _stable_regular_file_snapshot(path)
    if source_snapshot is None:
        raise ValueError("source must be a stable regular, non-symlink ARW")
    source_sha256, source_size = source_snapshot
    if source_size > MAX_SOURCE_BYTES:
        raise ValueError("ARW exceeds the 1 GiB safety limit")
    metadata, metadata_reader = _read_exiftool(path, executable=exiftool_executable)
    after_metadata = _stable_regular_file_snapshot(path)
    if after_metadata is None or after_metadata[0] != source_sha256:
        raise ValueError("source ARW changed during metadata inspection")
    if metadata.get("FileType") != "ARW" or metadata.get("MIMEType") != "image/x-sony-arw":
        raise ValueError("source metadata does not identify a Sony ARW")
    try:
        width = int(metadata["ImageWidth"])
        height = int(metadata["ImageHeight"])
        orientation = int(metadata.get("Orientation", 1))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("ARW dimensions or orientation are missing or invalid") from exc
    if width <= 0 or height <= 0 or width * height > MAX_PIXEL_COUNT:
        raise ValueError("ARW dimensions exceed the 200 megapixel safety limit")
    if orientation not in range(1, 9):
        raise ValueError("ARW EXIF orientation is outside the supported range")
    if orientation != 1:
        raise ValueError(
            f"RAW EXIF orientation {orientation} is not yet supported safely; "
            "only normal orientation 1 is accepted"
        )
    oriented_width, oriented_height = (
        (height, width) if orientation in {5, 6, 7, 8} else (width, height)
    )
    capture_keys = (
        "Make",
        "Model",
        "LensMake",
        "LensModel",
        "ExposureTime",
        "FNumber",
        "ISO",
        "FocalLength",
        "DateTimeOriginal",
    )
    return {
        "schema_version": "1.0.0",
        "source_path": str(path.resolve()),
        "source_sha256": source_sha256,
        "format": "ARW",
        "stored_dimensions": {"width": width, "height": height},
        "oriented_dimensions": {"width": oriented_width, "height": oriented_height},
        "exif_orientation": orientation,
        "color_mode": "camera-raw",
        "icc_profile": {
            "embedded": False,
            "byte_length": 0,
            "assumption": "camera RAW is developed through the pinned RawTherapee profile",
        },
        "capture_metadata": {key: metadata[key] for key in capture_keys if key in metadata},
        "metadata_reader": metadata_reader,
        "warnings": [],
    }


def _source_signature(path: Path) -> bytes:
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened_status = os.fstat(stream.fileno())
            opened_identity = _file_identity(opened_status)
            if not stat.S_ISREG(opened_status.st_mode):
                raise OSError("source is not a regular file")
            signature = stream.read(4)
            closed_identity = _file_identity(os.fstat(stream.fileno()))
        path_status = path.lstat()
    except OSError as exc:
        raise ValueError("source must be a regular, non-symlink file") from exc
    if (
        not stat.S_ISREG(path_status.st_mode)
        or opened_identity != closed_identity
        or _file_identity(path_status) != opened_identity
    ):
        raise ValueError("source changed during signature inspection")
    return signature


def inspect_photo(path: Path, *, exiftool_executable: Path = EXIFTOOL) -> dict[str, Any]:
    signature = _source_signature(path)
    if signature[:3] == b"\xff\xd8\xff":
        result, _ = _inspect_jpeg(path, exiftool_executable=exiftool_executable)
        return result
    if signature in {b"II*\x00", b"MM\x00*"}:
        return _inspect_raw(path, exiftool_executable=exiftool_executable)
    raise ValueError("unsupported source format; expected JPEG or Sony ARW")


def _embedded_profile(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            profile = image.info.get("icc_profile")
    except (OSError, SyntaxError) as exc:
        raise RuntimeError(f"developed TIFF cannot be decoded: {exc}") from exc
    if not profile:
        raise RuntimeError("developed TIFF has no embedded ICC profile")
    return {
        "embedded": True,
        "byte_length": len(profile),
        "sha256": hashlib.sha256(profile).hexdigest(),
    }


def _sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def _file_identity(status: os.stat_result) -> tuple[int, int, int, int, int]:
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns)


def _validate_working_tiff(path: Path) -> tuple[dict[str, int], str, str]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("working TIFF must be a regular, non-symlink file")
    try:
        with path.open("rb") as stream:
            opened_identity = _file_identity(os.fstat(stream.fileno()))
            working_sha256 = _sha256_stream(stream)
            stream.seek(0)
            with Image.open(stream) as image:
                if not isinstance(image, TiffImagePlugin.TiffImageFile) or image.format != "TIFF":
                    raise OSError("decoded working image is not a TIFF")
                bits_per_sample = image.tag_v2.get(258)
                samples_per_pixel = image.tag_v2.get(277)
                rgb_channels = image.mode == "RGB" and image.getbands() == ("R", "G", "B")
                profile = image.info.get("icc_profile")
                dimensions = {"width": image.width, "height": image.height}
            closed_identity = _file_identity(os.fstat(stream.fileno()))
        path_identity = _file_identity(path.stat())
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as exc:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"working TIFF cannot be decoded safely: {exc}") from exc
    if path.is_symlink() or opened_identity != closed_identity or path_identity != opened_identity:
        path.unlink(missing_ok=True)
        raise RuntimeError("working TIFF changed during structural validation")
    bits = (bits_per_sample,) if isinstance(bits_per_sample, int) else tuple(bits_per_sample or ())
    if not bits or any(bit != 16 for bit in bits):
        path.unlink(missing_ok=True)
        raise RuntimeError("working TIFF must contain 16-bit samples")
    if not rgb_channels or samples_per_pixel != 3:
        path.unlink(missing_ok=True)
        raise RuntimeError("working TIFF must contain exactly three RGB channels")
    profile_sha256 = hashlib.sha256(profile).hexdigest() if isinstance(profile, bytes) else None
    if profile_sha256 is None or profile_sha256 != sha256_file(ACESCG_PROFILE):
        path.unlink(missing_ok=True)
        raise RuntimeError("working TIFF must embed the expected ACEScg profile")
    return dimensions, working_sha256, profile_sha256


def _load_validated_srgb(
    path: Path, *, expected_dimensions: tuple[int, int]
) -> tuple[np.ndarray[Any, np.dtype[np.float32]], str, str]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("preview must be a regular, non-symlink JPEG")
    before = sha256_file(path)
    try:
        with Image.open(path) as image:
            if image.format != "JPEG":
                raise OSError("decoded preview is not a JPEG")
            if image.mode != "RGB" or image.getbands() != ("R", "G", "B"):
                raise OSError("decoded preview is not three-channel RGB")
            if image.size != expected_dimensions:
                path.unlink(missing_ok=True)
                raise RuntimeError(
                    "preview JPEG dimensions do not match the requested resize: "
                    f"expected {expected_dimensions[0]}x{expected_dimensions[1]}, "
                    f"got {image.width}x{image.height}"
                )
            profile = image.info.get("icc_profile")
            image.load()
            pixels = np.asarray(image, dtype=np.float32) / np.float32(255.0)
    except (OSError, SyntaxError, UnidentifiedImageError) as exc:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"preview JPEG cannot be decoded safely: {exc}") from exc
    profile_sha256 = hashlib.sha256(profile).hexdigest() if isinstance(profile, bytes) else None
    if profile_sha256 is None or profile_sha256 != sha256_file(SRGB_PROFILE):
        path.unlink(missing_ok=True)
        raise RuntimeError("preview JPEG must embed the expected sRGB profile")
    if path.is_symlink() or not path.is_file() or sha256_file(path) != before:
        path.unlink(missing_ok=True)
        raise RuntimeError("preview JPEG changed during validation and pixel loading")
    return pixels, before, profile_sha256


def _artifact_matches(path: Path, expected_sha256: str) -> bool:
    try:
        return not path.is_symlink() and path.is_file() and sha256_file(path) == expected_sha256
    except OSError:
        return False


def _stable_regular_file_snapshot(path: Path) -> tuple[str, int] | None:
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened_status = os.fstat(stream.fileno())
            opened_identity = _file_identity(opened_status)
            if not stat.S_ISREG(opened_status.st_mode):
                return None
            if opened_status.st_size > MAX_SOURCE_BYTES:
                raise ValueError("ARW exceeds the 1 GiB safety limit")
            actual_sha256 = _sha256_stream(stream)
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
    return actual_sha256, opened_status.st_size


def _raw_source_matches(path: Path, expected_sha256: str) -> bool:
    try:
        snapshot = _stable_regular_file_snapshot(path)
    except ValueError:
        return False
    return snapshot is not None and snapshot[0] == expected_sha256


def _orientation_axis_matches(expected: dict[str, int], actual: dict[str, int]) -> bool:
    expected_axis = (expected["width"] > expected["height"]) - (
        expected["width"] < expected["height"]
    )
    actual_axis = (actual["width"] > actual["height"]) - (actual["width"] < actual["height"])
    return expected_axis == 0 or expected_axis == actual_axis


def _raw_dimensions_within_border_crop(expected: dict[str, int], actual: dict[str, int]) -> bool:
    return all(
        actual[axis] <= expected[axis]
        and actual[axis] * 100 >= expected[axis] * RAW_MIN_DIMENSION_RETENTION_PERCENT
        for axis in ("width", "height")
    )


def _contact_sheet(preview_path: Path, candidates: list[dict[str, Any]], target: Path) -> None:
    with Image.open(preview_path) as opened:
        image = opened.convert("RGB")
        profile = opened.info.get("icc_profile") or SRGB_PROFILE.read_bytes()
    cells: list[Image.Image] = []
    for index, candidate in enumerate(candidates, start=1):
        bounds = candidate["pixel_bounds"]
        reference = candidate["reference_dimensions"]
        left = round(bounds["left"] * image.width / reference["width"])
        top = round(bounds["top"] * image.height / reference["height"])
        right = round(bounds["right"] * image.width / reference["width"])
        bottom = round(bounds["bottom"] * image.height / reference["height"])
        crop = image.crop((left, top, right, bottom))
        crop.thumbnail((480, 360), Image.Resampling.LANCZOS)
        cell = Image.new("RGB", (500, 410), "#181818")
        cell.paste(crop, ((500 - crop.width) // 2, 28 + (360 - crop.height) // 2))
        ImageDraw.Draw(cell).text((12, 8), f"{index}. {candidate['id']}", fill="white")
        cells.append(cell)
    sheet = Image.new("RGB", (1000, 820), "#101010")
    for index, cell in enumerate(cells):
        sheet.paste(cell, ((index % 2) * 500, (index // 2) * 410))
    sheet.save(target, quality=92, subsampling=0, icc_profile=profile)


def _example_plan(source_sha256: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "description": "Conservative correction",
        "rotation_degrees": 0.0,
        "exposure_ev": 0.0,
        "temperature_mired_shift": 0.0,
        "contrast": 0.0,
        "black_lift": 0.0,
        "highlight_rolloff": 0.1,
        "saturation": 0.0,
        "vignette": 0.0,
        "sharpen": 0.25,
        "crop_id": "feed-4x5-center",
    }
    candidates = []
    for identifier, look_id, strength, description in (
        ("01-natural-clean", "natural-clean", 0.50, "Conservative clean correction"),
        ("02-warm-editorial", "warm-editorial", 0.60, "Warm editorial alternative"),
        ("03-muted-cinematic", "muted-cinematic", 0.55, "Muted cinematic alternative"),
    ):
        candidate = dict(base)
        candidate.update(
            {
                "id": identifier,
                "description": description,
                "look": {"id": look_id, "version": "1.0.0", "strength": strength},
            }
        )
        candidates.append(candidate)
    return {"schema_version": "1.0.0", "source_sha256": source_sha256, "candidates": candidates}


def _artifact_manifest(
    job: Path,
    source: dict[str, Any],
    *,
    working_profile_sha256: str,
    output_profile_sha256: str,
    raw_output_profile_artifact: tuple[Path, str] | None = None,
) -> dict[str, Any]:
    doctor = build_doctor_report(run_color_probe=False)
    doctor["profiles"]["acescg"] = {
        "available": True,
        "path": str(ACESCG_PROFILE),
        "sha256": working_profile_sha256,
    }
    doctor["profiles"]["srgb"] = {
        "available": True,
        "path": str(SRGB_PROFILE),
        "sha256": output_profile_sha256,
    }
    if raw_output_profile_artifact is not None:
        raw_output_profile, raw_output_profile_sha256 = raw_output_profile_artifact
        doctor["profiles"]["rawtherapee_output"]["path"] = str(raw_output_profile)
        doctor["profiles"]["rawtherapee_output"]["sha256"] = raw_output_profile_sha256
    artifacts: dict[str, dict[str, Any]] = {}
    for path in sorted(job.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            relative = str(path.relative_to(job))
            artifacts[relative] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    return {
        "schema_version": "1.0.0",
        "state": "prepared",
        "source_sha256": source["source_sha256"],
        "source_path": source["source_path"],
        "profiles": {
            "working": {"path": str(ACESCG_PROFILE), "sha256": working_profile_sha256},
            "output": {"path": str(SRGB_PROFILE), "sha256": output_profile_sha256},
        },
        "tools": doctor,
        "executed_tools": source.get("processing_tools", {}),
        "artifacts": artifacts,
    }


def prepare_job(
    source_path: Path,
    output_path: Path,
    *,
    exiftool_executable: Path = EXIFTOOL,
    rawtherapee_executable: Path = RAWTHERAPEE_CLI,
    imagemagick_executable: Path = MAGICK,
) -> Path:
    signature = _source_signature(source_path)
    if signature[:3] == b"\xff\xd8\xff":
        source, embedded_profile = _inspect_jpeg(
            source_path, exiftool_executable=exiftool_executable
        )
    elif signature in {b"II*\x00", b"MM\x00*"}:
        source = _inspect_raw(source_path, exiftool_executable=exiftool_executable)
        embedded_profile = None
    else:
        raise ValueError("unsupported source format; expected JPEG or Sony ARW")
    source_format = source["format"]
    inspected_oriented_dimensions = dict(source["oriented_dimensions"])
    if source_format == "ARW" and (
        DEFAULT_RAW_PROFILE.is_symlink()
        or not DEFAULT_RAW_PROFILE.is_file()
        or sha256_file(DEFAULT_RAW_PROFILE) != EXPECTED_DEFAULT_RAW_PROFILE_SHA256
    ):
        raise RawTherapeeError("shipped RAW development profile does not match its pinned identity")
    job = create_job_directory(source_path, output_path)
    working = job / "intermediate/working.tif"
    preview = job / "preview.jpg"
    raw_profile_artifact: tuple[Path, str] | None = None
    raw_output_profile_artifact: tuple[Path, str] | None = None
    if source_format == "JPEG":
        normalization_tool = normalize_jpeg(
            source_path.resolve(),
            working,
            has_profile=embedded_profile is not None,
            executable=imagemagick_executable,
        )
    else:
        raw_work = job / "intermediate/rawtherapee"
        developed = raw_work / "developed.tif"
        lensfun_database = lensfun_database_for_executable(rawtherapee_executable)
        raw_output_profile = rawtherapee_output_profile_for_executable(rawtherapee_executable)
        if raw_output_profile.is_symlink() or not raw_output_profile.is_file():
            raise RuntimeError("expected RawTherapee output ICC profile is unavailable")
        expected_intermediate_profile_sha256 = sha256_file(raw_output_profile)
        raw_output_profile_artifact = (
            raw_output_profile,
            expected_intermediate_profile_sha256,
        )
        camera_input_profile = inspect_camera_input_profile(
            source["capture_metadata"], executable=rawtherapee_executable
        )
        lens_correction = inspect_lensfun_support(
            source["capture_metadata"], database=lensfun_database
        )
        if lens_correction["database_sha256"] is None:
            raise RawTherapeeError("Lensfun database cannot be fingerprinted")
        result = develop_raw(
            source_path,
            developed,
            work_directory=raw_work,
            profile=DEFAULT_RAW_PROFILE,
            executable=rawtherapee_executable,
        )
        if result.profile_sha256 != EXPECTED_DEFAULT_RAW_PROFILE_SHA256 or not _artifact_matches(
            result.profile_path, result.profile_sha256
        ):
            raise RawTherapeeError("shipped RAW development profile changed before execution")
        raw_profile_artifact = (result.profile_path, result.profile_sha256)
        if result.source_sha256 != source["source_sha256"]:
            raise RawTherapeeError("source RAW changed after inspection")
        if (
            inspect_camera_input_profile(
                source["capture_metadata"], executable=rawtherapee_executable
            )
            != camera_input_profile
        ):
            raise RawTherapeeError("RawTherapee camera input resources changed during development")
        if (
            inspect_lensfun_support(source["capture_metadata"], database=lensfun_database)
            != lens_correction
        ):
            raise RawTherapeeError("Lensfun database changed during RAW development")
        if sha256_file(developed) != result.output_sha256:
            raise RawTherapeeError("developed TIFF changed before profile inspection")
        intermediate_profile = _embedded_profile(developed)
        if (
            raw_output_profile.is_symlink()
            or not raw_output_profile.is_file()
            or sha256_file(raw_output_profile) != expected_intermediate_profile_sha256
        ):
            raise RawTherapeeError("RawTherapee output ICC profile changed during development")
        if intermediate_profile["sha256"] != expected_intermediate_profile_sha256:
            raise RuntimeError("developed TIFF ICC profile does not match the expected profile")
        normalization_input_sha256 = sha256_file(developed)
        if normalization_input_sha256 != result.output_sha256:
            raise RawTherapeeError("developed TIFF changed before normalization")
        normalization_tool = normalize_profiled_tiff(
            developed, working, executable=imagemagick_executable
        )
        if sha256_file(developed) != normalization_input_sha256:
            working.unlink(missing_ok=True)
            raise RawTherapeeError("developed TIFF changed during normalization")
        source["raw_development"] = {
            "engine": "RawTherapee",
            "profile_path": str(result.profile_path.relative_to(job)),
            "profile_sha256": result.profile_sha256,
            "camera_input_profile": camera_input_profile,
            "run_report_path": str(result.report_path.relative_to(job)),
            "developed_tiff_sha256": result.output_sha256,
            "inspected_oriented_dimensions": inspected_oriented_dimensions,
            "intermediate_profile": intermediate_profile,
            "expected_intermediate_profile_path": str(raw_output_profile),
            "expected_intermediate_profile_sha256": expected_intermediate_profile_sha256,
            "requested_capabilities": {
                "demosaic": "AMaZE",
                "white_balance": "camera",
                "highlight_recovery": "Coloropp",
                "raw_chromatic_aberration": True,
                "lens_distortion": True,
                "lens_vignetting": True,
                "denoising": False,
                "sharpening": False,
            },
            "lens_correction": lens_correction,
        }
    working_dimensions, working_sha256, working_profile_sha256 = _validate_working_tiff(working)
    if working_sha256 != normalization_tool["output_sha256"]:
        working.unlink(missing_ok=True)
        raise RuntimeError("working TIFF does not match ImageMagick output")
    if source_format == "JPEG" and working_dimensions != source["oriented_dimensions"]:
        working.unlink(missing_ok=True)
        raise RuntimeError("JPEG working TIFF dimensions do not match the oriented source")
    if source_format == "ARW":
        if not _orientation_axis_matches(inspected_oriented_dimensions, working_dimensions):
            working.unlink(missing_ok=True)
            raise RuntimeError(
                "RAW working TIFF orientation does not match the inspected EXIF orientation"
            )
        if not _raw_dimensions_within_border_crop(
            inspected_oriented_dimensions, working_dimensions
        ):
            working.unlink(missing_ok=True)
            raise RuntimeError(
                "RAW working TIFF dimensions are outside the allowed border-crop tolerance"
            )
        source["oriented_dimensions"] = working_dimensions
        source["raw_development"]["working_profile_sha256"] = working_profile_sha256
    if not _artifact_matches(working, working_sha256):
        raise RuntimeError("working TIFF changed before preview generation")
    preview_tool = make_preview(working, preview, executable=imagemagick_executable)
    if not _artifact_matches(working, working_sha256):
        preview.unlink(missing_ok=True)
        raise RuntimeError("working TIFF changed during preview generation")
    source["processing_tools"] = {
        "normalization": normalization_tool,
        "preview": preview_tool,
    }
    expected_preview_dimensions = preview_dimensions(
        working_dimensions["width"], working_dimensions["height"]
    )
    pixels, preview_sha256, output_profile_sha256 = _load_validated_srgb(
        preview, expected_dimensions=expected_preview_dimensions
    )
    if preview_sha256 != preview_tool["output_sha256"]:
        preview.unlink(missing_ok=True)
        raise RuntimeError("preview JPEG does not match ImageMagick output")
    source["prepared_artifacts"] = {
        "working_tiff_sha256": working_sha256,
        "preview_jpeg_sha256": preview_sha256,
    }
    analysis = analyze_srgb(pixels)
    analysis["dimensions"] = {"width": pixels.shape[1], "height": pixels.shape[0]}
    analysis["aspect_ratio"] = pixels.shape[1] / pixels.shape[0]
    candidates = generate_crop_candidates(
        source["oriented_dimensions"]["width"], source["oriented_dimensions"]["height"]
    )
    write_json(job / "source.json", source)
    write_json(job / "analysis.json", analysis)
    write_json(job / "crops/candidates.json", {"schema_version": "1.0.0", "candidates": candidates})
    _contact_sheet(preview, candidates, job / "crops/contact-sheet.jpg")
    write_json(job / "plans/example-plan.json", _example_plan(source["source_sha256"]))
    write_json(job / "looks.json", {"schema_version": "1.0.0", "looks": looks_as_json()})
    write_json(job / "edit-plan.schema.json", EditPlan.model_json_schema())
    if not _artifact_matches(working, working_sha256) or not _artifact_matches(
        preview, preview_sha256
    ):
        raise RuntimeError(
            "prepared working or preview artifact changed before manifest publication"
        )
    if source_format == "ARW" and not _raw_source_matches(source_path, source["source_sha256"]):
        raise RawTherapeeError("source RAW changed during job preparation")
    if raw_profile_artifact is not None and not _artifact_matches(*raw_profile_artifact):
        raise RawTherapeeError("copied RAW development profile changed before manifest publication")
    if raw_output_profile_artifact is not None and not _artifact_matches(
        *raw_output_profile_artifact
    ):
        raise RawTherapeeError("RawTherapee output ICC profile changed before manifest publication")
    if not _artifact_matches(ACESCG_PROFILE, working_profile_sha256) or not _artifact_matches(
        SRGB_PROFILE, output_profile_sha256
    ):
        raise RuntimeError("validated color profile changed before manifest publication")
    manifest_path = job / "manifest.json"
    write_json(
        manifest_path,
        _artifact_manifest(
            job,
            source,
            working_profile_sha256=working_profile_sha256,
            output_profile_sha256=output_profile_sha256,
            raw_output_profile_artifact=raw_output_profile_artifact,
        ),
    )
    if not _artifact_matches(working, working_sha256) or not _artifact_matches(
        preview, preview_sha256
    ):
        manifest_path.unlink(missing_ok=True)
        raise RuntimeError(
            "prepared working or preview artifact changed during manifest publication"
        )
    if not _artifact_matches(ACESCG_PROFILE, working_profile_sha256) or not _artifact_matches(
        SRGB_PROFILE, output_profile_sha256
    ):
        manifest_path.unlink(missing_ok=True)
        raise RuntimeError("validated color profile changed while publishing the job manifest")
    if source_format == "ARW" and not _raw_source_matches(source_path, source["source_sha256"]):
        manifest_path.unlink(missing_ok=True)
        raise RawTherapeeError("source RAW changed while publishing the job manifest")
    if raw_profile_artifact is not None and not _artifact_matches(*raw_profile_artifact):
        manifest_path.unlink(missing_ok=True)
        raise RawTherapeeError(
            "copied RAW development profile changed while publishing the manifest"
        )
    if raw_output_profile_artifact is not None and not _artifact_matches(
        *raw_output_profile_artifact
    ):
        manifest_path.unlink(missing_ok=True)
        raise RawTherapeeError(
            "RawTherapee output ICC profile changed while publishing the manifest"
        )
    return job
