from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypedDict

from gekigrade.doctor import ACESCG_PROFILE, IMAGEMAGICK_CLI, SRGB_PROFILE

MAGICK = IMAGEMAGICK_CLI
PREVIEW_MAX_EDGE = 2048
MAGICK_ENVIRONMENT = {
    "LANG": "C",
    "LC_ALL": "C",
    "MAGICK_THREAD_LIMIT": "1",
    "OMP_DYNAMIC": "FALSE",
    "OMP_NUM_THREADS": "1",
    "OMP_SCHEDULE": "static",
    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
}


class ProcessorError(RuntimeError):
    """Raised when a deterministic external image processor fails."""


class ProcessorIdentity(TypedDict):
    name: str
    path: str
    version: str
    executable_sha256: str
    environment: dict[str, str]


class ProcessorResult(ProcessorIdentity):
    output_sha256: str


def preview_dimensions(
    width: int, height: int, *, max_edge: int = PREVIEW_MAX_EDGE
) -> tuple[int, int]:
    if width <= 0 or height <= 0 or max_edge <= 0:
        raise ValueError("preview dimensions and maximum edge must be positive")
    longest_edge = max(width, height)
    if longest_edge <= max_edge:
        return width, height

    def scaled(value: int) -> int:
        return max(1, (value * max_edge + longest_edge // 2) // longest_edge)

    return scaled(width), scaled(height)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_identity(status: os.stat_result) -> tuple[int, int, int, int, int]:
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns)


def _bind_output(target: Path, identity: ProcessorIdentity) -> ProcessorResult:
    if target.is_symlink() or not target.is_file():
        target.unlink(missing_ok=True)
        raise ProcessorError("ImageMagick output must be a regular, non-symlink file")
    try:
        with target.open("rb") as stream:
            opened_identity = _file_identity(os.fstat(stream.fileno()))
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            closed_identity = _file_identity(os.fstat(stream.fileno()))
        path_identity = _file_identity(target.stat())
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise ProcessorError(f"ImageMagick output could not be secured: {exc}") from exc
    if (
        target.is_symlink()
        or opened_identity != closed_identity
        or path_identity != opened_identity
    ):
        target.unlink(missing_ok=True)
        raise ProcessorError("ImageMagick output changed while it was fingerprinted")
    return {**identity, "output_sha256": digest.hexdigest()}


@contextmanager
def _stable_profile_snapshots(target: Path, profiles: dict[str, Path]) -> Iterator[dict[str, Path]]:
    snapshots: dict[str, Path] = {}
    expected_hashes: dict[str, str] = {}
    try:
        for label, profile in profiles.items():
            if profile.is_symlink() or not profile.is_file():
                raise ProcessorError(f"{label} color profile is unavailable or unsafe")
            expected_hash = _sha256(profile)
            snapshot = target.parent / f".{target.name}.{label}-{expected_hash[:12]}.icc"
            if snapshot.exists() or snapshot.is_symlink():
                raise ProcessorError(f"{label} color profile snapshot already exists")
            with profile.open("rb") as source, snapshot.open("xb") as destination:
                snapshots[label] = snapshot
                expected_hashes[label] = expected_hash
                shutil.copyfileobj(source, destination)
            if (
                profile.is_symlink()
                or _sha256(profile) != expected_hash
                or _sha256(snapshot) != expected_hash
            ):
                raise ProcessorError(f"{label} color profile changed while it was copied")
        yield snapshots
        for label, snapshot in snapshots.items():
            profile = profiles[label]
            expected_hash = expected_hashes[label]
            if (
                snapshot.is_symlink()
                or not snapshot.is_file()
                or _sha256(snapshot) != expected_hash
                or profile.is_symlink()
                or not profile.is_file()
                or _sha256(profile) != expected_hash
            ):
                raise ProcessorError(f"{label} color profile snapshot changed during processing")
    except ProcessorError:
        target.unlink(missing_ok=True)
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise ProcessorError(f"color profile snapshot could not be secured: {exc}") from exc
    finally:
        for snapshot in snapshots.values():
            snapshot.unlink(missing_ok=True)


def _magick_identity(executable: Path) -> ProcessorIdentity:
    if not executable.is_file() or executable.stat().st_mode & 0o111 == 0:
        raise ProcessorError(
            "ImageMagick is unavailable; run `geki doctor` for installation guidance"
        )
    resolved = executable.resolve(strict=True)
    executable_sha256 = _sha256(resolved)
    try:
        result = subprocess.run(
            [str(resolved), "-version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
            env=MAGICK_ENVIRONMENT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProcessorError(f"ImageMagick version could not be inspected: {exc}") from exc
    output = (result.stdout or result.stderr).strip().splitlines()
    if result.returncode != 0 or not output:
        detail = result.stderr.strip() or "version output was empty"
        raise ProcessorError(f"ImageMagick version could not be inspected: {detail}")
    if _sha256(resolved) != executable_sha256:
        raise ProcessorError("ImageMagick executable changed during version inspection")
    return {
        "name": "ImageMagick",
        "path": str(resolved),
        "version": output[0].strip(),
        "executable_sha256": executable_sha256,
        "environment": dict(MAGICK_ENVIRONMENT),
    }


def run_magick(
    arguments: list[str], *, executable: Path = MAGICK, timeout_seconds: float = 120.0
) -> ProcessorIdentity:
    identity = _magick_identity(executable)
    try:
        result = subprocess.run(
            [identity["path"], *arguments],
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
            stdin=subprocess.DEVNULL,
            env=MAGICK_ENVIRONMENT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProcessorError(f"ImageMagick could not complete: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown ImageMagick error"
        raise ProcessorError(f"ImageMagick failed: {detail}")
    if _magick_identity(Path(identity["path"])) != identity:
        raise ProcessorError("ImageMagick executable changed during processing")
    return identity


def normalize_jpeg(
    source: Path, target: Path, *, has_profile: bool, executable: Path = MAGICK
) -> ProcessorResult:
    required_profiles = {"working": ACESCG_PROFILE}
    if not has_profile:
        required_profiles["assumed-input"] = SRGB_PROFILE
    with _stable_profile_snapshots(target, required_profiles) as profiles:
        arguments = [str(source), "-auto-orient"]
        if not has_profile:
            arguments.extend(["-profile", str(profiles["assumed-input"])])
        arguments.extend(
            [
                "-profile",
                str(profiles["working"]),
                "-alpha",
                "off",
                "-depth",
                "16",
                "-define",
                "tiff:bits-per-sample=16",
                "-compress",
                "zip",
                f"TIFF:{target}",
            ]
        )
        identity = run_magick(arguments, executable=executable)
        return _bind_output(target, identity)


def normalize_profiled_tiff(
    source: Path, target: Path, *, executable: Path = MAGICK
) -> ProcessorResult:
    with _stable_profile_snapshots(target, {"working": ACESCG_PROFILE}) as profiles:
        working_profile = str(profiles["working"])
        identity = run_magick(
            [
                str(source),
                "-auto-orient",
                "-profile",
                working_profile,
                "-alpha",
                "off",
                "-depth",
                "16",
                "-strip",
                "-profile",
                working_profile,
                "-define",
                "tiff:bits-per-sample=16",
                "-compress",
                "zip",
                f"TIFF:{target}",
            ],
            executable=executable,
        )
        return _bind_output(target, identity)


def make_preview(
    source: Path,
    target: Path,
    *,
    max_edge: int = PREVIEW_MAX_EDGE,
    executable: Path = MAGICK,
) -> ProcessorResult:
    with _stable_profile_snapshots(target, {"output": SRGB_PROFILE}) as profiles:
        output_profile = str(profiles["output"])
        identity = run_magick(
            [
                str(source),
                "-profile",
                output_profile,
                "-resize",
                f"{max_edge}x{max_edge}>",
                "-strip",
                "-profile",
                output_profile,
                "-sampling-factor",
                "4:4:4",
                "-quality",
                "92",
                f"JPEG:{target}",
            ],
            executable=executable,
        )
        return _bind_output(target, identity)
