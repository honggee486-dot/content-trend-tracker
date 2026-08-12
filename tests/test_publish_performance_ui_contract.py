from __future__ import annotations

from pathlib import Path

import src.publish_performance_ui as performance_ui


def test_performance_ui_uses_standard_windows_and_manual_reference_only() -> None:
    source = Path(performance_ui.__file__).read_text(encoding="utf-8")

    assert "STANDARD_OBSERVATION_WINDOWS" in source
    assert "발행 후 {value}일" in source
    assert "성과 스냅샷 저장" in source
    assert "발행처별 동일 구간 비교" in source
    assert "추천 발행처 규칙을 자동으로 변경하지 않습니다" in source


def test_publish_history_panel_connects_performance_panel() -> None:
    source = Path("src/publish_history_ui.py").read_text(encoding="utf-8")

    assert "from src.publish_performance_ui import render_publish_performance_panel" in source
    assert "render_publish_performance_panel(" in source
    assert "selected_record=selected" in source
