from __future__ import annotations

import duckdb

from src.services.publish_preparation_service import (
    build_default_publish_preparation,
    build_publish_copy_package,
    ensure_publish_preparation_schema,
    get_publish_output_policy,
    get_publish_preparation,
    save_publish_preparation,
)


def _connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
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
        """
        CREATE TABLE blog_profiles (
            blog_profile_id VARCHAR PRIMARY KEY,
            profile_name VARCHAR NOT NULL,
            platform VARCHAR NOT NULL,
            output_format VARCHAR NOT NULL,
            default_tags_json VARCHAR NOT NULL DEFAULT '[]',
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """
    )
    con.execute(
        "INSERT INTO drafts VALUES ('draft_1', '윈도우 앱 오류 해결', '본문 원본')"
    )
    con.execute(
        """
        INSERT INTO blog_profiles VALUES
            ('tistory_tech', '티스토리 기술', 'tistory', 'markdown', '[]', TRUE),
            ('naver_life', '네이버 생활', 'naver_blog', 'plain_text', '[]', TRUE)
        """
    )
    return con


def _draft() -> dict:
    return {
        "draft_id": "draft_1",
        "title": "윈도우 11 앱 설치 오류 해결 방법",
        "summary": "설치 오류의 원인과 복구 순서를 단계별로 확인합니다.",
        "category": "PC 오류 해결",
        "tags": ["Windows", "오류", "앱"],
        "body_markdown": "# 윈도우 11 앱 설치 오류 해결 방법\n\n## 원인\n\n본문입니다.",
    }


def _profile(profile_id: str = "tistory_tech") -> dict:
    return {
        "blog_profile_id": profile_id,
        "profile_name": "티스토리 기술" if profile_id == "tistory_tech" else "네이버 생활",
        "platform": "tistory" if profile_id == "tistory_tech" else "naver_blog",
        "output_format": "markdown" if profile_id == "tistory_tech" else "plain_text",
        "default_tags": ["사용법", "오류"],
    }


def _strategy() -> dict:
    return {
        "strategy_code": "tistory_tech",
        "routing_terms": ["윈도우", "앱", "오류", "설치", "브라우저"],
    }


def test_policy_uses_strategy_before_platform() -> None:
    policy = get_publish_output_policy(
        platform="naver_blog",
        strategy_code="blogger_global",
    )

    assert policy.policy_code == "blogger_global"
    assert policy.seo_title_max_length == 60
    assert policy.meta_description_max_length == 155


def test_default_preparation_has_three_image_slots_and_keywords() -> None:
    preparation = build_default_publish_preparation(
        _draft(),
        _profile(),
        _strategy(),
    )

    assert preparation["seo_title"] == _draft()["title"]
    assert preparation["meta_description"].startswith("설치 오류의 원인")
    assert preparation["focus_keywords"][:3] == ["Windows", "오류", "앱"]
    assert len(preparation["image_slots"]) == 3
    assert [item["slot_number"] for item in preparation["image_slots"]] == [1, 2, 3]
    assert preparation["image_slots"][0]["role"] == "대표 이미지"


def test_save_is_idempotent_and_keeps_profile_specific_preparations() -> None:
    con = _connection()
    draft = _draft()

    save_publish_preparation(
        con,
        draft=draft,
        profile=_profile(),
        seo_title="티스토리용 제목",
        meta_description="티스토리 설명",
        focus_keywords=["윈도우", "오류", "윈도우"],
        image_slots=[{"alt_text": "대표 대체텍스트", "note": "직접 만든 이미지"}],
    )
    save_publish_preparation(
        con,
        draft=draft,
        profile=_profile("naver_life"),
        seo_title="네이버용 제목",
        meta_description="네이버 설명",
        focus_keywords=["생활", "오류"],
        image_slots=None,
    )
    save_publish_preparation(
        con,
        draft=draft,
        profile=_profile(),
        seo_title="티스토리용 제목 수정",
        meta_description="수정 설명",
        focus_keywords=["윈도우", "앱"],
        image_slots=None,
    )

    assert con.execute("SELECT COUNT(*) FROM draft_publish_preparations").fetchone()[0] == 2
    tistory = get_publish_preparation(
        con,
        draft_id="draft_1",
        blog_profile_id="tistory_tech",
    )
    naver = get_publish_preparation(
        con,
        draft_id="draft_1",
        blog_profile_id="naver_life",
    )
    assert tistory is not None
    assert naver is not None
    assert tistory["seo_title"] == "티스토리용 제목 수정"
    assert tistory["focus_keywords"] == ["윈도우", "앱"]
    assert len(tistory["image_slots"]) == 3
    assert naver["seo_title"] == "네이버용 제목"
    assert con.execute("SELECT title, body_markdown FROM drafts WHERE draft_id = 'draft_1'").fetchone() == (
        "윈도우 앱 오류 해결",
        "본문 원본",
    )


def test_copy_package_contains_seo_image_guide_body_and_limited_tags() -> None:
    preparation = {
        "seo_title": "윈도우 앱 설치 오류 해결",
        "meta_description": "윈도우 앱 설치 오류의 원인과 해결 단계를 정리합니다.",
        "focus_keywords": ["윈도우", "설치", "오류", "복구", "설정", "앱"],
        "image_slots": [
            {"alt_text": "윈도우 오류 화면", "note": "오류 메시지 캡처"},
            {"alt_text": "설정 순서", "note": "단계별 도표"},
            {"alt_text": "복구 요약", "note": "체크리스트"},
        ],
    }

    package = build_publish_copy_package(
        draft=_draft(),
        profile=_profile(),
        strategy=_strategy(),
        preparation=preparation,
    )

    assert package.output_body.startswith("## 원인")
    assert len(package.image_slots) == 3
    assert "[이미지 1 · 대표 이미지]" in package.image_guide_text
    assert "[SEO 제목]" in package.full_output_text
    assert "[메타 설명]" in package.full_output_text
    assert "[이미지 3개 배치 안내]" in package.full_output_text
    assert "[본문]" in package.full_output_text
    assert len(package.output_tags) <= package.policy.recommended_tag_count
    assert len(package.output_tags) == len(set(tag.casefold() for tag in package.output_tags))


def test_copy_package_warns_when_program_recommendations_are_exceeded() -> None:
    package = build_publish_copy_package(
        draft=_draft(),
        profile=_profile("naver_life"),
        strategy={"strategy_code": "naver_trend", "routing_terms": []},
        preparation={
            "seo_title": "가" * 41,
            "meta_description": "나" * 121,
            "focus_keywords": ["키워드"],
            "image_slots": [],
        },
    )

    assert len(package.warnings) == 2
    assert "SEO 제목" in package.warnings[0]
    assert "메타 설명" in package.warnings[1]


def test_schema_creation_is_idempotent() -> None:
    con = _connection()

    ensure_publish_preparation_schema(con)
    ensure_publish_preparation_schema(con)

    assert con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'draft_publish_preparations'"
    ).fetchone()[0] == 1
