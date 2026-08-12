from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from threading import Thread
from time import sleep

import pytest

from src.services.trend_refresh_lock_service import (
    LOCK_FILENAME,
    TrendRefreshLockOwner,
    acquire_trend_refresh_lock,
    inspect_trend_refresh_lock,
    run_with_trend_refresh_lock,
)


def test_first_run_acquires_lock_and_second_run_is_skipped(tmp_path: Path) -> None:
    first = acquire_trend_refresh_lock(tmp_path, launcher="first")

    assert first.acquired is True
    assert first.lock is not None

    second = acquire_trend_refresh_lock(tmp_path, launcher="second")

    assert second.acquired is False
    assert second.active_owner is not None
    assert second.active_owner.pid == first.lock.owner.pid
    assert "이미 실행 중" in second.message

    first.lock.release()
    assert not (tmp_path / "data" / LOCK_FILENAME).exists()


def test_new_lock_persists_process_start_identity(tmp_path: Path) -> None:
    attempt = acquire_trend_refresh_lock(
        tmp_path,
        launcher="identity-writer",
        process_id=12345,
        process_identity_reader=lambda pid: f"process-start:{pid}",
    )

    assert attempt.lock is not None
    payload = json.loads(attempt.lock.path.read_text(encoding="utf-8"))
    assert payload["process_start_identity"] == "process-start:12345"
    assert attempt.lock.owner.process_start_identity == "process-start:12345"
    attempt.lock.release()


def test_stale_lock_is_recovered(tmp_path: Path) -> None:
    lock_path = tmp_path / "data" / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 987654,
                "started_at": "2026-07-16T12:00:00",
                "launcher": "stale",
                "token": "stale-token",
            }
        ),
        encoding="utf-8",
    )

    attempt = acquire_trend_refresh_lock(
        tmp_path,
        launcher="replacement",
        process_id=12345,
        is_process_alive=lambda pid: False,
    )

    assert attempt.acquired is True
    assert attempt.lock is not None
    assert attempt.lock.owner.pid == 12345
    assert attempt.lock.owner.token != "stale-token"
    attempt.lock.release()


def test_live_process_lock_is_not_deleted(tmp_path: Path) -> None:
    lock_path = tmp_path / "data" / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True)
    original_payload = {
        "pid": 321,
        "started_at": "2026-07-16T12:00:00",
        "launcher": "active",
        "token": "active-token",
    }
    lock_path.write_text(json.dumps(original_payload), encoding="utf-8")

    attempt = acquire_trend_refresh_lock(
        tmp_path,
        launcher="blocked",
        process_id=654,
        is_process_alive=lambda pid: pid == 321,
    )

    assert attempt.acquired is False
    assert attempt.active_owner == TrendRefreshLockOwner(
        pid=321,
        started_at="2026-07-16T12:00:00",
        launcher="active",
        token="active-token",
    )
    assert json.loads(lock_path.read_text(encoding="utf-8")) == original_payload


def test_matching_process_identity_keeps_live_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "data" / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True)
    payload = {
        "pid": 321,
        "started_at": "2026-08-07T06:00:00",
        "launcher": "active",
        "token": "active-token",
        "process_start_identity": "windows-filetime:111",
    }
    lock_path.write_text(json.dumps(payload), encoding="utf-8")

    attempt = acquire_trend_refresh_lock(
        tmp_path,
        launcher="blocked",
        process_id=654,
        is_process_alive=lambda pid: pid == 321,
        process_identity_reader=lambda pid: "windows-filetime:111",
    )

    assert attempt.acquired is False
    assert attempt.active_owner is not None
    assert attempt.active_owner.process_start_identity == "windows-filetime:111"
    assert json.loads(lock_path.read_text(encoding="utf-8")) == payload


def test_reused_live_pid_lock_is_recovered(tmp_path: Path) -> None:
    lock_path = tmp_path / "data" / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 321,
                "started_at": "2026-08-07T05:00:00",
                "launcher": "old-process",
                "token": "old-token",
                "process_start_identity": "windows-filetime:111",
            }
        ),
        encoding="utf-8",
    )

    attempt = acquire_trend_refresh_lock(
        tmp_path,
        launcher="replacement",
        process_id=654,
        is_process_alive=lambda pid: pid == 321,
        process_identity_reader=lambda pid: f"windows-filetime:{pid}",
    )

    assert attempt.acquired is True
    assert attempt.lock is not None
    assert attempt.lock.owner.pid == 654
    assert attempt.lock.owner.process_start_identity == "windows-filetime:654"
    assert attempt.lock.owner.token != "old-token"
    attempt.lock.release()


def test_inspection_reads_live_owner_without_changing_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "data" / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True)
    original_text = json.dumps(
        {
            "pid": 321,
            "started_at": "2026-08-07T09:10:00",
            "launcher": "dashboard_manual_refresh",
            "token": "fake-token",
        }
    )
    lock_path.write_text(original_text, encoding="utf-8")

    status = inspect_trend_refresh_lock(
        tmp_path,
        is_process_alive=lambda pid: pid == 321,
    )

    assert status.exists is True
    assert status.active is True
    assert status.owner is not None
    assert status.owner.launcher == "dashboard_manual_refresh"
    assert lock_path.read_text(encoding="utf-8") == original_text


def test_inspection_rejects_reused_pid_without_changing_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "data" / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True)
    original_text = json.dumps(
        {
            "pid": 321,
            "started_at": "2026-08-07T05:00:00",
            "launcher": "old-process",
            "token": "old-token",
            "process_start_identity": "windows-filetime:111",
        }
    )
    lock_path.write_text(original_text, encoding="utf-8")

    status = inspect_trend_refresh_lock(
        tmp_path,
        is_process_alive=lambda pid: pid == 321,
        process_identity_reader=lambda pid: "windows-filetime:222",
    )

    assert status.exists is True
    assert status.active is False
    assert status.owner is not None
    assert status.owner.process_start_identity == "windows-filetime:111"
    assert lock_path.read_text(encoding="utf-8") == original_text


def test_lock_is_released_after_exception(tmp_path: Path) -> None:
    attempt = acquire_trend_refresh_lock(
        tmp_path,
        launcher="exception-test",
        now=datetime(2026, 7, 16, 12, 0, 0),
    )
    assert attempt.lock is not None

    with pytest.raises(RuntimeError):
        with attempt.lock:
            raise RuntimeError("boom")

    assert not (tmp_path / "data" / LOCK_FILENAME).exists()


def test_owner_does_not_delete_replaced_lock(tmp_path: Path) -> None:
    attempt = acquire_trend_refresh_lock(tmp_path, launcher="owner")
    assert attempt.lock is not None
    lock_path = attempt.lock.path

    replacement = {
        "pid": 777,
        "started_at": "2026-07-16T12:30:00",
        "launcher": "replacement",
        "token": "replacement-token",
    }
    lock_path.write_text(json.dumps(replacement), encoding="utf-8")

    attempt.lock.release()

    assert lock_path.exists()
    assert json.loads(lock_path.read_text(encoding="utf-8")) == replacement


def test_overlap_wrapper_returns_success_without_running_callback(
    tmp_path: Path,
) -> None:
    first = acquire_trend_refresh_lock(tmp_path, launcher="first")
    assert first.lock is not None
    called = False
    messages: list[str] = []

    def runner() -> int:
        nonlocal called
        called = True
        return 1

    result = run_with_trend_refresh_lock(
        tmp_path,
        launcher="second",
        runner=runner,
        message_callback=messages.append,
    )

    assert result == 0
    assert called is False
    assert messages and messages[0].startswith("[SKIP]")
    first.lock.release()


def test_overlap_history_failure_does_not_change_success_exit_code(tmp_path: Path) -> None:
    first = acquire_trend_refresh_lock(tmp_path, launcher="first")
    assert first.lock is not None

    result = run_with_trend_refresh_lock(
        tmp_path,
        launcher="second",
        runner=lambda: 99,
        message_callback=lambda _message: None,
        overlap_callback=lambda _attempt: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )

    assert result == 0
    first.lock.release()


def test_wrapper_releases_lock_after_runner_exception(tmp_path: Path) -> None:
    def runner() -> int:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_with_trend_refresh_lock(
            tmp_path,
            launcher="runner",
            runner=runner,
            message_callback=lambda message: None,
        )

    assert not (tmp_path / "data" / LOCK_FILENAME).exists()


def test_in_progress_lock_metadata_is_not_mistaken_for_stale(tmp_path: Path) -> None:
    lock_path = tmp_path / "data" / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("", encoding="utf-8")

    payload = {
        "pid": os.getpid(),
        "started_at": "2026-07-16T12:00:00",
        "launcher": "slow-writer",
        "token": "slow-token",
    }

    def finish_metadata() -> None:
        sleep(0.02)
        lock_path.write_text(json.dumps(payload), encoding="utf-8")

    writer = Thread(target=finish_metadata)
    writer.start()
    attempt = acquire_trend_refresh_lock(tmp_path, launcher="contender")
    writer.join()

    assert attempt.acquired is False
    assert attempt.active_owner is not None
    assert attempt.active_owner.token == "slow-token"
    assert json.loads(lock_path.read_text(encoding="utf-8")) == payload
