from __future__ import annotations

import src.program_button_log_ui as button_ui


def test_button_log_records_label_and_key(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(
        button_ui,
        "record_program_event",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    button_ui._record_button(
        "최신 데이터 수집·분석",
        {"key": "refresh_now"},
        source="test.button",
    )

    assert captured == [
        {
            "event_type": "button",
            "status": "clicked",
            "source": "test.button",
            "action": "최신 데이터 수집·분석",
            "detail": "버튼 키 refresh_now",
        }
    ]


def test_non_operational_refresh_buttons_are_not_recorded(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(
        button_ui,
        "record_program_event",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    button_ui._record_button(
        "상태 새로고침",
        {"key": "refresh_clustering_job_status"},
        source="test.button",
    )
    button_ui._record_button(
        "원격 브랜치 새로고침",
        {"key": "web_update_refresh_branches"},
        source="test.button",
    )

    assert captured == []


def test_empty_button_label_is_not_recorded(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(
        button_ui,
        "record_program_event",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    button_ui._record_button("", {}, source="test.button")

    assert captured == []
