"""저장된 NAVER·Daum 논리 검색 요청 원장을 조회합니다."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import duckdb

from src.services.portal_request_schema_service import ensure_portal_request_ledger_schema


def list_recent_portal_requests(
    con: duckdb.DuckDBPyConnection,
    *,
    days: int = 7,
    source_name: str = "",
    limit: int = 200,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    ensure_portal_request_ledger_schema(con)
    bounded_days = 7 if int(days) <= 7 else 30
    bounded_limit = max(1, min(int(limit), 500))
    selected_source = str(source_name or "").strip().casefold()
    if selected_source not in {"", "naver", "daum"}:
        selected_source = ""

    where_sql = "finished_at >= ?"
    parameters: list[Any] = [
        (now or datetime.now()) - timedelta(days=bounded_days)
    ]
    if selected_source:
        where_sql += " AND source_name = ?"
        parameters.append(selected_source)

    cursor = con.execute(
        f"""
        SELECT run_id, source_name, source_type, discovery_query, request_page,
               requested_result_count, status, attempt_count, retry_count,
               result_count, newly_saved_count, updated_count, skipped_count,
               http_status, error_type, error_message, duration_ms,
               started_at, finished_at
        FROM collection_query_requests
        WHERE {where_sql}
        ORDER BY finished_at DESC, source_name, source_type, discovery_query, request_page
        LIMIT ?
        """,
        [*parameters, bounded_limit],
    )
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
