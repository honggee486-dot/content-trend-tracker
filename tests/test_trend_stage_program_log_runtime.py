from __future__ import annotations

import pytest

from src.services import trend_stage_program_log_runtime as runtime


def test_precise_stage_logging_splits_first_second_and_ranking(monkeypatch) -> None:
    from src.services import trend_discovery_service as discovery

    captured = []

    def fake_calculation(*args, **kwargs):
        progress = kwargs["progress_callback"]
        progress(0.05, "최근 미처리 원문 1,435개에서 1차 군집 구성 중")
        progress(0.20, "Flash-Lite 2차 군집 194개 요청 중")
        progress(0.85, "2차 군집 결과의 순위 점수 계산 중")
        progress(1.0, "2차 군집 배치 194개 계산 완료")
        return "result"

    monkeypatch.setattr(discovery, "calculate_prepared_trend_rankings", fake_calculation)
    monkeypatch.setattr(
        runtime,
        "record_program_event",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    runtime.install_precise_trend_stage_logging()
    assert discovery.calculate_prepared_trend_rankings(object()) == "result"

    assert [row["action"] for row in captured] == [
        "1차 군집 구성",
        "1차 군집 구성",
        "2차 군집 Gemini 처리",
        "2차 군집 Gemini 처리",
        "통합 순위 점수 계산",
        "통합 순위 점수 계산",
    ]
    assert [row["status"] for row in captured] == [
        "started",
        "completed",
        "started",
        "completed",
        "started",
        "completed",
    ]
    assert captured[0]["item_count"] == 1435
    assert captured[2]["item_count"] == 194
    assert "계산 완료" in captured[-1]["detail"]


def test_precise_stage_logging_marks_current_stage_failed(monkeypatch) -> None:
    from src.services import trend_discovery_service as discovery

    captured = []

    def fake_calculation(*args, **kwargs):
        kwargs["progress_callback"](0.20, "Flash-Lite 2차 군집 200개 요청 중")
        raise RuntimeError("temporary Gemini failure")

    monkeypatch.setattr(discovery, "calculate_prepared_trend_rankings", fake_calculation)
    monkeypatch.setattr(
        runtime,
        "record_program_event",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    runtime.install_precise_trend_stage_logging()
    with pytest.raises(RuntimeError, match="temporary Gemini failure"):
        discovery.calculate_prepared_trend_rankings(object())

    assert captured[-1]["status"] == "failed"
    assert captured[-1]["action"] == "2차 군집 Gemini 처리"
    assert "RuntimeError" in captured[-1]["detail"]
