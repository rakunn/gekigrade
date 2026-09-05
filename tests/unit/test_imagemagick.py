from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from gekigrade.adapters.imagemagick import ProcessorError, preview_dimensions, run_magick


def _write_executable(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (240, 320, (240, 320)),
        (8685, 4883, (2048, 1151)),
        (4883, 8685, (1151, 2048)),
    ],
)
def test_preview_dimensions_match_imagemagick_bounding_geometry(
    width: int, height: int, expected: tuple[int, int]
) -> None:
    assert preview_dimensions(width, height) == expected


def test_run_magick_returns_the_exact_executable_identity(tmp_path: Path) -> None:
    executable = _write_executable(
        tmp_path / "magick",
        """#!/bin/sh
if [ "$1" = "-version" ]; then
  echo "ImageMagick 7.1.1-test"
  exit 0
fi
exit 0
""",
    )

    identity = run_magick(["input.tif", "output.tif"], executable=executable)

    assert identity == {
        "name": "ImageMagick",
        "path": str(executable.resolve()),
        "version": "ImageMagick 7.1.1-test",
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
    }


def test_run_magick_rejects_an_executable_changed_during_processing(tmp_path: Path) -> None:
    executable = _write_executable(
        tmp_path / "magick",
        """#!/bin/sh
if [ "$1" = "-version" ]; then
  echo "ImageMagick 7.1.1-test"
  exit 0
fi
echo "# changed" >> "$0"
exit 0
""",
    )

    with pytest.raises(ProcessorError, match="changed during processing"):
        run_magick(["input.tif", "output.tif"], executable=executable)
