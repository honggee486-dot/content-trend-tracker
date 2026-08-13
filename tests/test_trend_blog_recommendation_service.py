from __future__ import annotations

import json

import duckdb

from src.services.trend_blog_recommendation_service import (
    TISTORY_CHANNEL_KEY,
    build_trend_blog_recommendation_labels,
    format_recommended_blog_label,
    get_recommendation_display_name,
    managed_strategy_definitions_for_trend_list,
    set_recommendation_display_name,
)


def _connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE app_settings (
            setting_key VARCHAR PRIMARY KEY,
            setting_value VARCHAR,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE trend_cluster_ai_profiles (
            cluster_id VARCHAR PRIMARY KEY,
            display_title VARCHAR,
            summary VARCHAR,
            content_plan_json VARCHAR
        )
        """
    )
    return con


def _create_blog_profile_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE blog_profiles (
            blog_profile_id VARCHAR PRIMARY KEY,
            profile_name VARCHAR,
            platform VARCHAR,
            is_default BOOLEAN,
            is_active BOOLEAN,
            updated_at TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE TABLE blog_profile_strategies (
            blog_profile_id VARCHAR PRIMARY KEY,
            strategy_code VARCHAR NOT NULL UNIQUE
        )
        """
    )


def test_platform_prefix_labels_keep_blank_name_visible() -> None:
    assert format_recommended_blog_label("blogger", "요즘화제") == "B:요즘화제"
    assert format_recommended_blog_label("naver_blog", "") == "N:"
    assert format_recommended_blog_label("tistory", "") == "T:"
    assert TISTORY_CHANNEL_KEY == "tistory"


def test_recommendation_display_name_is_optional_setting() -> None:
    con = _connection()

    assert get_recommendation_display_name(con, "blogger_current") == ""
    set_recommendation_display_name(con, "blogger_current", "  요즘화제  ")
    assert get_recommendation_display_name(con, "blogger_current") == "요즘화제"
    set_recommendation_display_name(con, "blogger_current", "")
    assert get_recommendation_display_name(con, "blogger_current") == ""


def test_recommendation_display_name_falls_back_to_current_blog_profile_name() -> None:
    con = _connection()
    _create_blog_profile_tables(con)
    con.execute(
        """
        INSERT INTO blog_profiles VALUES
            ('blog-life', '생활자료', 'blogger', FALSE, TRUE, CURRENT_TIMESTAMP)
        """
    )
    con.execute(
        """
        INSERT INTO blog_profile_strategies VALUES ('blog-life', 'blogger_life')
        """
    )

    assert get_recommendation_display_name(con, "blogger_life") == "생활자료"

    set_recommendation_display_name(con, "blogger_life", "생활 정보 노트")
    assert get_recommendation_display_name(con, "blogger_life") == "생활 정보 노트"

    set_recommendation_display_name(con, "blogger_life", "")
    assert get_recommendation_display_name(con, "blogger_life") == "생활자료"


def test_tistory_display_name_falls_back_to_active_profile_name() -> None:
    con = _connection()
    _create_blog_profile_tables(con)
    con.execute(
        """
        INSERT INTO blog_profiles VALUES
            ('blog-tistory', '기록장', 'tistory', TRUE, TRUE, CURRENT_TIMESTAMP)
        """
    )

    assert get_recommendation_display_name(con, TISTORY_CHANNEL_KEY) == "기록장"


def test_trend_list_routes_using_profile_name_without_duplicate_display_setting() -> None:
    con = _connection()
    _create_blog_profile_tables(con)
    con.execute(
        """
        INSERT INTO blog_profiles VALUES
            ('blog-life', '생활자료', 'blogger', FALSE, TRUE, CURRENT_TIMESTAMP)
        """
    )
    con.execute(
        """
        INSERT INTO blog_profile_strategies VALUES ('blog-life', 'blogger_life')
        """
    )

    labels = build_trend_blog_recommendation_labels(
        con,
        [{"cluster_id": "life-1", "주제": "정부 지원금 신청 자격과 신청 방법"}],
    )

    assert labels["life-1"] == "B:생활자료"


def test_trend_list_routes_using_saved_display_names_and_ai_profile_context() -> None:
    con = _connection()
    set_recommendation_display_name(con, "blogger_tech", "디지털 생활")
    set_recommendation_display_name(con, "blogger_current", "요즘화제")
    con.execute(
        """
        INSERT INTO trend_cluster_ai_profiles VALUES (?, ?, ?, ?)
        """,
        [
            "tech-1",
            "윈도우 11 앱 설치 오류 해결 순서",
            "Windows 설정과 앱 재설치 절차를 정리합니다.",
            json.dumps({"category": "IT·AI·기기"}, ensure_ascii=False),
        ],
    )

    labels = build_trend_blog_recommendation_labels(
        con,
        [
            {"cluster_id": "tech-1", "주제": "앱 오류"},
            {"cluster_id": "current-1", "주제": "프로야구 순위와 이번 주 경기 일정"},
            {"cluster_id": "local-1", "주제": "부산 전시관 방문 예약과 주차 정보"},
        ],
    )

    assert labels["tech-1"] == "B:디지털 생활"
    assert labels["current-1"] == "B:요즘화제"
    assert labels["local-1"] == "N:"


def test_unclassified_trend_falls_back_to_current_issues_blogger() -> None:
    con = _connection()
    set_recommendation_display_name(con, "blogger_current", "요즘화제")

    labels = build_trend_blog_recommendation_labels(
        con,
        [{"cluster_id": "unknown-1", "주제": "새로운 검색 주제"}],
    )

    assert labels == {"unknown-1": "B:요즘화제"}
    current = next(
        item
        for item in managed_strategy_definitions_for_trend_list()
        if item["strategy_code"] == "blogger_current"
    )
    assert current["is_default"] is True
