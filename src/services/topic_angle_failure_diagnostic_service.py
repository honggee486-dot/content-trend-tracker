"""Gemini 주제 방향 요청의 최종 실패 표본을 읽기 전용으로 집계합니다."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import duckdb

from src.services.topic_angle_ai_service import (
    TOPIC_ANGLE_FEATURE_ID,
    TOPIC_ANGLE_FEATURE_VERSION,
)


DEFAULT_FAILURE_SAMPLE_LIMIT = 10
_SUCCESS_STATUSES = frozenset({"success", "success_after_retry"})
_RETRYING_STATUS = "retrying"
_FAILURE_CATEGORY_ORDER = (
    "max_tokens",
    "daily_quota",
    "rate_limit",
    "retry_wait_exhausted",
    "request_timeout",
    "json_syntax",
    "cluster_id_validation",
    "response_validation",
    "transport",
    "request_rejected",
    "other",
)
_FAILURE_CATEGORY_LABELS = {
    "max_tokens": "MAX_TOKENS·출력 한도",
    "daily_quota": "일일 할당량",
    "rate_limit": "분당 제한·재시도 없음",
    "retry_wait_exhausted": "재시도 후 분당 제한 종료",
    "request_timeout": "요청 제한 시간",
    "json_syntax": "JSON 구문·절단",
    "cluster_id_validation": "cluster_id 검증",
    "response_validation": "기타 응답 검증",
    "transport": "서비스·네트워크",
    "request_rejected": "요청·권한",
    "other": "기타",
}
_TRANSPORT_ERROR_TYPES = frozenset(
    {"service_unavailable", "network_error", "request_cancelled"}
)
_REQUEST_REJECTED_ERROR_TYPES = frozenset(
    {
        "invalid_request",
        "authentication_error",
        "permission_error",
        "model_not_found",
        "api_error",
        "empty_api_response",
    }
)


def _table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    return str(table_name) in {
        str(row[0]) for row in con.execute("SHOW TABLES").fetchall()
    }


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _non_negative_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _compact_message(value: Any, *, limit: int = 300) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat(sep=" ", timespec="seconds"))
    return str(value)


def _failure_category(row: dict[str, Any], *, had_retry: bool) -> str:
    status = str(row.get("status") or "").strip().casefold()
    error_type = str(row.get("error_type") or "").strip().casefold()
    finish_reason = str(row.get("finish_reason") or "").strip().casefold()
    message = " ".join(
        str(row.get(name) or "")
        for name in ("error_message", "finish_message")
    ).casefold()

    if (
        "max_token" in finish_reason
        or finish_reason in {"length", "max_output_tokens"}
        or "max_tokens" in message
        or "max output token" in message
    ):
        return "max_tokens"
    if error_type == "daily_quota_exhausted" or status == "daily_quota_exhausted":
        return "daily_quota"
    if error_type == "rate_limit_timeout" or status == "rate_limit_timeout":
        return "retry_wait_exhausted"
    if error_type == "rate_limited" or status == "rate_limited":
        return "retry_wait_exhausted" if had_retry else "rate_limit"
    if error_type == "request_timeout" or status == "request_timeout":
        return "request_timeout"
    if (
        "json 오류" in message
        or "json을 읽을 수 없습니다" in message
        or "json decode" in message
        or "unterminated" in message
        or "expecting" in message
    ):
        return "json_syntax"
    if (
        "cluster_id" in message
        or "요청하지 않은 id" in message
        or "id가 반환" in message
        or "id 누락" in message
    ):
        return "cluster_id_validation"
    if (
        status == "response_validation_error"
        or error_type == "response_validation_error"
        or error_type == "invalid_api_response"
    ):
        return "response_validation"
    if error_type in _TRANSPORT_ERROR_TYPES:
        return "transport"
    if error_type in _REQUEST_REJECTED_ERROR_TYPES:
        return "request_rejected"
    return "other"


def _empty_result(*, sample_limit: int, reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "missing_columns": [],
        "sample_limit": sample_limit,
        "terminal_failure_count": 0,
        "current_runtime_failure_count": 0,
        "other_runtime_failure_count": 0,
        "retried_terminal_failure_count": 0,
        "current_runtime_retried_failure_count": 0,
        "retrying_attempt_count": 0,
        "current_runtime_retrying_attempt_count": 0,
        "total_retry_wait_seconds": 0.0,
        "current_runtime_total_retry_wait_seconds": 0.0,
        "maximum_retry_wait_seconds": 0.0,
        "failure_categories": [],
        "samples": [],
    }


def build_topic_angle_failure_diagnostic(
    con: duckdb.DuckDBPyConnection,
    *,
    app_id: str,
    items_per_request: int,
    thinking_level: str,
    timeout_seconds: int,
    sample_limit: int = DEFAULT_FAILURE_SAMPLE_LIMIT,
) -> dict[str, Any]:
    """재시도 묶음별 마지막 완료 시도 중 최종 실패만 최신 순으로 반환합니다."""
    bounded_limit = max(1, min(int(sample_limit), 50))
    if not _table_exists(con, "gemini_api_calls"):
        return _empty_result(sample_limit=bounded_limit, reason="missing_table")

    available_columns = {
        str(row[1])
        for row in con.execute("PRAGMA table_info('gemini_api_calls')").fetchall()
    }
    required_columns = {
        "call_id",
        "app_id",
        "feature_id",
        "feature_version",
        "request_hash",
        "attempt_number",
        "cache_hit",
        "status",
        "configured_items_per_request",
        "thinking_level",
        "request_timeout_seconds",
        "created_at",
    }
    missing_columns = sorted(required_columns - available_columns)
    if missing_columns:
        result = _empty_result(
            sample_limit=bounded_limit,
            reason="missing_columns",
        )
        result["missing_columns"] = missing_columns
        return result

    def optional_column(name: str, sql_type: str) -> str:
        if name in available_columns:
            return name
        return f"CAST(NULL AS {sql_type}) AS {name}"

    optional_selects = ",\n               ".join(
        [
            optional_column("http_status", "INTEGER"),
            optional_column("error_type", "VARCHAR"),
            optional_column("retry_reason", "VARCHAR"),
            optional_column("retry_wait_seconds", "DOUBLE"),
            optional_column("requested_item_count", "INTEGER"),
            optional_column("input_tokens", "BIGINT"),
            optional_column("output_tokens", "BIGINT"),
            optional_column("thought_tokens", "BIGINT"),
            optional_column("total_tokens", "BIGINT"),
            optional_column("finish_reason", "VARCHAR"),
            optional_column("finish_message", "VARCHAR"),
            optional_column("duration_ms", "BIGINT"),
            optional_column("error_message", "VARCHAR"),
        ]
    )
    cursor = con.execute(
        f"""
        SELECT call_id, request_hash, status, attempt_number, created_at,
               configured_items_per_request, thinking_level,
               request_timeout_seconds,
               {optional_selects}
        FROM gemini_api_calls
        WHERE app_id = ?
          AND feature_id = ?
          AND feature_version = ?
          AND cache_hit = FALSE
        ORDER BY created_at, attempt_number, call_id
        """,
        [app_id, TOPIC_ANGLE_FEATURE_ID, TOPIC_ANGLE_FEATURE_VERSION],
    )
    columns = [str(item[0]) for item in cursor.description]
    rows = [dict(zip(columns, values)) for values in cursor.fetchall()]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for index, row in enumerate(rows):
        key = (
            str(row.get("request_hash") or "").strip()
            or str(row.get("call_id") or "").strip()
            or f"unkeyed-{index}"
        )
        grouped.setdefault(key, []).append(row)

    failures: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for group in grouped.values():
        terminals = [
            row
            for row in group
            if str(row.get("status") or "").strip().casefold()
            != _RETRYING_STATUS
        ]
        if not terminals:
            continue
        terminal = max(
            terminals,
            key=lambda row: (
                _non_negative_int(row.get("attempt_number")),
                row.get("created_at") or datetime.min,
                str(row.get("call_id") or ""),
            ),
        )
        if (
            str(terminal.get("status") or "").strip().casefold()
            in _SUCCESS_STATUSES
        ):
            continue

        retry_rows = [
            row
            for row in group
            if str(row.get("status") or "").strip().casefold()
            == _RETRYING_STATUS
        ]
        retry_waits = [
            _non_negative_float(row.get("retry_wait_seconds")) for row in retry_rows
        ]
        retry_reasons: list[str] = []
        seen_reasons: set[str] = set()
        for row in retry_rows:
            reason = _compact_message(
                row.get("retry_reason") or row.get("error_type"),
                limit=100,
            )
            key = reason.casefold()
            if reason and key not in seen_reasons:
                seen_reasons.add(key)
                retry_reasons.append(reason)

        retry_summary = {
            "had_retry": bool(retry_rows)
            or _non_negative_int(terminal.get("attempt_number")) > 1,
            "retrying_attempt_count": len(retry_rows),
            "total_retry_wait_seconds": round(sum(retry_waits), 3),
            "maximum_retry_wait_seconds": round(max(retry_waits, default=0.0), 3),
            "retry_reasons": tuple(retry_reasons),
        }
        failures.append((terminal, retry_summary))

    expected_thinking = str(thinking_level or "").strip().casefold()

    def matches_runtime(row: dict[str, Any]) -> bool:
        return (
            _non_negative_int(row.get("configured_items_per_request"))
            == int(items_per_request)
            and str(row.get("thinking_level") or "").strip().casefold()
            == expected_thinking
            and _non_negative_int(row.get("request_timeout_seconds"))
            == int(timeout_seconds)
        )

    ordered_failures = sorted(
        failures,
        key=lambda item: (
            item[0].get("created_at") or datetime.min,
            _non_negative_int(item[0].get("attempt_number")),
            str(item[0].get("call_id") or ""),
        ),
        reverse=True,
    )

    category_counts = {name: 0 for name in _FAILURE_CATEGORY_ORDER}
    current_category_counts = {name: 0 for name in _FAILURE_CATEGORY_ORDER}
    for row, retry_summary in failures:
        category = _failure_category(
            row,
            had_retry=bool(retry_summary["had_retry"]),
        )
        category_counts[category] += 1
        if matches_runtime(row):
            current_category_counts[category] += 1

    samples: list[dict[str, Any]] = []
    for row, retry_summary in ordered_failures[:bounded_limit]:
        input_tokens = _non_negative_int(row.get("input_tokens"))
        output_tokens = _non_negative_int(row.get("output_tokens"))
        thought_tokens = _non_negative_int(row.get("thought_tokens"))
        total_tokens = _non_negative_int(row.get("total_tokens"))
        if total_tokens <= 0:
            total_tokens = input_tokens + output_tokens + thought_tokens
        runtime_match = matches_runtime(row)
        had_retry = bool(retry_summary["had_retry"])
        category = _failure_category(row, had_retry=had_retry)
        category_label = _FAILURE_CATEGORY_LABELS[category]
        total_retry_wait = float(retry_summary["total_retry_wait_seconds"])
        if category == "retry_wait_exhausted" and total_retry_wait > 0:
            category_label = f"{category_label}·실제 대기 {total_retry_wait:g}초"
        samples.append(
            {
                "created_at": _iso(row.get("created_at")),
                "matches_current_runtime": runtime_match,
                "runtime_scope": "current" if runtime_match else "other",
                "status": str(row.get("status") or "").strip(),
                "http_status": _optional_int(row.get("http_status")),
                "error_type": _compact_message(row.get("error_type")),
                "finish_reason": _compact_message(row.get("finish_reason")),
                "finish_message": _compact_message(row.get("finish_message")),
                "error_message": _compact_message(row.get("error_message")),
                "attempt_number": _non_negative_int(row.get("attempt_number")),
                "had_retry": had_retry,
                "retrying_attempt_count": int(
                    retry_summary["retrying_attempt_count"]
                ),
                "total_retry_wait_seconds": total_retry_wait,
                "maximum_retry_wait_seconds": float(
                    retry_summary["maximum_retry_wait_seconds"]
                ),
                "retry_reasons": list(retry_summary["retry_reasons"]),
                "failure_category": category,
                "failure_category_label": category_label,
                "requested_item_count": _non_negative_int(
                    row.get("requested_item_count")
                ),
                "configured_items_per_request": _non_negative_int(
                    row.get("configured_items_per_request")
                ),
                "thinking_level": str(row.get("thinking_level") or "").strip(),
                "request_timeout_seconds": _non_negative_int(
                    row.get("request_timeout_seconds")
                ),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "thought_tokens": thought_tokens,
                "generation_tokens": output_tokens + thought_tokens,
                "total_tokens": total_tokens,
                "duration_ms": _non_negative_int(row.get("duration_ms")),
            }
        )

    current_count = sum(matches_runtime(row) for row, _summary in failures)
    retried_count = sum(bool(summary["had_retry"]) for _row, summary in failures)
    current_retried_count = sum(
        bool(summary["had_retry"]) and matches_runtime(row)
        for row, summary in failures
    )
    retrying_attempt_count = sum(
        int(summary["retrying_attempt_count"]) for _row, summary in failures
    )
    current_retrying_attempt_count = sum(
        int(summary["retrying_attempt_count"])
        for row, summary in failures
        if matches_runtime(row)
    )
    total_retry_wait_seconds = round(
        sum(float(summary["total_retry_wait_seconds"]) for _row, summary in failures),
        3,
    )
    current_runtime_total_retry_wait_seconds = round(
        sum(
            float(summary["total_retry_wait_seconds"])
            for row, summary in failures
            if matches_runtime(row)
        ),
        3,
    )
    maximum_retry_wait_seconds = round(
        max(
            (
                float(summary["maximum_retry_wait_seconds"])
                for _row, summary in failures
            ),
            default=0.0,
        ),
        3,
    )
    failure_categories = [
        {
            "category": category,
            "label": _FAILURE_CATEGORY_LABELS[category],
            "count": category_counts[category],
            "current_runtime_count": current_category_counts[category],
        }
        for category in _FAILURE_CATEGORY_ORDER
        if category_counts[category] > 0
    ]
    return {
        "available": True,
        "reason": "",
        "missing_columns": [],
        "sample_limit": bounded_limit,
        "terminal_failure_count": len(failures),
        "current_runtime_failure_count": current_count,
        "other_runtime_failure_count": len(failures) - current_count,
        "retried_terminal_failure_count": retried_count,
        "current_runtime_retried_failure_count": current_retried_count,
        "retrying_attempt_count": retrying_attempt_count,
        "current_runtime_retrying_attempt_count": current_retrying_attempt_count,
        "total_retry_wait_seconds": total_retry_wait_seconds,
        "current_runtime_total_retry_wait_seconds": (
            current_runtime_total_retry_wait_seconds
        ),
        "maximum_retry_wait_seconds": maximum_retry_wait_seconds,
        "failure_categories": failure_categories,
        "samples": samples,
    }
