from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import duckdb

from src.blog_channel_strategy_ui import (
    MANAGED_CHANNEL_COUNT,
    build_channel_strategy_rows,
    build_publish_channel_state,
)
from src.services.blog_channel_strategy_service import (
    install_default_blog_channels,
    list_managed_blog_channel_strategies,
    recommend_blog_channel,
    save_draft_blog_assignment,
)
from src.services.blog_profile_service import list_blog_profiles


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


def _tech_draft() -> dict[str, object]:
    return {
        "draft_id": "draft_tech",
        "title": "윈도우 11 앱 설치 오류 해결 방법",
        "category": "PC 오류 해결",
        "tags": ["Windows", "앱"],
        "summary": "설정과 재설치 순서를 단계별로 확인합니다.",
        "body_markdown": "윈도우 앱 설치 오류의 원인과 복구 순서를 설명합니다.",
    }


def test_build_publish_state_prefers_recommended_channel() -> None:
    con = _connection()
    install_default_blog_channels(con)
    profiles = list_blog_profiles(con)

    state = build_publish_channel_state(
        con,
        draft=_tech_draft(),
        profiles=profiles,
    )

    assert state.is_ready is True
    assert state.active_strategy_count == MANAGED_CHANNEL_COUNT
    assert state.managed_strategy_count == MANAGED_CHANNEL_COUNT
    assert state.recommendation is not None
    assert state.recommendation.strategy_code == "blogger_tech"
    assert state.recommendation.blog_profile_id == "blog_blogger_tech"
    assert state.selected_profile_id == state.recommendation.blog_profile_id
    assert state.selected_profile_id in state.strategy_by_profile_id


def test_build_publish_state_preserves_saved_legacy_profile_override() -> None:
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
    con.execute("INSERT INTO drafts VALUES ('draft_tech', '윈도우 앱 오류')")
    recommendation = recommend_blog_channel(
        _tech_draft(),
        list_managed_blog_channel_strategies(con),
    )
    assert recommendation is not None
    save_draft_blog_assignment(
        con,
        draft_id="draft_tech",
        recommendation=recommendation,
        selected_blog_profile_id="legacy_tistory",
    )

    state = build_publish_channel_state(
        con,
        draft=_tech_draft(),
        profiles=list_blog_profiles(con),
    )

    assert state.assignment is not None
    assert state.assignment["selection_source"] == "user_override"
    assert state.selected_profile_id == "legacy_tistory"
    assert state.recommendation is not None
    assert state.recommendation.blog_profile_id == "blog_blogger_tech"
    assert "legacy_tistory" not in state.strategy_by_profile_id


def test_inactive_saved_profile_falls_back_to_current_recommendation() -> None:
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
    con.execute("INSERT INTO drafts VALUES ('draft_tech', '윈도우 앱 오류')")
    recommendation = recommend_blog_channel(
        _tech_draft(),
        list_managed_blog_channel_strategies(con),
    )
    assert recommendation is not None
    save_draft_blog_assignment(
        con,
        draft_id="draft_tech",
        recommendation=recommendation,
        selected_blog_profile_id="legacy_tistory",
    )
    con.execute(
        """
        UPDATE blog_profiles
        SET is_active = FALSE
        WHERE blog_profile_id = 'legacy_tistory'
        """
    )

    state = build_publish_channel_state(
        con,
        draft=_tech_draft(),
        profiles=list_blog_profiles(con),
    )

    assert state.active_strategy_count == MANAGED_CHANNEL_COUNT
    assert state.recommendation is not None
    assert state.selected_profile_id == state.recommendation.blog_profile_id
    assert state.selected_profile_id != "legacy_tistory"


def test_strategy_rows_expose_editorial_contract() -> None:
    con = _connection()
    install_default_blog_channels(con)

    rows = build_channel_strategy_rows(
        list_managed_blog_channel_strategies(con, active_only=False)
    )

    assert len(rows) == MANAGED_CHANNEL_COUNT
    assert Counter(row["플랫폼"] for row in rows) == Counter(
        {"blogger": 3, "naver_blog": 1}
    )
    assert all(int(row["목표 분량"]) >= 2000 for row in rows)
    assert all(int(row["기본 이미지"]) == 3 for row in rows)
    assert all(str(row["목표 독자"]).strip() for row in rows)
    assert all(str(row["문체"]).strip() for row in rows)
    assert all(str(row["SEO"]).strip() for row in rows)


def test_settings_copy_describes_fixed_profile_set_and_preservation() -> None:
    source = Path("src/blog_channel_strategy_ui.py").read_text(encoding="utf-8")

    assert "Blogger 3개" in source
    assert "생활자료·IT 사용법·요즘 화제" in source
    assert "티스토리 1개만 표시" in source
    assert "비활성 보관" in source
    assert "4개 기본 발행 채널 준비" in source


def test_app_connects_strategy_settings_and_publish_assignment() -> None:
    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "src.blog_channel_strategy_ui"
        for alias in node.names
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert {
        "render_blog_channel_strategy_settings",
        "render_publish_channel_assignment",
    }.issubset(imported_names)
    assert "render_blog_channel_strategy_settings" in called_names
    assert "render_publish_channel_assignment" in called_names

    publish_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "render_publish"
    )
    publish_calls = {
        node.func.id
        for node in ast.walk(publish_function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "render_publish_channel_assignment" in publish_calls
