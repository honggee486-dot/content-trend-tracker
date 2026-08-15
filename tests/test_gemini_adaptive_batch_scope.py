from __future__ import annotations

from pathlib import Path

from src.services import trend_blog_ai_routing_service as routing
from src.services import trend_candidate_ai_evaluation_service as evaluation
from src.services import trend_cluster_sparse_executor as clustering
from src.services import trend_cluster_token_runtime as token_runtime
from src.services.trend_cluster_request_cap_runtime import (
    TOPIC_ANGLE_FEATURE_ID,
    install_adaptive_gemini_batch_contract,
    uses_adaptive_gemini_batching,
)


class _SmallEstimator:
    def estimate_characters(self, characters: int) -> int:
        return max(1, int(characters * 0.2))

    def estimate_text(self, text: str) -> int:
        return max(1, int(len(text) * 0.2))


def _route_candidate(index: int) -> dict[str, object]:
    return {
        "cluster_id": f"route-{index}",
        "title": f"생활 정보 테스트 {index}",
        "display_title": "",
        "summary": "",
        "category": "생활정보",
        "source_titles": [f"근거 {index}"],
        "source_types": ["naver_news"],
        "content_hash": f"hash-{index}",
    }


def test_automatic_batch_services_share_one_estimator_and_tpm_limiter() -> None:
    assert clustering.GLOBAL_TOKEN_ESTIMATOR is token_runtime.GLOBAL_TOKEN_ESTIMATOR
    assert routing.GLOBAL_TOKEN_ESTIMATOR is token_runtime.GLOBAL_TOKEN_ESTIMATOR
    assert evaluation.GLOBAL_TOKEN_ESTIMATOR is token_runtime.GLOBAL_TOKEN_ESTIMATOR
    assert clustering.GLOBAL_TPM_LIMITER is token_runtime.GLOBAL_TPM_LIMITER
    assert routing.GLOBAL_TPM_LIMITER is token_runtime.GLOBAL_TPM_LIMITER
    assert evaluation.GLOBAL_TPM_LIMITER is token_runtime.GLOBAL_TPM_LIMITER


def test_all_automatic_batch_limits_match_225k_245k_250k_contract() -> None:
    assert token_runtime.CLUSTERING_TARGET_INPUT_TOKENS == 225_000
    assert token_runtime.CLUSTERING_HARD_INPUT_TOKENS == 245_000
    assert token_runtime.CLUSTERING_TPM_LIMIT == 250_000
    assert routing.CLUSTERING_TARGET_INPUT_TOKENS == 225_000
    assert routing.CLUSTERING_HARD_INPUT_TOKENS == 245_000
    assert evaluation.CLUSTERING_TARGET_INPUT_TOKENS == 225_000
    assert evaluation.CLUSTERING_HARD_INPUT_TOKENS == 245_000


def test_blog_routing_does_not_split_by_candidate_count_when_tokens_fit() -> None:
    candidates = [_route_candidate(index) for index in range(1, 401)]
    chunks, oversized = routing.partition_blog_route_candidates(
        candidates,
        estimator=_SmallEstimator(),
        target_tokens=225_000,
    )

    assert oversized == []
    assert len(chunks) == 1
    assert len(chunks[0].candidates) == 400
    assert chunks[0].estimated_tokens <= 225_000


def test_candidate_evaluation_installed_contract_removes_item_count_cap() -> None:
    install_adaptive_gemini_batch_contract()
    assert evaluation.MAX_ITEMS_PER_REQUEST > 1_000_000
    assert evaluation.partition_candidate_evaluations.__kwdefaults__[
        "max_items_per_request"
    ] > 1_000_000


def test_estimator_feedback_contract_expands_after_successes_and_contracts_on_failure() -> None:
    estimator = token_runtime.AdaptiveInputTokenEstimator(tokens_per_character=2.0)
    for _ in range(3):
        estimator.observe(
            request_characters=1_000,
            estimated_tokens=2_256,
            actual_tokens=1_000,
            status="success",
        )
    expanded_ratio = estimator.tokens_per_character
    assert expanded_ratio < 2.0

    estimator.observe(
        request_characters=1_000,
        estimated_tokens=2_256,
        actual_tokens=None,
        status="failed",
        error_type="rate_limited",
    )
    assert estimator.tokens_per_character > expanded_ratio


def test_latest_data_scheduler_and_backlog_install_same_adaptive_contract() -> None:
    project_root = Path(__file__).resolve().parents[1]
    entrypoints = (
        (project_root / "scripts" / "refresh_trends_dashboard.py", "utf-8"),
        (project_root / "scripts" / "refresh_trends_safe.py", "utf-8-sig"),
        (project_root / "scripts" / "process_cluster_backlog.py", "utf-8"),
    )

    for path, encoding in entrypoints:
        text = path.read_text(encoding=encoding)
        adaptive_call = "install_adaptive_gemini_batch_contract()"
        cluster_call = "install_trend_cluster_runtime_contract()"
        assert "install_adaptive_gemini_batch_contract" in text
        assert adaptive_call in text
        assert cluster_call in text
        assert text.index(adaptive_call) < text.index(cluster_call)


def test_topic_angle_generation_stays_outside_adaptive_batch_scope() -> None:
    assert TOPIC_ANGLE_FEATURE_ID == "trend_topic_angle_batch_v1"
    assert uses_adaptive_gemini_batching(TOPIC_ANGLE_FEATURE_ID) is False
