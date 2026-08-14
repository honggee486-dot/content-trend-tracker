from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from src.dashboard_background_refresh_ui import (
    _should_request_full_app_rerun,
    build_trend_dashboard_action_guard,
    install_dashboard_background_refresh,
    launch_dashboard_background_refresh,
    sync_dashboard_background_progress,
)
from src.services.dashboard_refresh_progress_service import DashboardRefreshProgress


class _Process:
    pid = 4321


class _FakeStreamlit:
    def __init__(self) -> None:
        self.session_state = {}
        self.rerun_count = 0

    def rerun(self, *args, **kwargs):
        self.rerun_count += 1
        return "rerun"


def test_launcher_uses_dashboard_script_and_current_python(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "refresh_trends_dashboard.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')", encoding="utf-8")
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return _Process()

    pid = launch_dashboard_background_refresh(
        project_root=tmp_path,
        python_executable=sys.executable,
        popen_factory=fake_popen,
    )

    assert pid == 4321
    assert calls[0][0] == [str(Path(sys.executable).resolve()), str(script)]
    assert calls[0][1]["cwd"] == str(tmp_path.resolve())
    assert calls[0][1]["close_fds"] is True


def test_refresh_rerun_launches_background_and_keeps_progress_state(monkeypatch) -> None:
    fake = _FakeStreamlit()
    fake.session_state.update(
        {
            "trend_dashboard_pending_action": "refresh",
            "trend_dashboard_pending_model": "gemini-test",
            "trend_dashboard_progress": {"value": 10},
        }
    )
    calls = []
    monkeypatch.setattr(
        "src.dashboard_background_refresh_ui.launch_dashboard_background_refresh",
        lambda: calls.append("launch") or 9876,
    )
    monkeypatch.setattr(
        "src.dashboard_background_refresh_ui.start_dashboard_refresh_progress",
        lambda **kwargs: calls.append(("progress", kwargs["pid"])),
    )

    install_dashboard_background_refresh(fake)
    first = fake.rerun
    install_dashboard_background_refresh(fake)
    result = fake.rerun()

    assert fake.rerun is first
    assert result == "rerun"
    assert calls == ["launch", ("progress", 9876)]
    assert fake.rerun_count == 1
    assert "trend_dashboard_pending_action" not in fake.session_state
    assert "trend_dashboard_pending_model" not in fake.session_state
    assert fake.session_state["trend_dashboard_progress"] == {
        "value": 1,
        "message": "백그라운드 수집 프로세스 시작 중",
        "status": "running",
        "pid": 9876,
    }
    assert "백그라운드에서 시작" in fake.session_state["trend_refresh_flash"]["summary"]
    assert "PID 9876" in fake.session_state["trend_refresh_flash"]["summary"]


def test_full_app_rerun_heartbeat_skips_initial_render_then_repeats() -> None:
    state = {}

    assert _should_request_full_app_rerun(state, now=100.0) is False
    assert _should_request_full_app_rerun(state, now=101.9) is False
    assert _should_request_full_app_rerun(state, now=102.1) is True
    assert _should_request_full_app_rerun(state, now=102.2) is False
    assert _should_request_full_app_rerun(state, now=104.2) is True


def test_running_progress_file_is_copied_to_streamlit_session(monkeypatch) -> None:
    fake = _FakeStreamlit()
    progress = DashboardRefreshProgress(
        status="running",
        value=64,
        message="통합 군집 계산 중",
        run_id="run-64",
        pid=2468,
        started_at="2026-08-07T02:22:21",
        updated_at="2026-08-07T02:23:21",
    )
    monkeypatch.setattr(
        "src.dashboard_background_refresh_ui.read_dashboard_refresh_progress",
        lambda: progress,
    )
    monkeypatch.setattr(
        "src.dashboard_background_refresh_ui.is_dashboard_refresh_active",
        lambda p: True,
    )

    sync_dashboard_background_progress(fake)

    assert fake.session_state["trend_dashboard_progress"]["value"] == 64
    assert fake.session_state["trend_dashboard_progress"]["message"] == "통합 군집 계산 중"
    assert fake.session_state["trend_dashboard_progress"]["status"] == "running"


def test_terminal_progress_becomes_flash_and_status_file_is_cleared(monkeypatch) -> None:
    fake = _FakeStreamlit()
    fake.session_state["trend_dashboard_progress"] = {"value": 90}
    fake.session_state["trend_dashboard_background_refresh_last_app_rerun"] = 123.0
    progress = DashboardRefreshProgress(
        status="failure",
        value=90,
        message="군집 저장 실패",
        run_id="run-failed",
        pid=2468,
        started_at="2026-08-07T02:22:21",
        updated_at="2026-08-07T02:23:41",
        finished_at="2026-08-07T02:23:41",
        summary="최신 데이터 수집·분석을 완료하지 못했습니다.",
        error_message="duplicate key",
    )
    cleared = []
    monkeypatch.setattr(
        "src.dashboard_background_refresh_ui.read_dashboard_refresh_progress",
        lambda: progress,
    )
    monkeypatch.setattr(
        "src.dashboard_background_refresh_ui.clear_dashboard_refresh_progress",
        lambda: cleared.append(True),
    )

    sync_dashboard_background_progress(fake)

    assert "trend_dashboard_progress" not in fake.session_state
    assert "trend_dashboard_background_refresh_last_app_rerun" not in fake.session_state
    assert fake.session_state["trend_refresh_flash"]["warnings"] == ["duplicate key"]
    assert cleared == [True]


def test_non_refresh_rerun_keeps_existing_action(monkeypatch) -> None:
    fake = _FakeStreamlit()
    fake.session_state["trend_dashboard_pending_action"] = "rebuild"
    monkeypatch.setattr(
        "src.dashboard_background_refresh_ui.launch_dashboard_background_refresh",
        lambda: (_ for _ in ()).throw(AssertionError("should not launch")),
    )

    install_dashboard_background_refresh(fake)
    fake.rerun()

    assert fake.session_state["trend_dashboard_pending_action"] == "rebuild"
    assert fake.rerun_count == 1


def test_dashboard_refresh_script_keeps_manual_history_lock_progress_and_safe_storage() -> None:
    project_root = Path(__file__).resolve().parents[1]
    text = (project_root / "scripts" / "refresh_trends_dashboard.py").read_text(
        encoding="utf-8"
    )

    assert '_RUN_TYPE = "manual_refresh"' in text
    assert 'launcher="dashboard_background"' in text
    assert "run_with_trend_refresh_lock" in text
    assert "install_post_collection_cleanup_contract()" in text
    assert "install_trend_cluster_runtime_contract()" in text
    assert "finalize_prepared_trend_rankings_safely" in text
    assert "update_dashboard_refresh_progress" in text
    assert 'call_kwargs["progress_callback"] = progress' in text


def test_active_refresh_disables_all_actions_with_refresh_specific_reason() -> None:
    refresh_status = SimpleNamespace(
        active=True,
        owner=SimpleNamespace(
            launcher="dashboard_background",
            started_at="2026-08-06T14:16:50",
            pid=90580,
        ),
    )
    clustering_status = SimpleNamespace(active=False, owner=None)

    guard = build_trend_dashboard_action_guard(
        refresh_status=refresh_status,
        clustering_status=clustering_status,
        active_clustering_job=None,
    )

    assert guard.is_disabled("refresh") is True
    assert guard.is_disabled("rebuild") is True
    assert guard.is_disabled("angles") is True
    assert "최신 데이터 수집이 실행 중" in guard.reason_for("angles")
    assert "주제 방향 생성을 시작하지 않았습니다" in guard.reason_for("angles")
    assert "dashboard_background" in guard.reason_for("angles")
    assert "PID 90580" in guard.reason_for("angles")


def test_active_clustering_disables_rebuild_and_angles_with_distinct_reason() -> None:
    refresh_status = SimpleNamespace(active=False, owner=None)
    clustering_status = SimpleNamespace(
        active=True,
        owner=SimpleNamespace(
            launcher="clustering-job:job-1",
            started_at="2026-08-06T14:24:20",
            pid=12345,
        ),
    )

    guard = build_trend_dashboard_action_guard(
        refresh_status=refresh_status,
        clustering_status=clustering_status,
        active_clustering_job=None,
    )

    assert guard.is_disabled("refresh") is False
    assert guard.is_disabled("rebuild") is True
    assert guard.is_disabled("angles") is True
    assert "기존 2차 군집 작업이 실행 중" in guard.reason_for("rebuild")
    assert "새 군집 작업을 시작하지 않았습니다" in guard.reason_for("rebuild")
    assert "최신 데이터 수집이 실행 중" not in guard.reason_for("rebuild")
    assert guard.notices()[0].startswith(
        "현재 다른 군집 처리 작업이 실행 중입니다."
    )


def test_active_clustering_job_without_lock_still_disables_related_actions() -> None:
    guard = build_trend_dashboard_action_guard(
        refresh_status=SimpleNamespace(active=False, owner=None),
        clustering_status=SimpleNamespace(active=False, owner=None),
        active_clustering_job={
            "job_id": "job-2",
            "status": "running",
            "launcher": "refresh_trends_dashboard",
            "started_at": "2026-08-06T15:00:00",
        },
    )

    assert guard.is_disabled("refresh") is False
    assert guard.is_disabled("rebuild") is True
    assert guard.is_disabled("angles") is True
    assert guard.notices()[0].startswith(
        "현재 다른 군집 처리 작업이 실행 중입니다."
    )
