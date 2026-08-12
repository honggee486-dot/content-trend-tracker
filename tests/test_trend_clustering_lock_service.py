from __future__ import annotations

import json
from pathlib import Path

from src.services.trend_clustering_lock_service import (
    LOCK_FILENAME,
    acquire_trend_clustering_lock,
    inspect_trend_clustering_lock,
)


def test_inspection_reads_live_owner_without_changing_lock(tmp_path: Path) -> None:
    data_directory = tmp_path / "data"
    data_directory.mkdir(parents=True)
    lock_path = data_directory / LOCK_FILENAME
    original_text = json.dumps(
        {
            "pid": 654,
            "started_at": "2026-08-07T09:20:00",
            "launcher": "process_cluster_backlog",
            "token": "fake-token",
        }
    )
    lock_path.write_text(original_text, encoding="utf-8")

    status = inspect_trend_clustering_lock(
        data_directory=data_directory,
        is_process_alive=lambda pid: pid == 654,
    )

    assert status.exists is True
    assert status.active is True
    assert status.owner is not None
    assert status.owner.launcher == "process_cluster_backlog"
    assert status.owner.process_start_identity == ""
    assert lock_path.read_text(encoding="utf-8") == original_text


def test_matching_process_identity_blocks_second_clustering_job(tmp_path: Path) -> None:
    data_directory = tmp_path / "data"
    data_directory.mkdir(parents=True)
    lock_path = data_directory / LOCK_FILENAME
    original_text = json.dumps(
        {
            "pid": 654,
            "started_at": "2026-08-07T05:00:00",
            "launcher": "process_cluster_backlog",
            "token": "active-token",
            "process_start_identity": "windows-filetime:654",
        }
    )
    lock_path.write_text(original_text, encoding="utf-8")

    attempt = acquire_trend_clustering_lock(
        data_directory=data_directory,
        launcher="contender",
        process_id=777,
        is_process_alive=lambda pid: pid == 654,
        process_identity_reader=lambda pid: f"windows-filetime:{pid}",
    )

    assert attempt.acquired is False
    assert attempt.active_owner is not None
    assert attempt.active_owner.process_start_identity == "windows-filetime:654"
    assert lock_path.read_text(encoding="utf-8") == original_text


def test_reused_pid_is_replaced_by_new_clustering_lock(tmp_path: Path) -> None:
    data_directory = tmp_path / "data"
    data_directory.mkdir(parents=True)
    lock_path = data_directory / LOCK_FILENAME
    lock_path.write_text(
        json.dumps(
            {
                "pid": 654,
                "started_at": "2026-08-07T04:00:00",
                "launcher": "old-clustering-job",
                "token": "old-token",
                "process_start_identity": "windows-filetime:111",
            }
        ),
        encoding="utf-8",
    )

    attempt = acquire_trend_clustering_lock(
        data_directory=data_directory,
        launcher="replacement",
        process_id=777,
        is_process_alive=lambda pid: pid == 654,
        process_identity_reader=lambda pid: f"windows-filetime:{pid}",
    )

    assert attempt.acquired is True
    assert attempt.lock is not None
    assert attempt.lock.owner.pid == 777
    assert attempt.lock.owner.process_start_identity == "windows-filetime:777"
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["token"] != "old-token"
    assert payload["process_start_identity"] == "windows-filetime:777"
    attempt.lock.release()


def test_inspection_marks_reused_pid_inactive_without_deleting_lock(tmp_path: Path) -> None:
    data_directory = tmp_path / "data"
    data_directory.mkdir(parents=True)
    lock_path = data_directory / LOCK_FILENAME
    original_text = json.dumps(
        {
            "pid": 654,
            "started_at": "2026-08-07T04:00:00",
            "launcher": "old-clustering-job",
            "token": "old-token",
            "process_start_identity": "windows-filetime:111",
        }
    )
    lock_path.write_text(original_text, encoding="utf-8")

    status = inspect_trend_clustering_lock(
        data_directory=data_directory,
        is_process_alive=lambda pid: pid == 654,
        process_identity_reader=lambda pid: "windows-filetime:222",
    )

    assert status.exists is True
    assert status.active is False
    assert status.owner is not None
    assert status.owner.process_start_identity == "windows-filetime:111"
    assert lock_path.read_text(encoding="utf-8") == original_text
