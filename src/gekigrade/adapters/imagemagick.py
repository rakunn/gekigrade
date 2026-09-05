from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import TypedDict

from gekigrade.doctor import ACESCG_PROFILE, IMAGEMAGICK_CLI, SRGB_PROFILE

MAGICK = IMAGEMAGICK_CLI
PREVIEW_MAX_EDGE = 2048


class ProcessorError(RuntimeError):
    """Raised when a deterministic external image processor fails."""


class ProcessorIdentity(TypedDict):
    name: str
    path: str
    version: str
    executable_sha256: str


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
) -> ProcessorIdentity:
    arguments = [str(source), "-auto-orient"]
    if not has_profile:
        arguments.extend(["-profile", str(SRGB_PROFILE)])
    arguments.extend(
        [
            "-profile",
            str(ACESCG_PROFILE),
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
    return run_magick(arguments, executable=executable)


def normalize_profiled_tiff(
    source: Path, target: Path, *, executable: Path = MAGICK
) -> ProcessorIdentity:
    return run_magick(
        [
            str(source),
            "-auto-orient",
            "-profile",
            str(ACESCG_PROFILE),
            "-alpha",
            "off",
            "-depth",
            "16",
            "-strip",
            "-profile",
            str(ACESCG_PROFILE),
            "-define",
            "tiff:bits-per-sample=16",
            "-compress",
            "zip",
            f"TIFF:{target}",
        ],
        executable=executable,
    )


def make_preview(
    source: Path,
    target: Path,
    *,
    max_edge: int = PREVIEW_MAX_EDGE,
    executable: Path = MAGICK,
) -> ProcessorIdentity:
    return run_magick(
        [
            str(source),
            "-profile",
            str(SRGB_PROFILE),
            "-resize",
            f"{max_edge}x{max_edge}>",
            "-strip",
            "-profile",
            str(SRGB_PROFILE),
            "-sampling-factor",
            "4:4:4",
            "-quality",
            "92",
            f"JPEG:{target}",
        ],
        executable=executable,
    )
