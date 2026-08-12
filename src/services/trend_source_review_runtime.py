from __future__ import annotations

import hashlib
from functools import wraps
from typing import Any

from src.services.trend_source_review_policy import (
    PUBLIC_INTEREST_SOURCE_TYPES,
    TREND_SOURCE_REVIEW_POLICY_VERSION,
    evaluate_trend_review_promotion,
)

_EXPECTED_PUBLIC_SIGNAL_TYPES = {
    "google_trends": "google_trend",
    "wikipedia_pageviews": "wikipedia_pageview",
}


def _versioned_ranking_signature(value: object) -> str:
    payload = (
        f"{str(value or '')}|trend-source-review-policy:"
        f"{TREND_SOURCE_REVIEW_POLICY_VERSION}"
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _has_canonical_public_interest_signals(items: list[dict[str, Any]]) -> bool:
    """정식 Google Trends·Wikimedia 어댑터가 만든 관심 신호만 승격에 사용합니다."""
    saw_public_signal = False
    for item in items:
        source_type = str(item.get("source_type") or "")
        expected = _EXPECTED_PUBLIC_SIGNAL_TYPES.get(source_type)
        if expected is None:
            continue
        saw_public_signal = True
        metadata = item.get("metadata") or {}
        if str(metadata.get("signal_type") or "") != expected:
            return False
    return saw_public_signal


def install_trend_source_review_contract(discovery_module: Any | None = None) -> None:
    """모든 실행 경로에서 강한 트렌드 단독·교차 신호를 검토 후보로만 승격합니다."""
    if discovery_module is None:
        from src.services import trend_discovery_service as discovery_module

    # 런타임 정책이 바뀌면 기존 순위 서명을 재사용하지 않도록 정책 버전을
    # 순위 서명에 포함합니다. 다음 계산에서 모든 기존 군집도 새 정책으로 재점수됩니다.
    original_context = getattr(discovery_module, "_trend_ranking_signature_context", None)
    if callable(original_context) and not getattr(
        original_context,
        "_trend_source_review_signature_contract",
        False,
    ):

        @wraps(original_context)
        def context_with_review_policy(*args: Any, **kwargs: Any) -> dict[str, Any]:
            context = dict(original_context(*args, **kwargs))
            context["signature"] = _versioned_ranking_signature(context.get("signature"))
            return context

        context_with_review_policy._trend_source_review_signature_contract = True  # type: ignore[attr-defined]
        discovery_module._trend_ranking_signature_context = context_with_review_policy

    original = getattr(discovery_module, "_score_cluster", None)
    if not callable(original) or getattr(original, "_trend_source_review_contract", False):
        return

    @wraps(original)
    def score_with_trend_review(cluster: dict[str, Any]) -> dict[str, Any]:
        scored = dict(original(cluster))
        if str(scored.get("recommendation_status") or "") != "hold":
            return scored

        items = list(cluster.get("items") or ())
        title = str(scored.get("title") or "")
        source_types = {
            str(value or "").strip()
            for value in scored.get("source_types") or ()
            if str(value or "").strip()
        }
        # 이름만 Google/위키 출처처럼 보이는 임의 데이터는 승격 근거로 쓰지 않습니다.
        # 실제 어댑터는 각각 고정 signal_type을 저장하므로 정상 수집 데이터에는 영향이 없습니다.
        if (
            source_types & PUBLIC_INTEREST_SOURCE_TYPES
            and not _has_canonical_public_interest_signals(items)
        ):
            return scored

        evidence_groups = discovery_module._evidence_groups(items)
        youtube_strength = float(discovery_module._youtube_momentum(items) or 0.0)
        external_strength = float(discovery_module._external_interest_momentum(items) or 0.0)
        decision = evaluate_trend_review_promotion(
            source_types=source_types,
            title=title,
            current_status=str(scored.get("recommendation_status") or ""),
            quality_score=float(scored.get("quality") or 0.0),
            opportunity_score=float(scored.get("opportunity") or 0.0),
            youtube_momentum=youtube_strength,
            # 원래 점수 함수는 실질 원문이 없을 때 검색·위키 점수를 45%로 줄여
            # 총점에 반영합니다. 승격 판단은 실제 관심 신호의 원래 강도를 사용합니다.
            external_interest=external_strength,
            has_editorial_identity=bool(discovery_module._editorial_identity_tokens(title)),
            entity_only_title=bool(discovery_module._is_entity_only_title(title)),
            navigation_page_cluster=(
                discovery_module._navigation_page_ratio(evidence_groups) >= 0.5
            ),
        )
        if not decision.promote_to_review:
            return scored

        scored["recommendation_status"] = "review"
        original_score = float(scored.get("score") or 0.0)
        if decision.minimum_trend_score > original_score:
            scored["score"] = decision.minimum_trend_score
        reasons = list(scored.get("reasons") or ())
        reasons.append(decision.reason)
        if decision.minimum_trend_score > original_score:
            reasons.append(
                "강한 트렌드 검토 후보 점수 하한 "
                f"{original_score:.1f}→{decision.minimum_trend_score:.1f} "
                "(추천 승격에는 사용하지 않음)"
            )
        scored["reasons"] = reasons
        return scored

    score_with_trend_review._trend_source_review_contract = True  # type: ignore[attr-defined]
    discovery_module._score_cluster = score_with_trend_review
