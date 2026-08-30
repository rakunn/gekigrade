from __future__ import annotations

from pathlib import Path
from typing import Any

from gekigrade.doctor import sha256_file
from gekigrade.domain.jsonio import read_json, write_json


def assert_source_unchanged(job: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = read_json(job / "manifest.json")
    source = Path(manifest["source_path"])
    if not source.is_file() or source.is_symlink():
        raise ValueError("source is unavailable or no longer a regular file")
    if sha256_file(source) != manifest["source_sha256"]:
        raise ValueError("source checksum changed after job preparation")
    return manifest


def refresh_manifest(job: Path, *, state: str, plan_sha256: str | None = None) -> dict[str, Any]:
    manifest: dict[str, Any] = read_json(job / "manifest.json")
    artifacts: dict[str, dict[str, Any]] = {}
    for path in sorted(job.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            relative = str(path.relative_to(job))
            artifacts[relative] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    manifest["state"] = state
    manifest["artifacts"] = artifacts
    if plan_sha256 is not None:
        manifest["plan_sha256"] = plan_sha256
    write_json(job / "manifest.json", manifest)
    return manifest
