from __future__ import annotations

from collections import Counter
from functools import wraps
from typing import Any

from src.services.trend_source_review_policy import (
    PUBLIC_INTEREST_SOURCE_TYPES,
    TREND_DISCOVERY_SOURCE_TYPES,
    evaluate_trend_review_promotion,
)
from src.services.trend_source_review_runtime import (
    _has_canonical_public_interest_signals,
)


_BLOCKER_LABELS = {
    "ranking_not_refreshed": "현재 정책이면 검토 승격 대상이나 저장 순위가 아직 갱신되지 않음",
    "noncanonical_public_interest_signal": "정식 Google Trends·위키 관심 신호 형식이 아님",
    "missing_source_type": "출처 유형을 확인할 수 없음",
    "missing_title": "대표 제목이 비어 있음",
    "missing_editorial_identity": "구체적 주제 식별 정보가 부족함",
    "navigation_page_cluster": "탐색·섹션형 원문 비중이 높음",
    "generic_title": "구체적 주제 확인이 필요한 일반 제목임",
    "entity_only_title": "YouTube 단독 제목이 사건·변화 맥락 없는 단일 엔터티형임",
    "quality_score": "자료 품질 점수가 승격 기준에 못 미침",
    "opportunity_score": "글감 기회 점수가 승격 기준에 못 미침",
    "youtube_momentum": "YouTube 확산 강도가 승격 기준에 못 미침",
    "external_interest": "검색·위키 관심 강도가 승격 기준에 못 미침",
    "combined_signal_strength": "복수 관심 신호 조합이 승격 기준을 함께 충족하지 못함",
    "policy_scope": "현재 출처 조합에 적용 가능한 검토 승격 규칙이 없음",
}


def _candidate_rule(source_types: set[str]) -> str:
    has_youtube = "youtube" in source_types
    public_sources = source_types & PUBLIC_INTEREST_SOURCE_TYPES
    non_trend_sources = source_types - TREND_DISCOVERY_SOURCE_TYPES
    if non_trend_sources and (has_youtube or public_sources):
        return "portal_trend_cross"
    if has_youtube and public_sources:
        return "youtube_public_cross"
    if source_types == PUBLIC_INTEREST_SOURCE_TYPES:
        return "public_interest_cross"
    if source_types == {"youtube"}:
        return "youtube_standalone"
    if source_types == {"google_trends"}:
        return "google_trends_standalone"
    if source_types == {"wikipedia_pageviews"}:
        return "wikipedia_standalone"
    return "unsupported"


def _policy_diagnostic(
    discovery_module: Any,
    cluster: dict[str, Any],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    title = str(cluster.get("canonical_title") or cluster.get("title") or "")
    status = str(cluster.get("recommendation_status") or "")
    quality = float(cluster.get("quality_score") or cluster.get("quality") or 0.0)
    opportunity = float(
        cluster.get("opportunity_score") or cluster.get("opportunity") or 0.0
    )
    source_types = {
        str(item.get("source_type") or "").strip()
        for item in items
        if str(item.get("source_type") or "").strip()
    }
    evidence_groups = discovery_module._evidence_groups(items)
    youtube_strength = float(discovery_module._youtube_momentum(items) or 0.0)
    external_strength = float(
        discovery_module._external_interest_momentum(items) or 0.0
    )
    has_identity = bool(discovery_module._editorial_identity_tokens(title))
    entity_only = bool(discovery_module._is_entity_only_title(title))
    navigation_page_cluster = discovery_module._navigation_page_ratio(evidence_groups) >= 0.5
    canonical_public = (
        not bool(source_types & PUBLIC_INTEREST_SOURCE_TYPES)
        or _has_canonical_public_interest_signals(items)
    )

    kwargs = {
        "source_types": source_types,
        "title": title,
        "current_status": status,
        "quality_score": quality,
        "opportunity_score": opportunity,
        "youtube_momentum": youtube_strength,
        "external_interest": external_strength,
        "has_editorial_identity": has_identity,
        "entity_only_title": entity_only,
        "navigation_page_cluster": navigation_page_cluster,
    }
    blockers: list[str] = []
    actual_decision = None

    if not source_types:
        blockers.append("missing_source_type")
    if not title:
        blockers.append("missing_title")
    if not has_identity:
        blockers.append("missing_editorial_identity")
    if navigation_page_cluster:
        blockers.append("navigation_page_cluster")
    if title.startswith("구체적 주제 확인 필요"):
        blockers.append("generic_title")
    if source_types == {"youtube"} and entity_only:
        blockers.append("entity_only_title")
    if not canonical_public:
        blockers.append("noncanonical_public_interest_signal")

    if canonical_public:
        actual_decision = evaluate_trend_review_promotion(**kwargs)
        if actual_decision.promote_to_review:
            blockers = ["ranking_not_refreshed"]
        elif not blockers:
            safe = dict(kwargs)
            safe.update(
                quality_score=100.0,
                opportunity_score=100.0,
                youtube_momentum=10.0,
                external_interest=10.0,
            )
            safe_decision = evaluate_trend_review_promotion(**safe)
            if not safe_decision.promote_to_review:
                blockers.append("policy_scope")
            else:
                for field_name, blocker_name, actual_value in (
                    ("quality_score", "quality_score", quality),
                    ("opportunity_score", "opportunity_score", opportunity),
                    ("youtube_momentum", "youtube_momentum", youtube_strength),
                    ("external_interest", "external_interest", external_strength),
                ):
                    isolated = dict(safe)
                    isolated[field_name] = actual_value
                    if not evaluate_trend_review_promotion(**isolated).promote_to_review:
                        blockers.append(blocker_name)
                if not blockers:
                    blockers.append("combined_signal_strength")

    unique_blockers = tuple(dict.fromkeys(blockers))
    return {
        "candidate_rule": _candidate_rule(source_types),
        "would_promote_now": bool(
            actual_decision is not None and actual_decision.promote_to_review
        ),
        "blocking_reasons": list(unique_blockers),
        "blocking_labels": [_BLOCKER_LABELS[value] for value in unique_blockers],
        "source_types": sorted(source_types),
        "quality_score": round(quality, 1),
        "opportunity_score": round(opportunity, 1),
        "youtube_momentum": round(youtube_strength, 1),
        "external_interest": round(external_strength, 1),
        "has_editorial_identity": has_identity,
        "entity_only_title": entity_only,
        "navigation_page_cluster": navigation_page_cluster,
        "canonical_public_interest_signals": canonical_public,
    }


def install_trend_source_visibility_policy_diagnostic_contract(
    diagnostic_module: Any | None = None,
) -> None:
    """보류 표본에 현재 승격 정책의 실제 차단 지표를 읽기 전용으로 붙입니다."""
    if diagnostic_module is None:
        from src.services import trend_source_visibility_diagnostic_service as diagnostic_module

    original = getattr(diagnostic_module, "_group_metrics", None)
    if not callable(original) or getattr(
        original,
        "_trend_source_visibility_policy_diagnostic_contract",
        False,
    ):
        return

    @wraps(original)
    def group_metrics_with_policy_details(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = dict(original(*args, **kwargs))
        con = args[0] if args else kwargs.get("con")
        if con is None:
            return result

        from src.services import trend_discovery_service as discovery

        blocker_counts: Counter[str] = Counter()
        examples = list(result.get("examples") or ())
        for example in examples:
            if str(example.get("recommendation_status") or "") != "hold":
                continue
            cluster_id = str(example.get("cluster_id") or "")
            if not cluster_id:
                continue
            cluster = discovery.get_trend_cluster(con, cluster_id)
            if not cluster:
                continue
            items = discovery.get_trend_cluster_items(con, cluster_id)
            details = _policy_diagnostic(discovery, cluster, items)
            example["review_policy"] = details
            blocker_counts.update(details["blocking_reasons"])

        if blocker_counts:
            summary = [
                {
                    "code": code,
                    "label": _BLOCKER_LABELS[code],
                    "count": count,
                }
                for code, count in sorted(
                    blocker_counts.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ]
            result["policy_blocker_summary"] = summary
            if str(result.get("diagnosis") or "") == "held_by_policy":
                labels = [item["label"] for item in summary[:3]]
                result["diagnosis_label"] = (
                    f"{result.get('diagnosis_label') or '현재 군집이 모두 보류 상태'}"
                    " · 표본 승격 차단: "
                    + ", ".join(labels)
                )
        else:
            result["policy_blocker_summary"] = []
        result["examples"] = examples
        return result

    group_metrics_with_policy_details._trend_source_visibility_policy_diagnostic_contract = True  # type: ignore[attr-defined]
    diagnostic_module._group_metrics = group_metrics_with_policy_details
