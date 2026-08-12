from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_BAT = PROJECT_ROOT / "run_app.bat"
STOP_BAT = PROJECT_ROOT / "stop_app.bat"
SUPERVISOR = PROJECT_ROOT / "scripts" / "app_supervisor.ps1"
STOP_SCRIPT = PROJECT_ROOT / "scripts" / "stop_registered_app.ps1"
UPDATE_REQUEST_SCRIPT = PROJECT_ROOT / "scripts" / "apply_update_and_restart.ps1"
STREAMLIT_CONFIG = PROJECT_ROOT / ".streamlit" / "config.toml"


def test_run_and_stop_launchers_use_same_managed_port() -> None:
    run_text = RUN_BAT.read_text(encoding="utf-8")
    stop_text = STOP_BAT.read_text(encoding="utf-8")

    assert "scripts\\app_supervisor.ps1" in run_text
    assert "-Action Run" in run_text
    assert "-Port 8518" in run_text
    assert "-m streamlit run" not in run_text
    assert "scripts\\stop_registered_app.ps1" in stop_text
    assert "-Port 8518" in stop_text
    assert "app_supervisor.ps1" not in stop_text


def test_streamlit_config_reserves_project_specific_loopback_port() -> None:
    text = STREAMLIT_CONFIG.read_text(encoding="utf-8")

    assert "[server]" in text
    assert 'address = "127.0.0.1"' in text
    assert "port = 8518" in text
    assert "headless = true" in text
    assert "runOnSave = false" in text


def test_supervisor_keeps_terminal_ownership_and_prevents_duplicate_instances() -> None:
    text = SUPERVISOR.read_text(encoding="utf-8-sig")

    assert "Local\\content-trend-tracker-app-supervisor" in text
    assert "Start-Process" in text
    assert "-NoNewWindow" in text
    assert "-PassThru" in text
    assert "Ctrl+C" in text
    assert "CONTENT_TREND_TRACKER_SUPERVISOR_PID" in text
    assert "CONTENT_TREND_TRACKER_RUNTIME_STATE" in text
    assert "app_runtime.json" in text
    assert "Test-PortAvailable" in text
    assert "Get-PortOwnerPid" in text
    assert "전용 포트 $Port 이 이미 사용 중" in text


def test_exact_stop_uses_only_registered_process_identities() -> None:
    text = STOP_SCRIPT.read_text(encoding="utf-8-sig")

    assert "app_runtime.json" in text
    assert "project_root" in text
    assert "supervisor_start_ticks" in text
    assert "streamlit_start_ticks" in text
    assert "Test-ProcessIdentity" in text
    assert "taskkill.exe" in text
    assert "/PID $ProcessId /T /F" in text
    assert "상태 파일 없이 포트 $Port 이 사용 중" in text
    assert "임의 종료하지 않았습니다" in text
    assert "Stop-Process -Name python" not in text
    assert "taskkill /IM python.exe" not in text


def test_supervisor_applies_update_only_after_managed_streamlit_exits() -> None:
    text = SUPERVISOR.read_text(encoding="utf-8-sig")

    stopped_index = text.index("Write-RuntimeState -State 'streamlit_stopped'")
    request_index = text.index("Test-UpdateRequest `", stopped_index)
    apply_index = text.index("Invoke-RequestedUpdate -Request $request")
    assert stopped_index < request_index < apply_index
    assert "apply_update.bat" in text
    assert "같은 터미널 관리자가 앱을 다시 시작" in text


@pytest.mark.parametrize(
    "script_path",
    (SUPERVISOR, STOP_SCRIPT, UPDATE_REQUEST_SCRIPT),
)
def test_managed_lifecycle_powershell_scripts_parse(script_path: Path) -> None:
    powershell = (
        shutil.which("pwsh.exe")
        or shutil.which("pwsh")
        or shutil.which("powershell.exe")
        or shutil.which("powershell")
    )
    if not powershell:
        pytest.skip("PowerShell parser is unavailable")

    escaped_path = str(script_path).replace("'", "''")
    parser_command = (
        "$tokens=$null; $errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{escaped_path}', [ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors.Count -gt 0) { "
        "$errors | ForEach-Object { [Console]::Error.WriteLine($_.Message) }; "
        "exit 1 }"
    )
    completed = subprocess.run(
        [powershell, "-NoProfile", "-Command", parser_command],
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
