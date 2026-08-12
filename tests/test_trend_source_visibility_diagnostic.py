from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.database import connect_database, init_database
from src.services import trend_discovery_service as discovery
from src.services.topic_service import upsert_source_signal
from src.services.trend_discovery_service import rebuild_trend_rankings
from src.services.trend_source_visibility_diagnostic_service import (
    build_trend_source_visibility_diagnostic,
)


def _signal(
    source_type: str,
    external_id: str,
    title: str,
    *,
    signal_value: float | None = None,
    observed_at: datetime | None = None,
    **metadata,
) -> dict[str, object]:
    captured_at = observed_at or (datetime.now() - timedelta(hours=1))
    return {
        "source_type": source_type,
        "external_id": external_id,
        "title": title,
        "source_name": source_type,
        "source_url": f"https://example.com/{external_id}",
        "published_at": captured_at,
        "observed_at": captured_at,
        "signal_value": signal_value,
        "metadata": {"item_title": title, **metadata},
    }


def _cluster_id_for_source_type(con, source_type: str) -> str:
    row = con.execute(
        """
        SELECT DISTINCT tc.cluster_id
        FROM trend_clusters tc
        JOIN trend_cluster_items tci ON tci.cluster_id = tc.cluster_id
        JOIN source_items s ON s.source_item_id = tci.source_item_id
        WHERE s.source_type = ?
        LIMIT 1
        """,
        [source_type],
    ).fetchone()
    assert row is not None
    return str(row[0])


def test_visibility_diagnostic_distinguishes_visible_and_unclustered_sources(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "visibility.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "youtube",
                "yt-strong",
                "갤럭시 S26 카메라 기능 공개",
                signal_value=9.0,
                signal_type="emerging_topic",
                topic_score=9.0,
                views_per_hour=12_000,
                view_delta=80_000,
            ),
            create_topic=False,
        )
        rebuild_trend_rankings(con, lookback_hours=72)

        # 순위 계산 이후 새 원문만 추가해 '수집됨 → 아직 현재 군집에 미연결' 상태를 만듭니다.
        upsert_source_signal(
            con,
            _signal(
                "daum_web",
                "daum-unclustered",
                "전기요금 지원 정책 변경",
            ),
            create_topic=False,
        )

        report = build_trend_source_visibility_diagnostic(
            con,
            lookback_hours=72,
            minimum_score=30,
        )

    assert report["available"] is True
    assert report["display_limit"] == 100
    assert report["sort_by"] == "opportunity"
    youtube = report["groups"]["youtube"]
    assert youtube["recent_items"] == 1
    assert youtube["cluster_count"] == 1
    assert youtube["review_count"] == 1
    assert youtube["default_visible_count"] == 1
    assert youtube["diagnosis"] == "visible"

    daum = report["groups"]["daum"]
    assert daum["recent_items"] == 1
    assert daum["recent_unclustered_items"] == 1
    assert daum["cluster_count"] == 0
    assert daum["diagnosis"] == "unclustered_or_stale"


def test_visibility_diagnostic_identifies_score_filter_without_changing_policy(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "score-filter.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "youtube",
                "yt-score",
                "전기차 보조금 신청 일정 변경",
                signal_value=9.0,
                signal_type="emerging_topic",
                topic_score=9.0,
                views_per_hour=12_000,
                view_delta=80_000,
            ),
            create_topic=False,
        )
        rebuild_trend_rankings(con, lookback_hours=72)
        con.execute(
            """
            UPDATE trend_clusters
            SET recommendation_status = 'review', trend_score = 20
            """
        )

        report = build_trend_source_visibility_diagnostic(
            con,
            lookback_hours=72,
            minimum_score=30,
        )

    youtube = report["groups"]["youtube"]
    assert youtube["review_count"] == 1
    assert youtube["default_visible_count"] == 0
    assert youtube["eligible_at_or_above_score_count"] == 0
    assert youtube["eligible_below_score_count"] == 1
    assert youtube["diagnosis"] == "hidden_by_score"


def test_visibility_diagnostic_does_not_treat_old_cluster_as_recent_source_visibility(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "stale-cluster.duckdb"
    init_database(db_path)
    now = datetime.now()

    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "youtube",
                "yt-old",
                "과거 스마트폰 카메라 비교",
                observed_at=now - timedelta(hours=120),
                signal_value=8.0,
                signal_type="emerging_topic",
                topic_score=8.0,
                views_per_hour=8_000,
                view_delta=50_000,
            ),
            create_topic=False,
        )
        rebuild_trend_rankings(con, lookback_hours=240)
        upsert_source_signal(
            con,
            _signal(
                "youtube",
                "yt-new-unclustered",
                "새 스마트폰 배터리 기능 공개",
                observed_at=now - timedelta(hours=1),
                signal_value=9.0,
                signal_type="emerging_topic",
                topic_score=9.0,
                views_per_hour=12_000,
                view_delta=80_000,
            ),
            create_topic=False,
        )

        report = build_trend_source_visibility_diagnostic(
            con,
            lookback_hours=72,
            minimum_score=30,
            now=now,
        )

    youtube = report["groups"]["youtube"]
    assert youtube["recent_items"] == 1
    assert youtube["recent_unclustered_items"] == 1
    assert youtube["cluster_count"] == 0
    assert youtube["default_visible_count"] == 0
    assert youtube["diagnosis"] == "unclustered_or_stale"


def test_visibility_diagnostic_distinguishes_filter_eligibility_from_display_limit(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "display-limit.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "youtube",
                "yt-ranked-out",
                "갤럭시 S26 카메라 기능 공개",
                signal_value=9.0,
                signal_type="emerging_topic",
                topic_score=9.0,
                views_per_hour=12_000,
                view_delta=80_000,
            ),
            create_topic=False,
        )
        upsert_source_signal(
            con,
            _signal(
                "naver_news",
                "naver-top",
                "전기요금 지원 정책 변경 안내",
                signal_value=8.0,
            ),
            create_topic=False,
        )
        rebuild_trend_rankings(con, lookback_hours=72)

        youtube_cluster_id = _cluster_id_for_source_type(con, "youtube")
        naver_cluster_id = _cluster_id_for_source_type(con, "naver_news")
        assert youtube_cluster_id != naver_cluster_id
        con.execute(
            """
            UPDATE trend_clusters
            SET recommendation_status = 'review', trend_score = 40,
                opportunity_score = 10
            """
        )
        con.execute(
            "UPDATE trend_clusters SET opportunity_score = 90 WHERE cluster_id = ?",
            [naver_cluster_id],
        )

        report = build_trend_source_visibility_diagnostic(
            con,
            lookback_hours=72,
            minimum_score=30,
            display_limit=1,
            sort_by="opportunity",
        )

    assert report["eligible_clusters"] == 2
    assert report["default_visible_clusters"] == 1
    youtube = report["groups"]["youtube"]
    assert youtube["cluster_count"] == 1
    assert youtube["eligible_at_or_above_score_count"] == 1
    assert youtube["default_visible_count"] == 0
    assert youtube["ranked_out_count"] == 1
    assert youtube["diagnosis"] == "ranked_out"
    assert youtube["examples"][0]["in_default_list"] is False

    naver = report["groups"]["naver"]
    assert naver["default_visible_count"] == 1
    assert naver["ranked_out_count"] == 0
    assert naver["diagnosis"] == "visible"


def test_first_stage_candidates_keep_every_supported_source_type(tmp_path: Path) -> None:
    db_path = tmp_path / "all-source-first-stage.duckdb"
    init_database(db_path)
    signals = (
        _signal("youtube", "yt-all", "유튜브 카메라 기능 공개", signal_value=5.0),
        _signal("naver_news", "naver-all", "네이버 전기요금 정책 변경"),
        _signal("daum_web", "daum-all", "다음 자동차 지원 정책 변경"),
        _signal(
            "google_trends",
            "google-all",
            "검색 관심 키워드",
            signal_value=20_000,
            signal_type="google_trend",
            traffic_count=20_000,
        ),
        _signal(
            "wikipedia_pageviews",
            "wiki-all",
            "위키백과 관심 주제",
            signal_value=10_000,
            signal_type="wikipedia_pageview",
            views=10_000,
            rank=10,
        ),
    )

    with connect_database(db_path) as con:
        for signal in signals:
            upsert_source_signal(con, signal, create_topic=False)
        items = discovery._parse_source_rows(con, 72)

    source_types = {str(item.get("source_type") or "") for item in items}
    assert {
        "youtube",
        "naver_news",
        "daum_web",
        "google_trends",
        "wikipedia_pageviews",
    } <= source_types

    candidates, _ = discovery._build_first_stage_candidates(items)
    candidate_source_item_ids = {
        str(source_item_id)
        for candidate in candidates
        for source_item_id in candidate.get("source_item_ids") or ()
    }
    selected_source_item_ids = {
        str(item.get("source_item_id") or "")
        for item in items
    }
    assert selected_source_item_ids <= candidate_source_item_ids


def test_visibility_diagnostic_explains_wikipedia_hold_policy_blockers(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wiki-hold-policy.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                "wikipedia_pageviews",
                "wiki-hold",
                "트로이 전쟁",
                signal_value=50_000,
                signal_type="wikipedia_pageview",
                views=50_000,
                rank=3,
            ),
            create_topic=False,
        )
        rebuild_trend_rankings(con, lookback_hours=72)
        cluster_id = _cluster_id_for_source_type(con, "wikipedia_pageviews")
        con.execute(
            """
            UPDATE trend_clusters
            SET recommendation_status = 'hold', quality_score = 37,
                opportunity_score = 23.5, trend_score = 14.8
            WHERE cluster_id = ?
            """,
            [cluster_id],
        )

        report = build_trend_source_visibility_diagnostic(
            con,
            lookback_hours=72,
            minimum_score=30,
        )

    wikipedia = report["groups"]["wikipedia"]
    assert wikipedia["diagnosis"] == "held_by_policy"
    assert "표본 승격 차단" in wikipedia["diagnosis_label"]
    example = wikipedia["examples"][0]
    policy = example["review_policy"]
    assert policy["candidate_rule"] == "wikipedia_standalone"
    assert policy["canonical_public_interest_signals"] is True
    assert policy["would_promote_now"] is False
    assert "quality_score" in policy["blocking_reasons"]
    assert "opportunity_score" in policy["blocking_reasons"]
    assert wikipedia["policy_blocker_summary"]
