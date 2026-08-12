"""검색어별 발견 결과와 실제 NAVER·Daum 요청 원장을 읽기 전용으로 집계합니다."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import duckdb

from src.services.portal_request_schema_service import ensure_portal_request_ledger_schema


QUERY_SOURCE_LABELS = {
    "naver": "NAVER",
    "daum": "Daum",
}

QUERY_SOURCE_TYPE_LABELS = {
    "naver_news": "NAVER 뉴스",
    "naver_blog": "NAVER 블로그",
    "daum_web": "Daum 웹문서",
    "daum_cafe": "Daum 카페",
}

QUERY_PERIOD_OPTIONS = (7, 30)


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100, 1)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 2)


def _cursor_rows(cursor) -> list[dict[str, Any]]:
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, values)) for values in cursor.fetchall()]


def get_query_discovery_diagnostics(
    con: duckdb.DuckDBPyConnection,
    *,
    days: int = 7,
    source_name: str = "",
    limit: int = 100,
    now: datetime | None = None,
) -> dict[str, Any]:
    """실제 발견 원장과 0.10.66 이후 검색 요청 원장을 함께 집계합니다."""
    ensure_portal_request_ledger_schema(con)
    bounded_days = 7 if int(days) <= 7 else 30
    bounded_limit = max(1, min(int(limit), 300))
    selected_source = str(source_name or "").strip().casefold()
    if selected_source not in {"", *QUERY_SOURCE_LABELS}:
        selected_source = ""
    cutoff = (now or datetime.now()) - timedelta(days=bounded_days)

    discovery_where = "discovered_at >= ?"
    request_where = "finished_at >= ?"
    parameters: list[Any] = [cutoff]
    if selected_source:
        discovery_where += " AND source_name = ?"
        request_where += " AND source_name = ?"
        parameters.append(selected_source)

    summary_row = con.execute(
        f"""
        SELECT COUNT(*) AS discovery_count,
               COUNT(DISTINCT discovery_query) AS query_count,
               COUNT(DISTINCT source_item_id) AS unique_item_count,
               COALESCE(SUM(CASE WHEN is_new THEN 1 ELSE 0 END), 0) AS new_count,
               AVG(result_rank) AS average_rank,
               MIN(result_rank) AS best_rank,
               MAX(discovered_at) AS last_discovered_at
        FROM collection_query_discoveries
        WHERE {discovery_where}
        """,
        parameters,
    ).fetchone()

    discovery_count = int(summary_row[0] or 0)
    query_count = int(summary_row[1] or 0)
    unique_item_count = int(summary_row[2] or 0)
    new_count = int(summary_row[3] or 0)
    repeat_count = max(0, discovery_count - new_count)
    duplicate_discovery_count = max(0, discovery_count - unique_item_count)

    discovery_cursor = con.execute(
        f"""
        SELECT source_name, source_type, discovery_query,
               COUNT(*) AS discovery_count,
               COUNT(DISTINCT source_item_id) AS unique_item_count,
               COALESCE(SUM(CASE WHEN is_new THEN 1 ELSE 0 END), 0) AS new_count,
               AVG(result_rank) AS average_rank,
               MIN(result_rank) AS best_rank,
               MAX(discovered_at) AS last_discovered_at
        FROM collection_query_discoveries
        WHERE {discovery_where}
        GROUP BY source_name, source_type, discovery_query
        ORDER BY new_count DESC, unique_item_count DESC,
                 discovery_count DESC, last_discovered_at DESC,
                 source_name, source_type, discovery_query
        LIMIT ?
        """,
        [*parameters, bounded_limit],
    )
    rows = _cursor_rows(discovery_cursor)
    for row in rows:
        row_discovery_count = int(row.get("discovery_count") or 0)
        row_unique_count = int(row.get("unique_item_count") or 0)
        row_new_count = int(row.get("new_count") or 0)
        row["repeat_count"] = max(0, row_discovery_count - row_new_count)
        row["duplicate_discovery_count"] = max(
            0, row_discovery_count - row_unique_count
        )
        row["new_rate_percent"] = _rate(row_new_count, row_discovery_count)
        row["duplicate_rate_percent"] = _rate(
            int(row["duplicate_discovery_count"]), row_discovery_count
        )

    request_summary = con.execute(
        f"""
        SELECT COUNT(*) AS request_count,
               COALESCE(SUM(attempt_count), 0) AS attempt_count,
               COALESCE(SUM(retry_count), 0) AS retry_count,
               COALESCE(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), 0)
                   AS successful_request_count,
               COALESCE(SUM(CASE WHEN status = 'failure' THEN 1 ELSE 0 END), 0)
                   AS failed_request_count,
               COALESCE(SUM(CASE WHEN status = 'success' AND result_count = 0 THEN 1 ELSE 0 END), 0)
                   AS zero_result_count,
               COALESCE(SUM(result_count), 0) AS request_result_count,
               COALESCE(SUM(newly_saved_count), 0) AS request_new_count,
               COALESCE(SUM(updated_count), 0) AS request_updated_count,
               AVG(duration_ms) AS average_request_duration_ms,
               MAX(finished_at) AS last_request_at
        FROM collection_query_requests
        WHERE {request_where}
        """,
        parameters,
    ).fetchone()

    request_count = int(request_summary[0] or 0)
    attempt_count = int(request_summary[1] or 0)
    request_retry_count = int(request_summary[2] or 0)
    successful_request_count = int(request_summary[3] or 0)
    failed_request_count = int(request_summary[4] or 0)
    zero_result_count = int(request_summary[5] or 0)
    request_result_count = int(request_summary[6] or 0)
    request_new_count = int(request_summary[7] or 0)
    request_updated_count = int(request_summary[8] or 0)

    request_cursor = con.execute(
        f"""
        SELECT source_name, source_type, discovery_query,
               COUNT(*) AS request_count,
               COALESCE(SUM(attempt_count), 0) AS attempt_count,
               COALESCE(SUM(retry_count), 0) AS retry_count,
               COALESCE(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), 0)
                   AS successful_request_count,
               COALESCE(SUM(CASE WHEN status = 'failure' THEN 1 ELSE 0 END), 0)
                   AS failed_request_count,
               COALESCE(SUM(CASE WHEN status = 'success' AND result_count = 0 THEN 1 ELSE 0 END), 0)
                   AS zero_result_count,
               COALESCE(SUM(result_count), 0) AS result_count,
               COALESCE(SUM(newly_saved_count), 0) AS new_count,
               COALESCE(SUM(updated_count), 0) AS updated_count,
               AVG(duration_ms) AS average_duration_ms,
               MAX(finished_at) AS last_request_at
        FROM collection_query_requests
        WHERE {request_where}
        GROUP BY source_name, source_type, discovery_query
        ORDER BY new_count DESC, request_count DESC, result_count DESC,
                 last_request_at DESC, source_name, source_type, discovery_query
        LIMIT ?
        """,
        [*parameters, bounded_limit],
    )
    request_rows = _cursor_rows(request_cursor)
    for row in request_rows:
        row_requests = int(row.get("request_count") or 0)
        row_success = int(row.get("successful_request_count") or 0)
        row_failures = int(row.get("failed_request_count") or 0)
        row_zero = int(row.get("zero_result_count") or 0)
        row_results = int(row.get("result_count") or 0)
        row_new = int(row.get("new_count") or 0)
        row["error_rate_percent"] = _rate(row_failures, row_requests)
        row["zero_result_rate_percent"] = _rate(row_zero, row_success)
        row["average_results_per_success"] = _ratio(row_results, row_success)
        row["requests_per_new_item"] = _ratio(row_requests, row_new)

    return {
        "days": bounded_days,
        "source_name": selected_source,
        "query_count": query_count,
        "discovery_count": discovery_count,
        "unique_item_count": unique_item_count,
        "new_count": new_count,
        "repeat_count": repeat_count,
        "duplicate_discovery_count": duplicate_discovery_count,
        "new_rate_percent": _rate(new_count, discovery_count),
        "duplicate_rate_percent": _rate(
            duplicate_discovery_count, discovery_count
        ),
        "average_rank": float(summary_row[4]) if summary_row[4] is not None else None,
        "best_rank": int(summary_row[5]) if summary_row[5] is not None else None,
        "last_discovered_at": summary_row[6],
        "rows": rows,
        "request_count": request_count,
        "attempt_count": attempt_count,
        "request_retry_count": request_retry_count,
        "successful_request_count": successful_request_count,
        "failed_request_count": failed_request_count,
        "zero_result_count": zero_result_count,
        "request_result_count": request_result_count,
        "request_new_count": request_new_count,
        "request_updated_count": request_updated_count,
        "request_error_rate_percent": _rate(failed_request_count, request_count),
        "zero_result_rate_percent": _rate(zero_result_count, successful_request_count),
        "average_results_per_success": _ratio(
            request_result_count, successful_request_count
        ),
        "requests_per_new_item": _ratio(request_count, request_new_count),
        "average_request_duration_ms": (
            float(request_summary[9]) if request_summary[9] is not None else None
        ),
        "last_request_at": request_summary[10],
        "request_rows": request_rows,
    }
