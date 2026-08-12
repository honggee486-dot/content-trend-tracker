from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from src.database import connect_database, init_database
from src.services.collection_history_service import (
    cleanup_collection_history,
    finish_collection_run,
    get_collection_history_summary,
    list_collection_run_sources,
    list_recent_collection_runs,
    record_skipped_overlap,
    run_type_for_dashboard_action,
    start_collection_run,
)


def _refresh_result(*, partial: bool = False) -> dict[str, object]:
    return {
        "youtube": {
            "status": "skipped",
            "items_read": 0,
            "items_added": 0,
            "items_updated": 0,
            "items_skipped": 0,
            "unchanged": True,
        },
        "google_trends": None,
        "wikipedia": None,
        "naver": {
            "status": "success",
            "items_read": 8,
            "items_added": 3,
            "items_updated": 5,
            "items_skipped": 1,
            "request_count": 4,
            "retry_count": 1,
        },
        "daum": (
            {
                "status": "partial",
                "items_read": 2,
                "items_added": 1,
                "items_updated": 1,
                "items_skipped": 2,
                "request_count": 3,
                "retry_count": 2,
            }
            if partial
            else None
        ),
        "errors": {},
        "warnings": {"daum": "요청 일부 실패"} if partial else {},
        "ranking": {"items": 10, "clusters": 4},
        "timings": {"youtube": 0.01, "naver": 1.2, "daum": 0.8, "ranking": 0.2},
    }


def _topic_angle_result(
    *,
    status: str = "success_after_retry",
    requested: int = 10,
    generated: int = 8,
    directions: int = 24,
    error_message: str = "",
) -> dict[str, object]:
    return {
        "status": status,
        "requested_clusters": requested,
        "generated_clusters": generated,
        "generated_angles": directions,
        "skipped_sensitive_clusters": 1,
        "attempts": 3,
        "requested_batches": 2,
        "completed_batches": 2 if generated else 0,
        "failed_batches": 0 if generated == requested else 1,
        "items_per_request": 25,
        "max_parallel_requests": 4,
        "duration_seconds": 4.5,
        "error_message": error_message,
    }


def test_schema_creation_and_idempotent_initialization(tmp_path: Path) -> None:
    db_path = tmp_path / "history.duckdb"

    init_database(db_path)
    init_database(db_path)

    with connect_database(db_path) as con:
        tables = {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}
        assert {"collection_runs", "collection_run_sources"} <= tables
        columns = {
            str(row[1]) for row in con.execute("PRAGMA table_info('collection_runs')").fetchall()
        }
        assert {
            "run_id",
            "run_type",
            "status",
            "request_count",
            "retry_count",
            "newly_saved_count",
        } <= columns


def test_existing_database_table_remains_readable(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.duckdb"
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE legacy_marker(value VARCHAR)")
        con.execute("INSERT INTO legacy_marker VALUES ('keep')")

    init_database(db_path)

    with connect_database(db_path) as con:
        assert con.execute("SELECT value FROM legacy_marker").fetchone()[0] == "keep"


def test_start_and_successful_finish_group_sources_and_totals(tmp_path: Path) -> None:
    db_path = tmp_path / "success.duckdb"
    init_database(db_path)
    started = datetime(2026, 7, 16, 9, 0, 0)
    with connect_database(db_path) as con:
        run_id = start_collection_run(
            con,
            "manual_refresh",
            started_at=started,
        )
        running = con.execute(
            "SELECT run_type, status FROM collection_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
        assert running == ("manual_refresh", "running")

        status = finish_collection_run(
            con,
            run_id,
            result=_refresh_result(),
            finished_at=started + timedelta(seconds=2),
        )

        assert status == "success"
        row = con.execute(
            """
            SELECT status, duration_ms, source_count, succeeded_source_count,
                   failed_source_count, request_count, retry_count,
                   newly_saved_count, updated_count, skipped_count
            FROM collection_runs WHERE run_id = ?
            """,
            [run_id],
        ).fetchone()
        assert row == ("success", 2000, 2, 2, 0, 4, 1, 3, 5, 1)
        sources = list_collection_run_sources(con, run_id)
        assert {row["source_name"] for row in sources} == {"youtube", "naver"}


def test_mixed_source_result_finishes_partial_success(tmp_path: Path) -> None:
    db_path = tmp_path / "partial.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        run_id = start_collection_run(con, "background_refresh")
        status = finish_collection_run(con, run_id, result=_refresh_result(partial=True))
        row = con.execute(
            """
            SELECT status, source_count, succeeded_source_count, failed_source_count,
                   request_count, retry_count, newly_saved_count, updated_count, skipped_count
            FROM collection_runs WHERE run_id = ?
            """,
            [run_id],
        ).fetchone()

    assert status == "partial_success"
    assert row == ("partial_success", 3, 3, 1, 7, 3, 4, 6, 3)


def test_unrecoverable_exception_finishes_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "failure.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        run_id = start_collection_run(con, "ranking_rebuild")
        status = finish_collection_run(con, run_id, error=RuntimeError("ranking failed"))
        row = con.execute(
            "SELECT status, error_message FROM collection_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()

    assert status == "failure"
    assert row == ("failure", "ranking failed")


def test_overlap_skip_is_recorded_with_requested_run_type(tmp_path: Path) -> None:
    db_path = tmp_path / "overlap.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        run_id = record_skipped_overlap(con, "background_refresh")
        row = con.execute(
            "SELECT run_type, status, duration_ms FROM collection_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()

    assert row == ("background_refresh", "skipped_overlap", 0)


def test_streamlit_actions_map_to_expected_run_types() -> None:
    assert run_type_for_dashboard_action("refresh") == "manual_refresh"
    assert run_type_for_dashboard_action("rebuild") == "ranking_rebuild"
    assert run_type_for_dashboard_action("angles") == "topic_angle_generation"


def test_ranking_rebuild_and_duplicate_finish_create_one_run(tmp_path: Path) -> None:
    db_path = tmp_path / "ranking.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        run_id = start_collection_run(con, "ranking_rebuild")
        result = {"items": 20, "clusters": 5, "timings": {"total": 0.3}}
        assert finish_collection_run(con, run_id, result=result) == "success"
        assert finish_collection_run(con, run_id, error="late error") == "success"
        assert con.execute(
            "SELECT COUNT(*) FROM collection_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()[0] == 1
        assert con.execute(
            "SELECT COUNT(*) FROM collection_run_sources WHERE run_id = ?",
            [run_id],
        ).fetchone()[0] == 1


def test_recent_history_is_newest_first_and_limited(tmp_path: Path) -> None:
    db_path = tmp_path / "recent.duckdb"
    init_database(db_path)
    base = datetime(2026, 7, 16, 8, 0, 0)
    with connect_database(db_path) as con:
        run_ids = [
            record_skipped_overlap(
                con,
                "manual_refresh",
                recorded_at=base + timedelta(minutes=index),
            )
            for index in range(5)
        ]
        recent = list_recent_collection_runs(con, limit=3)

    assert [row["run_id"] for row in recent] == list(reversed(run_ids[-3:]))


def test_start_preserves_existing_running_history(tmp_path: Path) -> None:
    db_path = tmp_path / "stale.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 16, 12, 0, 0)
    with connect_database(db_path) as con:
        stale_id = start_collection_run(
            con,
            "manual_refresh",
            started_at=now - timedelta(hours=7),
        )
        recent_id = start_collection_run(
            con,
            "manual_refresh",
            started_at=now - timedelta(hours=1),
        )
        start_collection_run(con, "background_refresh", started_at=now)
        rows = dict(
            con.execute(
                "SELECT run_id, status FROM collection_runs WHERE run_id IN (?, ?)",
                [stale_id, recent_id],
            ).fetchall()
        )

    assert rows[stale_id] == "running"
    assert rows[recent_id] == "running"


def test_history_summary_and_retention_only_remove_old_history(tmp_path: Path) -> None:
    db_path = tmp_path / "retention.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 16, 12, 0, 0)
    with connect_database(db_path) as con:
        con.execute(
            "INSERT INTO topics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "topic_keep",
                "보존 주제",
                "보존 주제",
                None,
                None,
                "candidate",
                2,
                False,
                None,
                0,
                now,
                now,
                now,
                now,
                None,
            ],
        )
        old_id = record_skipped_overlap(
            con,
            "background_refresh",
            recorded_at=now - timedelta(days=91),
        )
        con.execute(
            """
            INSERT INTO collection_query_discoveries(
                run_id, source_name, source_type, discovery_query, source_item_id,
                external_id, source_url, is_new, result_rank, discovered_at
            ) VALUES (?, 'naver', 'naver_news', '오래된 검색어', 'src_old',
                      'old-external', 'https://example.com/old', TRUE, 1, ?)
            """,
            [old_id, now - timedelta(days=91)],
        )
        recent_id = start_collection_run(
            con,
            "manual_refresh",
            started_at=now - timedelta(hours=1),
        )
        finish_collection_run(
            con,
            recent_id,
            result=_refresh_result(),
            finished_at=now - timedelta(minutes=59),
        )
        summary = get_collection_history_summary(con, now=now)
        deleted = cleanup_collection_history(con, retention_days=90, now=now)

        assert deleted == 1
        assert con.execute(
            "SELECT COUNT(*) FROM collection_runs WHERE run_id = ?",
            [old_id],
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM collection_query_discoveries WHERE run_id = ?",
            [old_id],
        ).fetchone()[0] == 0
        assert con.execute("SELECT title FROM topics WHERE topic_id = 'topic_keep'").fetchone()[0] == "보존 주제"

    assert summary["last_success_at"] is not None
    assert summary["consecutive_success_count"] == 1


def test_topic_angle_generation_history_records_saved_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "topic-angle-history.duckdb"
    init_database(db_path)
    started = datetime(2026, 7, 26, 9, 0, 0)
    with connect_database(db_path) as con:
        run_id = start_collection_run(
            con,
            "topic_angle_generation",
            started_at=started,
        )
        status = finish_collection_run(
            con,
            run_id,
            result={
                "status": "success",
                "requested_clusters": 10,
                "generated_clusters": 8,
                "generated_angles": 24,
                "attempts": 1,
                "error_message": "일부 글감 2개 누락",
            },
            finished_at=started + timedelta(seconds=5),
        )
        row = con.execute(
            """
            SELECT run_type, status, request_count, newly_saved_count,
                   updated_count, skipped_count, summary
            FROM collection_runs WHERE run_id = ?
            """,
            [run_id],
        ).fetchone()

    assert status == "partial_success"
    assert row[:6] == ("topic_angle_generation", "partial_success", 1, 24, 8, 2)
    assert "요청 글감 10개" in row[6]


def test_background_refresh_records_gemini_detail_without_mixing_source_totals(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "background-gemini-history.duckdb"
    init_database(db_path)
    started = datetime(2026, 7, 26, 11, 0, 0)
    refresh_result = _refresh_result()
    refresh_result["topic_angles"] = _topic_angle_result()

    with connect_database(db_path) as con:
        run_id = start_collection_run(
            con,
            "background_refresh",
            started_at=started,
        )
        status = finish_collection_run(
            con,
            run_id,
            result=refresh_result,
            finished_at=started + timedelta(seconds=8),
        )
        run_row = con.execute(
            """
            SELECT status, source_count, succeeded_source_count, failed_source_count,
                   request_count, retry_count, newly_saved_count, updated_count,
                   skipped_count, summary
            FROM collection_runs WHERE run_id = ?
            """,
            [run_id],
        ).fetchone()
        source_rows = {
            row["source_name"]: row
            for row in list_collection_run_sources(con, run_id)
        }

    assert status == "success"
    assert run_row[:9] == ("success", 2, 2, 0, 4, 1, 3, 5, 1)
    assert "Gemini 글감 8/10개 · 방향 24개" in run_row[9]
    assert set(source_rows) == {"youtube", "naver", "topic_angles"}
    gemini_row = source_rows["topic_angles"]
    assert gemini_row["status"] == "partial_success"
    assert gemini_row["duration_ms"] == 4500
    assert gemini_row["request_count"] == 3
    assert gemini_row["retry_count"] == 1
    assert gemini_row["newly_saved_count"] == 24
    assert gemini_row["updated_count"] == 8
    assert gemini_row["skipped_count"] == 2


def test_background_gemini_failure_is_visible_but_keeps_collection_success(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "background-gemini-failure-history.duckdb"
    init_database(db_path)
    refresh_result = _refresh_result()
    refresh_result["topic_angles"] = _topic_angle_result(
        status="response_validation_error",
        requested=5,
        generated=0,
        directions=0,
        error_message="Gemini 응답 형식 오류",
    )

    with connect_database(db_path) as con:
        run_id = start_collection_run(con, "background_refresh")
        status = finish_collection_run(con, run_id, result=refresh_result)
        run_row = con.execute(
            "SELECT status, error_message FROM collection_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
        source_rows = {
            row["source_name"]: row
            for row in list_collection_run_sources(con, run_id)
        }

    assert status == "success"
    assert run_row[0] == "success"
    assert "Gemini 응답 형식 오류" in run_row[1]
    assert source_rows["topic_angles"]["status"] == "failure"
    assert source_rows["topic_angles"]["skipped_count"] == 5


def test_background_missing_gemini_key_is_recorded_as_optional_skip(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "background-gemini-key-skip.duckdb"
    init_database(db_path)
    refresh_result = _refresh_result()
    refresh_result["topic_angles"] = _topic_angle_result(
        status="missing_api_key",
        requested=0,
        generated=0,
        directions=0,
    )

    with connect_database(db_path) as con:
        run_id = start_collection_run(con, "background_refresh")
        status = finish_collection_run(con, run_id, result=refresh_result)
        source_rows = {
            row["source_name"]: row
            for row in list_collection_run_sources(con, run_id)
        }

    assert status == "success"
    assert source_rows["topic_angles"]["status"] == "skipped"
