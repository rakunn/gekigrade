from __future__ import annotations

from pathlib import Path

from gekigrade.domain.jsonio import read_json, write_json


def test_write_json_is_not_blocked_by_an_abandoned_fixed_temporary_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "manifest.json"
    abandoned = tmp_path / "manifest.json.tmp"
    abandoned.write_text("incomplete", encoding="utf-8")

    write_json(target, {"state": "prepared"})

    assert read_json(target) == {"state": "prepared"}
    assert abandoned.read_text(encoding="utf-8") == "incomplete"
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []


def test_write_json_does_not_follow_an_abandoned_fixed_temporary_symlink(
    tmp_path: Path,
) -> None:
    protected = tmp_path / "source.jpg"
    protected.write_bytes(b"protected source bytes")
    target = tmp_path / "run.json"
    abandoned = tmp_path / "run.json.tmp"
    abandoned.symlink_to(protected)

    write_json(target, {"returncode": 0})

    assert read_json(target) == {"returncode": 0}
    assert protected.read_bytes() == b"protected source bytes"
    assert abandoned.is_symlink()
