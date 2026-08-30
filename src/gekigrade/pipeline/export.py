from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

from PIL import Image

from gekigrade.domain.jsonio import read_json, write_json
from gekigrade.domain.models import EditPlan
from gekigrade.domain.paths import job_child
from gekigrade.grading.engine import read_linear_image
from gekigrade.pipeline.manifests import assert_source_unchanged, refresh_manifest
from gekigrade.pipeline.render import (
    _crop_map,
    evaluate_candidate,
    save_srgb_jpeg,
    validate_plan_model_for_job,
)

Preset = Literal["full-quality", "instagram-feed", "instagram-story"]


def select_candidate(job_path: Path, candidate_id: str) -> Path:
    job = job_path.resolve(strict=True)
    assert_source_unchanged(job)
    metadata: dict[str, Any] = read_json(job_child(job, "candidates/metadata.json"))
    if candidate_id not in metadata["candidates"]:
        raise ValueError(f"candidate was not rendered: {candidate_id}")
    selection = {
        "schema_version": "1.0.0",
        "candidate_id": candidate_id,
        "plan_sha256": metadata["plan_sha256"],
    }
    target = job_child(job, "selection.json")
    write_json(target, selection)
    refresh_manifest(job, state="selected", plan_sha256=metadata["plan_sha256"])
    return target


def _safe_exif(source: dict[str, Any]) -> Image.Exif:
    metadata: dict[str, Any] = source.get("capture_metadata", {})
    exif = Image.Exif()
    mapping = {
        "Make": 271,
        "Model": 272,
        "DateTimeOriginal": 36867,
        "ExposureTime": 33434,
        "FNumber": 33437,
        "ISO": 34855,
        "FocalLength": 37386,
    }
    for source_name, tag in mapping.items():
        if source_name in metadata:
            try:
                exif[tag] = metadata[source_name]
            except (TypeError, ValueError):
                continue
    exif[274] = 1
    return exif


def export_job(
    job_path: Path,
    *,
    preset: str,
    quality: int = 92,
    metadata_policy: str = "safe",
) -> Path:
    if not 1 <= quality <= 100:
        raise ValueError("JPEG quality must be between 1 and 100")
    if metadata_policy not in {"safe", "strip"}:
        raise ValueError(f"unknown metadata policy: {metadata_policy}")
    job = job_path.resolve(strict=True)
    selection: dict[str, Any] = read_json(job_child(job, "selection.json"))
    metadata: dict[str, Any] = read_json(job_child(job, "candidates/metadata.json"))
    if selection["plan_sha256"] != metadata["plan_sha256"]:
        raise ValueError("selection refers to a different rendered plan")
    candidate_data = metadata["candidates"][selection["candidate_id"]]["recipe"]
    plan = validate_plan_model_for_job(job, EditPlan.model_validate(metadata["plan"]))
    candidate = next(item for item in plan.candidates if item.id == selection["candidate_id"])
    if candidate.model_dump(mode="json") != candidate_data:
        raise ValueError("selected candidate metadata no longer matches the validated plan")
    dimensions: tuple[int, int] | None
    if preset == "instagram-feed":
        if candidate.crop_id != "feed-4x5-center":
            raise ValueError("instagram-feed export requires a selected 4:5 crop")
        dimensions = (1080, 1350)
    elif preset == "instagram-story":
        if candidate.crop_id != "story-9x16-center":
            raise ValueError("instagram-story export requires a selected 9:16 crop")
        dimensions = (1080, 1920)
    elif preset == "full-quality":
        dimensions = None
    else:
        raise ValueError(f"unknown export preset: {preset}")
    working = read_linear_image(str(job_child(job, "intermediate/working.tif")))
    crops = _crop_map(job)
    pixels, qa = evaluate_candidate(
        working, candidate, crops[candidate.crop_id], target_dimensions=dimensions
    )
    output = job_child(job, f"output/{preset}.jpg")
    source: dict[str, Any] = read_json(job_child(job, "source.json"))
    exif = _safe_exif(source) if metadata_policy == "safe" else None
    save_srgb_jpeg(pixels, output, quality=quality, exif=exif)
    with Image.open(output) as verified:
        qa["icc_profile_embedded"] = bool(verified.info.get("icc_profile"))
        qa["encoded_width"], qa["encoded_height"] = verified.size
    qa["file_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    report: dict[str, Any] = read_json(job_child(job, "qa/report.json"))
    report["exports"][preset] = qa
    warnings: list[str] = list(report.get("warnings", []))
    if qa["preclamp_low_percent"] > 1.0:
        warnings.append(f"{preset}: pre-clamp low-gamut pixels exceed 1.0%")
    if qa["preclamp_high_percent"] > 1.0:
        warnings.append(f"{preset}: pre-clamp high-gamut pixels exceed 1.0%")
    report["warnings"] = sorted(set(warnings))
    write_json(job_child(job, "qa/report.json"), report)
    refresh_manifest(job, state="exported", plan_sha256=selection["plan_sha256"])
    return output
