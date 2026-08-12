from datetime import datetime
from pathlib import Path

from src.database import connect_database, init_database
from src.services.trend_discovery_service import list_ranked_trends


def test_ranked_trend_filters_run_before_display_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "ranked-filter.duckdb"
    init_database(db_path)
    now = datetime.now()

    with connect_database(db_path) as con:
        con.executemany(
            """
            INSERT INTO trend_clusters(
                cluster_id, canonical_title, trend_score, opportunity_score,
                fact_risk_score, quality_score, rediscovery_score,
                recommendation_status, item_count, source_type_count,
                publisher_count, source_types_json, score_reasons_json,
                quality_reasons_json, first_seen_at, last_seen_at, calculated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [
                    "recommended-1", "추천 후보", 80.0, 70.0, 10.0, 80.0, 0.0,
                    "recommended", 3, 2, 2, "[]", "[]", "[]", now, now, now,
                ],
                [
                    "review-1", "검토 후보", 60.0, 60.0, 10.0, 70.0, 0.0,
                    "review", 2, 2, 2, "[]", "[]", "[]", now, now, now,
                ],
                [
                    "hold-1", "보류 후보", 95.0, 90.0, 10.0, 50.0, 0.0,
                    "hold", 1, 1, 1, "[]", "[]", "[]", now, now, now,
                ],
            ],
        )

        rankings = list_ranked_trends(
            con,
            limit=1,
            minimum_score=50,
            recommendation_statuses=["recommended", "review"],
        )

    assert len(rankings) == 1
    assert int(rankings.iloc[0]["matched_count"]) == 2
    assert str(rankings.iloc[0]["cluster_id"]) == "recommended-1"
    assert str(rankings.iloc[0]["recommendation_status"]) == "recommended"
    assert float(rankings.iloc[0]["트렌드점수"]) == 80.0


def test_dashboard_builds_filters_before_ranked_query() -> None:
    source = Path("app.py").read_text(encoding="utf-8")

    controls_position = source.index(
        'candidate_scope = filter_col1.segmented_control('
    )
    query_position = source.index("rankings = list_ranked_trends(")

    assert controls_position < query_position
    assert "minimum_score=minimum_score" in source
    assert "recommendation_statuses=recommendation_statuses" in source
    assert 'rankings["matched_count"]' in source

def test_ranked_trends_can_sort_by_opportunity_without_changing_trend_filter(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "ranked-opportunity-sort.duckdb"
    init_database(db_path)
    now = datetime.now()

    with connect_database(db_path) as con:
        con.executemany(
            """
            INSERT INTO trend_clusters(
                cluster_id, canonical_title, trend_score, opportunity_score,
                fact_risk_score, quality_score, rediscovery_score,
                recommendation_status, item_count, source_type_count,
                publisher_count, source_types_json, score_reasons_json,
                quality_reasons_json, first_seen_at, last_seen_at, calculated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [
                    "trend-first", "급상승 후보", 90.0, 55.0, 0.0, 75.0, 0.0,
                    "recommended", 3, 2, 2, "[]", "[]", "[]", now, now, now,
                ],
                [
                    "opportunity-first", "글감 우선 후보", 70.0, 88.0, 0.0, 85.0, 0.0,
                    "recommended", 3, 2, 2, "[]", "[]", "[]", now, now, now,
                ],
            ],
        )

        opportunity_rankings = list_ranked_trends(
            con,
            minimum_score=60,
            sort_by="opportunity",
        )
        trend_rankings = list_ranked_trends(
            con,
            minimum_score=60,
            sort_by="trend",
        )

    assert list(opportunity_rankings["cluster_id"].astype(str)) == [
        "opportunity-first",
        "trend-first",
    ]
    assert list(trend_rankings["cluster_id"].astype(str)) == [
        "trend-first",
        "opportunity-first",
    ]


def test_dashboard_defaults_to_opportunity_sort_and_clear_headers() -> None:
    source = Path("app.py").read_text(encoding="utf-8")

    assert '"글감 추천순": "opportunity"' in source
    assert "sort_by=sort_by" in source
    assert '>트렌드</div>' in source
    assert '>기회</div>' in source
