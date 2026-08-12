from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.services.dashboard_refresh_progress_service import (
    clear_dashboard_refresh_progress,
    finish_dashboard_refresh_progress,
    read_dashboard_refresh_progress,
    start_dashboard_refresh_progress,
    update_dashboard_refresh_progress,
)


def test_progress_file_tracks_start_update_and_success(tmp_path: Path) -> None:
    path = tmp_path / "dashboard-progress.json"

    started = start_dashboard_refresh_progress(
        pid=1234,
        run_id="run-1",
        message="준비 중",
        path=path,
        now=datetime(2026, 8, 7, 2, 0, 0),
    )
    updated = update_dashboard_refresh_progress(
        57.6,
        "NAVER·Daum 수집 중",
        path=path,
        now=datetime(2026, 8, 7, 2, 1, 0),
    )
    finished = finish_dashboard_refresh_progress(
        success=True,
        message="완료",
        summary="수집 결과 저장 완료",
        path=path,
        now=datetime(2026, 8, 7, 2, 2, 0),
    )

    assert started.active is True
    assert updated.value == 58
    assert updated.run_id == "run-1"
    assert updated.pid == 1234
    assert finished.status == "success"
    assert finished.value == 100
    assert finished.started_at == "2026-08-07T02:00:00"
    assert finished.finished_at == "2026-08-07T02:02:00"
    assert [row["message"] for row in finished.steps] == [
        "준비 중",
        "NAVER·Daum 수집 중",
        "완료",
    ]
    assert [row["value"] for row in finished.steps] == [1, 58, 100]
    assert [row["elapsed_seconds"] for row in finished.steps] == [0.0, 60.0, 120.0]
    assert read_dashboard_refresh_progress(path) == finished


def test_duplicate_progress_step_is_not_appended(tmp_path: Path) -> None:
    path = tmp_path / "dashboard-progress.json"
    start_dashboard_refresh_progress(
        pid=5678,
        message="준비 중",
        path=path,
        now=datetime(2026, 8, 7, 2, 0, 0),
    )
    first = update_dashboard_refresh_progress(
        50,
        "1차 군집 구성 중",
        path=path,
        now=datetime(2026, 8, 7, 2, 1, 0),
    )
    second = update_dashboard_refresh_progress(
        50,
        "1차 군집 구성 중",
        path=path,
        now=datetime(2026, 8, 7, 2, 1, 1),
    )

    assert len(first.steps) == 2
    assert len(second.steps) == 2


def test_failure_preserves_last_progress_and_can_be_cleared(tmp_path: Path) -> None:
    path = tmp_path / "dashboard-progress.json"
    start_dashboard_refresh_progress(pid=5678, path=path)
    update_dashboard_refresh_progress(83, "군집 저장 중", path=path)

    failed = finish_dashboard_refresh_progress(
        success=False,
        message="저장 실패",
        error_message="duplicate key",
        path=path,
    )

    assert failed.status == "failure"
    assert failed.value == 83
    assert failed.error_message == "duplicate key"
    assert failed.steps[-1]["status"] == "failure"
    assert failed.steps[-1]["message"] == "저장 실패"
    clear_dashboard_refresh_progress(path)
    assert read_dashboard_refresh_progress(path) is None


def test_invalid_progress_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "dashboard-progress.json"
    path.write_text("not-json", encoding="utf-8")

    assert read_dashboard_refresh_progress(path) is None
