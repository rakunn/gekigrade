from __future__ import annotations

import subprocess
from pathlib import Path

from gekigrade.doctor import ACESCG_PROFILE, SRGB_PROFILE

MAGICK = Path("/opt/homebrew/bin/magick")


class ProcessorError(RuntimeError):
    """Raised when a deterministic external image processor fails."""


def run_magick(arguments: list[str], *, timeout_seconds: float = 120.0) -> None:
    if not MAGICK.is_file():
        raise ProcessorError(
            "ImageMagick is unavailable; run `geki doctor` for installation guidance"
        )
    try:
        result = subprocess.run(
            [str(MAGICK), *arguments],
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


def normalize_jpeg(source: Path, target: Path, *, has_profile: bool) -> None:
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
    run_magick(arguments)


def make_preview(source: Path, target: Path, *, max_edge: int = 2048) -> None:
    run_magick(
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
        ]
    )
