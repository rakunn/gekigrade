from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from gekigrade.adapters.imagemagick import (
    MAGICK_ENVIRONMENT,
    ProcessorError,
    ProcessorIdentity,
    normalize_profiled_tiff,
    preview_dimensions,
    run_magick,
)
from gekigrade.doctor import ACESCG_PROFILE


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
        "environment": MAGICK_ENVIRONMENT,
    }


def test_run_magick_uses_and_records_a_pinned_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _write_executable(
        tmp_path / "magick",
        """#!/bin/sh
if [ "$1" = "-version" ]; then
  echo "ImageMagick 7.1.1-test"
  exit 0
fi
/usr/bin/env > "$1"
""",
    )
    report = tmp_path / "environment.txt"
    monkeypatch.setenv("MAGICK_CONFIGURE_PATH", "/caller/config")
    monkeypatch.setenv("MAGICK_CODER_MODULE_PATH", "/caller/coders")
    monkeypatch.setenv("MAGICK_FILTER_MODULE_PATH", "/caller/filters")
    monkeypatch.setenv("MAGICK_THREAD_LIMIT", "99")
    monkeypatch.setenv("UNRELATED_CALLER_VALUE", "must-not-cross")

    identity = run_magick([str(report)], executable=executable)

    observed = dict(
        line.split("=", 1)
        for line in report.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    assert identity["environment"] == MAGICK_ENVIRONMENT
    assert all(observed.get(key) == value for key, value in MAGICK_ENVIRONMENT.items())
    assert "MAGICK_CONFIGURE_PATH" not in observed
    assert "MAGICK_CODER_MODULE_PATH" not in observed
    assert "MAGICK_FILTER_MODULE_PATH" not in observed
    assert "UNRELATED_CALLER_VALUE" not in observed


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


def test_normalize_profiled_tiff_uses_one_stable_private_acescg_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "developed.tif"
    source.write_bytes(b"test input")
    target = tmp_path / "working.tif"
    captured_snapshot: Path | None = None

    def inspect_invocation(
        arguments: list[str], *, executable: Path, timeout_seconds: float = 120.0
    ) -> ProcessorIdentity:
        nonlocal captured_snapshot
        del executable, timeout_seconds
        profile_paths = [
            Path(arguments[index + 1])
            for index, value in enumerate(arguments)
            if value == "-profile"
        ]
        assert len(profile_paths) == 2
        assert profile_paths[0] == profile_paths[1]
        captured_snapshot = profile_paths[0]
        assert captured_snapshot != ACESCG_PROFILE
        assert captured_snapshot.read_bytes() == ACESCG_PROFILE.read_bytes()
        target.write_bytes(b"normalized")
        return {
            "name": "ImageMagick",
            "path": "/test/magick",
            "version": "test",
            "executable_sha256": "a" * 64,
            "environment": MAGICK_ENVIRONMENT,
        }

    monkeypatch.setattr("gekigrade.adapters.imagemagick.run_magick", inspect_invocation)

    result = normalize_profiled_tiff(source, target, executable=tmp_path / "magick")

    assert captured_snapshot is not None
    assert not captured_snapshot.exists()
    assert result["output_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_normalize_profiled_tiff_rejects_a_changed_profile_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "developed.tif"
    source.write_bytes(b"test input")
    target = tmp_path / "working.tif"

    def change_snapshot(
        arguments: list[str], *, executable: Path, timeout_seconds: float = 120.0
    ) -> ProcessorIdentity:
        del executable, timeout_seconds
        snapshot = Path(arguments[arguments.index("-profile") + 1])
        assert snapshot != ACESCG_PROFILE
        snapshot.write_bytes(b"changed")
        target.write_bytes(b"invalid normalized output")
        return {
            "name": "ImageMagick",
            "path": "/test/magick",
            "version": "test",
            "executable_sha256": "a" * 64,
            "environment": MAGICK_ENVIRONMENT,
        }

    monkeypatch.setattr("gekigrade.adapters.imagemagick.run_magick", change_snapshot)

    with pytest.raises(ProcessorError, match="profile snapshot changed"):
        normalize_profiled_tiff(source, target, executable=tmp_path / "magick")
    assert not target.exists()
