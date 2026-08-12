from __future__ import annotations

from collections import Counter

import duckdb
import pytest

from src.services.blog_channel_strategy_service import (
    MANAGED_BLOG_CHANNELS,
    MANAGED_STRATEGY_CODES,
    ensure_blog_channel_strategy_schema,
    get_draft_blog_assignment,
    install_default_blog_channels,
    list_blog_channel_strategies,
    list_managed_blog_channel_strategies,
    recommend_blog_channel,
    save_draft_blog_assignment,
)


def _connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE blog_profiles (
            blog_profile_id VARCHAR PRIMARY KEY,
            profile_name VARCHAR NOT NULL,
            platform VARCHAR NOT NULL,
            login_url VARCHAR,
            write_url VARCHAR NOT NULL,
            output_format VARCHAR NOT NULL DEFAULT 'plain_text',
            default_category VARCHAR,
            default_tags_json VARCHAR NOT NULL DEFAULT '[]',
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE drafts (
            draft_id VARCHAR PRIMARY KEY,
            title VARCHAR NOT NULL
        )
        """
    )
    return con


def _insert_legacy_strategy(
    con: duckdb.DuckDBPyConnection,
    *,
    profile_id: str,
    name: str,
    platform: str,
    strategy_code: str,
) -> None:
    con.execute(
        """
        INSERT INTO blog_profiles VALUES (
            ?, ?, ?, '', 'https://example.com/write', 'plain_text',
            '기존', '[]', FALSE, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """,
        [profile_id, name, platform],
    )
    con.execute(
        """
        INSERT INTO blog_profile_strategies(
            blog_profile_id, strategy_code, allowed_categories_json,
            excluded_categories_json, routing_terms_json, target_audience,
            writing_tone, target_length, title_rules_json, seo_strategy,
            default_image_count, created_at, updated_at
        ) VALUES (?, ?, '[]', '[]', '[]', '기존 독자', '기존 문체',
                  2000, '[]', '기존 SEO', 3, CURRENT_TIMESTAMP,
                  CURRENT_TIMESTAMP)
        """,
        [profile_id, strategy_code],
    )


def test_new_default_set_is_three_blogger_and_one_naver() -> None:
    assert [
        str(channel["profile_name"])
        for channel in MANAGED_BLOG_CHANNELS
    ] == [
        "생활자료",
        "IT 사용법",
        "네이버 국내 장소·서비스·경험",
        "요즘 화제",
    ]
    assert MANAGED_STRATEGY_CODES == (
        "blogger_life",
        "blogger_tech",
        "naver_local",
        "blogger_current",
    )
    assert Counter(
        str(channel["platform"])
        for channel in MANAGED_BLOG_CHANNELS
    ) == Counter({"blogger": 3, "naver_blog": 1})


def test_install_default_channels_is_idempotent_and_preserves_custom_profile() -> None:
    con = _connection()
    con.execute(
        """
        INSERT INTO blog_profiles VALUES (
            'custom_profile', '내 기존 블로그', 'custom', '',
            'https://example.com/write', 'plain_text', '기존', '[]',
            TRUE, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """
    )

    first = install_default_blog_channels(con)
    second = install_default_blog_channels(con)

    assert len(first["created_profile_ids"]) == 4
    assert len(first["created_strategy_codes"]) == 4
    assert second["created_profile_ids"] == []
    assert second["created_strategy_codes"] == []
    assert con.execute("SELECT COUNT(*) FROM blog_profiles").fetchone()[0] == 5
    assert con.execute(
        "SELECT COUNT(*) FROM blog_profile_strategies"
    ).fetchone()[0] == 4
    assert con.execute(
        """
        SELECT profile_name, write_url, is_default
        FROM blog_profiles
        WHERE blog_profile_id = 'custom_profile'
        """
    ).fetchone() == ("내 기존 블로그", "https://example.com/write", True)


def test_install_does_not_overwrite_existing_new_default_profile() -> None:
    con = _connection()
    con.execute(
        """
        INSERT INTO blog_profiles VALUES (
            'blog_blogger_life', '사용자가 바꾼 이름', 'blogger', '',
            'https://www.blogger.com/', 'plain_text', '사용자 카테고리',
            '[]', FALSE, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """
    )

    install_default_blog_channels(con)

    assert con.execute(
        """
        SELECT profile_name, write_url, default_category
        FROM blog_profiles
        WHERE blog_profile_id = 'blog_blogger_life'
        """
    ).fetchone() == (
        "사용자가 바꾼 이름",
        "https://www.blogger.com/",
        "사용자 카테고리",
    )
    assert con.execute(
        """
        SELECT COUNT(*)
        FROM blog_profile_strategies
        WHERE blog_profile_id = 'blog_blogger_life'
        """
    ).fetchone()[0] == 1


def test_install_adds_new_defaults_without_changing_legacy_strategies() -> None:
    con = _connection()
    ensure_blog_channel_strategy_schema(con)
    legacy_rows = (
        ("legacy_tistory_life", "기존 티스토리 생활", "tistory", "tistory_life"),
        ("legacy_tistory_tech", "기존 티스토리 IT", "tistory", "tistory_tech"),
        ("legacy_naver", "기존 네이버", "naver_blog", "naver_trend"),
        ("legacy_blogger", "기존 Blogger", "blogger", "blogger_global"),
    )
    for profile_id, name, platform, code in legacy_rows:
        _insert_legacy_strategy(
            con,
            profile_id=profile_id,
            name=name,
            platform=platform,
            strategy_code=code,
        )

    result = install_default_blog_channels(con)

    assert len(result["created_profile_ids"]) == 4
    assert set(result["created_strategy_codes"]) == set(MANAGED_STRATEGY_CODES)
    assert len(list_blog_channel_strategies(con, active_only=False)) == 8
    assert len(list_managed_blog_channel_strategies(con, active_only=False)) == 4
    assert con.execute(
        """
        SELECT profile_name, platform, strategy_code
        FROM blog_profiles p
        JOIN blog_profile_strategies s USING (blog_profile_id)
        WHERE strategy_code IN ('tistory_life', 'tistory_tech',
                                'naver_trend', 'blogger_global')
        ORDER BY strategy_code
        """
    ).fetchall() == sorted(
        [
            (name, platform, code)
            for _profile_id, name, platform, code in legacy_rows
        ],
        key=lambda item: item[2],
    )


@pytest.mark.parametrize(
    ("draft", "expected_code", "expected_name"),
    [
        (
            {
                "title": "청년 주거 지원금 신청 자격 정리",
                "category": "생활 제도",
                "tags": ["지원금", "주거"],
                "summary": "정부 지원 제도의 신청 조건을 설명합니다.",
            },
            "blogger_life",
            "생활자료",
        ),
        (
            {
                "title": "윈도우 11 앱 설치 오류 해결 방법",
                "category": "PC 오류 해결",
                "tags": ["Windows", "앱"],
                "summary": "설정과 재설치 순서를 단계별로 확인합니다.",
            },
            "blogger_tech",
            "IT 사용법",
        ),
        (
            {
                "title": "부산 전시관 방문 전 예약과 주차 정보",
                "category": "국내 장소",
                "tags": ["부산", "방문", "예약"],
                "summary": "사진과 함께 실제 이용 전에 확인할 정보를 정리합니다.",
            },
            "naver_local",
            "네이버 국내 장소·서비스·경험",
        ),
        (
            {
                "title": "프로야구 순위가 바뀐 이유와 이번 주 경기 일정",
                "category": "스포츠",
                "tags": ["야구", "순위", "일정"],
                "summary": "공식 경기 결과와 기준 시각을 확인해 설명합니다.",
            },
            "blogger_current",
            "요즘 화제",
        ),
    ],
)
def test_recommend_blog_channel_routes_distinct_topics(
    draft,
    expected_code,
    expected_name,
) -> None:
    con = _connection()
    install_default_blog_channels(con)
    strategies = list_managed_blog_channel_strategies(con)

    result = recommend_blog_channel(draft, strategies)

    assert result is not None
    assert result.strategy_code == expected_code
    assert result.profile_name == expected_name
    assert result.blog_profile_id in {
        str(channel["profile_id"])
        for channel in MANAGED_BLOG_CHANNELS
    }
    assert result.reason
    assert result.matched_terms


def test_assignment_persists_recommendation_and_legacy_profile_override() -> None:
    con = _connection()
    install_default_blog_channels(con)
    con.execute(
        """
        INSERT INTO blog_profiles VALUES (
            'legacy_tistory', '기존 티스토리', 'tistory', '',
            'https://www.tistory.com/', 'markdown', '기존', '[]',
            FALSE, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """
    )
    con.execute("INSERT INTO drafts VALUES ('draft_1', '윈도우 앱 오류')")
    recommendation = recommend_blog_channel(
        {
            "title": "윈도우 앱 오류 해결",
            "category": "PC",
            "tags": ["윈도우", "오류"],
        },
        list_managed_blog_channel_strategies(con),
    )
    assert recommendation is not None

    selected_id = save_draft_blog_assignment(
        con,
        draft_id="draft_1",
        recommendation=recommendation,
        selected_blog_profile_id="legacy_tistory",
    )
    assignment = get_draft_blog_assignment(con, "draft_1")

    assert selected_id == "legacy_tistory"
    assert assignment is not None
    assert assignment["recommended_blog_profile_id"] == "blog_blogger_tech"
    assert assignment["selected_blog_profile_id"] == "legacy_tistory"
    assert assignment["selection_source"] == "user_override"
    assert assignment["matched_terms"]


def test_assignment_rejects_unknown_draft_or_inactive_profile() -> None:
    con = _connection()
    install_default_blog_channels(con)
    recommendation = recommend_blog_channel(
        {"title": "청년 지원금 신청", "category": "지원금"},
        list_managed_blog_channel_strategies(con),
    )
    assert recommendation is not None

    with pytest.raises(ValueError, match="초안을 찾을 수 없습니다"):
        save_draft_blog_assignment(
            con,
            draft_id="missing",
            recommendation=recommendation,
        )

    con.execute("INSERT INTO drafts VALUES ('draft_2', '청년 지원금 신청')")
    con.execute(
        "UPDATE blog_profiles SET is_active = FALSE WHERE blog_profile_id = ?",
        [recommendation.blog_profile_id],
    )
    with pytest.raises(ValueError, match="추천 블로그 프로필"):
        save_draft_blog_assignment(
            con,
            draft_id="draft_2",
            recommendation=recommendation,
        )


def test_install_reuses_only_unmanaged_legacy_naver_profile() -> None:
    con = _connection()
    con.execute(
        """
        INSERT INTO blog_profiles VALUES
            ('blog_naver_default', '네이버 블로그', 'naver_blog',
             'https://nid.naver.com/nidlogin.login', 'https://blog.naver.com/',
             'plain_text', '', '[]', TRUE, TRUE,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            ('blog_tistory_default', '티스토리', 'tistory',
             'https://www.tistory.com/auth/login', 'https://www.tistory.com/',
             'markdown', '', '[]', FALSE, TRUE,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
    )

    result = install_default_blog_channels(con)

    assert con.execute("SELECT COUNT(*) FROM blog_profiles").fetchone()[0] == 5
    assert result["profile_ids_by_strategy"]["naver_local"] == "blog_naver_default"
    assert result["profile_ids_by_strategy"]["blogger_life"] == "blog_blogger_life"
    assert result["profile_ids_by_strategy"]["blogger_tech"] == "blog_blogger_tech"
    assert result["profile_ids_by_strategy"]["blogger_current"] == "blog_blogger_current"
    assert set(result["created_profile_ids"]) == {
        "blog_blogger_life",
        "blog_blogger_tech",
        "blog_blogger_current",
    }
    assert con.execute(
        """
        SELECT COUNT(*)
        FROM blog_profile_strategies
        WHERE blog_profile_id = 'blog_tistory_default'
        """
    ).fetchone()[0] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM blog_profile_strategies"
    ).fetchone()[0] == 4
