from pathlib import Path

import pytest

from src.database import connect_database, init_database
from src.services.blog_profile_service import (
    archive_blog_profile,
    list_blog_profiles,
    restore_blog_profile,
    save_blog_profile,
)


def test_init_database_migrates_legacy_blog_urls_to_default_profiles(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        profiles = list_blog_profiles(con)

    assert [item["platform"] for item in profiles] == ["naver_blog", "tistory"]
    assert profiles[0]["profile_name"] == "네이버 블로그"
    assert profiles[0]["is_default"] is True
    assert profiles[1]["output_format"] == "markdown"


def test_blog_profile_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    init_database(db_path)
    init_database(db_path)

    with connect_database(db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM blog_profiles").fetchone()[0] == 2


def test_can_add_multiple_profiles_for_same_platform(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        first_id = save_blog_profile(
            con,
            profile_name="개발 티스토리",
            platform="tistory",
            login_url="https://www.tistory.com/auth/login",
            write_url="https://dev-example.tistory.com/manage/newpost/",
            output_format="markdown",
            default_category="개발",
            default_tags="Python, AI, python",
        )
        second_id = save_blog_profile(
            con,
            profile_name="생활 티스토리",
            platform="tistory",
            login_url="https://www.tistory.com/auth/login",
            write_url="https://life-example.tistory.com/manage/newpost/",
            output_format="markdown",
        )
        profiles = list_blog_profiles(con)

    assert first_id != second_id
    added = {item["profile_name"]: item for item in profiles}
    assert added["개발 티스토리"]["default_tags"] == ["Python", "AI"]
    assert added["생활 티스토리"]["platform_label"] == "티스토리"


def test_setting_new_default_unsets_previous_default(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        profile_id = save_blog_profile(
            con,
            profile_name="주력 WordPress",
            platform="wordpress_com",
            login_url="https://wordpress.com/log-in",
            write_url="https://wordpress.com/post",
            output_format="markdown",
            is_default=True,
        )
        profiles = list_blog_profiles(con)

    defaults = [item for item in profiles if item["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["blog_profile_id"] == profile_id


def test_archiving_default_profile_selects_replacement(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        archive_blog_profile(con, "blog_naver_default")
        active_profiles = list_blog_profiles(con)
        all_profiles = list_blog_profiles(con, active_only=False)

    assert len(active_profiles) == 1
    assert active_profiles[0]["platform"] == "tistory"
    assert active_profiles[0]["is_default"] is True
    archived = next(item for item in all_profiles if item["blog_profile_id"] == "blog_naver_default")
    assert archived["is_active"] is False

    with connect_database(db_path) as con:
        restore_blog_profile(con, "blog_naver_default")
        restored = next(
            item
            for item in list_blog_profiles(con)
            if item["blog_profile_id"] == "blog_naver_default"
        )
    assert restored["is_active"] is True


def test_blog_profile_rejects_invalid_or_duplicate_data(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        with pytest.raises(ValueError, match="http 또는 https"):
            save_blog_profile(
                con,
                profile_name="잘못된 프로필",
                platform="custom",
                login_url="",
                write_url="javascript:alert(1)",
                output_format="plain_text",
            )
        with pytest.raises(ValueError, match="같은 이름"):
            save_blog_profile(
                con,
                profile_name="네이버 블로그",
                platform="naver_blog",
                login_url="https://nid.naver.com/nidlogin.login",
                write_url="https://blog.naver.com/",
                output_format="plain_text",
            )
