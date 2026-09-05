from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from gekigrade.adapters.imagemagick import ProcessorError, run_magick


def _write_executable(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


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
