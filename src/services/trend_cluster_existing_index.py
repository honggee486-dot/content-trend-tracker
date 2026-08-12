from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from functools import wraps
from typing import Any, Iterable


# 실제 품질 표본에서 서로 다른 상품의 제목이 아래와 같은 편집 템플릿 표현만
# 공유한 채 기존 군집 후보로 연결된 사례가 확인됐다. 이 단어들은 사건·제품
# 식별 근거가 아니므로 기존 군집 후보 인덱스와 점수 계산에서 제외한다.
_GENERIC_TEMPLATE_IDENTITY_TERMS = frozenset(
    {
        "특징",
        "선택",
        "포인트",
        "총정리",
    }
)
_KOREAN_CASE_PARTICLES = frozenset("은는이가을를와과도만의에로")


def _meaningful_index_token(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return ""
    if normalized in _GENERIC_TEMPLATE_IDENTITY_TERMS:
        return ""
    if (
        len(normalized) > 1
        and normalized[-1] in _KOREAN_CASE_PARTICLES
        and normalized[:-1] in _GENERIC_TEMPLATE_IDENTITY_TERMS
    ):
        return ""
    return normalized


def _without_generic_template_tokens(row: dict[str, Any]) -> dict[str, Any]:
    copied = dict(row)
    copied["editorial_tokens"] = {
        normalized
        for token in row.get("editorial_tokens") or ()
        if (normalized := _meaningful_index_token(token))
    }
    return copied


def install_existing_cluster_index(discovery_module: Any) -> None:
    """실제 식별 토큰을 공유한 기존 군집만 정밀 점수 계산 대상으로 좁힙니다."""
    original = getattr(discovery_module, "_attach_existing_cluster_candidates", None)
    if not callable(original) or getattr(
        original,
        "_trend_cluster_existing_index",
        False,
    ):
        return

    descriptor_builder = discovery_module._existing_cluster_descriptor
    score_match = discovery_module._existing_cluster_match_score
    option_limit = int(discovery_module.AI_EXISTING_CLUSTER_CANDIDATE_LIMIT)

    @wraps(original)
    def indexed_attach(
        candidates: list[dict[str, Any]],
        existing_clusters: Iterable[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        descriptors = [
            descriptor_builder(cluster)
            for cluster in existing_clusters
            if cluster.get("items") and bool(cluster.get("second_stage_ready"))
        ]
        scoring_descriptors = [
            _without_generic_template_tokens(descriptor) for descriptor in descriptors
        ]
        token_index: defaultdict[str, set[int]] = defaultdict(set)
        for index, descriptor in enumerate(scoring_descriptors):
            for token in descriptor.get("editorial_tokens") or ():
                normalized = _meaningful_index_token(token)
                if normalized:
                    token_index[normalized].add(index)

        attached: list[dict[str, Any]] = []
        total_references = 0
        for candidate in candidates:
            scoring_candidate = _without_generic_template_tokens(candidate)
            descriptor_indexes: set[int] = set()
            for token in scoring_candidate.get("editorial_tokens") or ():
                normalized = _meaningful_index_token(token)
                if normalized:
                    descriptor_indexes.update(token_index.get(normalized, ()))
            scored = [
                (
                    score_match(scoring_candidate, scoring_descriptors[index]),
                    descriptors[index],
                )
                for index in descriptor_indexes
            ]
            scored = [item for item in scored if item[0] >= 2.5]
            scored.sort(
                key=lambda item: (
                    item[0],
                    item[1].get("last_seen_at") or datetime.min,
                    str(item[1].get("cluster_id") or ""),
                ),
                reverse=True,
            )
            options = [
                {
                    "cluster_id": str(descriptor.get("cluster_id") or ""),
                    "title": str(descriptor.get("title") or ""),
                    "item_count": int(descriptor.get("item_count") or 0),
                    "first_seen_at": descriptor.get("first_seen_at"),
                    "last_seen_at": descriptor.get("last_seen_at"),
                    "examples": tuple(descriptor.get("examples") or ())[:2],
                }
                for _score, descriptor in scored[:option_limit]
            ]
            copied = dict(candidate)
            copied["existing_cluster_candidates"] = tuple(options)
            attached.append(copied)
            total_references += len(options)
        return attached, total_references

    indexed_attach._trend_cluster_existing_index = True  # type: ignore[attr-defined]
    discovery_module._attach_existing_cluster_candidates = indexed_attach
