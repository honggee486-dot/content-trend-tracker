from __future__ import annotations

from typing import Any

# 과거 후보 개수 상한과 호환하기 위한 충분히 큰 값입니다. 실제 요청 분할은
# 개수가 아니라 입력 토큰 목표/하드 상한과 적응형 estimator로 결정합니다.
_UNBOUNDED_ITEM_LIMIT = 2_147_483_647

ADAPTIVE_GEMINI_BATCH_FEATURE_IDS = frozenset(
    {
        "trend_cluster_grouping_v3",
        "trend_candidate_ai_evaluation_v1",
        "trend_blog_ai_routing_v1",
    }
)
TOPIC_ANGLE_FEATURE_ID = "trend_topic_angle_batch_v1"
ADAPTIVE_GEMINI_BATCH_EXCLUDED_FEATURE_IDS = frozenset({TOPIC_ANGLE_FEATURE_ID})


def uses_adaptive_gemini_batching(feature_id: str) -> bool:
    """자동 다중항목 Gemini 기능 중 공통 적응형 입력 예산 적용 여부를 반환합니다."""
    normalized = str(feature_id or "").strip()
    return normalized in ADAPTIVE_GEMINI_BATCH_FEATURE_IDS


def adaptive_gemini_batch_policy_snapshot() -> dict[str, Any]:
    """운영·테스트에서 공통 적응형 입력 예산 계약을 한 번에 확인합니다."""
    from src.services.trend_cluster_token_runtime import (
        CLUSTERING_GROWTH_STEP,
        CLUSTERING_GROWTH_SUCCESS_STREAK,
        CLUSTERING_HARD_INPUT_TOKENS,
        CLUSTERING_OVERRUN_FACTOR,
        CLUSTERING_RATE_LIMIT_FACTOR,
        CLUSTERING_TARGET_INPUT_TOKENS,
        CLUSTERING_TPM_LIMIT,
    )

    return {
        "target_input_tokens": CLUSTERING_TARGET_INPUT_TOKENS,
        "hard_input_tokens": CLUSTERING_HARD_INPUT_TOKENS,
        "tpm_limit": CLUSTERING_TPM_LIMIT,
        "growth_success_streak": CLUSTERING_GROWTH_SUCCESS_STREAK,
        "growth_step": CLUSTERING_GROWTH_STEP,
        "overrun_factor": CLUSTERING_OVERRUN_FACTOR,
        "rate_limit_factor": CLUSTERING_RATE_LIMIT_FACTOR,
        "feature_ids": tuple(sorted(ADAPTIVE_GEMINI_BATCH_FEATURE_IDS)),
        "excluded_feature_ids": tuple(
            sorted(ADAPTIVE_GEMINI_BATCH_EXCLUDED_FEATURE_IDS)
        ),
    }


def _restore_token_only_cluster_partition() -> None:
    """이미 설치된 과거 후보 개수 래퍼가 있으면 원래 토큰 분할기로 되돌립니다."""
    from src.services import trend_cluster_sparse_executor as sparse_module

    current = sparse_module.partition_for_view
    original = getattr(current, "_gemini_cluster_candidate_cap_original", None)
    if callable(original):
        sparse_module.partition_for_view = original


def _remove_candidate_evaluation_default_item_cap() -> None:
    """전체 글감 평가의 기본 120개 상한을 제거하고 입력 토큰 예산만 사용합니다."""
    from src.services import trend_candidate_ai_evaluation_service as evaluation_module

    evaluation_module.MAX_ITEMS_PER_REQUEST = _UNBOUNDED_ITEM_LIMIT
    partition = evaluation_module.partition_candidate_evaluations
    keyword_defaults: dict[str, Any] = dict(partition.__kwdefaults__ or {})
    keyword_defaults["max_items_per_request"] = _UNBOUNDED_ITEM_LIMIT
    partition.__kwdefaults__ = keyword_defaults


def install_adaptive_gemini_batch_contract() -> None:
    """3.7 주제방향을 제외한 자동 Gemini 묶음을 같은 입력 토큰 정책으로 맞춥니다.

    2차 군집·전체 글감 AI 평가·블로그 AI 분류는 같은 전역 estimator/TPM limiter를
    사용합니다. 성공이 안정적으로 누적되면 estimator가 2%씩 완화되고, 실패·초과·
    rate limit은 즉시 보수적으로 조정됩니다. 후보 개수 자체는 요청 분할 기준으로
    사용하지 않습니다. 주제방향 생성 feature는 이 적응형 배치 계약의 적용 대상이
    아니며 기존 전용 실행·fallback 계약을 그대로 사용합니다.
    """
    _restore_token_only_cluster_partition()
    _remove_candidate_evaluation_default_item_cap()


def install_trend_cluster_request_cap_contract() -> None:
    """과거 설치 함수명 호환용 alias. 새 코드는 적응형 Gemini 배치 계약을 설치합니다."""
    install_adaptive_gemini_batch_contract()
