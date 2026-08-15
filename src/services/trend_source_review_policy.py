from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


TREND_SOURCE_REVIEW_POLICY_VERSION = "4"
TREND_DISCOVERY_SOURCE_TYPES = frozenset(
    {"youtube", "google_trends", "wikipedia_pageviews"}
)
PUBLIC_INTEREST_SOURCE_TYPES = frozenset(
    {"google_trends", "wikipedia_pageviews"}
)


@dataclass(frozen=True)
class TrendReviewDecision:
    promote_to_review: bool
    reason: str = ""
    minimum_trend_score: float = 0.0


def _review_score_floor(*, opportunity_score: float, signal_strength: float) -> float:
    """강한 검토 후보가 기본 목록에서 사라지지 않게 하되 상위 추천 점수까지 올리지 않습니다."""
    opportunity = max(0.0, float(opportunity_score or 0.0))
    strength = max(0.0, min(10.0, float(signal_strength or 0.0)))
    opportunity_component = min(12.0, max(0.0, opportunity - 20.0) * 0.6)
    signal_component = min(8.0, strength)
    return round(min(50.0, 30.0 + opportunity_component + signal_component), 1)


def _promote(
    reason: str,
    *,
    opportunity_score: float,
    signal_strength: float,
) -> TrendReviewDecision:
    return TrendReviewDecision(
        True,
        reason,
        _review_score_floor(
            opportunity_score=opportunity_score,
            signal_strength=signal_strength,
        ),
    )


def evaluate_trend_review_promotion(
    *,
    source_types: Iterable[str],
    title: str,
    current_status: str,
    quality_score: float,
    opportunity_score: float,
    youtube_momentum: float,
    external_interest: float,
    has_editorial_identity: bool,
    entity_only_title: bool,
    navigation_page_cluster: bool,
) -> TrendReviewDecision:
    """강한 트렌드 신호를 추천이 아닌 수동 검토 후보로만 보수적으로 승격합니다."""
    sources = frozenset(
        str(value or "").strip()
        for value in source_types
        if str(value or "").strip()
    )
    clean_title = str(title or "").strip()
    quality = float(quality_score or 0.0)
    opportunity = float(opportunity_score or 0.0)
    youtube_strength = float(youtube_momentum or 0.0)
    external_strength = float(external_interest or 0.0)

    if (
        str(current_status or "") != "hold"
        or not sources
        or not clean_title
        or not has_editorial_identity
        or navigation_page_cluster
        or clean_title.startswith("구체적 주제 확인 필요")
        or opportunity < 20.0
    ):
        return TrendReviewDecision(False)

    has_youtube = "youtube" in sources
    public_sources = sources & PUBLIC_INTEREST_SOURCE_TYPES
    non_trend_sources = sources - TREND_DISCOVERY_SOURCE_TYPES

    # 포털·커뮤니티 원문과 관심 신호가 같은 군집이면 단독 신호보다 낮은
    # 임계값을 쓰되 추천이 아니라 검토까지만 허용합니다.
    if non_trend_sources and (has_youtube or public_sources):
        signal_strength = max(youtube_strength, external_strength)
        if (
            quality >= 42.0
            and opportunity >= 25.0
            and (youtube_strength >= 4.0 or external_strength >= 2.5)
        ):
            return _promote(
                "포털·커뮤니티 원문과 강한 트렌드 신호가 교차 확인됨 · 검토 단계에서 사실 근거 확인 필요",
                opportunity_score=opportunity,
                signal_strength=signal_strength,
            )

    # YouTube와 검색·위키가 함께 잡히면 서로 다른 관심 신호가 겹친 것으로 봅니다.
    if has_youtube and public_sources:
        if (
            quality >= 40.0
            and opportunity >= 25.0
            and youtube_strength >= 4.0
            and external_strength >= 2.3
        ):
            return _promote(
                "YouTube 확산과 검색·위키 관심 신호가 같은 주제로 겹침 · 검토 후 독립 사실 근거 보강 필요",
                opportunity_score=opportunity,
                signal_strength=max(youtube_strength, external_strength),
            )

    # Google Trends·위키는 서비스 특성상 인물명·프로그램명·사건명 자체가
    # 유효한 관심 키워드일 수 있으므로 entity-only라는 이유만으로 버리지 않습니다.
    if sources == PUBLIC_INTEREST_SOURCE_TYPES:
        if quality >= 35.0 and opportunity >= 22.0 and external_strength >= 4.2:
            return _promote(
                "Google Trends·위키백과 관심 신호가 같은 주제로 겹침 · 검토 후 독립 사실 근거 보강 필요",
                opportunity_score=opportunity,
                signal_strength=external_strength,
            )

    # YouTube 단독은 제목 자체가 구체적인 콘텐츠 후보여야 합니다. ASMR·브랜드명처럼
    # 맥락 없는 단일 엔터티는 강한 조회 신호여도 계속 보류합니다.
    if sources == {"youtube"}:
        if (
            not entity_only_title
            and quality >= 52.0
            and opportunity >= 30.0
            and youtube_strength >= 4.5
        ):
            return _promote(
                "강한 YouTube 단독 확산 신호 · 검토 후 독립 사실 근거 보강 필요",
                opportunity_score=opportunity,
                signal_strength=youtube_strength,
            )

    if sources == {"google_trends"}:
        required_strength = 5.0 if entity_only_title else 4.0
        required_quality = 38.0 if entity_only_title else 35.0
        if (
            quality >= required_quality
            and opportunity >= 24.0
            and external_strength >= required_strength
        ):
            return _promote(
                "강한 Google Trends 단독 관심 신호 · 검토 후 독립 사실 근거 보강 필요",
                opportunity_score=opportunity,
                signal_strength=external_strength,
            )

    if sources == {"wikipedia_pageviews"}:
        required_strength = 3.1 if entity_only_title else 2.7
        if (
            quality >= 38.0
            and opportunity >= 24.0
            and external_strength >= required_strength
        ):
            return _promote(
                "강한 위키백과 단독 관심 신호 · 검토 후 독립 사실 근거 보강 필요",
                opportunity_score=opportunity,
                signal_strength=external_strength,
            )

    return TrendReviewDecision(False)
