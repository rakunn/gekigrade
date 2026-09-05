from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, cast

import numpy as np
from PIL import Image, ImageDraw

from gekigrade.adapters.imagemagick import (
    MAGICK,
    make_preview,
    normalize_jpeg,
    normalize_profiled_tiff,
)
from gekigrade.adapters.rawtherapee import (
    DEFAULT_RAW_PROFILE,
    RAWTHERAPEE_CLI,
    RAWTHERAPEE_OUTPUT_PROFILE,
    RawTherapeeError,
    develop_raw,
    inspect_camera_input_profile,
    inspect_lensfun_support,
    lensfun_database_for_executable,
)
from gekigrade.analysis.metrics import analyze_srgb
from gekigrade.doctor import ACESCG_PROFILE, SRGB_PROFILE, build_doctor_report, sha256_file
from gekigrade.domain.jsonio import write_json
from gekigrade.domain.models import EditPlan
from gekigrade.domain.paths import create_job_directory
from gekigrade.geometry.crops import generate_crop_candidates
from gekigrade.grading.looks import looks_as_json

MAX_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_PIXEL_COUNT = 200_000_000
EXIFTOOL = Path("/opt/homebrew/bin/exiftool")


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


def _exiftool_identity(executable: Path) -> dict[str, str]:
    if not executable.is_file() or executable.stat().st_mode & 0o111 == 0:
        raise RuntimeError("ExifTool is unavailable; run `geki doctor`")
    resolved = executable.resolve(strict=True)
    executable_sha256 = sha256_file(resolved)
    version_result = subprocess.run(
        [str(resolved), "-ver"],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
        stdin=subprocess.DEVNULL,
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
    }


def _read_exiftool(
    path: Path, *, executable: Path = EXIFTOOL
) -> tuple[dict[str, Any], dict[str, str]]:
    identity = _exiftool_identity(executable)
    result = subprocess.run(
        [
            identity["path"],
            "-json",
            "-n",
            "-FileType",
            "-MIMEType",
            "-ImageWidth",
            "-ImageHeight",
            "-Orientation",
            "-Make",
            "-Model",
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
    if path.is_symlink() or not path.is_file():
        raise ValueError("source must be a regular, non-symlink ARW")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("ARW exceeds the 1 GiB safety limit")
    with path.open("rb") as stream:
        if stream.read(4) not in {b"II*\x00", b"MM\x00*"}:
            raise ValueError("source does not have a TIFF-based RAW signature")
    source_sha256 = sha256_file(path)
    metadata, metadata_reader = _read_exiftool(path, executable=exiftool_executable)
    if sha256_file(path) != source_sha256:
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
    oriented_width, oriented_height = (
        (height, width) if orientation in {5, 6, 7, 8} else (width, height)
    )
    capture_keys = (
        "Make",
        "Model",
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
    if path.is_symlink() or not path.is_file():
        raise ValueError("source must be a regular, non-symlink file")
    with path.open("rb") as stream:
        return stream.read(4)


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


def _load_srgb(path: Path) -> np.ndarray[Any, np.dtype[np.float32]]:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.float32) / np.float32(255.0)


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


def _artifact_manifest(job: Path, source: dict[str, Any]) -> dict[str, Any]:
    doctor = build_doctor_report(run_color_probe=False)
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
            "working": {"path": str(ACESCG_PROFILE), "sha256": sha256_file(ACESCG_PROFILE)},
            "output": {"path": str(SRGB_PROFILE), "sha256": sha256_file(SRGB_PROFILE)},
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
    raw_output_profile: Path = RAWTHERAPEE_OUTPUT_PROFILE,
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
    job = create_job_directory(source_path, output_path)
    working = job / "intermediate/working.tif"
    preview = job / "preview.jpg"
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
        if not raw_output_profile.is_file():
            raise RuntimeError("expected RawTherapee output ICC profile is unavailable")
        expected_intermediate_profile_sha256 = sha256_file(raw_output_profile)
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
        with Image.open(working) as normalized:
            source["oriented_dimensions"] = {
                "width": normalized.width,
                "height": normalized.height,
            }
        source["raw_development"] = {
            "engine": "RawTherapee",
            "profile_path": str(result.profile_path.relative_to(job)),
            "profile_sha256": result.profile_sha256,
            "camera_input_profile": camera_input_profile,
            "run_report_path": str(result.report_path.relative_to(job)),
            "developed_tiff_sha256": result.output_sha256,
            "intermediate_profile": intermediate_profile,
            "expected_intermediate_profile_sha256": expected_intermediate_profile_sha256,
            "working_profile_sha256": sha256_file(ACESCG_PROFILE),
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
    preview_tool = make_preview(working, preview, executable=imagemagick_executable)
    source["processing_tools"] = {
        "normalization": normalization_tool,
        "preview": preview_tool,
    }
    pixels = _load_srgb(preview)
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
    if source_format == "ARW" and sha256_file(source_path) != source["source_sha256"]:
        raise RawTherapeeError("source RAW changed during job preparation")
    manifest_path = job / "manifest.json"
    write_json(manifest_path, _artifact_manifest(job, source))
    if source_format == "ARW" and sha256_file(source_path) != source["source_sha256"]:
        manifest_path.unlink(missing_ok=True)
        raise RawTherapeeError("source RAW changed while publishing the job manifest")
    return job
