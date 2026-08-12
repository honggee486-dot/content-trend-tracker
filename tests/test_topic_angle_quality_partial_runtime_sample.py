from __future__ import annotations

from pathlib import Path

from src.config import GeminiConfig
from src.database import connect_database, init_database
from src.services.gemini_service import record_gemini_api_call
from src.services.topic_angle_quality_diagnostic_service import (
    build_topic_angle_quality_diagnostic,
)


def _config(*, items_per_request: int) -> GeminiConfig:
    return GeminiConfig(
        api_key="test-key",
        model="gemini-3.6-flash",
        app_id="content-trend-tracker",
        quota_scope_id="test-scope",
        timeout_seconds=60,
        retry_wait_seconds=2.0,
        retry_max_wait_seconds=30.0,
        topic_angle_batch_limit=items_per_request,
        topic_angle_thinking_level="high",
        topic_angle_timeout_seconds=600,
    )


def _record_success(
    con,
    *,
    request_hash: str,
    configured_items: int,
    requested_items: int,
    output_tokens: int,
    thought_tokens: int,
    duration_ms: int,
) -> None:
    record_gemini_api_call(
        con,
        config=_config(items_per_request=configured_items),
        content_pack_id=f"topic_angle_batch_{request_hash}",
        request_hash=request_hash,
        feature_id="trend_topic_angle_batch_v1",
        feature_version="6",
        attempt_number=1,
        cache_hit=False,
        status="success",
        http_status=200,
        error_type="",
        retry_reason="",
        retry_wait_seconds=0,
        input_tokens=100,
        output_tokens=output_tokens,
        thought_tokens=thought_tokens,
        total_tokens=100 + output_tokens + thought_tokens,
        duration_ms=duration_ms,
        error_message="",
        requested_item_count=requested_items,
        configured_items_per_request=configured_items,
        thinking_level="high",
        request_timeout_seconds=600,
    )


def test_partial_batch_counts_toward_matching_runtime_sample(tmp_path: Path) -> None:
    db_path = tmp_path / "partial-runtime-sample.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        _record_success(
            con,
            request_hash="configured-15-requested-2",
            configured_items=15,
            requested_items=2,
            output_tokens=200,
            thought_tokens=300,
            duration_ms=120000,
        )
        _record_success(
            con,
            request_hash="configured-20-requested-17",
            configured_items=20,
            requested_items=17,
            output_tokens=2000,
            thought_tokens=3000,
            duration_ms=240000,
        )

        diagnostic = build_topic_angle_quality_diagnostic(
            con,
            app_id="content-trend-tracker",
            items_per_request=15,
            thinking_level="high",
            timeout_seconds=600,
            min_opportunity_score=50,
        )

    assert diagnostic.operation.successful_request_count == 2
    assert diagnostic.operation.matching_runtime_request_count == 1
    assert diagnostic.operation.requested_items == 2
    assert diagnostic.operation.average_generation_tokens == 500
    assert diagnostic.operation.maximum_generation_tokens == 500
    assert diagnostic.operation.average_duration_ms == 120000
    assert diagnostic.operation.sample_sufficient is False
    assert "현재 조건 일치 성공 요청 1회·요청 글감 2개" in diagnostic.reasons[0]
