from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

import numpy as np
from PIL import Image, ImageDraw

from gekigrade.adapters.imagemagick import make_preview, normalize_jpeg
from gekigrade.analysis.metrics import analyze_srgb
from gekigrade.doctor import ACESCG_PROFILE, SRGB_PROFILE, build_doctor_report, sha256_file
from gekigrade.domain.jsonio import write_json
from gekigrade.domain.models import EditPlan
from gekigrade.domain.paths import create_job_directory
from gekigrade.geometry.crops import generate_crop_candidates
from gekigrade.grading.looks import looks_as_json

MAX_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_PIXEL_COUNT = 200_000_000


def _inspect_jpeg(path: Path) -> tuple[dict[str, Any], bytes | None]:
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
    metadata = _read_exiftool(path)
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
        "warnings": [] if profile else ["JPEG has no embedded ICC profile; sRGB was assumed"],
    }
    return result, profile


def inspect_jpeg(path: Path) -> dict[str, Any]:
    result, _ = _inspect_jpeg(path)
    return result


def _read_exiftool(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "/opt/homebrew/bin/exiftool",
            "-json",
            "-n",
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
    records: object = json.loads(result.stdout)
    if not isinstance(records, list) or not records or not isinstance(records[0], dict):
        return {}
    record = cast(dict[str, Any], records[0])
    record.pop("SourceFile", None)
    return record


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
        "artifacts": artifacts,
    }


def prepare_job(source_path: Path, output_path: Path) -> Path:
    source, embedded_profile = _inspect_jpeg(source_path)
    job = create_job_directory(source_path, output_path)
    working = job / "intermediate/working.tif"
    preview = job / "preview.jpg"
    normalize_jpeg(source_path.resolve(), working, has_profile=embedded_profile is not None)
    make_preview(working, preview)
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
    write_json(job / "manifest.json", _artifact_manifest(job, source))
    return job
