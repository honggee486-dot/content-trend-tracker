from __future__ import annotations

from pathlib import Path


def test_app_no_longer_renders_confusing_login_and_home_buttons() -> None:
    source = Path("app.py").read_text(encoding="utf-8")

    assert "로그인 페이지 열기" not in source
    assert "글쓰기 페이지 열기" not in source
    assert "open_in_regular_chrome" not in source
    assert "render_publish_preparation(" in source


def test_profile_editor_uses_platform_specific_single_connection_field() -> None:
    source = Path("src/curated_blog_profile_ui.py").read_text(encoding="utf-8")

    for label in (
        "Blogger 새 글 편집기 주소",
        "네이버 새 글 편집기 주소",
        "내 티스토리 블로그 주소",
    ):
        assert label in source
    assert "로그인 페이지 주소" not in source
    assert "normalize_platform_editor_url" in source
    assert "build_tistory_write_url" in source
    assert source.count("help=") >= 8


def test_publish_preparation_separates_required_and_optional_flow() -> None:
    source = Path("src/publish_preparation_ui.py").read_text(encoding="utf-8")
    navigation = Path("src/blog_editor_navigation_ui.py").read_text(encoding="utf-8")

    assert "조건부 · 변경한 준비 내용 저장" in source
    assert "필수 ② 전달 데이터 복사" in source
    assert "필수 · {target.action_label}" in navigation
    assert "선택 · 세부 확인과 수동 복사 도구" in source
    assert "선택 · 입력 오류가 있을 때만 호환성 보고서 검사" in source
    assert "선택 · Blogger API로 비공개 초안 만들기" in source


def test_navigation_ui_keeps_login_as_hidden_fallback_with_help() -> None:
    source = Path("src/blog_editor_navigation_ui.py").read_text(encoding="utf-8")

    assert "주소 확인과 로그인 문제 해결" in source
    assert "선택 · 로그인 화면만 열기" in source
    assert source.count("help=") >= 2
