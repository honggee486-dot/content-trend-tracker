from __future__ import annotations

from pathlib import Path

from src.config import (
    BACKGROUND_TOPIC_ANGLE_TIMEOUT_SECONDS,
    get_gemini_config,
)


def test_topic_angle_timeout_defaults_to_ten_minutes(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_TOPIC_ANGLE_TIMEOUT_SECONDS", raising=False)

    config = get_gemini_config()

    assert BACKGROUND_TOPIC_ANGLE_TIMEOUT_SECONDS == 600
    assert config.topic_angle_timeout_seconds == 600


def test_legacy_six_minute_timeout_is_upgraded_but_custom_value_is_kept(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GEMINI_TOPIC_ANGLE_TIMEOUT_SECONDS", "360")
    assert get_gemini_config().topic_angle_timeout_seconds == 600

    monkeypatch.setenv("GEMINI_TOPIC_ANGLE_TIMEOUT_SECONDS", "720")
    assert get_gemini_config().topic_angle_timeout_seconds == 720


def test_ai_request_ready_queue_is_grouped_in_collapsed_expander() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "content_work_queue_ui.py").read_text(
        encoding="utf-8"
    )

    assert 'str(row.get("stage") or "") == "request_ready"' in source
    assert 'with st_module.expander(label, expanded=False)' in source
    assert 'label = f"AI 요청서 준비 {total_request_ready:,}개 보기"' in source
    assert "첫 화면을 간결하게 유지하기 위해 AI 요청서 준비 작업은 기본 접힘" in source
    assert "_render_request_ready_evidence" in source


def test_cluster_case_diagnostic_runs_only_after_explicit_button() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "cluster_case_diagnostic_ui.py").read_text(
        encoding="utf-8"
    )

    assert '"사례 분석 실행"' in source
    assert "if run_clicked:" in source
    assert "st_module.session_state[_REPORT_KEY] = analyze_cluster_cases" in source
    assert "설정 화면을 열기만 해서는 대용량 비교를 실행하지 않습니다" in source
