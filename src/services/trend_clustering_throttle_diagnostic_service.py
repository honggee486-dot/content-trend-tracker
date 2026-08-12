"""2단계 군집 Gemini 요청의 로컬 TPM 대기와 공급자 제한을 읽기 전용으로 구분합니다."""

from __future__ import annotations

from typing import Any

import duckdb


_REQUEST_METRICS_TABLE = "trend_clustering_request_metrics"
_REQUIRED_COLUMNS = {"tpm_wait_seconds", "error_type", "created_at"}
_PROVIDER_RATE_LIMIT_ERRORS = {"rate_limited"}
_PROVIDER_DAILY_QUOTA_ERRORS = {"daily_quota_exhausted"}


def _table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    rows = con.execute("SHOW TABLES").fetchall()
    return str(table_name) in {str(row[0]) for row in rows}


def _table_columns(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
) -> set[str]:
    return {
        str(row[0])
        for row in con.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = ?
            """,
            [table_name],
        ).fetchall()
    }


def _empty_result(
    *,
    sample_limit: int,
    available: bool,
    reason: str,
    missing_columns: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "available": available,
        "reason": reason,
        "sample_scope": "recent_requests",
        "sample_limit": sample_limit,
        "missing_columns": list(missing_columns or []),
        "request_count": 0,
        "local_tpm_wait_count": 0,
        "local_tpm_wait_seconds_total": 0.0,
        "local_tpm_wait_seconds_max": 0.0,
        "provider_rate_limit_count": 0,
        "provider_daily_quota_count": 0,
        "provider_other_error_count": 0,
        "provider_restriction_count": 0,
        "classification": "no_requests" if available else "unavailable",
    }


def _classify(*, rate_limited: int, daily_quota: int, local_wait: int) -> str:
    if rate_limited and daily_quota:
        return "mixed_provider_limits"
    if daily_quota:
        return "provider_daily_quota"
    if rate_limited:
        return "provider_rate_limit"
    if local_wait:
        return "local_tpm_wait_only"
    return "no_throttle"


def build_trend_clustering_throttle_diagnostic(
    con: duckdb.DuckDBPyConnection,
    *,
    sample_limit: int = 100,
) -> dict[str, Any]:
    """최근 요청 메트릭에서 로컬 속도조절과 Gemini 제한을 구분합니다.

    요청 메트릭에는 군집 job_id가 없으므로 최신 군집 작업에 오류를 직접 귀속하지 않고
    최근 요청 표본으로만 요약합니다.
    """
    bounded_limit = max(1, min(int(sample_limit), 1000))
    if not _table_exists(con, _REQUEST_METRICS_TABLE):
        return _empty_result(
            sample_limit=bounded_limit,
            available=False,
            reason="request_metrics_table_missing",
        )

    columns = _table_columns(con, _REQUEST_METRICS_TABLE)
    missing_columns = sorted(_REQUIRED_COLUMNS - columns)
    if missing_columns:
        return _empty_result(
            sample_limit=bounded_limit,
            available=False,
            reason="request_metrics_columns_missing",
            missing_columns=missing_columns,
        )

    rows = con.execute(
        f"""
        SELECT tpm_wait_seconds, error_type, created_at
        FROM {_REQUEST_METRICS_TABLE}
        ORDER BY created_at DESC NULLS LAST
        LIMIT ?
        """,
        [bounded_limit],
    ).fetchall()
    if not rows:
        return _empty_result(
            sample_limit=bounded_limit,
            available=True,
            reason="",
        )

    waits = [max(0.0, float(row[0] or 0.0)) for row in rows]
    error_types = [str(row[1] or "").strip().lower() for row in rows]
    local_wait_count = sum(wait > 0 for wait in waits)
    rate_limit_count = sum(
        error_type in _PROVIDER_RATE_LIMIT_ERRORS for error_type in error_types
    )
    daily_quota_count = sum(
        error_type in _PROVIDER_DAILY_QUOTA_ERRORS for error_type in error_types
    )
    other_error_count = sum(
        bool(error_type)
        and error_type not in _PROVIDER_RATE_LIMIT_ERRORS
        and error_type not in _PROVIDER_DAILY_QUOTA_ERRORS
        for error_type in error_types
    )

    return {
        "available": True,
        "reason": "",
        "sample_scope": "recent_requests",
        "sample_limit": bounded_limit,
        "missing_columns": [],
        "request_count": len(rows),
        "local_tpm_wait_count": local_wait_count,
        "local_tpm_wait_seconds_total": round(sum(waits), 3),
        "local_tpm_wait_seconds_max": round(max(waits, default=0.0), 3),
        "provider_rate_limit_count": rate_limit_count,
        "provider_daily_quota_count": daily_quota_count,
        "provider_other_error_count": other_error_count,
        "provider_restriction_count": rate_limit_count + daily_quota_count,
        "classification": _classify(
            rate_limited=rate_limit_count,
            daily_quota=daily_quota_count,
            local_wait=local_wait_count,
        ),
    }
