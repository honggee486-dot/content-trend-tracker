from __future__ import annotations

from pathlib import Path

from src.blog_platform_presentation import BLOG_PLATFORM_PRESENTATION


def test_publish_flow_uses_platform_colors() -> None:
    assert BLOG_PLATFORM_PRESENTATION["blogger"].accent == "#4285F4"
    assert BLOG_PLATFORM_PRESENTATION["naver_blog"].accent == "#03C75A"
    assert BLOG_PLATFORM_PRESENTATION["tistory"].accent == "#F97316"

    source = Path("src/publish_flow_ui.py").read_text(encoding="utf-8")
    assert "발행 순서" in source
    assert "아래 단계와 필수 버튼은 선택한 발행처 색상" in source


def test_required_flow_is_explicit_and_ordered() -> None:
    source = Path("src/publish_flow_ui.py").read_text(encoding="utf-8")

    for title in (
        "준비값 확인",
        "전달 데이터 복사",
        "글쓰기 편집기 열기",
        "확장에서 불러오기·진단·입력",
        "직접 확인 후 저장·발행",
    ):
        assert title in source
    assert source.count('"필수"') >= 4
    assert '"조건부"' in source


def test_navigation_button_uses_platform_accent() -> None:
    source = Path("src/blog_editor_navigation_ui.py").read_text(encoding="utf-8")

    assert "presentation.accent" in source
    assert "필수 · {target.action_label}" in source
