from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.services import web_update_service as update_service
from src.services.web_update_launch_runtime import (
    MANAGED_APP_PORT,
    app_runtime_state_path,
    app_update_request_path,
    install_web_update_launch_contract,
    launch_update_and_restart_verified,
    update_bootstrap_log_path,
)


def _candidate() -> update_service.WorkBranchCandidate:
    return update_service.WorkBranchCandidate(
        branch_name="work/0.10.105",
        remote_ref="origin/work/0.10.105",
        commit_sha="a" * 40,
        committed_at="2026-08-06T12:00:00+09:00",
        ahead=1,
        behind=0,
        changed_files=2,
        eligible=True,
    )


def _prepare_script(root: Path) -> None:
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "apply_update_and_restart.ps1").write_text(
        "exit 0\n",
        encoding="utf-8",
    )


def _prepare_runtime(root: Path, *, streamlit_pid: int = 12345) -> None:
    state_path = app_runtime_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_root": str(root.resolve()),
                "port": MANAGED_APP_PORT,
                "supervisor_pid": 777,
                "supervisor_start_ticks": 111111,
                "streamlit_pid": streamlit_pid,
                "streamlit_start_ticks": 222222,
            }
        ),
        encoding="utf-8",
    )


def test_launcher_returns_only_after_request_worker_records_started_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _prepare_script(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    _prepare_runtime(tmp_path)
    captured = {}

    class Process:
        pid = 24680

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        update_service.write_update_status(
            {
                "status": "waiting_for_app",
                "stage": "stop_app",
                "message": "request worker started",
            }
        )
        return Process()

    pid = launch_update_and_restart_verified(
        _candidate(),
        tmp_path,
        parent_pid=12345,
        popen_factory=fake_popen,
        powershell_executable="pwsh.exe",
        sleep=lambda _seconds: None,
    )

    assert pid == 24680
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["stderr"] is not None
    assert update_bootstrap_log_path().is_file()
    assert "-SupervisorPid" in captured["command"]
    assert "777" in captured["command"]
    assert "-ParentStartTicks" in captured["command"]
    assert "222222" in captured["command"]
    assert "-RequestPath" in captured["command"]
    assert str(app_update_request_path().resolve()) in captured["command"]
    if os.name == "nt":
        detached = int(getattr(__import__("subprocess"), "DETACHED_PROCESS", 0))
        assert captured["kwargs"]["creationflags"] & detached == 0


def test_launcher_rejects_direct_unmanaged_streamlit(tmp_path: Path, monkeypatch) -> None:
    _prepare_script(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    with pytest.raises(RuntimeError, match="run_app.bat"):
        launch_update_and_restart_verified(
            _candidate(),
            tmp_path,
            parent_pid=12345,
            powershell_executable="pwsh.exe",
        )


def test_launcher_rejects_different_registered_streamlit_pid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _prepare_script(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    _prepare_runtime(tmp_path, streamlit_pid=99999)

    with pytest.raises(RuntimeError, match="중복 실행"):
        launch_update_and_restart_verified(
            _candidate(),
            tmp_path,
            parent_pid=12345,
            powershell_executable="pwsh.exe",
        )


def test_launcher_times_out_stops_worker_and_records_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _prepare_script(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    _prepare_runtime(tmp_path)

    class Process:
        pid = 13579
        terminated = False
        killed = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True

    process = Process()
    times = iter((0.0, 2.0))

    with pytest.raises(RuntimeError, match="제한 시간"):
        launch_update_and_restart_verified(
            _candidate(),
            tmp_path,
            parent_pid=12345,
            popen_factory=lambda command, **kwargs: process,
            powershell_executable="pwsh.exe",
            startup_timeout_seconds=1.0,
            monotonic=lambda: next(times),
            sleep=lambda _seconds: None,
        )

    assert process.terminated is True
    status = update_service.read_update_status()
    assert status["status"] == "failed"
    assert status["stage"] == "launch_failed"
    assert "제한 시간" in status["message"]


def test_installer_replaces_service_launcher_idempotently(monkeypatch) -> None:
    original = update_service.launch_update_and_restart
    monkeypatch.setattr(update_service, "launch_update_and_restart", original)

    install_web_update_launch_contract()
    first = update_service.launch_update_and_restart
    install_web_update_launch_contract()

    assert first is launch_update_and_restart_verified
    assert update_service.launch_update_and_restart is first
