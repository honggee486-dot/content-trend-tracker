from src.ui import (
    normalize_trend_dashboard_action,
    page_header_title,
    trend_dashboard_action_label,
    trend_dashboard_navigation_locked,
)


def test_trend_navigation_locks_for_refresh_rebuild_and_angles() -> None:
    assert trend_dashboard_navigation_locked("refresh") is True
    assert trend_dashboard_navigation_locked("rebuild") is True
    assert trend_dashboard_navigation_locked("angles") is True
    assert trend_dashboard_navigation_locked(" refresh ") is True


def test_trend_navigation_ignores_unknown_or_empty_actions() -> None:
    assert normalize_trend_dashboard_action(None) == ""
    assert normalize_trend_dashboard_action("") == ""
    assert normalize_trend_dashboard_action("unknown") == ""
    assert trend_dashboard_navigation_locked("unknown") is False


def test_trend_action_labels_are_user_readable() -> None:
    assert trend_dashboard_action_label("refresh") == "최신 데이터 수집·분석"
    assert trend_dashboard_action_label("rebuild") == "저장 자료 정리·순위 다시 계산"
    assert trend_dashboard_action_label("angles") == "주제 방향 자동 생성"
    assert trend_dashboard_action_label("unknown") == ""


def test_page_header_titles_follow_sidebar_navigation() -> None:
    expected = {
        "오늘의 트렌드": "오늘의 트렌드",
        "주제·트렌드": "주제·트렌드",
        "AI 요청서": "AI 요청서",
        "AI 결과 가져오기": "AI 결과 가져오기",
        "글 편집": "글 편집",
        "발행 보조": "발행 보조",
        "설정": "설정",
    }
    assert {page: page_header_title(page) for page in expected} == expected
    assert page_header_title("unknown") == "콘텐츠 트렌드 트래커"
