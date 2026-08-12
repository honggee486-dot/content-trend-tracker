from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_usage_log_exposes_terminal_failures_separately() -> None:
    ui_source = (PROJECT_ROOT / "src" / "gemini_stability_ui.py").read_text(
        encoding="utf-8"
    )

    assert "실패 호출 상세" in ui_source
    assert "최근 30일 조회 범위에서 최종 실패로 기록된 호출만 표시합니다." in ui_source
    assert "재시도 중 기록과 재시도 후 성공은 실패에서 제외합니다." in ui_source
    assert 'not in {"success", "success_after_retry", "retrying"}' in ui_source
    assert "for row in failure_rows" in ui_source
    assert "summary.rows[:50]" in ui_source
