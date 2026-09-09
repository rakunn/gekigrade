from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from pydantic import ValidationError

from gekigrade.analysis.stages import measure_stage
from gekigrade.doctor import SRGB_PROFILE
from gekigrade.domain.jsonio import canonical_json_bytes, read_json, write_json
from gekigrade.domain.models import EDIT_PLAN_ADAPTER, AnyEditPlan, AnyRecipe, CandidateRecipeV2
from gekigrade.domain.paths import job_child
from gekigrade.geometry.crops import CROP_SCHEMA_VERSION, generate_crop_candidates
from gekigrade.grading.engine import (
    _transform,
    apply_recipe,
    crop_normalized,
    linear_to_encoded_srgb,
    read_linear_image,
    resize_float,
    sharpen_uint8,
)
from gekigrade.grading.looks import LookError, get_look
from gekigrade.grading.tone import GAMUT_METHOD, compress_output_gamut
from gekigrade.pipeline.manifests import assert_source_unchanged, refresh_manifest


class PlanValidationError(ValueError):
    """Raised before rendering when a plan violates a schema or job invariant."""


PRECLAMP_WARNING_PERCENT = 1.0


def _crop_map(job: Path, *, working_dimensions: tuple[int, int]) -> dict[str, dict[str, Any]]:
    try:
        width, height = working_dimensions
        expected = {
            "schema_version": CROP_SCHEMA_VERSION,
            "candidates": generate_crop_candidates(width, height),
        }
        document = read_json(job_child(job, "crops/candidates.json"))
        if not isinstance(document, dict):
            raise PlanValidationError("prepared crop candidates must be a JSON object")
        if document.get("schema_version") != CROP_SCHEMA_VERSION:
            raise PlanValidationError(
                "unsupported crop candidate schema version: "
                f"{document.get('schema_version')!r}; re-run prepare"
            )
        if document != expected:
            raise PlanValidationError(
                "prepared crop candidates do not match deterministic working-image geometry"
            )
        return {candidate["id"]: candidate for candidate in document["candidates"]}
    except PlanValidationError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise PlanValidationError("prepared crop candidates are invalid") from exc


def validate_plan_for_job(
    job: Path,
    plan_path: Path,
    *,
    working_dimensions: tuple[int, int] | None = None,
) -> AnyEditPlan:
    try:
        plan = EDIT_PLAN_ADAPTER.validate_json(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise PlanValidationError(f"plan schema validation failed: {exc}") from exc
    return validate_plan_model_for_job(job, plan, working_dimensions=working_dimensions)


def validate_plan_model_for_job(
    job: Path,
    plan: AnyEditPlan,
    *,
    working_dimensions: tuple[int, int] | None = None,
) -> AnyEditPlan:
    manifest = assert_source_unchanged(job)
    if plan.source_sha256 != manifest["source_sha256"]:
        raise PlanValidationError("plan source checksum does not match the prepared job")
    if working_dimensions is None:
        try:
            working = read_linear_image(str(job_child(job, "intermediate/working.tif")))
        except (OSError, ValueError, RuntimeError) as exc:
            raise PlanValidationError(f"working image validation failed: {exc}") from exc
        working_dimensions = (working.shape[1], working.shape[0])
    crops = _crop_map(job, working_dimensions=working_dimensions)
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
    candidate: AnyRecipe,
    crop: dict[str, Any],
    *,
    target_dimensions: tuple[int, int] | None = None,
    max_edge: int | None = None,
) -> tuple[np.ndarray[Any, np.dtype[np.uint8]], dict[str, Any]]:
    look = get_look(candidate.look.id, candidate.look.version)
    stages: dict[str, Any] = {}

    def observe(name: str, pixels: np.ndarray[Any, np.dtype[np.float32]]) -> None:
        stages[name] = measure_stage(
            pixels, to_encoded_srgb=linear_to_encoded_srgb, scope="full-frame-after-rotation"
        )

    processed = apply_recipe(working_pixels, candidate, look, observe=observe)
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
    stages["before_output_gamut"] = measure_stage(encoded)
    gamut_method = "legacy-channel-clamp"
    if isinstance(candidate, CandidateRecipeV2):
        srgb_linear = _transform(cropped, "ACEScg", "Linear Rec.709 (sRGB)")
        for start in range(0, srgb_linear.shape[0], 256):
            srgb_linear[start : start + 256] = compress_output_gamut(
                srgb_linear[start : start + 256]
            )
        encoded = _transform(srgb_linear, "Linear Rec.709 (sRGB)", "sRGB Encoded Rec.709 (sRGB)")
        gamut_method = GAMUT_METHOD
    stages["before_output_clamp"] = measure_stage(encoded)
    finite = bool(np.isfinite(encoded).all())
    if not finite:
        raise RuntimeError(f"candidate {candidate.id} produced NaN or infinite pixels")
    preclamp_low = float(np.mean(np.any(encoded < 0.0, axis=2), dtype=np.float64) * 100.0)
    preclamp_high = float(np.mean(np.any(encoded > 1.0, axis=2), dtype=np.float64) * 100.0)
    integer = np.rint(np.clip(encoded, 0.0, 1.0) * 255.0).astype(np.uint8)
    integer = sharpen_uint8(integer, candidate.sharpen)
    qa = {
        "recipe_schema_version": "2.0.0" if isinstance(candidate, CandidateRecipeV2) else "1.0.0",
        "output_gamut_method": gamut_method,
        "stages": stages,
        "post_quantization_and_sharpen": measure_stage(integer.astype(np.float32) / 255.0),
        "finite": finite,
        "preclamp_low_percent": round(preclamp_low, 8),
        "preclamp_high_percent": round(preclamp_high, 8),
        "width": output_width,
        "height": output_height,
    }
    return integer, qa


def record_jpeg_qa(path: Path, qa: dict[str, Any]) -> None:
    """Read the actual lossy output rather than label pre-encode bytes as decoded pixels."""
    with Image.open(path) as opened:
        qa["icc_profile_embedded"] = bool(opened.info.get("icc_profile"))
        qa["encoded_width"], qa["encoded_height"] = opened.size
        decoded = np.asarray(opened.convert("RGB"), dtype=np.uint8)
    qa["decoded_sha256"] = hashlib.sha256(decoded.tobytes()).hexdigest()
    qa["post_encode"] = measure_stage(decoded.astype(np.float32) / 255.0)


def gamut_warnings(identifier: str, qa: dict[str, Any]) -> list[str]:
    warnings = []
    for name, stage in qa.get("stages", {}).items():
        for side in ("low", "high"):
            if stage["out_of_gamut"][f"{side}_any_percent"] > PRECLAMP_WARNING_PERCENT:
                warnings.append(
                    f"{identifier}: {name} {side}-gamut pixels exceed {PRECLAMP_WARNING_PERCENT}%"
                )
    if "stages" not in qa:
        # Persisted version-1 reports have only these final-stage fields.
        for side in ("low", "high"):
            if qa.get(f"preclamp_{side}_percent", 0.0) > PRECLAMP_WARNING_PERCENT:
                warnings.append(
                    f"{identifier}: pre-clamp {side}-gamut pixels "
                    f"exceed {PRECLAMP_WARNING_PERCENT}%"
                )
    clipping = qa.get("post_encode", {}).get("clipping", {})
    for tone in ("shadow", "highlight"):
        if clipping.get(f"{tone}_all_percent", 0.0) > 1.0:
            warnings.append(f"{identifier}: post-encode all-channel {tone} clipping exceeds 1.0%")
    return warnings


def current_report_warnings(report: dict[str, Any]) -> list[str]:
    """Derive warnings from current artifacts, never from superseded output warnings."""
    warnings = []
    for group in ("candidates", "exports"):
        for identifier, qa in report.get(group, {}).items():
            warnings.extend(gamut_warnings(identifier, qa))
    for identifier, verification in report.get("verification", {}).items():
        for tone in ("shadow", "highlight"):
            if verification.get("clipping", {}).get(f"{tone}_all_percent", 0.0) > 1.0:
                warnings.append(f"{identifier}: all-channel {tone} clipping exceeds 1.0%")
    return sorted(set(warnings))


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
    assert_source_unchanged(job)
    working = read_linear_image(str(job_child(job, "intermediate/working.tif")))
    working_dimensions = (working.shape[1], working.shape[0])
    plan = validate_plan_for_job(job, plan_path, working_dimensions=working_dimensions)
    crops = _crop_map(job, working_dimensions=working_dimensions)
    outputs: list[Path] = []
    qa_candidates: dict[str, Any] = {}
    metadata_candidates: dict[str, Any] = {}
    warnings: list[str] = []
    for candidate in plan.candidates:
        pixels, qa = evaluate_candidate(working, candidate, crops[candidate.crop_id], max_edge=1200)
        output = job_child(job, f"candidates/{candidate.id}.jpg")
        save_srgb_jpeg(pixels, output, quality=92)
        record_jpeg_qa(output, qa)
        qa["pre_encode_sha256"] = hashlib.sha256(pixels.tobytes()).hexdigest()
        warnings.extend(gamut_warnings(candidate.id, qa))
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
            "schema_version": "2.0.0",
            "candidates": qa_candidates,
            "exports": {},
            "warnings": warnings,
        },
    )
    refresh_manifest(job, state="rendered", plan_sha256=plan_hash)
    return job
