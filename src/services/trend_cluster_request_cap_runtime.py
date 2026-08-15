from __future__ import annotations

from typing import Any

# 과거 후보 개수 상한과 호환하기 위한 충분히 큰 값입니다. 실제 요청 분할은
# 개수가 아니라 CLUSTERING_TARGET_INPUT_TOKENS / HARD_INPUT_TOKENS로 결정합니다.
_UNBOUNDED_ITEM_LIMIT = 2_147_483_647


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


def install_trend_cluster_request_cap_contract() -> None:
    """레거시 개수 상한을 제거하고 Gemini 요청을 입력 토큰 기준으로만 분할합니다.

    함수명은 이미 이 설치 함수를 가져오는 런타임과의 호환을 위해 유지합니다.
    새 실행에서는 2차 군집과 전체 글감 평가 모두 후보 개수 자체로 요청을 자르지
    않습니다. 기존 프로세스에 과거 300개 래퍼가 남아 있어도 원래 토큰 분할기를
    복원합니다.
    """
    _restore_token_only_cluster_partition()
    _remove_candidate_evaluation_default_item_cap()
