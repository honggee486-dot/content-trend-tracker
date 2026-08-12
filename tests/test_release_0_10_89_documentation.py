from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_release_0_10_89_note_and_primary_features_remain_documented() -> None:
    ai_context = _read("AI_CONTEXT.md")
    readme = _read("README.md")
    next_work = _read("docs/NEXT_WORK.md")
    release_note = _read("docs/releases/0.10.89.md")

    assert "블로그 발행 채널·SEO·브라우저 보조·성과 비교" in ai_context
    assert "src/services/blog_channel_strategy_service.py" in ai_context
    assert "src/services/publish_preparation_service.py" in ai_context
    assert "src/services/blogger_draft_service.py" in ai_context
    assert "src/services/publish_performance_service.py" in ai_context

    for phrase in (
        "## 블로그 발행 워크플로",
        "관리형 추천 전략 4개와 실제 활성 프로필 5개",
        "현재 Chrome 탭에만 입력하는 Manifest V3 확장 프로그램",
        "Blogger 공식 API의 비공개 초안 생성",
        "발행 후 7일·30일·90일 수동 성과 스냅샷과 동일 구간 발행처 비교",
        "6. 발행 보조: 4개 추천 전략·5개 활성 프로필 배정·SEO·이미지 3개 슬롯",
    ):
        assert phrase in readme

    assert (
        "## P6. Chrome 확장 기반 네이버·티스토리 에디터 입력 보조 "
        "(구현 완료·실사용 검증 대기)"
    ) in next_work
    assert next_work.count("구현 완료·실사용 검증 대기") == 5

    assert release_note.startswith("# 0.10.89 블로그 발행 워크플로 확장")
    assert "posts.insert(isDraft=true)" in release_note
    assert "추천 발행처 규칙을 자동 변경하지 않습니다" in release_note


def test_release_0_10_89_preserves_manual_publish_safety_boundary() -> None:
    combined = "\n".join(
        (
            _read("README.md"),
            _read("AI_CONTEXT.md"),
            _read("docs/NEXT_WORK.md"),
            _read("docs/releases/0.10.89.md"),
        )
    )

    for phrase in (
        "자동 로그인",
        "쿠키",
        "CAPTCHA",
        "자동 게시",
    ):
        assert phrase in combined

    assert "OAuth 클라이언트·토큰" in combined
    assert "비공개 초안" in combined
    assert "추천 규칙을 자동 변경하지" in combined
