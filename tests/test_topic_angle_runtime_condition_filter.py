from __future__ import annotations

from pathlib import Path

from src.config import GeminiConfig
from src.database import connect_database, init_database
from src.services.gemini_service import record_gemini_api_call
from src.services.topic_angle_quality_diagnostic_service import (
    build_topic_angle_quality_diagnostic,
)


def _config() -> GeminiConfig:
    return GeminiConfig(
        api_key="test-key",
        model="gemini-3.6-flash",
        app_id="content-trend-tracker",
        quota_scope_id="test-scope",
        timeout_seconds=60,
        retry_wait_seconds=2.0,
        retry_max_wait_seconds=30.0,
        topic_angle_batch_limit=15,
        topic_angle_thinking_level="high",
        topic_angle_timeout_seconds=600,
    )


def _record_call(
    con,
    *,
    request_hash: str,
    status: str,
    error_type: str,
    configured_items: int,
    requested_items: int,
    thinking_level: str,
    timeout_seconds: int,
) -> None:
    config = _config()
    record_gemini_api_call(
        con,
        config=config,
        content_pack_id=f"topic_angle_batch_{request_hash}",
        request_hash=request_hash,
        feature_id="trend_topic_angle_batch_v1",
        feature_version="6",
        attempt_number=1,
        cache_hit=False,
        status=status,
        http_status=200,
        error_type=error_type,
        retry_reason="",
        retry_wait_seconds=0,
        input_tokens=100,
        output_tokens=200,
        thought_tokens=300,
        total_tokens=600,
        duration_ms=120000,
        error_message="",
        requested_item_count=requested_items,
        configured_items_per_request=configured_items,
        thinking_level=thinking_level,
        request_timeout_seconds=timeout_seconds,
    )


def _diagnose(db_path: Path):
    config = _config()
    with connect_database(db_path) as con:
        return build_topic_angle_quality_diagnostic(
            con,
            app_id=config.app_id,
            items_per_request=15,
            thinking_level="high",
            timeout_seconds=600,
            min_opportunity_score=50,
        )


def test_other_runtime_validation_failure_is_excluded_from_current_status(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "other-runtime-validation.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        _record_call(
            con,
            request_hash="old-20-medium",
            status="response_validation_error",
            error_type="response_validation_error",
            configured_items=20,
            requested_items=17,
            thinking_level="medium",
            timeout_seconds=600,
        )
        _record_call(
            con,
            request_hash="current-15-high",
            status="success",
            error_type="",
            configured_items=15,
            requested_items=15,
            thinking_level="high",
            timeout_seconds=600,
        )

    diagnostic = _diagnose(db_path)

    assert diagnostic.operation.validation_failure_count == 0
    assert diagnostic.operation.other_runtime_validation_failure_count == 1
    assert diagnostic.operation.matching_runtime_request_count == 1
    assert diagnostic.operation.requested_items == 15
    assert diagnostic.status == "표본 수집 중"
    assert any(
        "다른 실행 조건의 과거 응답 검증 실패 1회" in reason
        for reason in diagnostic.reasons
    )


def test_matching_runtime_validation_failure_keeps_warning_status(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "matching-runtime-validation.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        _record_call(
            con,
            request_hash="current-15-high-failed",
            status="response_validation_error",
            error_type="response_validation_error",
            configured_items=15,
            requested_items=15,
            thinking_level="high",
            timeout_seconds=600,
        )

    diagnostic = _diagnose(db_path)

    assert diagnostic.operation.validation_failure_count == 1
    assert diagnostic.operation.other_runtime_validation_failure_count == 0
    assert diagnostic.operation.matching_runtime_request_count == 0
    assert diagnostic.status == "응답 검증 주의"
    assert "현재 설정 상한·사고 수준·제한 시간과 일치한" in diagnostic.summary
