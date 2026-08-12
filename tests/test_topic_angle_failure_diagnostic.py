from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.database import connect_database, init_database
from src.services.topic_angle_ai_service import (
    TOPIC_ANGLE_FEATURE_ID,
    TOPIC_ANGLE_FEATURE_VERSION,
)
from src.services.topic_angle_failure_diagnostic_service import (
    _failure_category,
    build_topic_angle_failure_diagnostic,
)


def _insert_call(
    con,
    *,
    call_id: str,
    request_hash: str,
    attempt_number: int,
    status: str,
    error_type: str,
    created_at: datetime,
    http_status: int | None = 200,
    items_per_request: int = 15,
    thinking_level: str = "high",
    timeout_seconds: int = 600,
    finish_reason: str = "",
    finish_message: str = "",
    error_message: str = "",
    retry_reason: str = "",
    retry_wait_seconds: float = 0.0,
) -> None:
    con.execute(
        """
        INSERT INTO gemini_api_calls(
            call_id, app_id, quota_scope_id, feature_id, feature_version,
            content_pack_id, request_hash, model_name, attempt_number, cache_hit,
            status, http_status, error_type, retry_reason, retry_wait_seconds,
            input_tokens, output_tokens, thought_tokens, total_tokens,
            requested_item_count, configured_items_per_request, thinking_level,
            request_timeout_seconds, finish_reason, finish_message, duration_ms,
            error_message, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE, ?, ?, ?, ?, ?,
                  100, 200, 300, 600, 15, ?, ?, ?, ?, ?, 1000, ?, ?)
        """,
        [
            call_id,
            "content-trend-tracker",
            "test-scope",
            TOPIC_ANGLE_FEATURE_ID,
            TOPIC_ANGLE_FEATURE_VERSION,
            "topic-angle-test-pack",
            request_hash,
            "gemini-3.6-flash",
            attempt_number,
            status,
            http_status,
            error_type,
            retry_reason,
            retry_wait_seconds,
            items_per_request,
            thinking_level,
            timeout_seconds,
            finish_reason,
            finish_message,
            error_message,
            created_at,
        ],
    )


def test_invalid_api_response_distinguishes_json_parse_from_object_shape() -> None:
    assert (
        _failure_category(
            {
                "status": "invalid_api_response",
                "error_type": "invalid_api_response",
                "error_message": (
                    "Gemini API 응답 JSON을 읽을 수 없습니다: Expecting value"
                ),
            },
            had_retry=False,
        )
        == "json_syntax"
    )
    assert (
        _failure_category(
            {
                "status": "invalid_api_response",
                "error_type": "invalid_api_response",
                "error_message": "Gemini API 응답 객체가 올바르지 않습니다.",
            },
            had_retry=False,
        )
        == "response_validation"
    )


def test_terminal_failures_are_grouped_by_cause_and_retry_history(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "topic-angle-failures.duckdb"
    init_database(db_path)
    started_at = datetime(2026, 8, 7, 10, 0, 0)

    with connect_database(db_path) as con:
        _insert_call(
            con,
            call_id="max-retry",
            request_hash="max-tokens-request",
            attempt_number=1,
            status="retrying",
            error_type="rate_limited",
            http_status=429,
            retry_reason="rate_limited",
            retry_wait_seconds=7.5,
            error_message="분당 요청 제한",
            created_at=started_at,
        )
        _insert_call(
            con,
            call_id="max-terminal",
            request_hash="max-tokens-request",
            attempt_number=2,
            status="response_validation_error",
            error_type="response_validation_error",
            finish_reason="MAX_TOKENS",
            error_message="주제 방향 응답 JSON 오류: Unterminated string",
            created_at=started_at + timedelta(seconds=1),
        )
        _insert_call(
            con,
            call_id="json-terminal",
            request_hash="json-request",
            attempt_number=1,
            status="response_validation_error",
            error_type="response_validation_error",
            error_message="주제 방향 응답 JSON 오류: Expecting value",
            created_at=started_at + timedelta(seconds=2),
        )
        _insert_call(
            con,
            call_id="id-terminal",
            request_hash="cluster-id-request",
            attempt_number=1,
            status="response_validation_error",
            error_type="response_validation_error",
            error_message="요청하지 않은 cluster_id가 반환됐습니다: unknown",
            created_at=started_at + timedelta(seconds=3),
        )
        _insert_call(
            con,
            call_id="timeout-terminal",
            request_hash="timeout-request",
            attempt_number=1,
            status="request_timeout",
            error_type="request_timeout",
            http_status=None,
            items_per_request=10,
            thinking_level="low",
            timeout_seconds=120,
            error_message="Gemini API 응답이 제한 시간 안에 완료되지 않았습니다.",
            created_at=started_at + timedelta(seconds=4),
        )
        _insert_call(
            con,
            call_id="rate-no-retry",
            request_hash="rate-limit-no-retry-request",
            attempt_number=1,
            status="rate_limited",
            error_type="rate_limited",
            http_status=429,
            error_message="첫 요청에서 분당 제한으로 종료",
            created_at=started_at + timedelta(seconds=5),
        )
        _insert_call(
            con,
            call_id="rate-retry",
            request_hash="rate-limit-retried-request",
            attempt_number=1,
            status="retrying",
            error_type="rate_limited",
            http_status=429,
            retry_reason="rate_limited",
            retry_wait_seconds=12,
            error_message="분당 제한으로 대기",
            created_at=started_at + timedelta(seconds=6),
        )
        _insert_call(
            con,
            call_id="rate-terminal",
            request_hash="rate-limit-retried-request",
            attempt_number=2,
            status="rate_limited",
            error_type="rate_limited",
            http_status=429,
            error_message="재시도 후에도 분당 제한",
            created_at=started_at + timedelta(seconds=7),
        )
        _insert_call(
            con,
            call_id="retrying-only",
            request_hash="unfinished-retry-request",
            attempt_number=1,
            status="retrying",
            error_type="rate_limited",
            http_status=429,
            retry_reason="rate_limited",
            retry_wait_seconds=30,
            error_message="아직 재시도 중",
            created_at=started_at + timedelta(seconds=8),
        )

    with connect_database(db_path, read_only=True) as con:
        diagnostic = build_topic_angle_failure_diagnostic(
            con,
            app_id="content-trend-tracker",
            items_per_request=15,
            thinking_level="high",
            timeout_seconds=600,
        )

    assert diagnostic["available"] is True
    assert diagnostic["terminal_failure_count"] == 6
    assert diagnostic["current_runtime_failure_count"] == 5
    assert diagnostic["other_runtime_failure_count"] == 1
    assert diagnostic["retried_terminal_failure_count"] == 2
    assert diagnostic["current_runtime_retried_failure_count"] == 2
    assert diagnostic["retrying_attempt_count"] == 2
    assert diagnostic["current_runtime_retrying_attempt_count"] == 2
    assert diagnostic["total_retry_wait_seconds"] == 19.5
    assert diagnostic["current_runtime_total_retry_wait_seconds"] == 19.5
    assert diagnostic["maximum_retry_wait_seconds"] == 12

    categories = {
        item["category"]: item for item in diagnostic["failure_categories"]
    }
    assert categories["max_tokens"]["count"] == 1
    assert categories["max_tokens"]["current_runtime_count"] == 1
    assert categories["json_syntax"]["count"] == 1
    assert categories["cluster_id_validation"]["count"] == 1
    assert categories["request_timeout"]["count"] == 1
    assert categories["request_timeout"]["current_runtime_count"] == 0
    assert categories["rate_limit"]["count"] == 1
    assert categories["retry_wait_exhausted"]["count"] == 1

    max_tokens_sample = next(
        item
        for item in diagnostic["samples"]
        if item["failure_category"] == "max_tokens"
    )
    assert max_tokens_sample["had_retry"] is True
    assert max_tokens_sample["attempt_number"] == 2
    assert max_tokens_sample["finish_reason"] == "MAX_TOKENS"
    assert max_tokens_sample["total_retry_wait_seconds"] == 7.5

    retried_rate_limit_sample = next(
        item
        for item in diagnostic["samples"]
        if item["failure_category"] == "retry_wait_exhausted"
    )
    assert retried_rate_limit_sample["had_retry"] is True
    assert retried_rate_limit_sample["retrying_attempt_count"] == 1
    assert retried_rate_limit_sample["total_retry_wait_seconds"] == 12
    assert retried_rate_limit_sample["retry_reasons"] == ["rate_limited"]
    assert "실제 대기 12초" in retried_rate_limit_sample["failure_category_label"]
