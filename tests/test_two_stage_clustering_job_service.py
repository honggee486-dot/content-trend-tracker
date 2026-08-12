from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import duckdb

from src.database import connect_database, init_database, set_setting
import src.services.trend_clustering_job_service as jobs


class _Lock:
    def release(self) -> None:
        return None


def _successful_lock():
    return SimpleNamespace(
        acquired=True,
        lock=_Lock(),
        message="",
    )


def _memory_job_connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE trend_clustering_jobs (
            job_id VARCHAR PRIMARY KEY,
            status VARCHAR,
            launcher VARCHAR,
            model_name VARCHAR,
            max_batches INTEGER,
            completed_batches INTEGER,
            processed_units INTEGER,
            processed_source_items INTEGER,
            remaining_items INTEGER,
            total_tokens BIGINT,
            error_message VARCHAR,
            created_at TIMESTAMP,
            started_at TIMESTAMP,
            heartbeat_at TIMESTAMP,
            finished_at TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE TABLE trend_clustering_job_batches (
            job_id VARCHAR,
            batch_number INTEGER,
            status VARCHAR,
            scanned_pending_items INTEGER,
            first_stage_units INTEGER,
            all_first_stage_units INTEGER,
            source_items INTEGER,
            url_merged_items INTEGER,
            url_conflict_splits INTEGER,
            title_merged_groups INTEGER,
            existing_candidate_refs INTEGER,
            deferred_units INTEGER,
            processed_units INTEGER,
            processed_source_items INTEGER,
            existing_links INTEGER,
            new_clusters INTEGER,
            uncertain_units INTEGER,
            conflict_units INTEGER,
            needs_review_items INTEGER,
            input_tokens BIGINT,
            output_tokens BIGINT,
            thought_tokens BIGINT,
            total_tokens BIGINT,
            duration_ms INTEGER,
            error_message VARCHAR,
            started_at TIMESTAMP,
            finished_at TIMESTAMP
        )
        """
    )
    return con


def test_active_job_and_newer_overlap_attempt_are_queried_separately() -> None:
    con = _memory_job_connection()
    now = datetime.now()
    con.executemany(
        """
        INSERT INTO trend_clustering_jobs(
            job_id, status, launcher, model_name, max_batches,
            completed_batches, processed_units, processed_source_items,
            remaining_items, total_tokens, error_message, created_at,
            started_at, heartbeat_at, finished_at
        ) VALUES (?, ?, 'dashboard', 'gemini-test', 20, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "active-job",
                "running",
                2,
                600,
                610,
                900,
                1200,
                "",
                now - timedelta(minutes=2),
                now - timedelta(minutes=2),
                now,
                None,
            ),
            (
                "overlap-attempt",
                "skipped_overlap",
                0,
                0,
                0,
                0,
                0,
                "기존 2차 군집 작업이 실행 중이어서 새 군집 작업을 시작하지 않았습니다.",
                now - timedelta(minutes=1),
                now - timedelta(minutes=1),
                now - timedelta(minutes=1),
                now - timedelta(minutes=1),
            ),
        ],
    )

    legacy_latest = jobs.get_latest_clustering_job(con)
    active = jobs.get_active_clustering_job(con)
    latest_attempt = jobs.get_latest_clustering_attempt(con)
    representative = jobs.get_representative_clustering_job(con)

    assert legacy_latest is not None
    assert legacy_latest["job_id"] == "overlap-attempt"
    assert active is not None
    assert active["job_id"] == "active-job"
    assert latest_attempt is not None
    assert latest_attempt["job_id"] == "overlap-attempt"
    assert representative is not None
    assert representative["job_id"] == "active-job"
    con.close()


def test_previous_result_represents_card_when_latest_attempt_was_skipped() -> None:
    con = _memory_job_connection()
    now = datetime.now()
    con.executemany(
        """
        INSERT INTO trend_clustering_jobs(
            job_id, status, launcher, model_name, max_batches,
            completed_batches, processed_units, processed_source_items,
            remaining_items, total_tokens, error_message, created_at,
            started_at, heartbeat_at, finished_at
        ) VALUES (?, ?, 'dashboard', 'gemini-test', 20, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "previous-success",
                "success",
                3,
                900,
                920,
                0,
                1800,
                "",
                now - timedelta(minutes=5),
                now - timedelta(minutes=5),
                now - timedelta(minutes=4),
                now - timedelta(minutes=4),
            ),
            (
                "latest-overlap",
                "skipped_overlap",
                0,
                0,
                0,
                0,
                0,
                "다른 군집 처리 작업이 이미 실행 중입니다.",
                now - timedelta(minutes=1),
                now - timedelta(minutes=1),
                now - timedelta(minutes=1),
                now - timedelta(minutes=1),
            ),
        ],
    )

    latest_attempt = jobs.get_latest_clustering_attempt(con)
    latest_result = jobs.get_latest_clustering_result(con)
    representative = jobs.get_representative_clustering_job(con)

    assert latest_attempt is not None
    assert latest_attempt["job_id"] == "latest-overlap"
    assert latest_result is not None
    assert latest_result["job_id"] == "previous-success"
    assert representative is not None
    assert representative["job_id"] == "previous-success"
    con.close()


def test_skipped_attempt_alone_does_not_create_representative_result() -> None:
    con = _memory_job_connection()
    now = datetime.now()
    con.execute(
        """
        INSERT INTO trend_clustering_jobs(
            job_id, status, launcher, model_name, max_batches,
            completed_batches, processed_units, processed_source_items,
            remaining_items, total_tokens, error_message, created_at,
            started_at, heartbeat_at, finished_at
        ) VALUES (
            'only-overlap', 'skipped_overlap', 'dashboard', 'gemini-test', 20,
            0, 0, 0, 0, 0, '다른 군집 처리 작업이 이미 실행 중입니다.',
            ?, ?, ?, ?
        )
        """,
        [now, now, now, now],
    )

    assert jobs.get_representative_clustering_job(con) is None
    con.close()


def test_background_job_runs_at_most_twenty_batches_and_logs_tokens(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "jobs.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        set_setting(con, "trend_ai_clustering_max_items", "10000")
        set_setting(con, "trend_ai_clustering_batch_size", "200")
        set_setting(con, "trend_ai_clustering_max_batches", "5")
        created = jobs.create_clustering_job(con, launcher="test")
    job_id = str(created["job_id"])

    assert int(created["batch_size"]) == 300
    assert int(created["max_batches"]) == 20

    active_connections = [0]
    real_connect = connect_database

    @contextmanager
    def tracked_connect(path):
        active_connections[0] += 1
        try:
            with real_connect(path) as con:
                yield con
        finally:
            active_connections[0] -= 1

    prepare_calls = [0]

    def fake_prepare(con, **kwargs):
        assert active_connections[0] == 1
        prepare_calls[0] += 1
        remaining = max(0, 6200 - ((prepare_calls[0] - 1) * 300))
        return SimpleNamespace(status="ready", pending_item_count=remaining)

    def fake_calculate(preparation):
        assert active_connections[0] == 0
        assert preparation.ai_clustering_batch_size == 300
        assert preparation.ai_clustering_max_batches == 20
        return SimpleNamespace(batch=prepare_calls[0])

    def fake_finalize(con, calculation):
        assert active_connections[0] == 1
        remaining = 6200 - calculation.batch * 300
        return {
            "ai_clustering": {
                "status": "success",
                "processed_items": 300,
                "needs_review_items": 0,
                "remaining_items": remaining,
            },
            "batch_log": {
                "status": "success",
                "scanned_pending_items": 6200 - ((calculation.batch - 1) * 300),
                "first_stage_units": 300,
                "all_first_stage_units": 6200 - ((calculation.batch - 1) * 300),
                "source_items": 360,
                "url_merged_items": 40,
                "url_conflict_splits": 1,
                "title_merged_groups": 20,
                "deferred_units": max(0, 5900 - ((calculation.batch - 1) * 300)),
                "processed_units": 300,
                "processed_source_items": 300,
                "existing_links": 75,
                "new_clusters": 225,
                "uncertain_units": 0,
                "conflict_units": 0,
                "needs_review_items": 0,
                "input_tokens": 1000,
                "output_tokens": 500,
                "thought_tokens": 0,
                "total_tokens": 1500,
            },
        }

    monkeypatch.setattr(jobs, "connect_database", tracked_connect)
    monkeypatch.setattr(
        jobs,
        "acquire_trend_clustering_lock",
        lambda *args, **kwargs: _successful_lock(),
    )
    monkeypatch.setattr(jobs, "prepare_trend_ranking_rebuild", fake_prepare)
    monkeypatch.setattr(jobs, "calculate_prepared_trend_rankings", fake_calculate)
    monkeypatch.setattr(jobs, "finalize_prepared_trend_rankings", fake_finalize)

    exit_code = jobs.run_clustering_job(
        job_id,
        db_path=db_path,
        project_root=tmp_path,
    )

    assert exit_code == 0
    assert prepare_calls[0] == 20
    assert active_connections[0] == 0
    with connect_database(db_path) as con:
        job = jobs.get_latest_clustering_job(con)
    assert job is not None
    assert job["status"] == "partial"
    assert int(job["completed_batches"]) == 20
    assert int(job["processed_units"]) == 6000
    assert int(job["remaining_items"]) == 200
    assert int(job["total_tokens"]) == 30000
    assert len(job["batches"]) == 20
    newest_batch = job["batches"][0]
    assert int(newest_batch["scanned_pending_items"]) == 500
    assert int(newest_batch["url_merged_items"]) == 40
    assert int(newest_batch["title_merged_groups"]) == 20


def test_background_job_stops_before_api_when_no_pending_items(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "empty.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        created = jobs.create_clustering_job(con, launcher="test")
    job_id = str(created["job_id"])
    prepare_calls = [0]

    def fake_prepare(con, **kwargs):
        prepare_calls[0] += 1
        return SimpleNamespace(status="ready", pending_item_count=0)

    monkeypatch.setattr(
        jobs,
        "acquire_trend_clustering_lock",
        lambda *args, **kwargs: _successful_lock(),
    )
    monkeypatch.setattr(jobs, "prepare_trend_ranking_rebuild", fake_prepare)
    monkeypatch.setattr(
        jobs,
        "calculate_prepared_trend_rankings",
        lambda preparation: (_ for _ in ()).throw(AssertionError("API 계산을 호출하면 안 됨")),
    )
    monkeypatch.setattr(
        jobs,
        "finalize_prepared_trend_rankings",
        lambda con, calculation: (_ for _ in ()).throw(AssertionError("저장을 호출하면 안 됨")),
    )

    exit_code = jobs.run_clustering_job(job_id, db_path=db_path, project_root=tmp_path)

    assert exit_code == 0
    assert prepare_calls[0] == 1
    with connect_database(db_path) as con:
        job = jobs.get_latest_clustering_job(con)
    assert job is not None
    assert job["status"] == "success"
    assert int(job["completed_batches"]) == 0
    assert int(job["remaining_items"]) == 0


def test_running_job_exposes_visible_batch_progress(tmp_path: Path) -> None:
    db_path = tmp_path / "progress.duckdb"
    init_database(db_path)
    now = datetime.now()
    with connect_database(db_path) as con:
        con.execute(
            """
            INSERT INTO trend_clustering_jobs(
                job_id, status, launcher, model_name, scan_limit, batch_size,
                max_batches, completed_batches, processed_units,
                processed_source_items, remaining_items, created_at,
                started_at, heartbeat_at
            ) VALUES ('progress', 'running', 'test', 'gemini-3.5-flash-lite',
                      10000, 300, 20, 3, 900, 900, 3100, ?, ?, ?)
            """,
            [now, now, now],
        )
        job = jobs.get_latest_clustering_job(con)

    assert job is not None
    assert job["display_status"] == "실행 중 · 4/20차 · 15% 완료"
    assert int(job["current_batch"]) == 4
    assert int(job["progress_percent"]) == 15
    assert int(job["remaining_items"]) == 3100


def test_stale_active_job_is_preserved_when_new_job_is_created(tmp_path: Path) -> None:
    db_path = tmp_path / "stale.duckdb"
    init_database(db_path)
    old = datetime.now() - timedelta(minutes=jobs.JOB_STALE_AFTER_MINUTES + 1)
    with connect_database(db_path) as con:
        con.execute(
            """
            INSERT INTO trend_clustering_jobs(
                job_id, status, launcher, model_name, scan_limit, batch_size,
                max_batches, created_at, heartbeat_at
            ) VALUES ('old', 'running', 'test', 'gemini-3.5-flash-lite',
                      4000, 200, 5, ?, NULL)
            """,
            [old],
        )

        stale = jobs.get_latest_clustering_job(con)
        active = jobs.get_active_clustering_job(con)
        created = jobs.create_clustering_job(con, launcher="test")
        old_row = con.execute(
            """
            SELECT status, heartbeat_at, finished_at
            FROM trend_clustering_jobs
            WHERE job_id = 'old'
            """
        ).fetchone()

    assert stale is not None
    assert stale["display_status"] == "stale"
    assert active is None
    assert created["created"] is True
    assert int(created["batch_size"]) == 300
    assert int(created["max_batches"]) == 20
    assert old_row == ("running", None, None)


def test_fresh_active_job_still_blocks_new_job_creation(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh-active.duckdb"
    init_database(db_path)
    now = datetime.now()
    with connect_database(db_path) as con:
        con.execute(
            """
            INSERT INTO trend_clustering_jobs(
                job_id, status, launcher, model_name, scan_limit, batch_size,
                max_batches, created_at, heartbeat_at
            ) VALUES ('active', 'running', 'test', 'gemini-3.5-flash-lite',
                      4000, 300, 20, ?, ?)
            """,
            [now, now],
        )
        created = jobs.create_clustering_job(con, launcher="test")
        status = con.execute(
            "SELECT status FROM trend_clustering_jobs WHERE job_id = 'active'"
        ).fetchone()[0]

    assert created["created"] is False
    assert created["job_id"] == "active"
    assert created["status"] == "running"
    assert status == "running"


def test_stale_history_is_preserved_when_worker_lock_reports_overlap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "stale-overlap.duckdb"
    init_database(db_path)
    old = datetime.now() - timedelta(minutes=jobs.JOB_STALE_AFTER_MINUTES + 1)
    with connect_database(db_path) as con:
        con.execute(
            """
            INSERT INTO trend_clustering_jobs(
                job_id, status, launcher, model_name, scan_limit, batch_size,
                max_batches, created_at, heartbeat_at
            ) VALUES ('old', 'running', 'test', 'gemini-3.5-flash-lite',
                      4000, 300, 20, ?, ?)
            """,
            [old, old],
        )
        created = jobs.create_clustering_job(con, launcher="test")
    new_job_id = str(created["job_id"])

    monkeypatch.setattr(
        jobs,
        "acquire_trend_clustering_lock",
        lambda *args, **kwargs: SimpleNamespace(
            acquired=False,
            lock=None,
            message="다른 군집 처리 작업이 이미 실행 중입니다.",
        ),
    )

    exit_code = jobs.run_clustering_job(
        new_job_id,
        db_path=db_path,
        project_root=tmp_path,
    )

    assert exit_code == 0
    with connect_database(db_path) as con:
        old_status = con.execute(
            "SELECT status FROM trend_clustering_jobs WHERE job_id = 'old'"
        ).fetchone()[0]
        new_status = con.execute(
            "SELECT status FROM trend_clustering_jobs WHERE job_id = ?",
            [new_job_id],
        ).fetchone()[0]
    assert old_status == "running"
    assert new_status == "skipped_overlap"
