from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from src.services import scheduler_service
from src.services.scheduler_service import (
    TASK_NAME,
    calculate_quota_interval_recommendation,
    delete_refresh_scheduler,
    get_refresh_scheduler_status,
    register_or_update_refresh_scheduler,
)


def test_quota_recommendation_uses_normal_and_retry_worst_case() -> None:
    recommendation = calculate_quota_interval_recommendation(
        portal_query_limit=50,
        portal_pages_per_query=2,
        naver_daily_limit=25000,
        kakao_daily_limit=50000,
        max_retries=2,
    )

    assert recommendation.planned_calls_per_run == 200
    assert recommendation.retry_worst_calls_per_run == 600
    assert recommendation.normal_min_interval_minutes == 12
    assert recommendation.retry_safe_min_interval_minutes == 36
    assert recommendation.runs_per_day(30) == 48
    assert recommendation.planned_calls_per_day(30) == 9600
    assert recommendation.retry_worst_calls_per_day(60) == 14400


def test_register_updates_same_task_with_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    batch_path = tmp_path / "run_trend_refresh.bat"
    batch_path.write_text("@echo off\n", encoding="utf-8")
    captured: list[list[str]] = []

    def fake_run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        captured.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "SUCCESS", "")

    monkeypatch.setattr(scheduler_service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(scheduler_service, "_run_schtasks", fake_run)

    result = register_or_update_refresh_scheduler(tmp_path, interval_minutes=60)

    assert result.success is True
    assert captured
    command = captured[0]
    assert command[:4] == ["/Create", "/TN", TASK_NAME, "/TR"]
    assert command[command.index("/MO") + 1] == "60"
    assert "/F" in command
    assert "/IT" in command
    assert str(batch_path.resolve()) in command[4]


def test_register_rejects_invalid_interval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler_service.platform, "system", lambda: "Windows")
    result = register_or_update_refresh_scheduler(tmp_path, interval_minutes=1)
    assert result.success is False
    assert "5~1439분" in result.message


def test_scheduler_status_reads_interval_and_project_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_path = tmp_path / "run_trend_refresh.bat"
    batch_path.write_text("@echo off\n", encoding="utf-8")
    xml = f"""<?xml version=\"1.0\" encoding=\"UTF-16\"?>
    <Task xmlns=\"http://schemas.microsoft.com/windows/2004/02/mit/task\">
      <Triggers><TimeTrigger><Repetition><Interval>PT1H</Interval></Repetition></TimeTrigger></Triggers>
      <Actions><Exec><Command>powershell.exe</Command><Arguments>-Command &amp; '{batch_path.resolve()}'</Arguments></Exec></Actions>
    </Task>
    """

    def fake_run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        if "/XML" in arguments:
            return subprocess.CompletedProcess(arguments, 0, xml, "")
        return subprocess.CompletedProcess(
            arguments,
            0,
            f'"\\{TASK_NAME}","2026-07-16 오전 11:30:00","Ready"\n',
            "",
        )

    monkeypatch.setattr(scheduler_service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(scheduler_service, "_run_schtasks", fake_run)

    status = get_refresh_scheduler_status(tmp_path)

    assert status.registered is True
    assert status.interval_minutes == 60
    assert status.next_run == "2026-07-16 오전 11:30:00"
    assert status.action_matches_project is True


def test_delete_scheduler_uses_fixed_task_name(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def fake_run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        captured.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "SUCCESS", "")

    monkeypatch.setattr(scheduler_service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(scheduler_service, "_run_schtasks", fake_run)

    result = delete_refresh_scheduler()

    assert result.success is True
    assert captured == [["/Delete", "/TN", TASK_NAME, "/F"]]


def test_refresh_batch_quotes_python_and_safe_script_paths() -> None:
    project_root = Path(__file__).resolve().parents[1]
    batch_path = project_root / "run_trend_refresh.bat"
    batch_bytes = batch_path.read_bytes()
    batch_text = batch_bytes.decode("utf-8")

    assert 'set "PYTHON_EXE=%~dp0.venv\\Scripts\\python.exe"' in batch_text
    assert '"%PYTHON_EXE%" -u "%~dp0scripts\\refresh_trends_safe.py"' in batch_text
    assert "exit /b %EXIT_CODE%" in batch_text
    assert b"\r\n" in batch_bytes
    assert b"\n" not in batch_bytes.replace(b"\r\n", b"")
