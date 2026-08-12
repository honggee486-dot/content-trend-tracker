from __future__ import annotations

from datetime import datetime, timedelta

import duckdb

from src.services.gemini_usage_log_service import (
    CLUSTER_GROUPING_FEATURE_ID,
    LEGACY_CLUSTER_REVIEW_FEATURE_ID,
    TOPIC_ANGLE_FEATURE_ID,
    get_gemini_usage_log_summary,
)


def _create_full_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE gemini_api_calls (
            call_id VARCHAR,
            app_id VARCHAR,
            feature_id VARCHAR,
            model_name VARCHAR,
            status VARCHAR,
            cache_hit BOOLEAN,
            http_status INTEGER,
            error_type VARCHAR,
            requested_item_count INTEGER,
            input_tokens BIGINT,
            output_tokens BIGINT,
            thought_tokens BIGINT,
            total_tokens BIGINT,
            created_at TIMESTAMP
        )
        """
    )


def test_usage_summary_separates_models_features_and_actual_calls() -> None:
    con = duckdb.connect(":memory:")
    _create_full_table(con)
    now = datetime.now()
    con.executemany(
        "INSERT INTO gemini_api_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ["a", "content", TOPIC_ANGLE_FEATURE_ID, "gemini-3.6-flash", "success", False, 200, "", 15, 40, 30, 20, 90, now],
            ["b", "content", CLUSTER_GROUPING_FEATURE_ID, "gemini-3.5-flash-lite", "success", False, 200, "", 2, 10, 8, 2, 20, now],
            ["c", "content", LEGACY_CLUSTER_REVIEW_FEATURE_ID, "gemini-3.5-flash-lite", "failed", False, 429, "rate_limited", 1, 5, 0, 0, 5, now],
            ["d", "content", TOPIC_ANGLE_FEATURE_ID, "gemini-3.6-flash", "success", True, 200, "", 15, 99, 99, 99, 297, now],
            ["e", "other", TOPIC_ANGLE_FEATURE_ID, "gemini-3.6-flash", "success", False, 200, "", 15, 100, 100, 100, 300, now],
            ["f", "content", TOPIC_ANGLE_FEATURE_ID, "gemini-3.6-flash", "success", False, 200, "", 15, 100, 100, 100, 300, now - timedelta(days=31)],
        ],
    )

    summary = get_gemini_usage_log_summary(
        con,
        app_id="content",
        period_days=30,
    )

    assert summary.attempt_count == 3
    assert summary.successful_count == 2
    assert summary.failed_count == 1
    assert summary.total_tokens == 115
    assert summary.flash_36_attempt_count == 1
    assert summary.auto_analysis_attempt_count == 1
    assert summary.cluster_review_attempt_count == 2
    assert len(summary.grouped_rows) == 3
    grouping = next(
        row
        for row in summary.grouped_rows
        if row["feature_id"] == CLUSTER_GROUPING_FEATURE_ID
    )
    legacy = next(
        row
        for row in summary.grouped_rows
        if row["feature_id"] == LEGACY_CLUSTER_REVIEW_FEATURE_ID
    )
    assert grouping["attempt_count"] == 1
    assert grouping["requested_item_count"] == 2
    assert grouping["total_tokens"] == 20
    assert legacy["attempt_count"] == 1
    assert legacy["requested_item_count"] == 1


def test_usage_summary_handles_older_optional_column_shape() -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE gemini_api_calls (
            call_id VARCHAR,
            app_id VARCHAR,
            feature_id VARCHAR,
            model_name VARCHAR,
            status VARCHAR,
            cache_hit BOOLEAN,
            created_at TIMESTAMP
        )
        """
    )
    con.execute(
        "INSERT INTO gemini_api_calls VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            "legacy",
            "content",
            TOPIC_ANGLE_FEATURE_ID,
            "gemini-3.6-flash",
            "success",
            False,
            datetime.now(),
        ],
    )

    summary = get_gemini_usage_log_summary(con, app_id="content")

    assert summary.attempt_count == 1
    assert summary.total_tokens == 0
    assert summary.grouped_rows[0]["requested_item_count"] == 0
