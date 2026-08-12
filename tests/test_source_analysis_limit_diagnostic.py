from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from src.database import connect_database, init_database, set_setting
from src.services.source_analysis_limit_diagnostic_service import (
    build_source_analysis_limit_diagnostic,
)


def _insert_source_items(
    con,
    *,
    source_type: str,
    prefix: str,
    count: int,
    started_at: datetime,
) -> None:
    rows = []
    for index in range(count):
        source_id = f"{prefix}_{index:03d}"
        observed_at = started_at - timedelta(minutes=index)
        rows.append(
            [
                source_id,
                source_type,
                source_id,
                f"{source_type} 테스트 {index}",
                f"{source_type} 테스트 {index}",
                f"https://example.com/{source_id}",
                "테스트 출처",
                observed_at,
                observed_at,
                1.0,
                '{"discovery_query":"테스트 검색어"}',
                observed_at,
                observed_at,
                observed_at,
                1,
                observed_at,
            ]
        )
    con.executemany(
        """
        INSERT INTO source_items(
            source_item_id, source_type, external_id, raw_title, normalized_title,
            source_url, source_name, published_at, observed_at, signal_value,
            metadata_json, first_imported_at, previous_imported_at, last_imported_at,
            observation_count, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def test_portal_full_window_diagnostic_ignores_legacy_caps_and_selects_all(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "analysis-limit.duckdb"
    init_database(db_path)
    now = datetime.now().replace(microsecond=0)

    with connect_database(db_path) as con:
        set_setting(con, "trend_lookback_hours", "72")
        # 기존 UI에서 저장한 값은 데이터 호환을 위해 남아 있어도 런타임 상한으로 쓰지 않습니다.
        set_setting(con, "trend_analysis_naver_limit", "4000")
        set_setting(con, "trend_analysis_daum_limit", "4000")
        _insert_source_items(
            con,
            source_type="naver_news",
            prefix="naver_news",
            count=12,
            started_at=now,
        )
        _insert_source_items(
            con,
            source_type="naver_blog",
            prefix="naver_blog",
            count=12,
            started_at=now - timedelta(seconds=1),
        )
        _insert_source_items(
            con,
            source_type="daum_web",
            prefix="daum_web",
            count=4,
            started_at=now,
        )

        diagnostic = build_source_analysis_limit_diagnostic(con)

    assert diagnostic["available"] is True
    assert diagnostic["analysis_mode"] == "portal_full_window"
    assert diagnostic["lookback_hours"] == 72
    assert diagnostic["limits"] == {"naver": 0, "daum": 0}
    assert diagnostic["stored_legacy_limits"] == {"naver": 4000, "daum": 4000}

    naver = diagnostic["groups"]["naver"]
    assert naver["analysis_mode"] == "full_window"
    assert naver["configured_limit"] == 0
    assert naver["stored_legacy_limit"] == 4000
    assert naver["recent_items"] == 24
    assert naver["selected_items"] == 24
    assert naver["outside_limit_items"] == 0
    assert naver["recent_unclustered_items"] == 24
    assert naver["selected_unclustered_items"] == 24
    assert naver["outside_limit_unclustered_items"] == 0
    assert naver["outside_limit_unclustered_percent"] == 0.0
    assert naver["selected_pending_items"] == 24
    assert naver["limit_reached"] is False

    daum = diagnostic["groups"]["daum"]
    assert daum["configured_limit"] == 0
    assert daum["stored_legacy_limit"] == 4000
    assert daum["recent_items"] == 4
    assert daum["selected_items"] == 4
    assert daum["outside_limit_items"] == 0
    assert daum["outside_limit_unclustered_items"] == 0
    assert daum["selected_pending_items"] == 4
    assert daum["limit_reached"] is False
    assert diagnostic["outside_limit_unclustered_items"] == 0
    assert diagnostic["selected_pending_items"] == 28


def test_portal_full_window_diagnostic_keeps_clustered_rows_in_full_selection(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "analysis-limit-clustered.duckdb"
    init_database(db_path)
    now = datetime.now().replace(microsecond=0)

    with connect_database(db_path) as con:
        set_setting(con, "trend_analysis_naver_limit", "8000")
        _insert_source_items(
            con,
            source_type="naver_news",
            prefix="naver",
            count=12,
            started_at=now,
        )
        con.execute(
            """
            INSERT INTO trend_clusters(
                cluster_id, canonical_title, trend_score, opportunity_score,
                fact_risk_score, quality_score, rediscovery_score,
                recommendation_status, item_count, source_type_count,
                publisher_count, source_types_json, score_reasons_json,
                quality_reasons_json, first_seen_at, last_seen_at, calculated_at
            ) VALUES (
                'cluster_test', '테스트', 1, 1, 0, 50, 0, 'review',
                2, 1, 1, '["naver_news"]', '[]', '[]', ?, ?, ?
            )
            """,
            [now, now, now],
        )
        con.executemany(
            "INSERT INTO trend_cluster_items(cluster_id, source_item_id, linked_at) VALUES ('cluster_test', ?, ?)",
            [["naver_010", now], ["naver_011", now]],
        )

        diagnostic = build_source_analysis_limit_diagnostic(con)

    naver = diagnostic["groups"]["naver"]
    assert naver["stored_legacy_limit"] == 8000
    assert naver["recent_items"] == 12
    assert naver["selected_items"] == 12
    assert naver["outside_limit_items"] == 0
    assert naver["recent_unclustered_items"] == 10
    assert naver["selected_unclustered_items"] == 10
    assert naver["outside_limit_unclustered_items"] == 0
    assert naver["outside_limit_unclustered_percent"] == 0.0


def test_portal_full_window_diagnostic_returns_zeroes_without_recent_items(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "analysis-limit-empty.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        diagnostic = build_source_analysis_limit_diagnostic(con)

    assert diagnostic["available"] is True
    assert diagnostic["limits"] == {"naver": 0, "daum": 0}
    assert diagnostic["outside_limit_unclustered_items"] == 0
    assert diagnostic["selected_pending_items"] == 0
    assert diagnostic["groups"]["naver"]["recent_items"] == 0
    assert diagnostic["groups"]["daum"]["recent_items"] == 0


def test_portal_full_window_diagnostic_reports_missing_legacy_tables(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "analysis-limit-legacy.duckdb"
    with duckdb.connect(str(db_path)) as con:
        diagnostic = build_source_analysis_limit_diagnostic(con)

    assert diagnostic["available"] is False
    assert diagnostic["analysis_mode"] == "portal_full_window"
    assert diagnostic["limits"] == {"naver": 0, "daum": 0}
    assert "app_settings" in diagnostic["missing_tables"]
    assert "source_items" in diagnostic["missing_tables"]
    assert diagnostic["error_type"] == ""
