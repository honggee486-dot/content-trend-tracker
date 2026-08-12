from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUEST_SCRIPT = PROJECT_ROOT / "scripts" / "apply_update_and_restart.ps1"
SUPERVISOR_SCRIPT = PROJECT_ROOT / "scripts" / "app_supervisor.ps1"


def test_web_update_request_validates_managed_process_identity() -> None:
    text = REQUEST_SCRIPT.read_text(encoding="utf-8-sig")

    assert "^work/\\d+\\.\\d+\\.\\d+" in text
    assert "^[0-9a-fA-F]{40}$" in text
    assert "ParentStartTicks" in text
    assert "SupervisorStartTicks" in text
    assert "RuntimeStatePath" in text
    assert "RequestPath" in text
    assert "Assert-ProcessIdentity" in text
    assert "streamlit_start_ticks" in text
    assert "supervisor_start_ticks" in text
    assert "Stop-Process -Id $ParentPid -Force" in text


def test_web_update_request_delegates_apply_and_restart_to_supervisor() -> None:
    request_text = REQUEST_SCRIPT.read_text(encoding="utf-8-sig")
    supervisor_text = SUPERVISOR_SCRIPT.read_text(encoding="utf-8-sig")

    assert "app_update_request.json" not in request_text
    assert "Write-JsonAtomic -Path $RequestPath" in request_text
    assert "apply_update.bat" not in request_text
    # run_app.bat이라는 사용자 안내 문구는 허용하되, 요청 스크립트가 직접
    # 앱을 재실행하는 명령은 포함하지 않아야 합니다.
    assert "Start-Process" not in request_text
    assert "& $runBat" not in request_text
    assert "Start-Application" not in request_text
    assert "Invoke-Expression" not in request_text
    assert "iex " not in request_text.casefold()

    assert "Invoke-RequestedUpdate" in supervisor_text
    assert "apply_update.bat" in supervisor_text
    assert "ls-remote', 'origin'" in supervisor_text
    assert "same 터미널" not in supervisor_text.casefold()
    assert "같은 터미널 관리자가 앱을 다시 시작" in supervisor_text
    assert "failed_restarted" in supervisor_text
    assert "failed_restart_required" in supervisor_text
    assert "trend_refresh.lock" in supervisor_text
    assert "trend_clustering.lock" in supervisor_text


def test_failed_request_removes_pending_update_file() -> None:
    text = REQUEST_SCRIPT.read_text(encoding="utf-8-sig")

    assert "$RequestWritten = $false" in text
    assert "$RequestWritten = $true" in text
    assert "Remove-Item -LiteralPath $RequestPath" in text
    assert "request_failed" in text
