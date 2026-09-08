from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from gekigrade.geometry.crops import CROP_SCHEMA_VERSION, generate_crop_candidates
from gekigrade.pipeline.export import export_job, select_candidate
from gekigrade.pipeline.prepare import prepare_job
from gekigrade.pipeline.render import PlanValidationError, render_job, validate_plan_for_job


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_render_is_repeatable_and_selected_recipe_exports_profiled_feed(
    tagged_oriented_jpeg: Path, tmp_path: Path
) -> None:
    source_hash = _sha256(tagged_oriented_jpeg)
    job = prepare_job(tagged_oriented_jpeg, tmp_path / "job")
    plan = job / "plans/example-plan.json"

    validated = validate_plan_for_job(job, plan)
    assert validated.source_sha256 == source_hash
    render_job(job, plan)

    candidates = [
        job / "candidates/01-natural-clean.jpg",
        job / "candidates/02-warm-editorial.jpg",
        job / "candidates/03-muted-cinematic.jpg",
    ]
    first_hashes = [_sha256(path) for path in candidates]
    assert all(path.is_file() for path in candidates)
    assert (job / "candidates/contact-sheet.jpg").is_file()
    assert (job / "candidates/metadata.json").is_file()
    assert (job / "qa/report.json").is_file()

    render_job(job, plan)
    assert [_sha256(path) for path in candidates] == first_hashes

    select_candidate(job, "02-warm-editorial")
    output = export_job(job, preset="instagram-feed", quality=91)
    assert output == job / "output/instagram-feed.jpg"
    with Image.open(output) as image:
        assert image.size == (1080, 1350)
        assert image.info.get("icc_profile")
        assert image.getexif().get(271) == "GekiGrade Fixture"
        assert image.getexif().get(274) == 1
    assert _sha256(tagged_oriented_jpeg) == source_hash
    first_export_hash = _sha256(output)
    assert _sha256(export_job(job, preset="instagram-feed", quality=91)) == first_export_hash

    report = json.loads((job / "qa/report.json").read_text(encoding="utf-8"))
    assert report["candidates"]["02-warm-editorial"]["finite"] is True
    assert isinstance(report["warnings"], list)
    manifest = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["plan_sha256"]
    assert manifest["artifacts"]["output/instagram-feed.jpg"]["sha256"] == _sha256(output)


def test_unknown_look_is_rejected_before_rendering(
    tagged_oriented_jpeg: Path, tmp_path: Path
) -> None:
    job = prepare_job(tagged_oriented_jpeg, tmp_path / "job")
    plan_path = job / "plans/example-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["candidates"][0]["look"]["id"] = "unknown-look"
    invalid = tmp_path / "invalid-plan.json"
    invalid.write_text(json.dumps(plan), encoding="utf-8")

    with pytest.raises(PlanValidationError, match="unknown look"):
        render_job(job, invalid)

    assert list((job / "candidates").iterdir()) == []


def test_export_uses_the_plan_that_was_rendered_not_a_hardcoded_example(
    tagged_oriented_jpeg: Path, tmp_path: Path
) -> None:
    job = prepare_job(tagged_oriented_jpeg, tmp_path / "job")
    plan = json.loads((job / "plans/example-plan.json").read_text(encoding="utf-8"))
    plan["candidates"][0]["exposure_ev"] = 0.15
    for candidate in plan["candidates"]:
        candidate["crop_id"] = "feed-4x5-top"
    custom = tmp_path / "custom-plan.json"
    custom.write_text(json.dumps(plan), encoding="utf-8")

    render_job(job, custom)
    select_candidate(job, "01-natural-clean")
    output = export_job(job, preset="instagram-feed")

    assert output.is_file()


def test_full_quality_and_supported_story_exports_use_selected_crop(
    tagged_oriented_jpeg: Path, tmp_path: Path
) -> None:
    job = prepare_job(tagged_oriented_jpeg, tmp_path / "job")
    plan = json.loads((job / "plans/example-plan.json").read_text(encoding="utf-8"))
    for candidate in plan["candidates"]:
        candidate["crop_id"] = "story-9x16-right"
        candidate["rotation_degrees"] = 1.25
    story_plan = tmp_path / "story-plan.json"
    story_plan.write_text(json.dumps(plan), encoding="utf-8")
    render_job(job, story_plan)
    select_candidate(job, "03-muted-cinematic")

    full = export_job(job, preset="full-quality", metadata_policy="strip")
    with Image.open(full) as image:
        assert image.size == (180, 320)
        assert image.info.get("icc_profile")
        assert image.getexif().get(271) is None

    story = export_job(job, preset="instagram-story", quality=90)
    with Image.open(story) as image:
        assert image.size == (1080, 1920)
        assert image.info.get("icc_profile")


def test_social_export_rejects_a_crop_with_the_wrong_aspect_purpose(
    tagged_oriented_jpeg: Path, tmp_path: Path
) -> None:
    job = prepare_job(tagged_oriented_jpeg, tmp_path / "job")
    plan = json.loads((job / "plans/example-plan.json").read_text(encoding="utf-8"))
    for candidate in plan["candidates"]:
        candidate["crop_id"] = "square-1x1-top"
    square_plan = tmp_path / "square-plan.json"
    square_plan.write_text(json.dumps(plan), encoding="utf-8")
    render_job(job, square_plan)
    select_candidate(job, "01-natural-clean")

    with pytest.raises(ValueError, match="requires a selected 4:5 crop"):
        export_job(job, preset="instagram-feed")


def test_export_rejects_corrupted_prepared_crop_geometry(
    tagged_oriented_jpeg: Path, tmp_path: Path
) -> None:
    job = prepare_job(tagged_oriented_jpeg, tmp_path / "job")
    plan = job / "plans/example-plan.json"
    render_job(job, plan)
    select_candidate(job, "01-natural-clean")

    crops_path = job / "crops/candidates.json"
    crops = json.loads(crops_path.read_text(encoding="utf-8"))
    selected = next(
        candidate for candidate in crops["candidates"] if candidate["id"] == "feed-4x5-center"
    )
    selected["width"] = 0.5
    crops_path.write_text(json.dumps(crops), encoding="utf-8")

    with pytest.raises(
        PlanValidationError,
        match="crop candidates do not match deterministic working-image geometry",
    ):
        export_job(job, preset="instagram-feed")


def test_export_binds_regenerated_crops_to_loaded_working_dimensions(
    tagged_oriented_jpeg: Path, tmp_path: Path
) -> None:
    job = prepare_job(tagged_oriented_jpeg, tmp_path / "job")
    plan = job / "plans/example-plan.json"
    render_job(job, plan)
    select_candidate(job, "01-natural-clean")

    source_path = job / "source.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["oriented_dimensions"] = {"width": 400, "height": 300}
    source_path.write_text(json.dumps(source), encoding="utf-8")
    crops = {
        "schema_version": CROP_SCHEMA_VERSION,
        "candidates": generate_crop_candidates(400, 300),
    }
    (job / "crops/candidates.json").write_text(json.dumps(crops), encoding="utf-8")

    with pytest.raises(
        PlanValidationError,
        match="crop candidates do not match deterministic working-image geometry",
    ):
        export_job(job, preset="instagram-feed")


def test_old_crop_schema_requires_job_repreparation(
    tagged_oriented_jpeg: Path, tmp_path: Path
) -> None:
    job = prepare_job(tagged_oriented_jpeg, tmp_path / "job")
    crops_path = job / "crops/candidates.json"
    crops = json.loads(crops_path.read_text(encoding="utf-8"))
    crops["schema_version"] = "1.0.0"
    crops_path.write_text(json.dumps(crops), encoding="utf-8")

    with pytest.raises(
        PlanValidationError,
        match=r"unsupported crop candidate schema version.*re-run prepare",
    ):
        validate_plan_for_job(job, job / "plans/example-plan.json")


@pytest.mark.parametrize("malformed_root", [[], None, "not-an-object"])
def test_crop_document_requires_an_object_root(
    tagged_oriented_jpeg: Path, tmp_path: Path, malformed_root: object
) -> None:
    job = prepare_job(tagged_oriented_jpeg, tmp_path / "job")
    (job / "crops/candidates.json").write_text(json.dumps(malformed_root), encoding="utf-8")

    with pytest.raises(
        PlanValidationError,
        match="crop candidates must be a JSON object",
    ):
        validate_plan_for_job(job, job / "plans/example-plan.json")
