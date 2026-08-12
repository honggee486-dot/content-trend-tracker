from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path

from src.database import connect_database, init_database
from src.services.cluster_case_diagnostic_service import analyze_cluster_cases


NOW = datetime(2026, 7, 31, 22, 0, 0)


def _insert_item(
    con,
    *,
    item_id: str,
    source_type: str,
    title: str,
    observed_at: datetime,
    source_url: str | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO source_items(
            source_item_id, source_type, external_id, raw_title,
            normalized_title, source_url, normalized_url, source_name,
            observed_at, signal_value, metadata_json,
            first_imported_at, last_imported_at,
            observation_count, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 10, '{}', ?, ?, 1, ?)
        """,
        [
            item_id,
            source_type,
            f"external_{item_id}",
            title,
            title.casefold(),
            source_url,
            source_url,
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
    title: str,
    source_types: list[str],
    item_ids: list[str],
    last_seen_at: datetime,
) -> None:
    con.execute(
        """
        INSERT INTO trend_clusters(
            cluster_id, canonical_title, trend_score, opportunity_score,
            fact_risk_score, item_count, source_type_count, publisher_count,
            source_types_json, score_reasons_json,
            first_seen_at, last_seen_at, calculated_at
        ) VALUES (?, ?, 60, 60, 20, ?, ?, ?, ?, '[]', ?, ?, ?)
        """,
        [
            cluster_id,
            title,
            len(item_ids),
            len(set(source_types)),
            len(set(source_types)),
            json.dumps(source_types, ensure_ascii=False),
            last_seen_at,
            last_seen_at,
            last_seen_at,
        ],
    )
    for item_id in item_ids:
        con.execute(
            """
            INSERT INTO trend_cluster_items(cluster_id, source_item_id, linked_at)
            VALUES (?, ?, ?)
            """,
            [cluster_id, item_id, last_seen_at],
        )


def test_cluster_case_diagnostic_finds_cross_source_candidates_without_writes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cluster-cases.duckdb"
    init_database(db_path)
    recent = NOW - timedelta(hours=2)

    with connect_database(db_path) as con:
        _insert_item(
            con,
            item_id="unclustered_galaxy",
            source_type="google_trends",
            title="갤럭시 S30",
            observed_at=recent,
            source_url="https://trends.google.com/trending/galaxy-s30",
        )
        _insert_item(
            con,
            item_id="naver_galaxy",
            source_type="naver_news",
            title="삼성 갤럭시 S30 출시 공개",
            observed_at=recent,
        )
        _insert_cluster(
            con,
            cluster_id="cluster_galaxy",
            title="삼성 갤럭시 S30 출시 공개",
            source_types=["naver_news"],
            item_ids=["naver_galaxy"],
            last_seen_at=recent,
        )

        _insert_item(
            con,
            item_id="google_iphone",
            source_type="google_trends",
            title="아이폰 18",
            observed_at=recent,
        )
        _insert_cluster(
            con,
            cluster_id="cluster_google_iphone",
            title="아이폰 18",
            source_types=["google_trends"],
            item_ids=["google_iphone"],
            last_seen_at=recent,
        )
        _insert_item(
            con,
            item_id="naver_iphone",
            source_type="naver_news",
            title="애플 아이폰 18 공개",
            observed_at=recent,
        )
        _insert_cluster(
            con,
            cluster_id="cluster_naver_iphone",
            title="애플 아이폰 18 공개",
            source_types=["naver_news"],
            item_ids=["naver_iphone"],
            last_seen_at=recent,
        )

        before = {
            table: int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("source_items", "trend_clusters", "trend_cluster_items")
        }
        report = analyze_cluster_cases(con, lookback_hours=72, now=NOW)
        after = {
            table: int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("source_items", "trend_clusters", "trend_cluster_items")
        }

    assert report.unclustered_total == 1
    assert report.single_source_cluster_total == 3
    assert before == after

    unclustered = report.unclustered_cases[0]
    assert unclustered.source_item_id == "unclustered_galaxy"
    assert unclustered.source_label == "Google Trends"
    assert unclustered.normalized_title == "갤럭시 s30"
    assert unclustered.candidate is not None
    assert unclustered.candidate.cluster_id == "cluster_galaxy"
    assert unclustered.candidate.similarity >= 0.72
    assert "갤럭시" in unclustered.candidate.shared_tokens
    assert unclustered.reason_code == "analysis_scope_or_timing"

    single_map = {case.cluster_id: case for case in report.single_source_cases}
    google_iphone = single_map["cluster_google_iphone"]
    assert google_iphone.candidate is not None
    assert google_iphone.candidate.cluster_id == "cluster_naver_iphone"
    assert google_iphone.candidate.similarity >= 0.72
    assert google_iphone.source_label == "Google Trends"
    assert google_iphone.sample_titles == ("아이폰 18",)


def test_cluster_case_diagnostic_marks_short_google_search_term(tmp_path: Path) -> None:
    db_path = tmp_path / "cluster-short-query.duckdb"
    init_database(db_path)
    recent = NOW - timedelta(hours=1)

    with connect_database(db_path) as con:
        _insert_item(
            con,
            item_id="google_exchange",
            source_type="google_trends",
            title="환율",
            observed_at=recent,
        )
        _insert_item(
            con,
            item_id="naver_exchange",
            source_type="naver_news",
            title="환율",
            observed_at=recent,
        )
        _insert_cluster(
            con,
            cluster_id="cluster_exchange",
            title="환율",
            source_types=["naver_news"],
            item_ids=["naver_exchange"],
            last_seen_at=recent,
        )

        report = analyze_cluster_cases(con, lookback_hours=72, now=NOW)

    assert report.unclustered_total == 1
    case = report.unclustered_cases[0]
    assert case.candidate is not None
    assert case.candidate.similarity >= 0.72
    assert case.reason_code == "short_search_term"


def test_cluster_case_diagnostic_respects_lookback(tmp_path: Path) -> None:
    db_path = tmp_path / "cluster-case-lookback.duckdb"
    init_database(db_path)
    old = NOW - timedelta(hours=48)

    with connect_database(db_path) as con:
        _insert_item(
            con,
            item_id="old_unclustered",
            source_type="youtube",
            title="오래된 군집 밖 원문",
            observed_at=old,
        )
        report_24 = analyze_cluster_cases(con, lookback_hours=24, now=NOW)
        report_72 = analyze_cluster_cases(con, lookback_hours=72, now=NOW)

    assert report_24.unclustered_total == 0
    assert report_24.unclustered_cases == ()
    assert report_72.unclustered_total == 1
    assert report_72.unclustered_cases[0].source_item_id == "old_unclustered"


def test_cluster_case_panel_is_collapsed_and_reuses_read_only_settings_connection() -> None:
    root = Path(__file__).resolve().parents[1]
    panel_source = (root / "src" / "cluster_case_diagnostic_ui.py").read_text(
        encoding="utf-8"
    )
    source_ui = (root / "src" / "source_diversity_ui.py").read_text(
        encoding="utf-8"
    )
    settings_source = (root / "src" / "database_backup_ui.py").read_text(
        encoding="utf-8"
    )

    assert '"군집 실패·단일 출처 사례 상세 보기"' in panel_source
    assert "expanded=False" in panel_source
    assert "현재 병합 참고 기준 72%" in panel_source
    assert "자동 병합은 하지 않습니다" in panel_source
    assert "render_cluster_case_diagnostic_panel" in source_ui
    assert "군집 실패·단일 출처 사례를 불러오지 못했습니다" in source_ui
    assert "connect_database(DEFAULT_DB_PATH, read_only=True)" in settings_source
    assert "데이터베이스 백업·안전 복구" in settings_source
