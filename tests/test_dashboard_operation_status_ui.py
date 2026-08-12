from __future__ import annotations

from types import SimpleNamespace

from src import dashboard_operation_status_ui as ui
from src.services.dashboard_refresh_progress_service import DashboardRefreshProgress


def _progress(*, status: str = "running") -> DashboardRefreshProgress:
    return DashboardRefreshProgress(
        status=status,
        value=81 if status == "running" else 100,
        message="최근 미처리 원문 1,435개에서 1차 군집 구성 중",
        run_id="run-1",
        pid=1234,
        started_at="2026-08-07T02:00:00",
        updated_at="2026-08-07T02:01:10",
        finished_at="" if status == "running" else "2026-08-07T02:02:00",
        steps=(
            {
                "time": "2026-08-07T02:00:00",
                "elapsed_seconds": 0.0,
                "value": 1,
                "message": "수집 준비 중",
                "status": "started",
            },
            {
                "time": "2026-08-07T02:01:10",
                "elapsed_seconds": 70.0,
                "value": 81,
                "message": "최근 미처리 원문 1,435개에서 1차 군집 구성 중",
                "status": "running",
            },
        ),
    )


def test_refresh_history_is_newest_first_with_elapsed_progress_and_message() -> None:
    rows = ui._refresh_rows(_progress())

    assert rows[0] == {
        "시간": "2026-08-07 02:01:10",
        "경과": "1분 10.00초",
        "진행률": "81%",
        "내용": "최근 미처리 원문 1,435개에서 1차 군집 구성 중",
    }
    assert rows[1]["내용"] == "수집 준비 중"


def test_panel_is_expanded_only_while_work_is_active() -> None:
    active_label, active_expanded = ui._panel_label(_progress(), None, "")
    finished_label, finished_expanded = ui._panel_label(
        _progress(status="success"),
        None,
        "",
    )

    assert active_expanded is True
    assert "진행 중 81%" in active_label
    assert "1차 군집 구성 중" in active_label
    assert finished_expanded is False
    assert finished_label.startswith("최근 실행 시도 · 2026-08-07 02:02:00")


def test_active_second_stage_clustering_uses_precise_label() -> None:
    label, expanded = ui._panel_label(
        None,
        {
            "status": "running",
            "progress_percent": 42,
            "current_stage_label": "2차 주제 군집 · 제목 기준 · Gemini 호출 중",
        },
        "",
    )

    assert expanded is True
    assert "2차 군집 진행 중 42%" in label
    assert "Gemini 호출 중" in label


def test_action_buttons_are_disabled_while_refresh_progress_is_active(
    monkeypatch,
) -> None:
    from streamlit.delta_generator import DeltaGenerator

    captured = []

    def fake_button(self, label, *args, **kwargs):
        captured.append((label, kwargs))
        return False

    monkeypatch.setattr(DeltaGenerator, "button", fake_button)
    monkeypatch.setattr(ui, "read_dashboard_refresh_progress", lambda: _progress())
    st_module = SimpleNamespace(session_state={})

    ui._install_action_button_guard(st_module)
    DeltaGenerator.button(object(), "최신 데이터 수집·분석")
    DeltaGenerator.button(object(), "주제 방향 자동 생성")
    DeltaGenerator.button(object(), "상태 새로고침")

    assert captured[0][1]["disabled"] is True
    assert "진행률 81%" in captured[0][1]["help"]
    assert captured[1][1]["disabled"] is True
    assert "disabled" not in captured[2][1]
