from __future__ import annotations

import duckdb

from src.services.blog_channel_strategy_service import (
    ensure_blog_channel_strategy_schema,
)
from src.services.curated_blog_profile_service import (
    synchronize_curated_blog_profiles,
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
    return con


def _insert_profile(
    con: duckdb.DuckDBPyConnection,
    profile_id: str,
    name: str,
    platform: str,
    write_url: str,
    *,
    is_default: bool = False,
) -> None:
    con.execute(
        """
        INSERT INTO blog_profiles VALUES (
            ?, ?, ?, '', ?, ?, '', '[]', ?, TRUE,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """,
        [
            profile_id,
            name,
            platform,
            write_url,
            "markdown" if platform == "tistory" else "plain_text",
            is_default,
        ],
    )


def test_sync_keeps_only_three_blogger_one_naver_one_tistory_active() -> None:
    con = _connection()
    ensure_blog_channel_strategy_schema(con)
    _insert_profile(
        con,
        "blog_naver_default",
        "기존 네이버",
        "naver_blog",
        "https://blog.naver.com/connected-user",
        is_default=True,
    )
    con.execute(
        """
        INSERT INTO blog_profile_strategies(
            blog_profile_id, strategy_code, allowed_categories_json,
            excluded_categories_json, routing_terms_json, target_audience,
            writing_tone, target_length, title_rules_json, seo_strategy,
            default_image_count, created_at, updated_at
        ) VALUES (
            'blog_naver_default', 'naver_trend', '[]', '[]', '[]',
            '', '', 2000, '[]', '', 3, CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        )
        """
    )
    _insert_profile(
        con,
        "blog_tistory_default",
        "기본 티스토리",
        "tistory",
        "https://www.tistory.com/",
    )
    _insert_profile(
        con,
        "connected_tistory",
        "실제 티스토리",
        "tistory",
        "https://example.tistory.com/manage/newpost/",
    )
    _insert_profile(
        con,
        "legacy_blogger",
        "이전 Blogger",
        "blogger",
        "https://www.blogger.com/",
    )
    _insert_profile(
        con,
        "custom_profile",
        "기타 프로필",
        "custom",
        "https://example.com/write",
    )

    result = synchronize_curated_blog_profiles(con)

    assert len(result.profiles) == 5
    platforms = [str(item["platform"]) for item in result.profiles]
    assert platforms.count("blogger") == 3
    assert platforms.count("naver_blog") == 1
    assert platforms.count("tistory") == 1
    assert result.primary_tistory_profile_id == "connected_tistory"
    assert result.migrated_naver_from_profile_id == "blog_naver_default"
    assert con.execute(
        """
        SELECT write_url
        FROM blog_profiles
        WHERE blog_profile_id = 'blog_naver_local'
        """
    ).fetchone()[0] == "https://blog.naver.com/connected-user"
    assert con.execute(
        """
        SELECT COUNT(*)
        FROM blog_profiles
        WHERE is_active = TRUE
        """
    ).fetchone()[0] == 5
    assert con.execute(
        """
        SELECT is_active
        FROM blog_profiles
        WHERE blog_profile_id = 'custom_profile'
        """
    ).fetchone()[0] is False
    assert con.execute(
        "SELECT COUNT(*) FROM blog_profiles"
    ).fetchone()[0] > 5
    assert con.execute(
        """
        SELECT COUNT(*)
        FROM blog_profiles
        WHERE is_default = TRUE AND is_active = TRUE
        """
    ).fetchone()[0] == 1


def test_sync_is_idempotent() -> None:
    con = _connection()
    _insert_profile(
        con,
        "blog_naver_default",
        "네이버 블로그",
        "naver_blog",
        "https://blog.naver.com/",
        is_default=True,
    )
    _insert_profile(
        con,
        "blog_tistory_default",
        "티스토리",
        "tistory",
        "https://www.tistory.com/",
    )

    first = synchronize_curated_blog_profiles(con)
    second = synchronize_curated_blog_profiles(con)

    assert len(first.profiles) == 5
    assert first.profile_ids == second.profile_ids
    assert second.archived_profile_ids == ()
    assert second.restored_profile_ids == ()
    assert con.execute(
        "SELECT COUNT(*) FROM blog_profiles WHERE is_active = TRUE"
    ).fetchone()[0] == 5
