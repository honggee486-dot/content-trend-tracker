from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from src.config import DEFAULT_DB_PATH
from src.services.program_log_context import current_program_log_correlation_id

PROGRAM_LOG_TABLE = "program_operation_events"
PROGRAM_LOG_DEFAULT_LIMIT = 100
PROGRAM_LOG_MAX_LIMIT = 500

_FEATURE_LABELS = {
    "blog_draft_generation_v1": "블로그 초안 생성",
    "trend_topic_angle_batch_v1": "주제 방향 자동 생성",
    "trend_cluster_grouping_v3": "2차 군집",
    "trend_cluster_grouping_v2": "과거 2차 군집",
    "trend_cluster_review_v1": "과거 군집 재검토",
    "trend_candidate_ai_evaluation_v1": "전체 글감 AI 평가",
    "trend_blog_ai_routing_v1": "블로그 자동 분류",
}

_STATUS_LABELS = {
    "clicked": "클릭",
    "started": "시작",
    "completed": "완료",
    "success": "성공",
    "failed": "실패",
    "error": "실패",
    "skipped": "생략",
    "partial": "부분 완료",
    "running": "실행 중",
    "queued": "대기",
    "cache_hit": "캐시",
    "in_progress": "전송 중",
    "response_received": "응답 수신",
}

_EVENT_TYPE_LABELS = {
    "button": "버튼",
    "task": "작업",
    "stage": "단계",
    "api": "API",
    "system": "시스템",
}


def feature_label(feature_id: object) -> str:
    value = str(feature_id or "").strip()
    return _FEATURE_LABELS.get(value, value or "Gemini 호출")


def status_label(status: object) -> str:
    value = str(status or "").strip()
    return _STATUS_LABELS.get(value.casefold(), value or "-")


def event_type_label(event_type: object) -> str:
    value = str(event_type or "").strip()
    return _EVENT_TYPE_LABELS.get(value.casefold(), value or "-")


def _safe_limit(value: int) -> int:
    return max(1, min(int(value or PROGRAM_LOG_DEFAULT_LIMIT), PROGRAM_LOG_MAX_LIMIT))


def ensure_program_log_table(con: Any) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PROGRAM_LOG_TABLE} (
            event_id VARCHAR PRIMARY KEY,
            event_time TIMESTAMP NOT NULL,
            event_type VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            action VARCHAR NOT NULL,
            detail VARCHAR NOT NULL DEFAULT '',
            item_count BIGINT NOT NULL DEFAULT 0,
            duration_ms BIGINT NOT NULL DEFAULT 0,
            correlation_id VARCHAR NOT NULL DEFAULT '',
            metadata_json VARCHAR NOT NULL DEFAULT '{{}}'
        )
        """
    )
    con.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_program_operation_events_time
        ON {PROGRAM_LOG_TABLE}(event_time)
        """
    )


def record_program_event(
    *,
    event_type: str,
    status: str,
    action: str,
    detail: str = "",
    source: str = "app",
    item_count: int = 0,
    duration_ms: int = 0,
    correlation_id: str = "",
    metadata: Mapping[str, Any] | None = None,
    event_time: datetime | None = None,
    con: Any | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> bool:
    resolved_correlation_id = (
        current_program_log_correlation_id()
        or str(correlation_id or "").strip()
    )
    event_values = [
        f"opevt_{uuid4().hex}",
        event_time if isinstance(event_time, datetime) else datetime.now(),
        str(event_type or "system")[:40],
        str(status or "")[:40],
        str(source or "app")[:100],
        str(action or "")[:200],
        str(detail or "")[:2000],
        max(0, int(item_count or 0)),
        max(0, int(duration_ms or 0)),
        resolved_correlation_id[:200],
        json.dumps(dict(metadata or {}), ensure_ascii=False, default=str)[:4000],
    ]

    def _insert(connection: Any) -> None:
        ensure_program_log_table(connection)
        connection.execute(
            f"""
            INSERT INTO {PROGRAM_LOG_TABLE}(
                event_id, event_time, event_type, status, source, action,
                detail, item_count, duration_ms, correlation_id, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            event_values,
        )

    try:
        if con is not None:
            _insert(con)
            return True
        from src.database import connect_database

        with connect_database(db_path) as connection:
            _insert(connection)
        return True
    except Exception:
        return False


def list_recent_program_events(
    con: Any,
    *,
    limit: int = PROGRAM_LOG_DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    ensure_program_log_table(con)
    rows = con.execute(
        f"""
        SELECT event_id, event_time, event_type, status, source, action, detail,
               item_count, duration_ms, correlation_id, metadata_json
        FROM {PROGRAM_LOG_TABLE}
        ORDER BY event_time DESC, event_id DESC
        LIMIT ?
        """,
        [_safe_limit(limit)],
    ).fetchall()
    columns = (
        "event_id",
        "event_time",
        "event_type",
        "status",
        "source",
        "action",
        "detail",
        "item_count",
        "duration_ms",
        "correlation_id",
        "metadata_json",
    )
    return [dict(zip(columns, row)) for row in rows]


def list_recent_gemini_calls(
    con: Any,
    *,
    limit: int = PROGRAM_LOG_DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    from src.services.gemini_call_lifecycle_service import (
        ensure_gemini_call_lifecycle_schema,
    )

    ensure_gemini_call_lifecycle_schema(con)
    rows = con.execute(
        """
        SELECT created_at, COALESCE(started_at, created_at) AS started_at,
               finished_at, COALESCE(rate_limit_wait_seconds, 0) AS rate_limit_wait_seconds,
               status, model_name, feature_id, feature_version,
               attempt_number, cache_hit, requested_item_count,
               input_tokens, output_tokens, thought_tokens, total_tokens,
               http_status, finish_reason, duration_ms, error_type, error_message
        FROM gemini_api_calls
        ORDER BY COALESCE(started_at, created_at) DESC, call_id DESC
        LIMIT ?
        """,
        [_safe_limit(limit)],
    ).fetchall()
    columns = (
        "created_at",
        "started_at",
        "finished_at",
        "rate_limit_wait_seconds",
        "status",
        "model_name",
        "feature_id",
        "feature_version",
        "attempt_number",
        "cache_hit",
        "requested_item_count",
        "input_tokens",
        "output_tokens",
        "thought_tokens",
        "total_tokens",
        "http_status",
        "finish_reason",
        "duration_ms",
        "error_type",
        "error_message",
    )
    return [dict(zip(columns, row)) for row in rows]
