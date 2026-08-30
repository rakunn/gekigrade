from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


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


def _locate(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if "/" in candidate:
            path = Path(candidate)
            if path.is_file() and path.stat().st_mode & 0o111:
                return str(path)
        elif located := shutil.which(candidate):
            return located
    return None


def inspect_tool(tool: ExternalTool, *, timeout_seconds: float = 5.0) -> ToolStatus:
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
