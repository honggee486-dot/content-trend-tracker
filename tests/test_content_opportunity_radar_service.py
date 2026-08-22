from datetime import datetime, timedelta
from pathlib import Path

from src.database import connect_database, init_database
from src.services.content_opportunity_radar_service import (
    ACTION_CLOSE,
    ACTION_WRITE_NOW,
    STATUS_HOT,
    STATUS_LABELS,
    STATUS_SATURATED,
    get_opportunity_summary,
    list_opportunity_watchlist,
    refresh_opportunity_radar,
)


def _insert_cluster(
    con,
    *,
    cluster_id: str,
    title: str,
    trend_score: float,
    opportunity_score: float,
    source_ids: list[str],
    source_type_count: int,
    publisher_count: int,
    calculated_at: datetime,
) -> None:
    con.execute("DELETE FROM trend_cluster_items WHERE cluster_id = ?", [cluster_id])
    con.execute("DELETE FROM trend_clusters WHERE cluster_id = ?", [cluster_id])
    con.execute(
        """
        INSERT INTO trend_clusters(
            cluster_id, canonical_title, trend_score, opportunity_score,
            fact_risk_score, quality_score, rediscovery_score,
            recommendation_status, item_count, source_type_count, publisher_count,
            source_types_json, score_reasons_json, quality_reasons_json,
            first_seen_at, last_seen_at, calculated_at
        ) VALUES (?, ?, ?, ?, 0, 80, 0, 'review', ?, ?, ?, '[]', '[]', '[]', ?, ?, ?)
        """,
        [
            cluster_id,
            title,
            trend_score,
            opportunity_score,
            len(source_ids),
            source_type_count,
            publisher_count,
            calculated_at - timedelta(hours=8),
            calculated_at,
            calculated_at,
        ],
    )
    con.executemany(
        "INSERT INTO trend_cluster_items(cluster_id, source_item_id, linked_at) VALUES (?, ?, ?)",
        [[cluster_id, source_id, calculated_at] for source_id in source_ids],
    )


def _insert_source(
    con,
    *,
    source_id: str,
    source_type: str,
    observed_at: datetime,
    signal_value: float = 0,
    source_name: str | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO source_items(
            source_item_id, source_type, external_id, raw_title, normalized_title,
            source_url, normalized_url, source_name, published_at, observed_at,
            signal_value, metadata_json, first_imported_at, previous_imported_at,
            last_imported_at, observation_count, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, NULL, ?, 1, ?)
        """,
        [
            source_id,
            source_type,
            f"ext-{source_id}",
            "레이더 테스트 주제",
            "레이더 테스트 주제",
            f"https://example.com/{source_id}",
            f"https://example.com/{source_id}",
            source_name or source_type,
            observed_at,
            observed_at,
            signal_value,
            observed_at,
            observed_at,
            observed_at,
        ],
    )


def test_radar_persists_history_and_detects_positive_velocity(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.duckdb"
    init_database(db_path)
    base = datetime(2026, 8, 22, 9, 0, 0)

    with connect_database(db_path) as con:
        _insert_source(
            con,
            source_id="src-yt",
            source_type="youtube",
            observed_at=base,
            signal_value=20,
        )
        _insert_source(
            con,
            source_id="src-google",
            source_type="google_trends",
            observed_at=base,
            signal_value=30,
        )
        _insert_cluster(
            con,
            cluster_id="cluster-rising",
            title="지원 정책 신청 방법",
            trend_score=55,
            opportunity_score=55,
            source_ids=["src-yt", "src-google"],
            source_type_count=2,
            publisher_count=2,
            calculated_at=base,
        )

        first = refresh_opportunity_radar(con, observed_at=base)
        first_row = list_opportunity_watchlist(con)[0]
        assert first["status"] == "recorded"
        assert first["observed"] == 1
        assert first_row["velocity"] == 0
        assert first_row["status_label"] in STATUS_LABELS.values()

        later = base + timedelta(hours=2)
        _insert_source(
            con,
            source_id="src-wiki",
            source_type="wikipedia_pageviews",
            observed_at=later,
            signal_value=50,
        )
        _insert_cluster(
            con,
            cluster_id="cluster-rising",
            title="지원 정책 신청 방법",
            trend_score=90,
            opportunity_score=82,
            source_ids=["src-yt", "src-google", "src-wiki"],
            source_type_count=3,
            publisher_count=3,
            calculated_at=later,
        )

        second = refresh_opportunity_radar(con, observed_at=later)
        row = list_opportunity_watchlist(con)[0]
        snapshots = con.execute(
            "SELECT COUNT(*) FROM trend_opportunity_snapshots WHERE cluster_id = 'cluster-rising'"
        ).fetchone()[0]

    assert second["observed"] == 1
    assert snapshots == 2
    assert float(row["velocity"]) >= 3.0
    assert row["radar_status"] == STATUS_HOT
    assert row["recommended_action"] == ACTION_WRITE_NOW
    assert row["expected_lifetime"] in {"1_2_days", "weeks"}
    assert [item["source_type"] for item in row["source_spread"]] == [
        "google_trends",
        "youtube",
        "wikipedia_pageviews",
    ] or set(item["source_type"] for item in row["source_spread"]) == {
        "google_trends",
        "youtube",
        "wikipedia_pageviews",
    }


def test_high_recent_portal_supply_is_saturated(tmp_path: Path) -> None:
    db_path = tmp_path / "supply.duckdb"
    init_database(db_path)
    now = datetime(2026, 8, 22, 12, 0, 0)

    with connect_database(db_path) as con:
        source_ids: list[str] = []
        for index in range(25):
            source_id = f"src-blog-{index}"
            source_ids.append(source_id)
            _insert_source(
                con,
                source_id=source_id,
                source_type="naver_blog",
                observed_at=now - timedelta(minutes=index),
                source_name=f"블로그-{index}",
            )
            con.execute(
                """
                INSERT INTO collection_query_discoveries(
                    run_id, source_name, source_type, discovery_query, source_item_id,
                    external_id, source_url, is_new, result_rank, discovered_at
                ) VALUES (?, 'naver', 'naver_blog', '지원 정책', ?, ?, ?, TRUE, ?, ?)
                """,
                [
                    f"run-{index}",
                    source_id,
                    f"ext-{source_id}",
                    f"https://example.com/{source_id}",
                    index + 1,
                    now - timedelta(minutes=index),
                ],
            )
        _insert_cluster(
            con,
            cluster_id="cluster-saturated",
            title="지원 정책 정리",
            trend_score=48,
            opportunity_score=45,
            source_ids=source_ids,
            source_type_count=1,
            publisher_count=25,
            calculated_at=now,
        )

        refresh_opportunity_radar(con, observed_at=now)
        row = list_opportunity_watchlist(con)[0]
        summary = get_opportunity_summary(con)

    assert row["radar_status"] == STATUS_SATURATED
    assert row["recommended_action"] == ACTION_CLOSE
    assert float(row["supply_score"]) >= 80
    assert float(row["saturation_score"]) >= 72
    assert summary[STATUS_SATURATED] == 1
