from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from gekigrade.cli import app

runner = CliRunner()


def test_cli_help_lists_the_vertical_slice_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in (
        "doctor",
        "inspect",
        "prepare",
        "crops",
        "validate-plan",
        "render",
        "qa",
        "select",
        "export",
    ):
        assert command in result.stdout


def test_cli_runs_the_documented_jpeg_workflow(tagged_oriented_jpeg: Path, tmp_path: Path) -> None:
    doctor = runner.invoke(app, ["doctor"])
    assert doctor.exit_code == 0, doctor.output
    assert json.loads(doctor.stdout)["ready_for_jpeg"] is True

    inspected = runner.invoke(app, ["inspect", str(tagged_oriented_jpeg)])
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.stdout)["format"] == "JPEG"

    job = tmp_path / "cli-job"
    prepared = runner.invoke(app, ["prepare", str(tagged_oriented_jpeg), "--output", str(job)])
    assert prepared.exit_code == 0, prepared.output

    plan = job / "plans/example-plan.json"
    validated = runner.invoke(app, ["validate-plan", str(plan)])
    assert validated.exit_code == 0, validated.output

    rendered = runner.invoke(app, ["render", str(job), "--plan", str(plan)])
    assert rendered.exit_code == 0, rendered.output
    assessed = runner.invoke(app, ["qa", str(job)])
    assert assessed.exit_code == 0, assessed.output
    selected = runner.invoke(app, ["select", str(job), "02-warm-editorial"])
    assert selected.exit_code == 0, selected.output
    exported = runner.invoke(app, ["export", str(job), "--preset", "instagram-feed"])
    assert exported.exit_code == 0, exported.output
    assert (job / "output/instagram-feed.jpg").is_file()


def test_cli_uses_nonzero_exit_for_invalid_plan(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")

    result = runner.invoke(app, ["validate-plan", str(invalid)])

    assert result.exit_code != 0
    assert "validation" in result.output.lower()
