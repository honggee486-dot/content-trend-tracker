from __future__ import annotations

import duckdb

from src.services.trend_cluster_token_runtime import (
    AdaptiveInputTokenEstimator,
    CLUSTERING_HARD_INPUT_TOKENS,
    CLUSTERING_TARGET_INPUT_TOKENS,
    CLUSTERING_TPM_LIMIT,
    SlidingWindowTpmLimiter,
    record_request_metrics,
    register_call_metrics,
)


class _FakeTime:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def test_token_target_is_capped_at_ninety_percent_of_tpm() -> None:
    assert CLUSTERING_TPM_LIMIT == 250_000
    assert CLUSTERING_TARGET_INPUT_TOKENS == 225_000
    assert CLUSTERING_TARGET_INPUT_TOKENS == int(CLUSTERING_TPM_LIMIT * 0.9)
    assert CLUSTERING_TARGET_INPUT_TOKENS < CLUSTERING_HARD_INPUT_TOKENS
    assert CLUSTERING_HARD_INPUT_TOKENS < CLUSTERING_TPM_LIMIT


def test_estimator_expands_only_after_three_safe_successes() -> None:
    estimator = AdaptiveInputTokenEstimator(tokens_per_character=2.0)

    for _ in range(2):
        estimator.observe(
            request_characters=1_000,
            estimated_tokens=2_256,
            actual_tokens=1_000,
            status="success",
        )
    assert estimator.tokens_per_character == 2.0
    assert estimator.stable_successes == 2

    estimator.observe(
        request_characters=1_000,
        estimated_tokens=2_256,
        actual_tokens=1_000,
        status="success",
    )
    assert round(estimator.tokens_per_character, 4) == 1.96
    assert estimator.stable_successes == 0


def test_estimator_contracts_immediately_on_overrun_and_rate_limit() -> None:
    estimator = AdaptiveInputTokenEstimator(tokens_per_character=1.5)

    estimator.observe(
        request_characters=1_000,
        estimated_tokens=1_700,
        actual_tokens=2_000,
        status="success",
    )
    after_overrun = estimator.tokens_per_character
    assert after_overrun >= 2.3
    assert estimator.stable_successes == 0

    estimator.observe(
        request_characters=1_000,
        estimated_tokens=2_500,
        actual_tokens=None,
        status="failed",
        error_type="rate_limited",
    )
    assert estimator.tokens_per_character > after_overrun
    assert estimator.stable_successes == 0


def test_estimator_calibration_uses_recent_peak_with_safety_margin() -> None:
    estimator = AdaptiveInputTokenEstimator(tokens_per_character=0.5)

    estimator.calibrate(
        [
            (100_000, 90_000, "success", ""),
            (100_000, 92_000, "success", ""),
            (100_000, 91_000, "success", ""),
        ]
    )

    assert estimator.tokens_per_character >= 0.92 * 1.08


def test_same_calibration_snapshot_is_not_applied_repeatedly() -> None:
    estimator = AdaptiveInputTokenEstimator(tokens_per_character=2.0)
    samples = [
        (100_000, 90_000, "success", ""),
        (100_000, 91_000, "success", ""),
        (100_000, 92_000, "success", ""),
    ]

    estimator.calibrate(samples)
    first_ratio = estimator.tokens_per_character
    estimator.calibrate(samples)
    estimator.calibrate(samples)

    assert estimator.tokens_per_character == first_ratio


def test_tpm_limiter_waits_until_previous_request_leaves_window() -> None:
    fake = _FakeTime()
    limiter = SlidingWindowTpmLimiter(
        limit=250_000,
        window_seconds=60.0,
        clock=fake.clock,
        sleeper=fake.sleep,
    )

    first = limiter.reserve("first", 225_000)
    second = limiter.reserve("second", 30_000)

    assert first.wait_seconds == 0
    assert second.wait_seconds >= 60.0
    assert second.sent_at_monotonic >= 60.0
    assert fake.sleeps


def test_tpm_limiter_reconciles_estimate_to_actual_tokens() -> None:
    fake = _FakeTime()
    limiter = SlidingWindowTpmLimiter(
        limit=250,
        window_seconds=60.0,
        clock=fake.clock,
        sleeper=fake.sleep,
    )

    limiter.reserve("first", 200)
    limiter.reconcile("first", 250)
    second = limiter.reserve("second", 1)

    assert second.wait_seconds >= 60.0
    assert limiter.current_usage() == 1


def test_request_metrics_are_saved_to_additive_duckdb_table() -> None:
    con = duckdb.connect(":memory:")
    request_hash = "test-clustering-request"
    register_call_metrics(
        request_hash,
        {
            "feature_id": "trend_cluster_grouping_v3",
            "feature_version": "5",
            "analysis_view": "title",
            "requested_item_count": 123,
            "request_text": "가" * 1_000,
            "estimated_input_tokens": 1_500,
            "input_tokens": 1_420,
            "target_input_tokens": 225_000,
            "hard_input_tokens": 245_000,
            "tpm_limit": 250_000,
            "tpm_used_before": 10_000,
            "tpm_wait_seconds": 4.5,
            "estimator_tokens_per_character": 1.15,
            "duration_ms": 9_876,
            "finish_reason": "STOP",
            "status": "success",
            "error_type": "",
        },
    )

    record_request_metrics(
        con,
        request_hash=request_hash,
        model_name="gemini-test",
    )
    row = con.execute(
        """
        SELECT analysis_view, requested_item_count, request_char_count,
               estimated_input_tokens, actual_input_tokens,
               target_input_tokens, tpm_limit, tpm_wait_seconds,
               finish_reason, status
        FROM trend_clustering_request_metrics
        WHERE request_hash = ?
        """,
        [request_hash],
    ).fetchone()

    assert row == (
        "title",
        123,
        1_000,
        1_500,
        1_420,
        225_000,
        250_000,
        4.5,
        "STOP",
        "success",
    )
