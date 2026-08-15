from __future__ import annotations

import time
from dataclasses import replace
from queue import Queue

from src.config import GeminiConfig
from src.services import topic_angle_ai_service
from src.services.gemini_service import GeminiHttpError, _ApiErrorInfo
from src.services.topic_angle_model_fallback_runtime import (
    PROTECTED_TOPIC_ANGLE_MODEL,
    TOPIC_ANGLE_FALLBACK_MODEL,
)
from src.services.topic_angle_partial_recovery_runtime import (
    TopicAngleRecoveryExecution,
    recover_partial_topic_angle_execution,
)
from src.services.topic_angle_response_integrity_service import (
    annotate_missing_topic_angle_ids,
)


def _config() -> GeminiConfig:
    return GeminiConfig(
        api_key="test-key",
        model="gemini-3.6-flash",
        app_id="content-trend-tracker",
        quota_scope_id="test-scope",
        timeout_seconds=60,
        retry_wait_seconds=0,
        retry_max_wait_seconds=1,
        topic_angle_timeout_seconds=600,
        topic_angle_batch_limit=15,
        topic_angle_max_parallel_requests=1,
        topic_angle_request_stagger_seconds=0,
        topic_angle_thinking_level="high",
    )


def _cluster(cluster_id: str) -> dict[str, object]:
    return {
        "cluster_id": cluster_id,
        "topic": f"글감 {cluster_id}",
        "signals": [],
        "evidence_source_map": {},
    }


def _attempt(status: str = "success"):
    return topic_angle_ai_service._AttemptRecord(
        attempt_number=1,
        status=status,
        http_status=200,
        error_type="" if status == "success" else "response_validation_error",
        retry_reason="",
        retry_wait_seconds=0,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        duration_ms=100,
        error_message="",
    )


def _result(
    *,
    batch_number: int,
    clusters: tuple[dict[str, object], ...],
    returned_ids: tuple[str, ...],
    status: str = "success",
):
    enrichments = {
        cluster_id: {"display_title": cluster_id}
        for cluster_id in returned_ids
    }
    return topic_angle_ai_service._BatchExecutionResult(
        batch_number=batch_number,
        clusters=clusters,
        request_hash=f"hash-{batch_number}",
        enrichments=enrichments,
        validation_errors=(),
        attempts=(_attempt(status),),
        status=status,
        error_type="" if returned_ids else "response_validation_error",
        error_message="",
        response_text="{}",
    )


def _execution(result):
    clusters = tuple(result.clusters)
    preparation = topic_angle_ai_service.TopicAnglePreparation(
        status="ready",
        clusters=clusters,
        batches=(clusters,),
        skipped_sensitive_clusters=0,
        items_per_request=15,
        max_parallel_requests=1,
        min_opportunity_score=50.0,
        started_at=time.perf_counter(),
    )
    return topic_angle_ai_service.TopicAngleExecution(preparation, (result,))


def _run_protected_batch(monkeypatch, *, fallback_fails: bool = False):
    calls: list[str] = []

    def fake_call(config, request_text, request_hash, **kwargs):
        calls.append(config.model)
        if config.model == PROTECTED_TOPIC_ANGLE_MODEL or fallback_fails:
            raise GeminiHttpError(
                _ApiErrorInfo(
                    http_status=503,
                    error_type="service_unavailable",
                    message="temporary outage",
                    retryable=True,
                    retry_delay_seconds=0,
                )
            )
        return "{}", 10, 20, 30

    monkeypatch.setattr(topic_angle_ai_service, "call_gemini_structured_output", fake_call)
    monkeypatch.setattr(
        topic_angle_ai_service,
        "_validated_enrichments",
        lambda raw_response, requested_clusters: (
            {cluster_id: {"display_title": cluster_id} for cluster_id in requested_clusters},
            [],
        ),
    )
    result = topic_angle_ai_service._execute_batch_request(
        batch_number=1,
        clusters=[_cluster("trend_a")],
        start_delay_seconds=0,
        config=replace(_config(), model=PROTECTED_TOPIC_ANGLE_MODEL),
        event_queue=Queue(),
        sleep_func=lambda _: None,
    )
    return result, calls


def test_partial_response_recovers_only_missing_ids() -> None:
    clusters = (_cluster("trend_a"), _cluster("trend_b"))
    execution = _execution(
        _result(
            batch_number=1,
            clusters=clusters,
            returned_ids=("trend_a",),
        )
    )
    requested: list[tuple[str, ...]] = []

    def fake_runner(**kwargs):
        ids = tuple(str(item["cluster_id"]) for item in kwargs["clusters"])
        requested.append(ids)
        return _result(
            batch_number=int(kwargs["batch_number"]),
            clusters=tuple(kwargs["clusters"]),
            returned_ids=ids,
        )

    recovered = recover_partial_topic_angle_execution(
        execution,
        config=_config(),
        sleep_func=lambda _: None,
        batch_request_runner=fake_runner,
    )
    annotated = annotate_missing_topic_angle_ids(recovered)

    assert isinstance(recovered, TopicAngleRecoveryExecution)
    assert requested == [("trend_b",)]
    assert set(recovered.results[0].enrichments) == {"trend_a", "trend_b"}
    assert recovered.results[0].status == "success_after_retry"
    assert recovered.results[0].error_type == ""
    assert annotated.results[0].error_type == ""
    assert len(recovered.recovery_results) == 1
    assert len(recovered.recovery_results[0].clusters) == 1


def test_total_validation_failure_is_not_reissued_as_partial_recovery() -> None:
    clusters = (_cluster("trend_a"), _cluster("trend_b"))
    execution = _execution(
        _result(
            batch_number=1,
            clusters=clusters,
            returned_ids=(),
            status="response_validation_error",
        )
    )
    calls = 0

    def fake_runner(**kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("부분 성공이 아닌 전체 실패는 보강 요청하면 안 됩니다.")

    recovered = recover_partial_topic_angle_execution(
        execution,
        config=_config(),
        batch_request_runner=fake_runner,
    )

    assert recovered is execution
    assert calls == 0


def test_failed_recovery_preserves_valid_results_and_remaining_partial_signal() -> None:
    clusters = (_cluster("trend_a"), _cluster("trend_b"))
    execution = _execution(
        _result(
            batch_number=1,
            clusters=clusters,
            returned_ids=("trend_a",),
        )
    )

    def fake_runner(**kwargs):
        return _result(
            batch_number=int(kwargs["batch_number"]),
            clusters=tuple(kwargs["clusters"]),
            returned_ids=(),
            status="response_validation_error",
        )

    recovered = recover_partial_topic_angle_execution(
        execution,
        config=_config(),
        batch_request_runner=fake_runner,
    )
    annotated = annotate_missing_topic_angle_ids(recovered)

    assert set(recovered.results[0].enrichments) == {"trend_a"}
    assert annotated.results[0].error_type == "response_partial"
    assert "trend_b" in annotated.results[0].error_message
    assert len(recovered.recovery_results) == 1


def test_installed_finalizer_logs_recovery_with_actual_subset_size(monkeypatch) -> None:
    clusters = (_cluster("trend_a"), _cluster("trend_b"))
    base_result = _result(
        batch_number=1,
        clusters=clusters,
        returned_ids=("trend_a", "trend_b"),
    )
    execution = _execution(base_result)
    recovery_result = _result(
        batch_number=2,
        clusters=(clusters[1],),
        returned_ids=("trend_b",),
    )
    recovery_execution = TopicAngleRecoveryExecution(
        preparation=execution.preparation,
        results=(replace(base_result, status="success_after_retry"),),
        recovery_results=(recovery_result,),
    )
    logged_sizes: list[int] = []

    def fake_record(con, *, config, result):
        logged_sizes.append(len(result.clusters))

    monkeypatch.setattr(topic_angle_ai_service, "_record_batch_attempts", fake_record)
    monkeypatch.setattr(
        topic_angle_ai_service,
        "_save_batch_enrichments",
        lambda con, *, config, result: len(result.enrichments),
    )

    result = topic_angle_ai_service.finalize_prepared_topic_angles(
        object(),
        config=_config(),
        execution=recovery_execution,
    )

    assert logged_sizes == [2, 1]
    assert result.status == "success_after_retry"
    assert result.attempts == 2
    assert result.generated_clusters == 2


def test_gemini_37_transient_failure_uses_36_once_without_37_retry(monkeypatch) -> None:
    result, calls = _run_protected_batch(monkeypatch)

    assert calls == [PROTECTED_TOPIC_ANGLE_MODEL, TOPIC_ANGLE_FALLBACK_MODEL]
    assert result.status == "success_after_fallback"
    assert len(result.attempts) == 2
    assert result.attempts[0].status == "fallback"
    assert result.attempts[0].retry_reason == f"fallback_to:{TOPIC_ANGLE_FALLBACK_MODEL}"
    assert result.attempts[1].attempt_number == 2
    assert f"model:{TOPIC_ANGLE_FALLBACK_MODEL}" in result.attempts[1].retry_reason


def test_gemini_37_non_transient_failure_does_not_fallback(monkeypatch) -> None:
    calls: list[str] = []

    def fake_call(config, request_text, request_hash, **kwargs):
        calls.append(config.model)
        raise GeminiHttpError(
            _ApiErrorInfo(
                http_status=400,
                error_type="invalid_request",
                message="bad request",
                retryable=False,
                retry_delay_seconds=None,
            )
        )

    monkeypatch.setattr(topic_angle_ai_service, "call_gemini_structured_output", fake_call)
    result = topic_angle_ai_service._execute_batch_request(
        batch_number=1,
        clusters=[_cluster("trend_a")],
        start_delay_seconds=0,
        config=replace(_config(), model=PROTECTED_TOPIC_ANGLE_MODEL),
        event_queue=Queue(),
        sleep_func=lambda _: None,
    )

    assert calls == [PROTECTED_TOPIC_ANGLE_MODEL]
    assert result.status == "invalid_request"
    assert len(result.attempts) == 1


def test_gemini_37_and_36_transient_failures_stop_after_two_provider_calls(monkeypatch) -> None:
    result, calls = _run_protected_batch(monkeypatch, fallback_fails=True)

    assert calls == [PROTECTED_TOPIC_ANGLE_MODEL, TOPIC_ANGLE_FALLBACK_MODEL]
    assert result.status == "service_unavailable"
    assert len(result.attempts) == 2
    assert result.attempts[-1].attempt_number == 2


def test_fallback_attempt_logging_uses_actual_models(monkeypatch) -> None:
    result, calls = _run_protected_batch(monkeypatch)
    logged: list[tuple[str, int, str]] = []

    def fake_record(con, **kwargs):
        logged.append(
            (
                kwargs["config"].model,
                int(kwargs["attempt_number"]),
                str(kwargs["status"]),
            )
        )

    monkeypatch.setattr(topic_angle_ai_service, "record_gemini_api_call", fake_record)
    topic_angle_ai_service._record_batch_attempts(
        object(),
        config=replace(_config(), model=PROTECTED_TOPIC_ANGLE_MODEL),
        result=result,
    )

    assert calls == [PROTECTED_TOPIC_ANGLE_MODEL, TOPIC_ANGLE_FALLBACK_MODEL]
    assert logged == [
        (PROTECTED_TOPIC_ANGLE_MODEL, 1, "fallback"),
        (TOPIC_ANGLE_FALLBACK_MODEL, 2, "success_after_fallback"),
    ]


def test_gemini_37_partial_response_does_not_issue_recovery_request() -> None:
    clusters = (_cluster("trend_a"), _cluster("trend_b"))
    execution = _execution(
        _result(
            batch_number=1,
            clusters=clusters,
            returned_ids=("trend_a",),
        )
    )
    calls = 0

    def forbidden_runner(**kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("Gemini 3.7 부분 응답은 자동 보강 요청하면 안 됩니다.")

    recovered = recover_partial_topic_angle_execution(
        execution,
        config=replace(_config(), model=PROTECTED_TOPIC_ANGLE_MODEL),
        sleep_func=lambda _: None,
        batch_request_runner=forbidden_runner,
    )
    annotated = annotate_missing_topic_angle_ids(recovered)

    assert recovered is execution
    assert calls == 0
    assert set(recovered.results[0].enrichments) == {"trend_a"}
    assert annotated.results[0].error_type == "response_partial"
    assert "trend_b" in annotated.results[0].error_message
