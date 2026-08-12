from __future__ import annotations

from pathlib import Path

from src import publish_preparation_ui as ui


def test_publish_preparation_ui_builds_chrome_handoff_from_live_package() -> None:
    source = Path(ui.__file__).read_text(encoding="utf-8")

    assert "build_chrome_extension_handoff" in source
    assert "Chrome 편집기 입력 보조" in source
    assert "필수 ② 전달 데이터 복사" in source
    assert "handoff.serialized" in source
    assert "chrome_extension" in source
    assert "10분" in source


def test_publish_preparation_ui_keeps_existing_copy_package_controls() -> None:
    source = Path(ui.__file__).read_text(encoding="utf-8")

    for label in (
        "SEO 제목 복사",
        "메타 설명 복사",
        "핵심 키워드 복사",
        "본문 복사",
        "태그 복사",
        "전체 발행 패키지 복사",
    ):
        assert label in source
