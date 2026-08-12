"""포털 검색 요청 원장의 추가형 DuckDB 스키마를 보장합니다."""

from __future__ import annotations

import duckdb


def ensure_portal_request_ledger_schema(con: duckdb.DuckDBPyConnection) -> None:
    """기존 데이터와 테이블을 변경하지 않고 요청 원장만 추가합니다."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_query_requests (
            run_id VARCHAR NOT NULL,
            source_name VARCHAR NOT NULL,
            source_type VARCHAR NOT NULL,
            discovery_query VARCHAR NOT NULL,
            request_page INTEGER NOT NULL,
            requested_result_count INTEGER NOT NULL DEFAULT 0,
            status VARCHAR NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0,
            result_count INTEGER NOT NULL DEFAULT 0,
            newly_saved_count INTEGER NOT NULL DEFAULT 0,
            updated_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            http_status INTEGER,
            error_type VARCHAR,
            error_message VARCHAR,
            duration_ms BIGINT NOT NULL DEFAULT 0,
            started_at TIMESTAMP NOT NULL,
            finished_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP NOT NULL,
            PRIMARY KEY(run_id, source_name, source_type, discovery_query, request_page)
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_collection_query_requests_finished
        ON collection_query_requests(finished_at)
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_collection_query_requests_query
        ON collection_query_requests(source_name, discovery_query, finished_at)
        """
    )
