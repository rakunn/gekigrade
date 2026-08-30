from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from gekigrade.doctor import build_doctor_report
from gekigrade.domain.jsonio import read_json
from gekigrade.domain.models import EditPlan
from gekigrade.grading.looks import LookError, get_look
from gekigrade.pipeline.export import export_job, select_candidate
from gekigrade.pipeline.prepare import inspect_photo, prepare_job
from gekigrade.pipeline.qa import run_qa
from gekigrade.pipeline.render import PlanValidationError, render_job, validate_plan_for_job

app = typer.Typer(
    name="geki",
    help="Deterministic, non-generative photo correction and grading.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


class ExportPreset(StrEnum):
    FULL_QUALITY = "full-quality"
    INSTAGRAM_FEED = "instagram-feed"
    INSTAGRAM_STORY = "instagram-story"


class MetadataPolicy(StrEnum):
    SAFE = "safe"
    STRIP = "strip"


def _emit(value: Any) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _fail(message: str, *, code: int = 2) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=code)


@app.command()
def doctor() -> None:
    """Report exact local dependencies, profiles, and JPEG readiness."""
    report = build_doctor_report()
    _emit(report)
    if not report["ready_for_jpeg"]:
        raise typer.Exit(code=3)


@app.command("inspect")
def inspect_command(photo: Annotated[Path, typer.Argument(exists=True, readable=True)]) -> None:
    """Inspect a JPEG or Sony ARW without writing files."""
    try:
        _emit(inspect_photo(photo))
    except (ValueError, RuntimeError, OSError) as exc:
        _fail(str(exc))


@app.command()
def prepare(
    photo: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")],
) -> None:
    """Create an immutable-source analysis job for one JPEG or Sony ARW."""
    try:
        job = prepare_job(photo, output)
        _emit({"job": str(job), "state": "prepared"})
    except (ValueError, RuntimeError, OSError) as exc:
        _fail(str(exc), code=4 if isinstance(exc, RuntimeError) else 2)


@app.command()
def crops(job: Annotated[Path, typer.Argument(exists=True, file_okay=False)]) -> None:
    """Show the prepared normalized crop candidates."""
    try:
        _emit(read_json(job.resolve() / "crops/candidates.json"))
    except (ValueError, OSError) as exc:
        _fail(str(exc))


def _infer_job(plan: Path) -> Path | None:
    resolved = plan.resolve(strict=True)
    if resolved.parent.name == "plans" and (resolved.parent.parent / "manifest.json").is_file():
        return resolved.parent.parent
    return None


@app.command("validate-plan")
def validate_plan_command(
    plan: Annotated[Path, typer.Argument(exists=True, readable=True)],
    job: Annotated[Path | None, typer.Option("--job", file_okay=False)] = None,
) -> None:
    """Validate schema, bounds, looks, and—when available—the prepared job."""
    try:
        resolved_job = job.resolve(strict=True) if job is not None else _infer_job(plan)
        if resolved_job is not None:
            validated = validate_plan_for_job(resolved_job, plan)
        else:
            validated = EditPlan.model_validate_json(plan.read_text(encoding="utf-8"))
            for candidate in validated.candidates:
                look = get_look(candidate.look.id, candidate.look.version)
                if not look.strength_range[0] <= candidate.look.strength <= look.strength_range[1]:
                    raise PlanValidationError(f"look strength is outside {look.id}'s allowed range")
        _emit(
            {
                "valid": True,
                "schema_version": validated.schema_version,
                "candidate_ids": [candidate.id for candidate in validated.candidates],
            }
        )
    except (ValidationError, PlanValidationError, LookError, ValueError, OSError) as exc:
        _fail(f"plan validation failed: {exc}")


@app.command()
def render(
    job: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    plan: Annotated[Path, typer.Option("--plan", exists=True, readable=True)],
) -> None:
    """Render exactly three deterministic preview candidates."""
    try:
        rendered = render_job(job, plan)
        _emit({"job": str(rendered), "state": "rendered"})
    except (PlanValidationError, ValueError, RuntimeError, OSError) as exc:
        _fail(str(exc), code=4 if isinstance(exc, RuntimeError) else 2)


@app.command()
def qa(job: Annotated[Path, typer.Argument(exists=True, file_okay=False)]) -> None:
    """Re-open candidate files and verify structural and technical invariants."""
    try:
        report = run_qa(job)
        _emit({"report": str(report), "passed": True})
    except (ValueError, RuntimeError, OSError) as exc:
        _fail(str(exc), code=5)


@app.command()
def select(
    job: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    candidate_id: Annotated[str, typer.Argument()],
) -> None:
    """Persist an explicit candidate selection for later export."""
    try:
        path = select_candidate(job, candidate_id)
        _emit({"selection": str(path), "candidate_id": candidate_id})
    except (ValueError, RuntimeError, OSError) as exc:
        _fail(str(exc))


@app.command("export")
def export_command(
    job: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    preset: Annotated[ExportPreset, typer.Option("--preset")],
    quality: Annotated[int, typer.Option("--quality", min=1, max=100)] = 92,
    metadata_policy: Annotated[
        MetadataPolicy, typer.Option("--metadata-policy")
    ] = MetadataPolicy.SAFE,
) -> None:
    """Render the selected recipe at full or social dimensions with an sRGB profile."""
    try:
        output = export_job(
            job,
            preset=preset.value,
            quality=quality,
            metadata_policy=metadata_policy.value,
        )
        _emit({"output": str(output), "preset": preset.value})
    except (ValueError, RuntimeError, OSError) as exc:
        _fail(str(exc), code=4 if isinstance(exc, RuntimeError) else 2)


if __name__ == "__main__":
    app()
