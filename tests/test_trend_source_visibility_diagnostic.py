from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.database import connect_database, init_database
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
    **metadata,
) -> dict[str, object]:
    observed_at = datetime.now() - timedelta(hours=1)
    return {
        "source_type": source_type,
        "external_id": external_id,
        "title": title,
        "source_name": source_type,
        "source_url": f"https://example.com/{external_id}",
        "published_at": observed_at,
        "observed_at": observed_at,
        "signal_value": signal_value,
        "metadata": {"item_title": title, **metadata},
    }


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
    assert youtube["eligible_below_score_count"] == 1
    assert youtube["diagnosis"] == "hidden_by_score"
