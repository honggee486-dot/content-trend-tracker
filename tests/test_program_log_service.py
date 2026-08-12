from __future__ import annotations

from datetime import datetime, timedelta

import duckdb

from src.services.program_log_context import program_log_correlation
from src.services.program_log_service import (
    list_recent_gemini_calls,
    list_recent_program_events,
    record_program_event,
)


def test_program_events_are_additive_and_newest_first(tmp_path) -> None:
    db_path = tmp_path / "program-log.duckdb"
    with duckdb.connect(str(db_path)) as con:
        assert record_program_event(
            event_type="button",
            status="clicked",
            source="test",
            action="최신 데이터 수집·분석",
            detail="첫 기록",
            con=con,
        )
        assert record_program_event(
            event_type="stage",
            status="completed",
            source="test",
            action="2차 군집 전체 스냅샷 집계",
            detail="540개 집계",
            item_count=540,
            duration_ms=1250,
            con=con,
        )

        rows = list_recent_program_events(con, limit=100)

    assert len(rows) == 2
    assert rows[0]["event_id"].startswith("opevt_")
    assert rows[0]["action"] == "2차 군집 전체 스냅샷 집계"
    assert rows[0]["item_count"] == 540
    assert rows[0]["duration_ms"] == 1250
    assert rows[1]["action"] == "최신 데이터 수집·분석"


def test_current_execution_context_groups_nested_events(tmp_path) -> None:
    db_path = tmp_path / "program-log-context.duckdb"
    with duckdb.connect(str(db_path)) as con:
        with program_log_correlation("collection_parent"):
            assert record_program_event(
                event_type="api",
                status="completed",
                source="test",
                action="Gemini 전송",
                correlation_id="request_hash_child",
                con=con,
            )
        rows = list_recent_program_events(con, limit=1)

    assert rows[0]["correlation_id"] == "collection_parent"
    assert rows[0]["metadata_json"] == "{}"


def test_recent_gemini_calls_are_limited_and_include_status_fields(tmp_path) -> None:
    db_path = tmp_path / "gemini-log.duckdb"
    now = datetime.now()
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            """
            CREATE TABLE gemini_api_calls (
                call_id VARCHAR PRIMARY KEY,
                created_at TIMESTAMP,
                status VARCHAR,
                model_name VARCHAR,
                feature_id VARCHAR,
                feature_version VARCHAR,
                attempt_number INTEGER,
                cache_hit BOOLEAN,
                requested_item_count INTEGER,
                input_tokens BIGINT,
                output_tokens BIGINT,
                thought_tokens BIGINT,
                total_tokens BIGINT,
                http_status INTEGER,
                finish_reason VARCHAR,
                duration_ms BIGINT,
                error_type VARCHAR,
                error_message VARCHAR
            )
            """
        )
        con.executemany(
            """
            INSERT INTO gemini_api_calls VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                [
                    "call_old",
                    now - timedelta(minutes=1),
                    "failed",
                    "gemini-test",
                    "trend_cluster_grouping_v3",
                    "1",
                    1,
                    False,
                    20,
                    100,
                    0,
                    0,
                    100,
                    429,
                    "",
                    500,
                    "rate_limited",
                    "quota",
                ],
                [
                    "call_new",
                    now,
                    "success",
                    "gemini-test",
                    "trend_topic_angle_batch_v1",
                    "6",
                    1,
                    False,
                    15,
                    200,
                    30,
                    10,
                    240,
                    200,
                    "STOP",
                    750,
                    "",
                    "",
                ],
            ],
        )

        rows = list_recent_gemini_calls(con, limit=1)

    assert len(rows) == 1
    assert rows[0]["status"] == "success"
    assert rows[0]["feature_id"] == "trend_topic_angle_batch_v1"
    assert rows[0]["requested_item_count"] == 15
    assert rows[0]["finish_reason"] == "STOP"
