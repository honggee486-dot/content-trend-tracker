from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.database import connect_database, init_database
from src.services.gemini_stability_service import (
    GENERATION_TOKEN_WARNING,
    get_gemini_stability_recommendation,
)
from src.services.topic_angle_ai_service import TOPIC_ANGLE_FEATURE_ID


APP_ID = "stability-test-app"


def _database(tmp_path: Path) -> Path:
    db_path = tmp_path / "stability.duckdb"
    init_database(db_path)
    return db_path


def _seed_run(
    con,
    *,
    index: int,
    generated: int,
    skipped: int,
    source_status: str,
    request_count: int = 1,
    retry_count: int = 0,
) -> datetime:
    started = datetime(2026, 7, 1, 9, 0, 0) + timedelta(days=index)
    finished = started + timedelta(seconds=20 + index)
    run_id = f"run-{index}"
    con.execute(
        """
        INSERT INTO collection_runs(
            run_id, run_type, status, started_at, finished_at, duration_ms,
            source_count, succeeded_source_count, failed_source_count,
            request_count, retry_count, newly_saved_count, updated_count,
            skipped_count, summary, error_message, created_at
        ) VALUES (?, 'topic_angle_generation', ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, '', '', ?)
        """,
        [
            run_id,
            "success" if source_status == "success" else "partial_success",
            started,
            finished,
            int((finished - started).total_seconds() * 1000),
            1,
            0 if source_status == "success" else 1,
            request_count,
            retry_count,
            generated * 3,
            generated,
            skipped,
            started,
        ],
    )
    con.execute(
        """
        INSERT INTO collection_run_sources(
            run_id, source_name, status, duration_ms, request_count,
            retry_count, newly_saved_count, updated_count, skipped_count,
            error_message
        ) VALUES (?, 'topic_angles', ?, ?, ?, ?, ?, ?, ?, '')
        """,
        [
            run_id,
            source_status,
            int((finished - started).total_seconds() * 1000),
            request_count,
            retry_count,
            generated * 3,
            generated,
            skipped,
        ],
    )
    return started


def _seed_call(
    con,
    *,
    index: int,
    created_at: datetime,
    status: str = "success",
    error_type: str = "",
    output_tokens: int | None = 18_000,
    thought_tokens: int | None = 5_000,
    attempt_number: int = 1,
    requested_item_count: int | None = 25,
    configured_items_per_request: int | None = 25,
    thinking_level: str | None = "high",
    request_timeout_seconds: int | None = 600,
    finish_reason: str | None = "STOP",
    request_hash: str | None = None,
    content_pack_id: str | None = None,
    http_status: int | None = 200,
    retry_reason: str = "",
    retry_wait_seconds: float = 0.0,
) -> None:
    con.execute(
        """
        INSERT INTO gemini_api_calls(
            call_id, app_id, quota_scope_id, feature_id, content_pack_id,
            request_hash, model_name, attempt_number, cache_hit, status,
            http_status, error_type, retry_reason, retry_wait_seconds,
            output_tokens, thought_tokens, total_tokens, duration_ms,
            requested_item_count, configured_items_per_request,
            thinking_level, request_timeout_seconds, finish_reason,
            error_message, created_at
        ) VALUES (?, ?, 'quota', ?, ?, ?, 'gemini-test', ?, FALSE, ?, ?, ?, ?, ?, ?, ?, ?, 12000, ?, ?, ?, ?, ?, '', ?)
        """,
        [
            f"call-{index}",
            APP_ID,
            TOPIC_ANGLE_FEATURE_ID,
            content_pack_id if content_pack_id is not None else f"topic-angle-{index}",
            request_hash if request_hash is not None else f"hash-{index}",
            attempt_number,
            status,
            http_status,
            error_type,
            retry_reason,
            retry_wait_seconds,
            output_tokens,
            thought_tokens,
            (output_tokens or 0) + (thought_tokens or 0),
            requested_item_count,
            configured_items_per_request,
            thinking_level,
            request_timeout_seconds,
            finish_reason,
            created_at,
        ],
    )



def _table_counts(con) -> tuple[int, int, int, int]:
    return tuple(
        int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in (
            "collection_runs",
            "collection_run_sources",
            "gemini_api_calls",
            "app_settings",
        )
    )


def test_stable_history_keeps_current_twenty_five_and_high_read_only(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    with connect_database(db_path) as con:
        for index in range(5):
            started = _seed_run(
                con,
                index=index,
                generated=25,
                skipped=0,
                source_status="success",
            )
            _seed_call(con, index=index, created_at=started + timedelta(seconds=5))
        before = _table_counts(con)

        recommendation = get_gemini_stability_recommendation(
            con,
            app_id=APP_ID,
            current_items_per_request=25,
            current_thinking_level="high",
        )

        after = _table_counts(con)

    assert recommendation.sample_sufficient is True
    assert recommendation.evaluation_status == "유지 권장"
    assert recommendation.recommended_items_per_request == 25
    assert recommendation.recommendation_label == "25개 유지"
    assert recommendation.recent_30.save_rate == 1.0
    assert recommendation.calls.near_limit_count == 0
    assert recommendation.thinking_recommendation == "high 유지"
    assert before == after


def test_one_generation_token_warning_recommends_twenty_five(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    with connect_database(db_path) as con:
        for index in range(4):
            started = _seed_run(
                con,
                index=index,
                generated=30,
                skipped=0,
                source_status="success",
            )
            total = GENERATION_TOKEN_WARNING if index == 3 else 22_000
            _seed_call(
                con,
                index=index,
                created_at=started + timedelta(seconds=5),
                output_tokens=total - 5_000,
                thought_tokens=5_000,
            )

        recommendation = get_gemini_stability_recommendation(
            con,
            app_id=APP_ID,
            current_items_per_request=30,
        )

    assert recommendation.sample_sufficient is True
    assert recommendation.recommended_items_per_request == 25
    assert recommendation.recommendation_label == "25개로 낮추기 권장"
    assert recommendation.calls.near_limit_count == 1


def test_repeated_partial_storage_recommends_twenty(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    with connect_database(db_path) as con:
        for index in range(4):
            started = _seed_run(
                con,
                index=index,
                generated=20,
                skipped=10,
                source_status="partial_success",
            )
            _seed_call(con, index=index, created_at=started + timedelta(seconds=5))

        recommendation = get_gemini_stability_recommendation(
            con,
            app_id=APP_ID,
            current_items_per_request=25,
        )

    assert recommendation.sample_sufficient is True
    assert recommendation.recommended_items_per_request == 20
    assert recommendation.recommendation_label == "20개로 낮추기 권장"
    assert recommendation.recent_30.save_rate == 2 / 3
    assert recommendation.recent_30.partial_failure_rate == 1.0


def test_caution_keeps_current_twenty_five_for_observation(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    with connect_database(db_path) as con:
        for index in range(4):
            started = _seed_run(
                con,
                index=index,
                generated=25,
                skipped=0,
                source_status="success",
            )
            total = GENERATION_TOKEN_WARNING if index == 3 else 22_000
            _seed_call(
                con,
                index=index,
                created_at=started + timedelta(seconds=5),
                output_tokens=total - 5_000,
                thought_tokens=5_000,
            )

        recommendation = get_gemini_stability_recommendation(
            con,
            app_id=APP_ID,
            current_items_per_request=25,
        )

    assert recommendation.evaluation_status == "유지·관찰"
    assert recommendation.recommended_items_per_request == 25
    assert recommendation.recommendation_label == "25개 유지·관찰"


def test_caution_never_raises_current_twenty_to_twenty_five(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    with connect_database(db_path) as con:
        for index in range(4):
            started = _seed_run(
                con,
                index=index,
                generated=20,
                skipped=0,
                source_status="success",
            )
            total = GENERATION_TOKEN_WARNING if index == 3 else 22_000
            _seed_call(
                con,
                index=index,
                created_at=started + timedelta(seconds=5),
                output_tokens=total - 5_000,
                thought_tokens=5_000,
            )

        recommendation = get_gemini_stability_recommendation(
            con,
            app_id=APP_ID,
            current_items_per_request=20,
        )

    assert recommendation.evaluation_status == "유지·관찰"
    assert recommendation.recommended_items_per_request == 20
    assert recommendation.recommendation_label == "20개 유지·관찰"


def test_severe_signal_keeps_current_twenty_for_additional_checks(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    with connect_database(db_path) as con:
        for index in range(4):
            started = _seed_run(
                con,
                index=index,
                generated=14,
                skipped=6,
                source_status="partial_success",
            )
            _seed_call(con, index=index, created_at=started + timedelta(seconds=5))

        recommendation = get_gemini_stability_recommendation(
            con,
            app_id=APP_ID,
            current_items_per_request=20,
        )

    assert recommendation.evaluation_status == "유지·추가 점검"
    assert recommendation.recommended_items_per_request == 20
    assert recommendation.recommendation_label == "20개 유지·추가 점검"


def test_validation_failures_are_counted_as_terminal_failures(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    with connect_database(db_path) as con:
        for index in range(5):
            started = _seed_run(
                con,
                index=index,
                generated=30,
                skipped=0,
                source_status="success",
            )
            _seed_call(
                con,
                index=index,
                created_at=started + timedelta(seconds=5),
                status="response_validation_error" if index == 4 else "success",
                error_type="response_validation_error" if index == 4 else "",
            )

        recommendation = get_gemini_stability_recommendation(
            con,
            app_id=APP_ID,
            current_items_per_request=25,
        )

    assert recommendation.calls.terminal_attempt_count == 5
    assert recommendation.calls.validation_failure_count == 1
    assert recommendation.calls.validation_failure_rate == 0.2
    assert recommendation.recommended_items_per_request == 20


def test_runtime_metadata_and_max_tokens_are_summarized_separately_read_only(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path)
    with connect_database(db_path) as con:
        for index in range(3):
            started = _seed_run(
                con,
                index=index,
                generated=25,
                skipped=0,
                source_status="success",
            )
            if index == 0:
                _seed_call(
                    con,
                    index=index,
                    created_at=started + timedelta(seconds=5),
                    requested_item_count=None,
                    configured_items_per_request=None,
                    thinking_level=None,
                    request_timeout_seconds=None,
                    finish_reason="",
                )
            elif index == 1:
                _seed_call(
                    con,
                    index=index,
                    created_at=started + timedelta(seconds=5),
                    requested_item_count=18,
                    status="response_validation_error",
                    error_type="response_validation_error",
                    finish_reason="MAX_TOKENS",
                )
            else:
                _seed_call(
                    con,
                    index=index,
                    created_at=started + timedelta(seconds=5),
                    requested_item_count=25,
                    status="response_validation_error",
                    error_type="response_validation_error",
                )
        before = _table_counts(con)

        recommendation = get_gemini_stability_recommendation(
            con,
            app_id=APP_ID,
            current_items_per_request=25,
            current_thinking_level="high",
        )

        after = _table_counts(con)

    assert recommendation.calls.recorded_requested_item_count == 2
    assert recommendation.calls.average_requested_item_count == 21.5
    assert recommendation.calls.maximum_requested_item_count == 25
    assert recommendation.calls.thinking_level_counts == (("high", 2),)
    assert recommendation.calls.finish_reason_counts == (
        ("MAX_TOKENS", 1),
        ("STOP", 1),
    )
    assert recommendation.calls.max_tokens_count == 1
    assert recommendation.calls.validation_failure_count == 1
    assert recommendation.calls.missing_finish_reason_count == 1
    assert before == after


def test_insufficient_sample_keeps_current_limit(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    with connect_database(db_path) as con:
        started = _seed_run(
            con,
            index=0,
            generated=10,
            skipped=20,
            source_status="partial_success",
        )
        _seed_call(con, index=0, created_at=started + timedelta(seconds=5))

        recommendation = get_gemini_stability_recommendation(
            con,
            app_id=APP_ID,
            current_items_per_request=25,
        )

    assert recommendation.sample_sufficient is False
    assert recommendation.evaluation_status == "표본 부족"
    assert recommendation.recommended_items_per_request == 25
    assert recommendation.recommendation_label == "25개 유지"
    assert "표본이 부족" in recommendation.reasons[0]
    assert "현재 25개 설정" in recommendation.reasons[1]


def test_invalid_thinking_level_uses_config_default(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    with connect_database(db_path) as con:
        recommendation = get_gemini_stability_recommendation(
            con,
            app_id=APP_ID,
            current_thinking_level="unsupported",
        )

    assert recommendation.thinking_recommendation == "medium 유지"


def test_detailed_error_breakdown_metrics_all_scenarios(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    with connect_database(db_path) as con:
        started = _seed_run(con, index=0, generated=25, skipped=0, source_status="success")

        # 1 & 2 & 10 & 12: 429 retrying chain -> recovered & final failure & retry wait metrics
        # Batch A (hash-A): 2 retries -> success_after_retry (recovered)
        _seed_call(con, index=201, created_at=started + timedelta(seconds=1), status="retrying", error_type="rate_limited", retry_reason="rate_limited", retry_wait_seconds=2.0, request_hash="hash-A", http_status=429)
        _seed_call(con, index=202, created_at=started + timedelta(seconds=3), status="retrying", error_type="rate_limited", retry_reason="rate_limited", retry_wait_seconds=2.0, request_hash="hash-A", http_status=429)
        _seed_call(con, index=203, created_at=started + timedelta(seconds=5), status="success_after_retry", attempt_number=3, request_hash="hash-A", http_status=200)

        # Batch B (hash-B): 2 retries -> rate_limited final failure
        _seed_call(con, index=204, created_at=started + timedelta(seconds=10), status="retrying", error_type="rate_limited", retry_reason="rate_limited", retry_wait_seconds=2.0, request_hash="hash-B", http_status=429)
        _seed_call(con, index=205, created_at=started + timedelta(seconds=12), status="rate_limited", error_type="rate_limited", retry_wait_seconds=2.0, request_hash="hash-B", http_status=429)

        # 3: daily_quota_exhausted
        _seed_call(con, index=206, created_at=started + timedelta(seconds=15), status="daily_quota_exhausted", error_type="daily_quota_exhausted", http_status=429, request_hash="hash-C")

        # 4: request_timeout
        _seed_call(con, index=207, created_at=started + timedelta(seconds=20), status="request_timeout", error_type="request_timeout", http_status=None, request_hash="hash-D")

        # 5: network_error
        _seed_call(con, index=208, created_at=started + timedelta(seconds=25), status="network_error", error_type="network_error", http_status=0, request_hash="hash-E")

        # 6: server_error HTTP 503
        _seed_call(con, index=209, created_at=started + timedelta(seconds=30), status="service_unavailable", error_type="service_unavailable", http_status=503, request_hash="hash-F")

        # 7: invalid_request HTTP 400
        _seed_call(con, index=210, created_at=started + timedelta(seconds=35), status="invalid_request", error_type="invalid_request", http_status=400, request_hash="hash-G")

        # 8: response_validation_error
        _seed_call(con, index=211, created_at=started + timedelta(seconds=40), status="response_validation_error", error_type="response_validation_error", finish_reason="STOP", http_status=200, request_hash="hash-H")

        # 9: MAX_TOKENS + response_validation_error simultaneously -> MAX_TOKENS only (priority 1)
        _seed_call(con, index=212, created_at=started + timedelta(seconds=45), status="response_validation_error", error_type="response_validation_error", finish_reason="MAX_TOKENS", http_status=200, request_hash="hash-I")

        # 11: legacy row with NULL values
        _seed_call(con, index=213, created_at=started + timedelta(seconds=50), status="success", finish_reason="", request_hash="", thinking_level=None)

        recommendation = get_gemini_stability_recommendation(con, app_id=APP_ID)
        calls = recommendation.calls

    # Verifications
    assert calls.rate_limit_affected_request_count == 2
    assert calls.retry_recovered_request_count == 1
    assert calls.rate_limited_final_request_count == 1
    assert calls.retrying_attempt_count == 3
    assert calls.quota_exhausted_count == 1
    assert calls.timeout_count == 1
    assert calls.network_error_count == 1
    assert calls.server_error_count == 1
    assert calls.invalid_request_count == 1
    assert calls.validation_failure_count == 1  # Only from index=211
    assert calls.max_tokens_count == 1  # Priority 1 for index=212
    assert calls.retry_wait_total_seconds == 8.0  # 2.0 * 4
    assert calls.retry_wait_average_seconds == 2.0
    assert calls.retry_wait_max_seconds == 2.0
