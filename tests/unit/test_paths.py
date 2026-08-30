from __future__ import annotations

from pathlib import Path

import pytest

from gekigrade.domain.paths import PathSafetyError, create_job_directory, job_child


def test_create_job_directory_is_separate_from_source_and_has_expected_layout(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.jpg"
    source.write_bytes(b"source")
    output = tmp_path / "work" / "job-one"

    created = create_job_directory(source, output)

    assert created == output.resolve()
    assert source.read_bytes() == b"source"
    for name in ("crops", "plans", "candidates", "qa", "output", "intermediate"):
        assert (created / name).is_dir()


def test_create_job_directory_rejects_existing_output_and_symlink_source(tmp_path: Path) -> None:
    source = tmp_path / "original.jpg"
    source.write_bytes(b"source")
    existing = tmp_path / "existing"
    existing.mkdir()

    with pytest.raises(PathSafetyError, match="already exists"):
        create_job_directory(source, existing)

    linked = tmp_path / "linked.jpg"
    linked.symlink_to(source)
    with pytest.raises(PathSafetyError, match="symlink"):
        create_job_directory(linked, tmp_path / "new-job")


@pytest.mark.parametrize("unsafe", ["../escape.json", "/tmp/escape.json", "crops/../../escape"])
def test_job_child_rejects_traversal_and_absolute_paths(tmp_path: Path, unsafe: str) -> None:
    root = tmp_path / "job"
    root.mkdir()

    with pytest.raises(PathSafetyError):
        job_child(root, unsafe)


def test_job_child_accepts_contained_relative_path(tmp_path: Path) -> None:
    root = tmp_path / "job"
    root.mkdir()

    assert job_child(root, "crops/contact-sheet.jpg") == root / "crops/contact-sheet.jpg"


def test_job_child_rejects_a_final_symlink_that_points_outside(tmp_path: Path) -> None:
    root = tmp_path / "job"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("private", encoding="utf-8")
    (root / "manifest.json").symlink_to(outside)

    with pytest.raises(PathSafetyError, match="symlink"):
        job_child(root, "manifest.json")
