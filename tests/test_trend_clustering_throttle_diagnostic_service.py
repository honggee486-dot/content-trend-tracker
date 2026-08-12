from __future__ import annotations

from datetime import datetime, timedelta

import duckdb

from src.services.trend_clustering_throttle_diagnostic_service import (
    build_trend_clustering_throttle_diagnostic,
)


def _create_metrics_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE trend_clustering_request_metrics (
            tpm_wait_seconds DOUBLE,
            error_type VARCHAR,
            created_at TIMESTAMP
        )
        """
    )


def _insert_metric(
    con: duckdb.DuckDBPyConnection,
    *,
    wait_seconds: float = 0.0,
    error_type: str = "",
    created_at: datetime | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO trend_clustering_request_metrics
            (tpm_wait_seconds, error_type, created_at)
        VALUES (?, ?, ?)
        """,
        [wait_seconds, error_type, created_at or datetime(2026, 8, 10, 12, 0, 0)],
    )


def test_throttle_diagnostic_handles_missing_table() -> None:
    with duckdb.connect(":memory:") as con:
        result = build_trend_clustering_throttle_diagnostic(con)

    assert result["available"] is False
    assert result["reason"] == "request_metrics_table_missing"
    assert result["classification"] == "unavailable"
    assert result["sample_scope"] == "recent_requests"


def test_throttle_diagnostic_handles_missing_required_columns() -> None:
    with duckdb.connect(":memory:") as con:
        con.execute(
            "CREATE TABLE trend_clustering_request_metrics (error_type VARCHAR)"
        )
        result = build_trend_clustering_throttle_diagnostic(con)

    assert result["available"] is False
    assert result["reason"] == "request_metrics_columns_missing"
    assert result["missing_columns"] == ["created_at", "tpm_wait_seconds"]


def test_throttle_diagnostic_classifies_local_tpm_wait_without_provider_error() -> None:
    with duckdb.connect(":memory:") as con:
        _create_metrics_table(con)
        _insert_metric(con, wait_seconds=1.25)
        _insert_metric(con, wait_seconds=0.75)
        _insert_metric(con)
        result = build_trend_clustering_throttle_diagnostic(con)

    assert result["classification"] == "local_tpm_wait_only"
    assert result["local_tpm_wait_count"] == 2
    assert result["local_tpm_wait_seconds_total"] == 2.0
    assert result["local_tpm_wait_seconds_max"] == 1.25
    assert result["provider_restriction_count"] == 0


def test_throttle_diagnostic_classifies_provider_rate_limit_separately() -> None:
    with duckdb.connect(":memory:") as con:
        _create_metrics_table(con)
        _insert_metric(con, wait_seconds=2.5, error_type="rate_limited")
        result = build_trend_clustering_throttle_diagnostic(con)

    assert result["classification"] == "provider_rate_limit"
    assert result["local_tpm_wait_count"] == 1
    assert result["provider_rate_limit_count"] == 1
    assert result["provider_daily_quota_count"] == 0


def test_throttle_diagnostic_classifies_daily_quota_and_mixed_limits() -> None:
    with duckdb.connect(":memory:") as con:
        _create_metrics_table(con)
        _insert_metric(con, error_type="daily_quota_exhausted")
        daily = build_trend_clustering_throttle_diagnostic(con)
        _insert_metric(
            con,
            error_type="rate_limited",
            created_at=datetime(2026, 8, 10, 12, 0, 1),
        )
        mixed = build_trend_clustering_throttle_diagnostic(con)

    assert daily["classification"] == "provider_daily_quota"
    assert daily["provider_daily_quota_count"] == 1
    assert mixed["classification"] == "mixed_provider_limits"
    assert mixed["provider_restriction_count"] == 2


def test_throttle_diagnostic_counts_other_errors_without_calling_them_throttle() -> None:
    with duckdb.connect(":memory:") as con:
        _create_metrics_table(con)
        _insert_metric(con, error_type="timeout")
        result = build_trend_clustering_throttle_diagnostic(con)

    assert result["classification"] == "no_throttle"
    assert result["provider_other_error_count"] == 1
    assert result["provider_restriction_count"] == 0


def test_throttle_diagnostic_respects_recent_sample_limit() -> None:
    with duckdb.connect(":memory:") as con:
        _create_metrics_table(con)
        base = datetime(2026, 8, 10, 12, 0, 0)
        _insert_metric(con, error_type="rate_limited", created_at=base)
        _insert_metric(con, created_at=base + timedelta(seconds=1))
        result = build_trend_clustering_throttle_diagnostic(con, sample_limit=1)

    assert result["request_count"] == 1
    assert result["classification"] == "no_throttle"
    assert result["provider_rate_limit_count"] == 0
