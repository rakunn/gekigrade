from __future__ import annotations

from pathlib import Path

from gekigrade.adapters.tools import ExternalTool, inspect_tool


def test_inspect_tool_reports_missing_dependency_with_install_guidance() -> None:
    tool = ExternalTool(
        name="missing-test-tool",
        candidates=("/definitely/not/a/tool",),
        version_args=("--version",),
        install_hint="Install the test tool.",
    )

    status = inspect_tool(tool)

    assert status.available is False
    assert status.path is None
    assert status.version is None
    assert status.install_hint == "Install the test tool."


def test_inspect_tool_captures_a_version_without_shell(tmp_path: Path) -> None:
    executable = tmp_path / "version-tool"
    executable.write_text("#!/bin/sh\nprintf 'version-tool 1.2.3\\n'\n", encoding="utf-8")
    executable.chmod(0o755)
    tool = ExternalTool(
        name="version-tool",
        candidates=(str(executable),),
        version_args=("--version",),
        install_hint="Not needed.",
    )

    status = inspect_tool(tool)

    assert status.available is True
    assert status.path == str(executable)
    assert status.version == "version-tool 1.2.3"
