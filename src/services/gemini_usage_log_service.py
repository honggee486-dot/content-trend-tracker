"""Gemini API 호출 기록을 모델·기능별로 읽기 전용 집계합니다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import duckdb


TOPIC_ANGLE_FEATURE_ID = "trend_topic_angle_batch_v1"
CLUSTER_GROUPING_FEATURE_ID = "trend_cluster_grouping_v3"
PREVIOUS_CLUSTER_GROUPING_FEATURE_ID = "trend_cluster_grouping_v2"
LEGACY_CLUSTER_REVIEW_FEATURE_ID = "trend_cluster_review_v1"
# 외부 테스트·호출부의 과거 상수 이름은 새 기본 군집화 기능을 가리킵니다.
CLUSTER_REVIEW_FEATURE_ID = CLUSTER_GROUPING_FEATURE_ID
LEGACY_DRAFT_FEATURE_ID = "blog_draft_generation_v1"

FEATURE_LABELS = {
    TOPIC_ANGLE_FEATURE_ID: "자동·예약 글감 분석",
    CLUSTER_GROUPING_FEATURE_ID: "AI 2단계 군집화",
    PREVIOUS_CLUSTER_GROUPING_FEATURE_ID: "과거 AI 기본 군집화",
    LEGACY_CLUSTER_REVIEW_FEATURE_ID: "과거 군집 재검토",
    LEGACY_DRAFT_FEATURE_ID: "과거 직접 초안 생성",
}
SUCCESS_STATUSES = frozenset({"success", "success_after_retry"})
RETRYING_STATUS = "retrying"


@dataclass(frozen=True)
class GeminiUsageLogSummary:
    period_days: int
    rows: tuple[dict[str, Any], ...]
    grouped_rows: tuple[dict[str, Any], ...]
    attempt_count: int
    successful_count: int
    failed_count: int
    retrying_count: int
    total_tokens: int
    auto_analysis_attempt_count: int
    cluster_review_attempt_count: int
    flash_36_attempt_count: int


def feature_label(feature_id: Any) -> str:
    normalized = str(feature_id or "").strip()
    return FEATURE_LABELS.get(normalized, normalized or "기능 미기록")


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _row_total_tokens(row: dict[str, Any]) -> int:
    recorded = _non_negative_int(row.get("total_tokens"))
    if recorded > 0:
        return recorded
    return sum(
        _non_negative_int(row.get(name))
        for name in ("input_tokens", "output_tokens", "thought_tokens")
    )


def _empty_summary(period_days: int) -> GeminiUsageLogSummary:
    return GeminiUsageLogSummary(
        period_days=period_days,
        rows=(),
        grouped_rows=(),
        attempt_count=0,
        successful_count=0,
        failed_count=0,
        retrying_count=0,
        total_tokens=0,
        auto_analysis_attempt_count=0,
        cluster_review_attempt_count=0,
        flash_36_attempt_count=0,
    )


def get_gemini_usage_log_summary(
    con: duckdb.DuckDBPyConnection,
    *,
    app_id: str,
    period_days: int = 30,
    limit: int = 500,
) -> GeminiUsageLogSummary:
    """앱이 기록한 실제 외부 호출만 최근 기간 기준으로 집계합니다."""
    normalized_days = max(1, min(int(period_days), 365))
    tables = {
        str(row[0])
        for row in con.execute("SHOW TABLES").fetchall()
    }
    if "gemini_api_calls" not in tables:
        return _empty_summary(normalized_days)

    available_columns = {
        str(row[1])
        for row in con.execute("PRAGMA table_info('gemini_api_calls')").fetchall()
    }

    def optional_column(name: str, sql_type: str) -> str:
        if name in available_columns:
            return name
        return f"CAST(NULL AS {sql_type}) AS {name}"

    cache_condition = (
        "AND cache_hit = FALSE" if "cache_hit" in available_columns else ""
    )
    cutoff = datetime.now() - timedelta(days=normalized_days)
    rows = con.execute(
        f"""
        SELECT created_at, feature_id, model_name, status,
               {optional_column('http_status', 'INTEGER')},
               {optional_column('error_type', 'VARCHAR')},
               {optional_column('requested_item_count', 'INTEGER')},
               {optional_column('input_tokens', 'BIGINT')},
               {optional_column('output_tokens', 'BIGINT')},
               {optional_column('thought_tokens', 'BIGINT')},
               {optional_column('total_tokens', 'BIGINT')}
        FROM gemini_api_calls
        WHERE app_id = ?
          {cache_condition}
          AND created_at >= ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        [
            str(app_id or "").strip(),
            cutoff,
            max(1, min(int(limit), 2000)),
        ],
    ).fetchall()
    columns = [str(item[0]) for item in con.description]
    normalized_rows = [dict(zip(columns, row)) for row in rows]

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    successful_count = 0
    retrying_count = 0
    total_tokens = 0
    auto_attempts = 0
    cluster_attempts = 0
    flash_36_attempts = 0

    for row in normalized_rows:
        feature_id = str(row.get("feature_id") or "").strip()
        model_name = str(row.get("model_name") or "").strip() or "모델 미기록"
        status = str(row.get("status") or "").strip().casefold()
        is_success = status in SUCCESS_STATUSES
        is_retrying = status == RETRYING_STATUS
        if is_success:
            successful_count += 1
        if is_retrying:
            retrying_count += 1
        if feature_id == TOPIC_ANGLE_FEATURE_ID:
            auto_attempts += 1
        if feature_id in {
            CLUSTER_GROUPING_FEATURE_ID,
            PREVIOUS_CLUSTER_GROUPING_FEATURE_ID,
            LEGACY_CLUSTER_REVIEW_FEATURE_ID,
        }:
            cluster_attempts += 1
        if model_name.casefold().startswith("gemini-3.6-flash"):
            flash_36_attempts += 1

        row_tokens = _row_total_tokens(row)
        total_tokens += row_tokens
        key = (model_name, feature_id)
        group = grouped.setdefault(
            key,
            {
                "model_name": model_name,
                "feature_id": feature_id,
                "feature_label": feature_label(feature_id),
                "attempt_count": 0,
                "successful_count": 0,
                "failed_count": 0,
                "retrying_count": 0,
                "requested_item_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "thought_tokens": 0,
                "total_tokens": 0,
                "latest_created_at": row.get("created_at"),
            },
        )
        group["attempt_count"] += 1
        group["successful_count"] += int(is_success)
        group["retrying_count"] += int(is_retrying)
        group["failed_count"] += int(not is_success and not is_retrying)
        group["requested_item_count"] += _non_negative_int(
            row.get("requested_item_count")
        )
        group["input_tokens"] += _non_negative_int(row.get("input_tokens"))
        group["output_tokens"] += _non_negative_int(row.get("output_tokens"))
        group["thought_tokens"] += _non_negative_int(row.get("thought_tokens"))
        group["total_tokens"] += row_tokens

    grouped_rows = tuple(
        sorted(
            grouped.values(),
            key=lambda item: (
                item.get("latest_created_at") or datetime.min,
                str(item.get("model_name") or ""),
            ),
            reverse=True,
        )
    )
    attempt_count = len(normalized_rows)
    failed_count = max(0, attempt_count - successful_count - retrying_count)
    return GeminiUsageLogSummary(
        period_days=normalized_days,
        rows=tuple(normalized_rows),
        grouped_rows=grouped_rows,
        attempt_count=attempt_count,
        successful_count=successful_count,
        failed_count=failed_count,
        retrying_count=retrying_count,
        total_tokens=total_tokens,
        auto_analysis_attempt_count=auto_attempts,
        cluster_review_attempt_count=cluster_attempts,
        flash_36_attempt_count=flash_36_attempts,
    )
