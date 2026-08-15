from __future__ import annotations

from src.services import trend_cluster_sparse_executor as sparse_module
from src.services.trend_candidate_ai_evaluation_service import (
    partition_candidate_evaluations,
)
from src.services.trend_cluster_request_cap_runtime import (
    ADAPTIVE_GEMINI_BATCH_EXCLUDED_FEATURE_IDS,
    ADAPTIVE_GEMINI_BATCH_FEATURE_IDS,
    TOPIC_ANGLE_FEATURE_ID,
    install_adaptive_gemini_batch_contract,
    install_trend_cluster_request_cap_contract,
    uses_adaptive_gemini_batching,
)


class _Estimator:
    def estimate_characters(self, characters: int) -> int:
        return max(1, int(characters * 0.2))

    def estimate_text(self, text: str) -> int:
        return max(1, int(len(text) * 0.2))


def _candidate(index: int) -> dict[str, object]:
    return {
        "cluster_id": f"cluster-{index}",
        "topic": f"테스트 주제 {index}",
        "item_count": 2,
        "independent_evidence_count": 2,
        "source_type_count": 2,
        "publisher_count": 2,
        "source_types": ["naver_news", "daum_web"],
        "first_seen_at": "2026-08-15T00:00:00",
        "last_seen_at": "2026-08-15T01:00:00",
        "rediscovery_signal": 1,
        "evidence": [{"title": f"근거 {index}", "source_type": "naver_news"}],
    }


def test_adaptive_batch_scope_includes_all_automatic_multi_item_features() -> None:
    assert ADAPTIVE_GEMINI_BATCH_FEATURE_IDS == {
        "trend_cluster_grouping_v3",
        "trend_candidate_ai_evaluation_v1",
        "trend_blog_ai_routing_v1",
    }
    assert all(uses_adaptive_gemini_batching(value) for value in ADAPTIVE_GEMINI_BATCH_FEATURE_IDS)


def test_topic_angle_feature_is_explicitly_excluded_from_adaptive_batching() -> None:
    assert TOPIC_ANGLE_FEATURE_ID == "trend_topic_angle_batch_v1"
    assert ADAPTIVE_GEMINI_BATCH_EXCLUDED_FEATURE_IDS == {TOPIC_ANGLE_FEATURE_ID}
    assert uses_adaptive_gemini_batching(TOPIC_ANGLE_FEATURE_ID) is False


def test_candidate_evaluation_does_not_split_at_120_items_when_tokens_fit() -> None:
    install_adaptive_gemini_batch_contract()
    candidates = [_candidate(index) for index in range(1, 401)]

    chunks, oversized = partition_candidate_evaluations(
        candidates,
        estimator=_Estimator(),
        target_tokens=225_000,
    )

    assert oversized == []
    assert len(chunks) == 1
    assert len(chunks[0].candidates) == 400
    assert chunks[0].estimated_tokens <= 225_000


def test_installer_removes_previously_installed_300_item_cluster_wrapper() -> None:
    original = sparse_module.partition_for_view

    def legacy_capped(*args, **kwargs):
        return original(*args, **kwargs)

    legacy_capped._gemini_cluster_candidate_cap = True  # type: ignore[attr-defined]
    legacy_capped._gemini_cluster_candidate_cap_original = original  # type: ignore[attr-defined]
    sparse_module.partition_for_view = legacy_capped
    try:
        install_trend_cluster_request_cap_contract()
        assert sparse_module.partition_for_view is original
    finally:
        sparse_module.partition_for_view = original
