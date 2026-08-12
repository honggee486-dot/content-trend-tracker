from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from src.services import trend_discovery_service as discovery
from src.services.trend_source_review_policy import (
    TREND_SOURCE_REVIEW_POLICY_VERSION,
    evaluate_trend_review_promotion,
)
from src.services.trend_source_review_runtime import install_trend_source_review_contract
from src.services.trend_normalization import (
    compact_title,
    identity_tokens,
    normalize_title,
    normalize_url,
    source_domain,
)


def _item(
    source_type: str,
    title: str,
    *,
    external_id: str,
    signal_value: float = 0.0,
    metadata: dict | None = None,
) -> dict[str, object]:
    now = datetime.now()
    url = f"https://example.com/{external_id}"
    tokens = identity_tokens(title)
    return {
        "source_item_id": external_id,
        "source_type": source_type,
        "raw_title": title,
        "item_title": title,
        "canonical_title": title,
        "normalized_title": normalize_title(title),
        "compact_title": compact_title(title),
        "identity_tokens": tokens,
        "editorial_identity_tokens": discovery._editorial_identity_tokens(title),
        "calendar_identity_tokens": set(),
        "tokens": set(discovery._tokens(title)),
        "source_url": url,
        "normalized_url": normalize_url(url),
        "source_name": f"{source_type}.example",
        "domain": source_domain(url),
        "query": title,
        "query_supported": True,
        "published_at": now,
        "observed_at": now,
        "imported_at": now,
        "first_imported_at": now,
        "previous_imported_at": None,
        "last_imported_at": now,
        "observation_count": 1,
        "signal_value": signal_value,
        "metadata": dict(metadata or {}),
    }


def _score(title: str, items: list[dict[str, object]]) -> dict[str, object]:
    install_trend_source_review_contract(discovery)
    return discovery._score_cluster({"title": title, "items": items})


def test_policy_version_is_explicit_and_changes_ranking_signature() -> None:
    fake = SimpleNamespace(
        _score_cluster=lambda cluster: {"recommendation_status": "recommended"},
        _trend_ranking_signature_context=lambda *args, **kwargs: {"signature": "base"},
    )

    install_trend_source_review_contract(fake)
    first = fake._trend_ranking_signature_context()["signature"]
    second = fake._trend_ranking_signature_context()["signature"]

    assert TREND_SOURCE_REVIEW_POLICY_VERSION == "2"
    assert first != "base"
    assert first == second


def test_policy_never_promotes_recommended_or_unsafe_titles() -> None:
    assert not evaluate_trend_review_promotion(
        source_types={"youtube"},
        title="갤럭시 S26 카메라 기능 공개",
        current_status="recommended",
        quality_score=80,
        opportunity_score=70,
        youtube_momentum=10,
        external_interest=0,
        has_editorial_identity=True,
        entity_only_title=False,
        navigation_page_cluster=False,
    ).promote_to_review
    assert not evaluate_trend_review_promotion(
        source_types={"google_trends"},
        title="구체적 주제 확인 필요 · AI 관련 신호",
        current_status="hold",
        quality_score=80,
        opportunity_score=70,
        youtube_momentum=0,
        external_interest=6,
        has_editorial_identity=True,
        entity_only_title=False,
        navigation_page_cluster=False,
    ).promote_to_review


def test_strong_specific_youtube_signal_becomes_visible_review_not_recommended() -> None:
    title = "갤럭시 S26 카메라 기능 공개"
    item = _item(
        "youtube",
        title,
        external_id="youtube-strong",
        signal_value=9.0,
        metadata={
            "signal_type": "emerging_topic",
            "topic_score": 9.0,
            "views_per_hour": 12_000,
            "view_delta": 80_000,
        },
    )

    scored = _score(title, [item])

    assert scored["recommendation_status"] == "review"
    assert float(scored["score"]) >= 30.0
    assert float(scored["score"]) <= 50.0
    assert any("YouTube 단독" in reason for reason in scored["reasons"])


def test_weak_youtube_signal_remains_hold() -> None:
    title = "갤럭시 S26 카메라 기능 공개"
    item = _item(
        "youtube",
        title,
        external_id="youtube-weak",
        signal_value=1.0,
        metadata={
            "signal_type": "recent_video",
            "topic_score": 1.0,
            "views_per_hour": 20,
            "view_delta": 30,
        },
    )

    assert _score(title, [item])["recommendation_status"] == "hold"


def test_generic_youtube_scope_stays_hold_even_when_signal_is_strong() -> None:
    title = "구체적 주제 확인 필요 · VTUBER 관련 신호"
    item = _item(
        "youtube",
        title,
        external_id="youtube-generic",
        signal_value=10.0,
        metadata={
            "signal_type": "recent_video",
            "topic_score": 10.0,
            "views_per_hour": 20_000,
            "view_delta": 100_000,
        },
    )

    assert _score(title, [item])["recommendation_status"] == "hold"


def test_entity_only_youtube_signal_stays_hold() -> None:
    decision = evaluate_trend_review_promotion(
        source_types={"youtube"},
        title="ASMR",
        current_status="hold",
        quality_score=70,
        opportunity_score=40,
        youtube_momentum=9,
        external_interest=0,
        has_editorial_identity=True,
        entity_only_title=True,
        navigation_page_cluster=False,
    )

    assert not decision.promote_to_review


def test_strong_google_trends_signal_becomes_visible_review() -> None:
    title = "근로장려금 신청 일정 변경"
    item = _item(
        "google_trends",
        title,
        external_id="google-strong",
        signal_value=100_000,
        metadata={"traffic_count": 100_000, "signal_type": "google_trend"},
    )

    scored = _score(title, [item])

    assert scored["recommendation_status"] == "review"
    assert float(scored["score"]) >= 30.0
    assert any("Google Trends 단독" in reason for reason in scored["reasons"])


def test_strong_entity_only_google_keyword_is_valid_review_signal() -> None:
    decision = evaluate_trend_review_promotion(
        source_types={"google_trends"},
        title="송영길",
        current_status="hold",
        quality_score=42,
        opportunity_score=34,
        youtube_momentum=0,
        external_interest=5.4,
        has_editorial_identity=True,
        entity_only_title=True,
        navigation_page_cluster=False,
    )

    assert decision.promote_to_review
    assert decision.minimum_trend_score >= 30.0


def test_weak_entity_only_google_keyword_remains_hold() -> None:
    decision = evaluate_trend_review_promotion(
        source_types={"google_trends"},
        title="엘리베이터",
        current_status="hold",
        quality_score=40,
        opportunity_score=28,
        youtube_momentum=0,
        external_interest=3.0,
        has_editorial_identity=True,
        entity_only_title=True,
        navigation_page_cluster=False,
    )

    assert not decision.promote_to_review


def test_strong_wikipedia_signal_becomes_visible_review() -> None:
    title = "전기차 보조금 정책 변경"
    item = _item(
        "wikipedia_pageviews",
        title,
        external_id="wiki-strong",
        signal_value=50_000,
        metadata={"views": 50_000, "rank": 3, "signal_type": "wikipedia_pageview"},
    )

    scored = _score(title, [item])

    assert scored["recommendation_status"] == "review"
    assert float(scored["score"]) >= 30.0
    assert any("위키백과 단독" in reason for reason in scored["reasons"])


def test_entity_only_wikipedia_topic_can_be_reviewed_when_interest_is_strong() -> None:
    decision = evaluate_trend_review_promotion(
        source_types={"wikipedia_pageviews"},
        title="트로이 전쟁",
        current_status="hold",
        quality_score=56,
        opportunity_score=26,
        youtube_momentum=0,
        external_interest=3.4,
        has_editorial_identity=True,
        entity_only_title=True,
        navigation_page_cluster=False,
    )

    assert decision.promote_to_review


def test_youtube_and_google_overlap_is_review_but_not_recommended() -> None:
    title = "갤럭시 S26 카메라 기능 공개"
    items = [
        _item(
            "youtube",
            title,
            external_id="yt-cross",
            signal_value=8.0,
            metadata={
                "signal_type": "emerging_topic",
                "topic_score": 8.0,
                "views_per_hour": 6_000,
                "view_delta": 40_000,
            },
        ),
        _item(
            "google_trends",
            title,
            external_id="google-cross",
            signal_value=30_000,
            metadata={"traffic_count": 30_000, "signal_type": "google_trend"},
        ),
    ]

    scored = _score(title, items)

    assert scored["recommendation_status"] == "review"
    assert float(scored["score"]) >= 30.0
    assert any("YouTube 확산과 검색·위키" in reason for reason in scored["reasons"])


def test_google_and_wikipedia_overlap_is_review_but_not_recommended() -> None:
    title = "근로장려금 신청 일정 변경"
    items = [
        _item(
            "google_trends",
            title,
            external_id="google-public-cross",
            signal_value=30_000,
            metadata={"traffic_count": 30_000, "signal_type": "google_trend"},
        ),
        _item(
            "wikipedia_pageviews",
            title,
            external_id="wiki-public-cross",
            signal_value=20_000,
            metadata={"views": 20_000, "rank": 5, "signal_type": "wikipedia_pageview"},
        ),
    ]

    scored = _score(title, items)

    assert scored["recommendation_status"] == "review"
    assert float(scored["score"]) >= 30.0
    assert any("Google Trends·위키백과" in reason for reason in scored["reasons"])
