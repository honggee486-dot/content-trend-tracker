from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_apply_update_launcher_uses_final_powershell_command_for_replacement_safety() -> None:
    source = (PROJECT_ROOT / "apply_update.bat").read_text(encoding="ascii")

    entrypoint_index = source.index(
        'set "SCRIPT_PATH=%~dp0scripts\\apply_update_entrypoint.ps1"'
    )
    run_index = source.index(
        '"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass '
        '-File "%SCRIPT_PATH%" "%~1" & exit /b'
    )

    assert entrypoint_index < run_index
    assert source.rstrip().endswith('& exit /b')
    assert "TEMP_LAUNCHER" not in source
    assert "%~f0" not in source
    assert "CONTENT_TREND_TRACKER_APPLY_BRANCH" not in source
    assert "APPLY_UPDATE_LAUNCHER_TRACE" not in source
    assert "EnableDelayedExpansion" not in source
    assert "shift" not in source.lower()


def test_apply_update_launcher_keeps_windows_line_endings() -> None:
    data = (PROJECT_ROOT / "apply_update.bat").read_bytes()
    attributes = (PROJECT_ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert b"\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert "apply_update.bat text eol=crlf" in attributes.splitlines()


def _write_stub_script(path: Path, label: str) -> None:
    path.write_text(
        "[CmdletBinding()]\n"
        "param([string]$BranchName)\n"
        f"$value = '{label}:' + [string]$BranchName\n"
        "[IO.File]::WriteAllText($env:APPLY_UPDATE_LAUNCHER_RESULT, $value)\n"
        "$launcherPath = Join-Path (Split-Path -Parent $PSScriptRoot) "
        "'apply_update.bat'\n"
        "$replacement = \"@echo off`r`nexit /b 99`r`n\"\n"
        "[IO.File]::WriteAllText("
        "$launcherPath, $replacement, [Text.Encoding]::ASCII)\n"
        "exit 0\n",
        encoding="utf-8-sig",
    )


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("cmd.exe") is None,
    reason="cmd.exe execution is only available on Windows.",
)
@pytest.mark.parametrize(
    ("branch_name", "expected_result"),
    (("", "release:"), ("work/0.10.107", "work:work/0.10.107")),
)
def test_apply_update_launcher_executes_through_cmd(
    tmp_path: Path,
    branch_name: str,
    expected_result: str,
) -> None:
    root = tmp_path / "launcher repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "apply_update.bat", root / "apply_update.bat")
    shutil.copy2(
        PROJECT_ROOT / "scripts" / "apply_update_entrypoint.ps1",
        scripts / "apply_update_entrypoint.ps1",
    )
    _write_stub_script(scripts / "apply_update_release.ps1", "release")
    _write_stub_script(scripts / "apply_update_work.ps1", "work")

    result_path = tmp_path / "launcher-result.txt"
    environment = dict(os.environ)
    environment["APPLY_UPDATE_LAUNCHER_RESULT"] = str(result_path)
    command = "apply_update.bat"
    if branch_name:
        command += f' "{branch_name}"'

    result = subprocess.run(
        ["cmd.exe", "/d", "/c", command],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="mbcs",
        errors="replace",
        env=environment,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert result_path.read_text(encoding="utf-8") == expected_result
    assert (root / "apply_update.bat").read_text(encoding="ascii") == (
        "@echo off\nexit /b 99\n"
    )
