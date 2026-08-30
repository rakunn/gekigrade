from __future__ import annotations

from pathlib import Path, PurePath


class PathSafetyError(ValueError):
    """Raised when a filesystem path crosses the job safety boundary."""


JOB_DIRECTORIES = ("crops", "plans", "candidates", "qa", "output", "intermediate")


def create_job_directory(source: Path, output: Path) -> Path:
    if source.is_symlink():
        raise PathSafetyError("source must not be a symlink")
    if not source.is_file():
        raise PathSafetyError(f"source is not a regular file: {source}")
    if output.exists() or output.is_symlink():
        raise PathSafetyError(f"output already exists: {output}")

    source_path = source.resolve(strict=True)
    output_path = output.resolve(strict=False)
    if source_path == output_path or source_path.is_relative_to(output_path):
        raise PathSafetyError("source must remain outside the writable job directory")

    output_path.mkdir(parents=True, exist_ok=False)
    for name in JOB_DIRECTORIES:
        (output_path / name).mkdir()
    return output_path


def job_child(job_root: Path, relative: str | PurePath) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise PathSafetyError(f"job path must be a contained relative path: {relative}")
    root = job_root.resolve(strict=True)
    candidate = root.joinpath(relative_path)
    if candidate.is_symlink():
        raise PathSafetyError(f"job artifact must not be a symlink: {relative}")
    resolved_parent = candidate.parent.resolve(strict=False)
    if not resolved_parent.is_relative_to(root):
        raise PathSafetyError(f"job path escapes its root: {relative}")
    return candidate
