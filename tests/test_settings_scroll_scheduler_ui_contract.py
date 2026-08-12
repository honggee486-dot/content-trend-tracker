from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_settings_tabs_keep_horizontal_scroll_on_tab_list_only() -> None:
    app = (ROOT / "app.py").read_text(encoding="utf-8")

    assert '[data-testid="stTabs"] {\n            overflow: visible !important;' in app
    assert '[data-testid="stTabs"] [data-baseweb="tab-list"]' in app
    assert "overflow-x: auto;" in app
    assert "overflow-y: hidden;" in app
    assert '[data-testid="stTabs"] [role="tabpanel"]' in app
    assert "padding-bottom: 5.5rem !important" in app
    assert 'width: max-content;\n            min-width: 100%;' not in app


def test_scheduler_page_shows_actual_database_run_separately() -> None:
    app = (ROOT / "app.py").read_text(encoding="utf-8")

    assert "최근 예약 수집 실제 결과" in app
    assert "작업 등록 상태" in app
    assert "get_latest_background_refresh_snapshot" in app
    assert "최근 예약 실행 출처별 상세" in app
    assert "Gemini API 사용 로그의 최근 시각은 바뀌지 않습니다" in app
    assert "_render_latest_background_refresh_status(" in app


def test_scheduler_request_metric_does_not_mix_gemini_into_refresh_total() -> None:
    app = (ROOT / "app.py").read_text(encoding="utf-8")

    assert "수집 출처 행에 기록된 요청 수 합계입니다" in app
    assert "Gemini 주제 방향 행에서 따로 확인하세요" in app
    assert "수집 출처와 Gemini 주제 방향 행에 기록된 요청 수를 합산" not in app
