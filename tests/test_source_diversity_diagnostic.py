from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.database import connect_database, init_database
from src.services.source_diversity_service import analyze_source_diversity


NOW = datetime(2026, 7, 31, 21, 0, 0)



def _insert_item(
    con,
    *,
    item_id: str,
    source_type: str,
    observed_at: datetime,
) -> None:
    con.execute(
        """
        INSERT INTO source_items(
            source_item_id, source_type, external_id, raw_title,
            normalized_title, source_name, observed_at, signal_value,
            metadata_json, first_imported_at, last_imported_at,
            observation_count, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, '{}', ?, ?, 1, ?)
        """,
        [
            item_id,
            source_type,
            f"external_{item_id}",
            f"원문 {item_id}",
            f"원문 {item_id}",
            source_type,
            observed_at,
            observed_at,
            observed_at,
            observed_at,
        ],
    )



def _insert_cluster(
    con,
    *,
    cluster_id: str,
    source_types: list[str],
    item_count: int,
) -> None:
    con.execute(
        """
        INSERT INTO trend_clusters(
            cluster_id, canonical_title, trend_score, opportunity_score,
            fact_risk_score, item_count, source_type_count, publisher_count,
            source_types_json, score_reasons_json, first_seen_at,
            last_seen_at, calculated_at
        ) VALUES (?, ?, 60, 60, 20, ?, ?, ?, ?, '[]', ?, ?, ?)
        """,
        [
            cluster_id,
            f"군집 {cluster_id}",
            item_count,
            len(set(source_types)),
            len(set(source_types)),
            str(source_types).replace("'", '"'),
            NOW,
            NOW,
            NOW,
        ],
    )



def _link(con, *, cluster_id: str, item_id: str) -> None:
    con.execute(
        """
        INSERT INTO trend_cluster_items(cluster_id, source_item_id, linked_at)
        VALUES (?, ?, ?)
        """,
        [cluster_id, item_id, NOW],
    )



def _source_map(report):
    return {row.source_type: row for row in report.source_rows}



def test_source_diversity_counts_pairs_and_does_not_write(tmp_path: Path) -> None:
    db_path = tmp_path / "source-diversity.duckdb"
    init_database(db_path)
    recent = NOW - timedelta(hours=2)

    with connect_database(db_path) as con:
        for item_id, source_type in (
            ("y1", "youtube"),
            ("n1", "naver_news"),
            ("n2", "naver_news"),
            ("g1", "google_trends"),
            ("n3", "naver_news"),
            ("y2", "youtube"),
            ("d1", "daum_web"),
            ("w1", "wikipedia_pageviews"),
        ):
            _insert_item(
                con,
                item_id=item_id,
                source_type=source_type,
                observed_at=recent,
            )

        _insert_cluster(
            con,
            cluster_id="c1",
            source_types=["youtube", "naver_news"],
            item_count=2,
        )
        _link(con, cluster_id="c1", item_id="y1")
        _link(con, cluster_id="c1", item_id="n1")

        _insert_cluster(
            con,
            cluster_id="c2",
            source_types=["naver_news"],
            item_count=1,
        )
        _link(con, cluster_id="c2", item_id="n2")

        _insert_cluster(
            con,
            cluster_id="c3",
            source_types=["google_trends", "naver_news", "youtube"],
            item_count=3,
        )
        _link(con, cluster_id="c3", item_id="g1")
        _link(con, cluster_id="c3", item_id="n3")
        _link(con, cluster_id="c3", item_id="y2")

        _insert_cluster(
            con,
            cluster_id="c4",
            source_types=["daum_web"],
            item_count=1,
        )
        _link(con, cluster_id="c4", item_id="d1")

        before = {
            table: int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("source_items", "trend_clusters", "trend_cluster_items")
        }
        report = analyze_source_diversity(con, lookback_hours=72, now=NOW)
        after = {
            table: int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("source_items", "trend_clusters", "trend_cluster_items")
        }

    assert report.collected_item_count == 8
    assert report.clustered_item_count == 7
    assert report.unclustered_item_count == 1
    assert report.cluster_count == 4
    assert report.single_source_cluster_count == 2
    assert report.multi_source_cluster_count == 2
    assert report.three_plus_source_cluster_count == 1
    assert report.multi_source_ratio == 0.5
    assert report.cluster_coverage == 7 / 8
    assert report.status == "sample_low"
    assert before == after

    sources = _source_map(report)
    assert sources["naver_news"].cluster_count == 3
    assert sources["naver_news"].multi_source_cluster_count == 2
    assert sources["naver_news"].cross_source_rate == 2 / 3
    assert sources["youtube"].cross_source_rate == 1.0
    assert sources["wikipedia_pageviews"].collected_item_count == 1
    assert sources["wikipedia_pageviews"].clustered_item_count == 0

    pairs = {row.pair_label: row.cluster_count for row in report.pair_rows}
    assert pairs["NAVER 뉴스 + YouTube"] == 2
    assert pairs["Google Trends + NAVER 뉴스"] == 1
    assert pairs["Google Trends + YouTube"] == 1



def test_source_diversity_flags_google_trends_single_source_dominance(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "source-diversity-warning.duckdb"
    init_database(db_path)
    recent = NOW - timedelta(hours=1)

    with connect_database(db_path) as con:
        for index in range(20):
            item_id = f"g{index}"
            cluster_id = f"google_cluster_{index}"
            _insert_item(
                con,
                item_id=item_id,
                source_type="google_trends",
                observed_at=recent,
            )
            _insert_cluster(
                con,
                cluster_id=cluster_id,
                source_types=["google_trends"],
                item_count=1,
            )
            _link(con, cluster_id=cluster_id, item_id=item_id)

        report = analyze_source_diversity(con, lookback_hours=72, now=NOW)

    issue_codes = {issue.code for issue in report.issues}
    assert report.cluster_count == 20
    assert report.multi_source_cluster_count == 0
    assert report.status == "critical"
    assert "critical_multi_source_ratio" in issue_codes
    assert "dominant_source" in issue_codes
    assert "weak_cross_source:google_trends" in issue_codes
    assert "sources_without_recent_items" in issue_codes



def test_source_diversity_respects_lookback_window(tmp_path: Path) -> None:
    db_path = tmp_path / "source-diversity-lookback.duckdb"
    init_database(db_path)
    observed_at = NOW - timedelta(hours=48)

    with connect_database(db_path) as con:
        _insert_item(
            con,
            item_id="old_youtube",
            source_type="youtube",
            observed_at=observed_at,
        )
        _insert_cluster(
            con,
            cluster_id="old_cluster",
            source_types=["youtube"],
            item_count=1,
        )
        _link(con, cluster_id="old_cluster", item_id="old_youtube")

        report_24h = analyze_source_diversity(con, lookback_hours=24, now=NOW)
        report_72h = analyze_source_diversity(con, lookback_hours=72, now=NOW)

    assert report_24h.collected_item_count == 0
    assert report_24h.cluster_count == 0
    assert report_24h.status == "empty"
    assert report_72h.collected_item_count == 1
    assert report_72h.cluster_count == 1
    assert report_72h.status == "sample_low"



def test_source_diversity_panel_is_collapsed_and_settings_connection_is_read_only() -> None:
    root = Path(__file__).resolve().parents[1]
    panel_source = (root / "src" / "source_diversity_ui.py").read_text(
        encoding="utf-8"
    )
    settings_source = (root / "src" / "database_backup_ui.py").read_text(
        encoding="utf-8"
    )

    assert 'with st_module.expander("수집 출처 다양성 진단", expanded=False)' in panel_source
    assert "analyze_source_diversity" in panel_source
    assert "수집 설정·분석 상한·군집 기준은 자동으로 변경하지 않습니다" in panel_source
    assert "render_source_diversity_panel" in settings_source
    assert "connect_database(DEFAULT_DB_PATH, read_only=True)" in settings_source
    assert "데이터베이스 백업·안전 복구" in settings_source
