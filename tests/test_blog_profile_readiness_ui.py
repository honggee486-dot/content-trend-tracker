from __future__ import annotations

from pathlib import Path


def test_readiness_ui_shows_overall_and_platform_counts() -> None:
    source = Path("src/blog_profile_readiness_ui.py").read_text(encoding="utf-8")
    presentation = Path("src/blog_platform_presentation.py").read_text(
        encoding="utf-8"
    )

    assert "연결 준비도" in source
    assert "전체 연결" in source
    assert "BLOG_PLATFORM_PRESENTATION" in source
    for label in ("Blogger", "네이버", "티스토리"):
        assert label in presentation
    assert "미설정" in source
    assert "주소 확인 필요" in source
    assert "외부 접속이나 로그인은 수행하지 않고" in source


def test_curated_profile_settings_renders_summary_and_profile_status() -> None:
    source = Path("src/curated_blog_profile_ui.py").read_text(encoding="utf-8")

    assert "render_blog_profile_readiness_summary" in source
    assert "profiles=sync_result.profiles" in source
    assert "render_profile_readiness_status" in source
    assert source.index("render_blog_profile_readiness_summary") < source.index("st.tabs(")


def test_publish_navigation_blocks_unready_profile_before_open_button() -> None:
    source = Path("src/blog_editor_navigation_ui.py").read_text(encoding="utf-8")

    assert "readiness = render_profile_readiness_status" in source
    assert "if not readiness.is_ready" in source
    assert "readiness.recommended_action" in source
    assert source.index("if not readiness.is_ready") < source.index("st_module.button(")
    assert "설정 → 발행 채널 → 해당 프로필 → 연결 정보 저장" in source
