from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class ExternalTool:
    name: str
    candidates: tuple[str, ...]
    version_args: tuple[str, ...]
    install_hint: str


@dataclass(frozen=True)
class ToolStatus:
    name: str
    available: bool
    path: str | None
    version: str | None
    install_hint: str
    error: str | None = None


class ExecutableSnapshotError(RuntimeError):
    """Raised when an executable cannot be bound to a private stable snapshot."""


@dataclass(frozen=True)
class ExecutableSnapshot:
    original_path: Path
    path: Path
    sha256: str


def _file_identity(status: os.stat_result) -> tuple[int, int, int, int, int]:
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns)


def _sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def _executable_fingerprint(path: Path) -> tuple[str, tuple[int, int, int, int, int]]:
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            opened_identity = _file_identity(opened)
            if not stat.S_ISREG(opened.st_mode) or opened.st_mode & 0o111 == 0:
                raise OSError("path is not an executable regular file")
            digest = _sha256_stream(stream)
            closed_identity = _file_identity(os.fstat(stream.fileno()))
        path_status = path.lstat()
    except OSError as exc:
        raise ExecutableSnapshotError(f"executable is unavailable or unsafe: {exc}") from exc
    if (
        not stat.S_ISREG(path_status.st_mode)
        or path_status.st_mode & 0o111 == 0
        or opened_identity != closed_identity
        or _file_identity(path_status) != opened_identity
    ):
        raise ExecutableSnapshotError("executable changed while it was fingerprinted")
    return digest, opened_identity


@contextmanager
def identity_bound_executable(executable: Path, *, label: str) -> Iterator[ExecutableSnapshot]:
    try:
        original = executable.resolve(strict=True)
    except OSError as exc:
        raise ExecutableSnapshotError(f"{label} executable is unavailable: {exc}") from exc
    source_flags = os.O_RDONLY | os.O_NONBLOCK
    target_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
        target_flags |= os.O_NOFOLLOW
    with tempfile.TemporaryDirectory(prefix=f"gekigrade-{label.lower()}-executable-") as directory:
        snapshot = Path(directory) / original.name
        try:
            source_descriptor = os.open(original, source_flags)
        except OSError as exc:
            raise ExecutableSnapshotError(
                f"{label} executable snapshot source could not be opened: {exc}"
            ) from exc
        try:
            target_descriptor = os.open(snapshot, target_flags, 0o500)
        except OSError as exc:
            os.close(source_descriptor)
            raise ExecutableSnapshotError(
                f"{label} executable snapshot could not be created: {exc}"
            ) from exc
        try:
            with (
                os.fdopen(source_descriptor, "rb") as source_stream,
                os.fdopen(target_descriptor, "wb") as target_stream,
            ):
                opened = os.fstat(source_stream.fileno())
                opened_identity = _file_identity(opened)
                if not stat.S_ISREG(opened.st_mode) or opened.st_mode & 0o111 == 0:
                    raise ExecutableSnapshotError(
                        f"{label} executable is not an executable regular file"
                    )
                digest = hashlib.sha256()
                for block in iter(lambda: source_stream.read(1024 * 1024), b""):
                    digest.update(block)
                    target_stream.write(block)
                target_stream.flush()
                os.fsync(target_stream.fileno())
                os.fchmod(target_stream.fileno(), 0o500)
                closed_identity = _file_identity(os.fstat(source_stream.fileno()))
            source_status = original.lstat()
            if (
                not stat.S_ISREG(source_status.st_mode)
                or source_status.st_mode & 0o111 == 0
                or opened_identity != closed_identity
                or _file_identity(source_status) != opened_identity
            ):
                raise ExecutableSnapshotError(
                    f"{label} executable changed while its snapshot was created"
                )
            source_sha256 = digest.hexdigest()
            snapshot_sha256, snapshot_identity = _executable_fingerprint(snapshot)
            if snapshot_sha256 != source_sha256:
                raise ExecutableSnapshotError(f"{label} executable snapshot digest differs")
        except Exception:
            snapshot.unlink(missing_ok=True)
            raise
        yield ExecutableSnapshot(
            original_path=original,
            path=snapshot,
            sha256=source_sha256,
        )
        final_snapshot_sha256, final_snapshot_identity = _executable_fingerprint(snapshot)
        final_source_sha256, final_source_identity = _executable_fingerprint(original)
        if (
            final_snapshot_sha256 != source_sha256
            or final_snapshot_identity != snapshot_identity
            or final_source_sha256 != source_sha256
            or final_source_identity != opened_identity
        ):
            raise ExecutableSnapshotError(f"{label} executable changed during processing")


def _locate(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if "/" in candidate:
            path = Path(candidate)
            if path.is_file() and path.stat().st_mode & 0o111:
                return str(path)
        elif located := shutil.which(candidate):
            return located
    return None


def inspect_tool(
    tool: ExternalTool,
    *,
    timeout_seconds: float = 5.0,
    environment: Mapping[str, str] | None = None,
) -> ToolStatus:
    path = _locate(tool.candidates)
    if path is None:
        return ToolStatus(
            name=tool.name,
            available=False,
            path=None,
            version=None,
            install_hint=tool.install_hint,
        )

    try:
        completed = subprocess.run(
            [path, *tool.version_args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            stdin=subprocess.DEVNULL,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ToolStatus(
            name=tool.name,
            available=False,
            path=path,
            version=None,
            install_hint=tool.install_hint,
            error=str(exc),
        )

    output = (completed.stdout or completed.stderr).strip().splitlines()
    version = output[0].strip() if output else None
    return ToolStatus(
        name=tool.name,
        available=completed.returncode == 0,
        path=path,
        version=version,
        install_hint=tool.install_hint,
        error=None if completed.returncode == 0 else (completed.stderr.strip() or None),
    )
