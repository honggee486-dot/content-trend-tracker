from __future__ import annotations

from datetime import datetime, timedelta

import duckdb

from src.services.background_refresh_status_service import (
    get_latest_background_refresh_snapshot,
)


def _connection():
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE collection_runs(
            run_id VARCHAR,
            run_type VARCHAR,
            status VARCHAR,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            duration_ms BIGINT,
            request_count BIGINT,
            retry_count BIGINT,
            newly_saved_count BIGINT,
            updated_count BIGINT,
            skipped_count BIGINT,
            summary VARCHAR,
            error_message VARCHAR,
            created_at TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE TABLE collection_run_sources(
            run_id VARCHAR,
            source_name VARCHAR,
            status VARCHAR,
            duration_ms BIGINT,
            request_count BIGINT,
            retry_count BIGINT,
            newly_saved_count BIGINT,
            updated_count BIGINT,
            skipped_count BIGINT,
            error_message VARCHAR
        )
        """
    )
    return con


def _insert_run(
    con,
    *,
    status: str = "success",
    started_at: datetime,
    newly_saved: int = 0,
    updated: int = 0,
    summary: str = "출처 5개 · 성공 5개 · 실패 0개 · Gemini 새 분석 없음",
):
    con.execute(
        """
        INSERT INTO collection_runs VALUES (
            'run-1', 'background_refresh', ?, ?, ?, 1200,
            12, 1, ?, ?, 0, ?, '', ?
        )
        """,
        [
            status,
            started_at,
            started_at + timedelta(seconds=2),
            newly_saved,
            updated,
            summary,
            started_at,
        ],
    )


def test_missing_background_run_is_reported() -> None:
    con = _connection()

    snapshot = get_latest_background_refresh_snapshot(con)

    assert snapshot.available is False
    assert snapshot.diagnostic_status == "missing"


def test_success_without_changes_explains_missing_gemini_log() -> None:
    con = _connection()
    now = datetime(2026, 8, 4, 14, 0, 0)
    _insert_run(con, started_at=now - timedelta(minutes=20))
    con.execute(
        """
        INSERT INTO collection_run_sources VALUES
        ('run-1', 'naver', 'success', 100, 10, 0, 0, 0, 0, ''),
        ('run-1', 'topic_angles', 'skipped', 0, 0, 0, 0, 0, 0, '')
        """
    )

    snapshot = get_latest_background_refresh_snapshot(
        con, expected_interval_minutes=180, now=now
    )

    assert snapshot.available is True
    assert snapshot.diagnostic_status == "no_change"
    assert snapshot.changed_count == 0
    assert snapshot.topic_angle_status == "skipped"
    assert len(snapshot.sources) == 2


def test_optional_gemini_failure_is_visible_even_when_run_row_says_success() -> None:
    con = _connection()
    now = datetime(2026, 8, 4, 14, 0, 0)
    _insert_run(con, started_at=now - timedelta(minutes=20), newly_saved=3)
    con.execute(
        """
        INSERT INTO collection_run_sources VALUES
        ('run-1', 'naver', 'success', 100, 10, 0, 3, 0, 0, ''),
        ('run-1', 'topic_angles', 'failure', 200, 1, 0, 0, 0, 15, 'quota')
        """
    )

    snapshot = get_latest_background_refresh_snapshot(con, now=now)

    assert snapshot.run_status == "success"
    assert snapshot.diagnostic_status == "partial_success"
    assert snapshot.source_problem_count == 1
    assert snapshot.error_message == ""


def test_old_success_is_marked_stale() -> None:
    con = _connection()
    now = datetime(2026, 8, 4, 14, 0, 0)
    _insert_run(con, started_at=now - timedelta(hours=8), updated=4)

    snapshot = get_latest_background_refresh_snapshot(
        con, expected_interval_minutes=180, now=now
    )

    assert snapshot.diagnostic_status == "stale"
    assert snapshot.updated_count == 4
