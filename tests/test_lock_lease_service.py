from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from threading import Event

from src.services.lock_lease_service import (
    LockHeartbeatThread,
    lock_lease_is_current,
)
from src.services.trend_clustering_lock_service import (
    LOCK_FILENAME as CLUSTERING_LOCK_FILENAME,
    acquire_trend_clustering_lock,
    inspect_trend_clustering_lock,
)
from src.services.trend_refresh_lock_service import (
    LOCK_FILENAME as REFRESH_LOCK_FILENAME,
    acquire_trend_refresh_lock,
    inspect_trend_refresh_lock,
)


def test_lock_lease_keeps_legacy_metadata_compatible() -> None:
    assert lock_lease_is_current("", 0, now=datetime(2030, 1, 1)) is True
    assert lock_lease_is_current(
        "invalid-heartbeat",
        180,
        now=datetime(2030, 1, 1),
    ) is True


def test_lock_lease_distinguishes_current_and_expired_heartbeat() -> None:
    current = datetime(2030, 1, 1, 12, 0, 0)

    assert lock_lease_is_current(
        "2030-01-01T11:58:00",
        180,
        now=current,
    ) is True
    assert lock_lease_is_current(
        "2030-01-01T11:56:59",
        180,
        now=current,
    ) is False


def test_heartbeat_thread_runs_callback_and_stops() -> None:
    called = Event()

    def callback() -> bool:
        called.set()
        return False

    heartbeat = LockHeartbeatThread(callback, interval_seconds=0.01)
    heartbeat.start()

    assert called.wait(timeout=0.5) is True
    heartbeat.stop()


def test_refresh_lock_heartbeat_renews_owned_metadata(tmp_path: Path) -> None:
    attempt = acquire_trend_refresh_lock(
        tmp_path,
        launcher="test-refresh",
        process_id=321,
        now=datetime(2030, 1, 1, 12, 0, 0),
        process_identity_reader=lambda pid: f"process-start:{pid}",
    )
    assert attempt.lock is not None

    renewed = attempt.lock.refresh_heartbeat(
        now=datetime(2030, 1, 1, 12, 1, 0)
    )
    payload = json.loads(attempt.lock.path.read_text(encoding="utf-8"))

    assert renewed is True
    assert payload["token"] == attempt.lock.owner.token
    assert payload["heartbeat_at"] == "2030-01-01T12:01:00"
    assert payload["lease_seconds"] == 180
    attempt.lock.release()


def test_expired_refresh_lease_is_recovered_even_when_pid_is_alive(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "data" / REFRESH_LOCK_FILENAME
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 321,
                "started_at": "2000-01-01T00:00:00",
                "launcher": "old-refresh",
                "token": "old-token",
                "process_start_identity": "process-start:321",
                "heartbeat_at": "2000-01-01T00:00:00",
                "lease_seconds": 180,
            }
        ),
        encoding="utf-8",
    )

    attempt = acquire_trend_refresh_lock(
        tmp_path,
        launcher="replacement-refresh",
        process_id=654,
        is_process_alive=lambda pid: pid == 321,
        process_identity_reader=lambda pid: f"process-start:{pid}",
    )

    assert attempt.acquired is True
    assert attempt.lock is not None
    assert attempt.lock.owner.pid == 654
    assert attempt.lock.owner.token != "old-token"
    attempt.lock.release()


def test_expired_clustering_lease_is_inactive_and_recoverable(
    tmp_path: Path,
) -> None:
    data_directory = tmp_path / "data"
    data_directory.mkdir(parents=True)
    lock_path = data_directory / CLUSTERING_LOCK_FILENAME
    original_text = json.dumps(
        {
            "pid": 777,
            "started_at": "2000-01-01T00:00:00",
            "launcher": "old-clustering",
            "token": "old-token",
            "process_start_identity": "process-start:777",
            "heartbeat_at": "2000-01-01T00:00:00",
            "lease_seconds": 180,
        }
    )
    lock_path.write_text(original_text, encoding="utf-8")

    status = inspect_trend_clustering_lock(
        data_directory=data_directory,
        is_process_alive=lambda pid: pid == 777,
        process_identity_reader=lambda pid: f"process-start:{pid}",
    )

    assert status.exists is True
    assert status.active is False
    assert lock_path.read_text(encoding="utf-8") == original_text

    attempt = acquire_trend_clustering_lock(
        data_directory=data_directory,
        launcher="replacement-clustering",
        process_id=888,
        is_process_alive=lambda pid: pid == 777,
        process_identity_reader=lambda pid: f"process-start:{pid}",
    )

    assert attempt.acquired is True
    assert attempt.lock is not None
    assert attempt.lock.owner.pid == 888
    assert attempt.lock.owner.token != "old-token"
    attempt.lock.release()


def test_legacy_live_lock_without_lease_remains_active(tmp_path: Path) -> None:
    lock_path = tmp_path / "data" / REFRESH_LOCK_FILENAME
    lock_path.parent.mkdir(parents=True)
    original_text = json.dumps(
        {
            "pid": 999,
            "started_at": "2000-01-01T00:00:00",
            "launcher": "legacy-refresh",
            "token": "legacy-token",
        }
    )
    lock_path.write_text(original_text, encoding="utf-8")

    status = inspect_trend_refresh_lock(
        tmp_path,
        is_process_alive=lambda pid: pid == 999,
        process_identity_reader=lambda _pid: "",
    )

    assert status.exists is True
    assert status.active is True
    assert status.owner is not None
    assert status.owner.heartbeat_at == ""
    assert status.owner.lease_seconds == 0
    assert lock_path.read_text(encoding="utf-8") == original_text
