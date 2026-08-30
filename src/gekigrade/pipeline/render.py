from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from pydantic import ValidationError

from gekigrade.doctor import SRGB_PROFILE
from gekigrade.domain.jsonio import canonical_json_bytes, read_json, write_json
from gekigrade.domain.models import CandidateRecipe, EditPlan
from gekigrade.domain.paths import job_child
from gekigrade.grading.engine import (
    apply_recipe,
    crop_normalized,
    linear_to_encoded_srgb,
    read_linear_image,
    resize_float,
    sharpen_uint8,
)
from gekigrade.grading.looks import LookError, get_look
from gekigrade.pipeline.manifests import assert_source_unchanged, refresh_manifest


class PlanValidationError(ValueError):
    """Raised before rendering when a plan violates a schema or job invariant."""


PRECLAMP_WARNING_PERCENT = 1.0


def _crop_map(job: Path) -> dict[str, dict[str, Any]]:
    document = read_json(job_child(job, "crops/candidates.json"))
    return {candidate["id"]: candidate for candidate in document["candidates"]}


def validate_plan_for_job(job: Path, plan_path: Path) -> EditPlan:
    try:
        plan = EditPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise PlanValidationError(f"plan schema validation failed: {exc}") from exc
    return validate_plan_model_for_job(job, plan)


def validate_plan_model_for_job(job: Path, plan: EditPlan) -> EditPlan:
    manifest = assert_source_unchanged(job)
    if plan.source_sha256 != manifest["source_sha256"]:
        raise PlanValidationError("plan source checksum does not match the prepared job")
    crops = _crop_map(job)
    for candidate in plan.candidates:
        if candidate.crop_id not in crops:
            raise PlanValidationError(f"unknown crop: {candidate.crop_id}")
        try:
            look = get_look(candidate.look.id, candidate.look.version)
        except LookError as exc:
            raise PlanValidationError(str(exc)) from exc
        minimum, maximum = look.strength_range
        if not minimum <= candidate.look.strength <= maximum:
            raise PlanValidationError(
                f"look strength for {look.id} must be between {minimum} and {maximum}"
            )
    return plan


def _target_dimensions(width: int, height: int, max_edge: int | None) -> tuple[int, int]:
    if max_edge is None or max(width, height) <= max_edge:
        return width, height
    scale = max_edge / max(width, height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def evaluate_candidate(
    working_pixels: np.ndarray[Any, np.dtype[np.float32]],
    candidate: CandidateRecipe,
    crop: dict[str, Any],
    *,
    target_dimensions: tuple[int, int] | None = None,
    max_edge: int | None = None,
) -> tuple[np.ndarray[Any, np.dtype[np.uint8]], dict[str, Any]]:
    look = get_look(candidate.look.id, candidate.look.version)
    processed = apply_recipe(working_pixels, candidate, look)
    cropped = crop_normalized(processed, crop)
    if target_dimensions is not None:
        output_width, output_height = target_dimensions
    else:
        output_width, output_height = _target_dimensions(
            cropped.shape[1], cropped.shape[0], max_edge
        )
    if (cropped.shape[1], cropped.shape[0]) != (output_width, output_height):
        cropped = resize_float(cropped, output_width, output_height)
    encoded = linear_to_encoded_srgb(cropped)
    finite = bool(np.isfinite(encoded).all())
    if not finite:
        raise RuntimeError(f"candidate {candidate.id} produced NaN or infinite pixels")
    preclamp_low = float(np.mean(np.any(encoded < 0.0, axis=2), dtype=np.float64) * 100.0)
    preclamp_high = float(np.mean(np.any(encoded > 1.0, axis=2), dtype=np.float64) * 100.0)
    integer = np.rint(np.clip(encoded, 0.0, 1.0) * 255.0).astype(np.uint8)
    integer = sharpen_uint8(integer, candidate.sharpen)
    qa = {
        "finite": finite,
        "preclamp_low_percent": round(preclamp_low, 8),
        "preclamp_high_percent": round(preclamp_high, 8),
        "width": output_width,
        "height": output_height,
    }
    return integer, qa


def save_srgb_jpeg(
    pixels: np.ndarray[Any, np.dtype[np.uint8]],
    path: Path,
    *,
    quality: int,
    exif: Image.Exif | None = None,
) -> None:
    options: dict[str, Any] = {
        "format": "JPEG",
        "quality": quality,
        "subsampling": 0,
        "optimize": False,
        "progressive": False,
        "icc_profile": SRGB_PROFILE.read_bytes(),
    }
    if exif is not None:
        options["exif"] = exif
    Image.fromarray(pixels, mode="RGB").save(path, **options)


def _candidate_contact_sheet(paths: list[Path], target: Path) -> None:
    profile = SRGB_PROFILE.read_bytes()
    cells: list[Image.Image] = []
    for index, path in enumerate(paths, start=1):
        with Image.open(path) as opened:
            preview = opened.convert("RGB")
        preview.thumbnail((520, 520), Image.Resampling.LANCZOS)
        cell = Image.new("RGB", (560, 590), "#151515")
        cell.paste(preview, ((560 - preview.width) // 2, 44 + (520 - preview.height) // 2))
        ImageDraw.Draw(cell).text((16, 14), f"{index}. {path.stem}", fill="white")
        cells.append(cell)
    sheet = Image.new("RGB", (len(cells) * 560, 590), "#0d0d0d")
    for index, cell in enumerate(cells):
        sheet.paste(cell, (index * 560, 0))
    sheet.save(target, quality=92, subsampling=0, icc_profile=profile)


def render_job(job_path: Path, plan_path: Path) -> Path:
    job = job_path.resolve(strict=True)
    plan = validate_plan_for_job(job, plan_path)
    crops = _crop_map(job)
    working = read_linear_image(str(job_child(job, "intermediate/working.tif")))
    outputs: list[Path] = []
    qa_candidates: dict[str, Any] = {}
    metadata_candidates: dict[str, Any] = {}
    warnings: list[str] = []
    for candidate in plan.candidates:
        pixels, qa = evaluate_candidate(working, candidate, crops[candidate.crop_id], max_edge=1200)
        output = job_child(job, f"candidates/{candidate.id}.jpg")
        save_srgb_jpeg(pixels, output, quality=92)
        with Image.open(output) as verified:
            qa["icc_profile_embedded"] = bool(verified.info.get("icc_profile"))
        qa["decoded_sha256"] = hashlib.sha256(pixels.tobytes()).hexdigest()
        if qa["preclamp_low_percent"] > PRECLAMP_WARNING_PERCENT:
            warnings.append(
                f"{candidate.id}: pre-clamp low-gamut pixels exceed {PRECLAMP_WARNING_PERCENT}%"
            )
        if qa["preclamp_high_percent"] > PRECLAMP_WARNING_PERCENT:
            warnings.append(
                f"{candidate.id}: pre-clamp high-gamut pixels exceed {PRECLAMP_WARNING_PERCENT}%"
            )
        qa_candidates[candidate.id] = qa
        metadata_candidates[candidate.id] = {
            "recipe": candidate.model_dump(mode="json"),
            "output": str(output.relative_to(job)),
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        }
        outputs.append(output)
    _candidate_contact_sheet(outputs, job_child(job, "candidates/contact-sheet.jpg"))
    plan_payload = plan.model_dump(mode="json")
    plan_hash = hashlib.sha256(canonical_json_bytes(plan_payload)).hexdigest()
    write_json(
        job_child(job, "candidates/metadata.json"),
        {
            "schema_version": "1.0.0",
            "plan_sha256": plan_hash,
            "plan": plan_payload,
            "candidates": metadata_candidates,
        },
    )
    write_json(
        job_child(job, "qa/report.json"),
        {
            "schema_version": "1.0.0",
            "candidates": qa_candidates,
            "exports": {},
            "warnings": warnings,
        },
    )
    refresh_manifest(job, state="rendered", plan_sha256=plan_hash)
    return job
