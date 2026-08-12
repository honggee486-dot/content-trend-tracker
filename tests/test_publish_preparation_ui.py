from __future__ import annotations

from pathlib import Path

import duckdb

from src.publish_preparation_ui import build_publish_preparation_state
from src.services.blog_channel_strategy_service import (
    install_default_blog_channels,
    list_blog_channel_strategies,
)
from src.services.publish_preparation_service import save_publish_preparation


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
            title VARCHAR NOT NULL,
            body_markdown VARCHAR NOT NULL
        )
        """
    )
    con.execute(
        "INSERT INTO drafts VALUES ('draft_1', '윈도우 오류 해결', '원본 본문')"
    )
    install_default_blog_channels(con)
    return con


def _draft() -> dict:
    return {
        "draft_id": "draft_1",
        "title": "윈도우 앱 설치 오류 해결",
        "summary": "설치 오류의 원인과 복구 순서를 설명합니다.",
        "category": "PC 오류 해결",
        "tags": ["윈도우", "오류"],
        "body_markdown": "# 윈도우 앱 설치 오류 해결\n\n## 원인\n\n본문입니다.",
    }


def _profile(con, strategy_code: str) -> dict:
    strategy = next(
        item
        for item in list_blog_channel_strategies(con, active_only=True)
        if item["strategy_code"] == strategy_code
    )
    return strategy


def test_state_builds_default_package_for_selected_channel() -> None:
    con = _connection()
    profile = _profile(con, "blogger_tech")

    state = build_publish_preparation_state(
        con,
        draft=_draft(),
        profile=profile,
    )

    assert state.saved is None
    assert state.strategy is not None
    assert state.strategy["strategy_code"] == "blogger_tech"
    assert state.package.policy.policy_code == "blogger"
    assert state.package.seo_title == _draft()["title"]
    assert len(state.package.image_slots) == 3


def test_state_restores_saved_values_for_same_draft_and_profile() -> None:
    con = _connection()
    profile = _profile(con, "blogger_tech")
    save_publish_preparation(
        con,
        draft=_draft(),
        profile=profile,
        seo_title="저장한 SEO 제목",
        meta_description="저장한 설명",
        focus_keywords=["저장", "키워드"],
        image_slots=[
            {"alt_text": "저장 이미지 1", "note": "메모 1"},
            {"alt_text": "저장 이미지 2", "note": "메모 2"},
            {"alt_text": "저장 이미지 3", "note": "메모 3"},
        ],
    )

    state = build_publish_preparation_state(
        con,
        draft=_draft(),
        profile=profile,
    )

    assert state.saved is not None
    assert state.package.seo_title == "저장한 SEO 제목"
    assert state.package.meta_description == "저장한 설명"
    assert state.package.focus_keywords == ("저장", "키워드")
    assert state.package.image_slots[0]["alt_text"] == "저장 이미지 1"


def test_preparations_are_independent_between_channels() -> None:
    con = _connection()
    blogger_profile = _profile(con, "blogger_tech")
    naver_profile = _profile(con, "naver_local")
    save_publish_preparation(
        con,
        draft=_draft(),
        profile=blogger_profile,
        seo_title="Blogger 전용 제목",
        meta_description="Blogger 설명",
        focus_keywords=["Blogger"],
        image_slots=None,
    )

    blogger_state = build_publish_preparation_state(
        con,
        draft=_draft(),
        profile=blogger_profile,
    )
    naver_state = build_publish_preparation_state(
        con,
        draft=_draft(),
        profile=naver_profile,
    )

    assert blogger_state.package.seo_title == "Blogger 전용 제목"
    assert blogger_state.package.policy.policy_code == "blogger"
    assert naver_state.saved is None
    assert naver_state.package.seo_title == _draft()["title"]
    assert naver_state.package.policy.policy_code == "naver_blog"


def test_app_publish_flow_uses_publish_preparation_ui() -> None:
    source = Path("app.py").read_text(encoding="utf-8")

    assert "from src.publish_preparation_ui import render_publish_preparation" in source
    assert "render_publish_preparation(" in source
    assert "build_full_output_text(" not in source
    assert "render_body_for_output(" not in source
