from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.database import connect_database, init_database
from src.services.topic_service import upsert_source_signal
from src.services.trend_discovery_service import list_ranked_trends, rebuild_trend_rankings


def _signal(
    source_type: str,
    external_id: str,
    title: str,
    *,
    signal_value: float,
    metadata: dict[str, object],
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


@pytest.mark.parametrize(
    ("source_type", "title", "signal_value", "metadata", "count_column"),
    (
        (
            "youtube",
            "갤럭시 S26 카메라 기능 공개",
            9.0,
            {
                "signal_type": "emerging_topic",
                "topic_score": 9.0,
                "views_per_hour": 12_000,
                "view_delta": 80_000,
            },
            "youtube_count",
        ),
        (
            "google_trends",
            "근로장려금 신청 일정 변경",
            100_000.0,
            {
                "signal_type": "google_trend",
                "traffic_count": 100_000,
                "approx_traffic": "100K+",
            },
            "google_count",
        ),
        (
            "wikipedia_pageviews",
            "전기차 보조금 정책 변경",
            50_000.0,
            {
                "signal_type": "wikipedia_pageview",
                "views": 50_000,
                "rank": 3,
            },
            "wikipedia_count",
        ),
    ),
)
def test_strong_standalone_trend_signal_survives_persisted_default_review_filter(
    tmp_path: Path,
    source_type: str,
    title: str,
    signal_value: float,
    metadata: dict[str, object],
    count_column: str,
) -> None:
    db_path = tmp_path / f"{source_type}-review-visibility.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal(
                source_type,
                f"{source_type}-strong",
                title,
                signal_value=signal_value,
                metadata=metadata,
            ),
            create_topic=False,
        )

        result = rebuild_trend_rankings(con, lookback_hours=72)
        visible = list_ranked_trends(
            con,
            minimum_score=30,
            recommendation_statuses=("recommended", "review"),
            sort_by="trend",
        )

    assert result["clusters"] == 1
    assert len(visible) == 1
    row = visible.iloc[0]
    assert str(row["recommendation_status"]) == "review"
    assert str(row["판정"]) == "검토"
    assert 30.0 <= float(row["트렌드점수"]) <= 50.0
    assert int(row[count_column]) == 1
    for other_column in {"youtube_count", "google_count", "wikipedia_count"} - {
        count_column
    }:
        assert int(row[other_column]) == 0
