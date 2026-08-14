from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from src import dashboard_operation_status_ui as status_ui
from src.dashboard_background_refresh_ui import (
    sync_dashboard_background_progress,
)
from src.services.dashboard_refresh_progress_service import (
    DashboardRefreshProgress,
    clear_dashboard_refresh_progress,
    finish_dashboard_refresh_progress,
    is_dashboard_refresh_active,
    read_dashboard_refresh_progress,
    start_dashboard_refresh_progress,
    update_dashboard_refresh_progress,
)
from src.services.trend_refresh_lock_service import (
    TrendRefreshLockOwner,
    TrendRefreshLockStatus,
)


class _FakeStreamlit:
    def __init__(self) -> None:
        self.session_state = {}


def test_is_dashboard_refresh_active_returns_true_for_live_process_and_active_lock(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, 23, 30, 0)
    progress = DashboardRefreshProgress(
        status="running",
        value=50,
        message="수집 중",
        run_id="run-100",
        pid=5555,
        started_at="2026-08-14T23:25:00",
        updated_at="2026-08-14T23:26:00",
    )
    active_lock = TrendRefreshLockStatus(
        exists=True,
        active=True,
        owner=TrendRefreshLockOwner(
            pid=5555,
            started_at="2026-08-14T23:25:00",
            launcher="dashboard_background",
            token="token-1",
            heartbeat_at="2026-08-14T23:29:00",
            lease_seconds=180,
        ),
    )

    is_active = is_dashboard_refresh_active(
        progress,
        project_root=tmp_path,
        is_process_alive=lambda pid: pid == 5555,
        refresh_lock_inspector=lambda root, **kwargs: active_lock,
        now=now,
    )

    assert is_active is True


def test_is_dashboard_refresh_active_returns_false_when_pid_is_dead(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, 23, 35, 0)
    progress = DashboardRefreshProgress(
        status="running",
        value=82,
        message="Flash-Lite 2차 군집 1,136개 요청 중",
        run_id="run-stale",
        pid=72488,
        started_at="2026-08-14T23:14:34",
        updated_at="2026-08-14T23:15:13",
    )
    # Lock이 디스크에 남아 있어도 lease 만료 / PID 사망으로 active=False인 상태
    inactive_lock = TrendRefreshLockStatus(
        exists=True,
        active=False,
        owner=TrendRefreshLockOwner(
            pid=72488,
            started_at="2026-08-14T23:14:34",
            launcher="dashboard_background",
            token="token-old",
            heartbeat_at="2026-08-14T23:22:34",
            lease_seconds=180,
        ),
    )

    is_active = is_dashboard_refresh_active(
        progress,
        project_root=tmp_path,
        is_process_alive=lambda pid: False,  # PID 72488 is dead
        refresh_lock_inspector=lambda root, **kwargs: inactive_lock,
        now=now,
    )

    assert is_active is False


def test_is_dashboard_refresh_active_returns_false_on_pid_reuse_without_active_lock(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, 23, 35, 0)
    progress = DashboardRefreshProgress(
        status="running",
        value=60,
        message="1차 군집 구성 중",
        run_id="run-old",
        pid=9999,
        started_at="2026-08-14T23:10:00",
        updated_at="2026-08-14T23:11:00",
    )
    # PID 9999가 OS에 살아있으나 다른 프로세스가 재사용한 경우 (lock 비활성, 시작 30초 초과)
    inactive_lock = TrendRefreshLockStatus(exists=False, active=False, owner=None)

    is_active = is_dashboard_refresh_active(
        progress,
        project_root=tmp_path,
        is_process_alive=lambda pid: pid == 9999,
        refresh_lock_inspector=lambda root, **kwargs: inactive_lock,
        now=now,
    )

    assert is_active is False


def test_is_dashboard_refresh_active_graces_startup_window(tmp_path: Path) -> None:
    now = datetime(2026, 8, 14, 23, 30, 10)
    progress = DashboardRefreshProgress(
        status="running",
        value=1,
        message="백그라운드 수집 프로세스 시작 중",
        run_id="",
        pid=8888,
        started_at="2026-08-14T23:30:00",  # 10초 전 시작
        updated_at="2026-08-14T23:30:00",
    )
    # 시작 직후라 lock을 잡기 직전
    inactive_lock = TrendRefreshLockStatus(exists=False, active=False, owner=None)

    is_active = is_dashboard_refresh_active(
        progress,
        project_root=tmp_path,
        is_process_alive=lambda pid: pid == 8888,
        refresh_lock_inspector=lambda root, **kwargs: inactive_lock,
        now=now,
    )

    assert is_active is True


def test_sync_dashboard_background_progress_recovers_stale_running_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    progress_file = tmp_path / "progress.json"
    start_dashboard_refresh_progress(
        pid=72488,
        run_id="run-stale-1",
        message="수집 중",
        path=progress_file,
        now=datetime(2026, 8, 14, 23, 14, 34),
    )
    update_dashboard_refresh_progress(
        82,
        "Flash-Lite 2차 군집 1,136개 요청 중",
        path=progress_file,
        now=datetime(2026, 8, 14, 23, 15, 13),
    )

    monkeypatch.setattr(
        "src.dashboard_background_refresh_ui.read_dashboard_refresh_progress",
        lambda: read_dashboard_refresh_progress(progress_file),
    )
    monkeypatch.setattr(
        "src.dashboard_background_refresh_ui.finish_dashboard_refresh_progress",
        lambda **kwargs: finish_dashboard_refresh_progress(path=progress_file, **kwargs),
    )
    monkeypatch.setattr(
        "src.dashboard_background_refresh_ui.clear_dashboard_refresh_progress",
        lambda: clear_dashboard_refresh_progress(progress_file),
    )

    fake = _FakeStreamlit()
    fake.session_state["trend_dashboard_progress"] = {"value": 82}

    # stale 상태 (is_refresh_active_fn = False)
    sync_dashboard_background_progress(fake, is_refresh_active_fn=lambda p: False)

    assert "trend_dashboard_progress" not in fake.session_state
    flash = fake.session_state.get("trend_refresh_flash", {})
    assert "수집 작업 중단" in flash.get("summary", "")
    assert any("예기치 않게 중단" in w for w in flash.get("warnings", []))
    assert any("72488" in w for w in flash.get("warnings", []))
    assert read_dashboard_refresh_progress(progress_file) is None


def test_sync_dashboard_background_progress_keeps_active_running_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    progress_file = tmp_path / "progress.json"
    start_dashboard_refresh_progress(
        pid=5555,
        run_id="run-live-1",
        message="수집 중",
        path=progress_file,
        now=datetime(2026, 8, 14, 23, 30, 0),
    )

    monkeypatch.setattr(
        "src.dashboard_background_refresh_ui.read_dashboard_refresh_progress",
        lambda: read_dashboard_refresh_progress(progress_file),
    )

    fake = _FakeStreamlit()

    # 정상 활성 상태 (is_refresh_active_fn = True)
    sync_dashboard_background_progress(fake, is_refresh_active_fn=lambda p: True)

    assert fake.session_state["trend_dashboard_progress"]["value"] == 1
    assert fake.session_state["trend_dashboard_progress"]["status"] == "running"
    assert fake.session_state["trend_dashboard_progress"]["pid"] == 5555


def test_action_button_guard_does_not_disable_when_stale(monkeypatch) -> None:
    from streamlit.delta_generator import DeltaGenerator

    captured = []

    def fake_button(self, label, *args, **kwargs):
        captured.append((label, kwargs))
        return False

    stale_progress = DashboardRefreshProgress(
        status="running",
        value=82,
        message="Flash-Lite 2차 군집 1,136개 요청 중",
        run_id="run-stale",
        pid=72488,
        started_at="2026-08-14T23:14:34",
        updated_at="2026-08-14T23:15:13",
    )

    monkeypatch.setattr(DeltaGenerator, "button", fake_button)
    monkeypatch.setattr(
        status_ui,
        "read_dashboard_refresh_progress",
        lambda: stale_progress,
    )
    # is_dashboard_refresh_active returns False (stale)
    monkeypatch.setattr(
        status_ui,
        "is_dashboard_refresh_active",
        lambda p: False,
    )
    st_module = SimpleNamespace(session_state={})

    status_ui._install_action_button_guard(st_module)
    DeltaGenerator.button(object(), "최신 데이터 수집·분석")
    DeltaGenerator.button(object(), "저장 자료 정리·순위 다시 계산")

    assert len(captured) == 2
    assert "disabled" not in captured[0][1]
    assert "disabled" not in captured[1][1]


def test_render_lightweight_refresh_dashboard_if_active_triggers_and_stops_on_live_work(
    monkeypatch,
) -> None:
    from src.dashboard_background_refresh_ui import (
        render_lightweight_refresh_dashboard_if_active,
    )

    live_progress = DashboardRefreshProgress(
        status="running",
        value=35,
        message="3/7 위키백과 수집 중",
        run_id="run-live-2",
        pid=12345,
        started_at="2026-08-14T23:30:00",
        updated_at="2026-08-14T23:30:10",
    )
    monkeypatch.setattr(
        "src.dashboard_background_refresh_ui.read_dashboard_refresh_progress",
        lambda: live_progress,
    )

    calls = []
    fake_st = SimpleNamespace(
        title=lambda text: calls.append(("title", text)),
        caption=lambda text: calls.append(("caption", text)),
        progress=lambda val, text=None: calls.append(("progress", val, text)),
        dataframe=lambda df, **kwargs: calls.append(("dataframe", len(df))),
        info=lambda text: calls.append(("info", text)),
        fragment=lambda run_every=None: lambda fn: calls.append(("fragment_installed", run_every)) or fn,
        stop=lambda: calls.append("stop"),
        rerun=lambda *args, **kwargs: calls.append(("rerun", kwargs)),
        session_state={},
    )

    active = render_lightweight_refresh_dashboard_if_active(
        fake_st,
        is_refresh_active_fn=lambda p: True,
    )

    assert active is True
    assert "stop" in calls
    assert any(item[0] == "title" for item in calls)


def test_render_lightweight_refresh_dashboard_if_active_bypasses_when_inactive_or_stale(
    monkeypatch,
) -> None:
    from src.dashboard_background_refresh_ui import (
        render_lightweight_refresh_dashboard_if_active,
    )

    stale_progress = DashboardRefreshProgress(
        status="running",
        value=82,
        message="2차 군집 요청 중",
        run_id="run-stale-2",
        pid=72488,
        started_at="2026-08-14T23:14:34",
        updated_at="2026-08-14T23:15:13",
    )
    monkeypatch.setattr(
        "src.dashboard_background_refresh_ui.read_dashboard_refresh_progress",
        lambda: stale_progress,
    )

    calls = []
    fake_st = SimpleNamespace(
        title=lambda text: calls.append(("title", text)),
        stop=lambda: calls.append("stop"),
        session_state={},
    )

    # stale 상태 (is_refresh_active_fn = False)
    active = render_lightweight_refresh_dashboard_if_active(
        fake_st,
        is_refresh_active_fn=lambda p: False,
    )

    assert active is False
    assert "stop" not in calls


def test_lightweight_fragment_triggers_full_rerun_on_completion(monkeypatch) -> None:
    from src.dashboard_background_refresh_ui import (
        render_lightweight_refresh_dashboard,
    )

    finished_progress = DashboardRefreshProgress(
        status="success",
        value=100,
        message="최신 데이터 수집·분석 완료",
        run_id="run-done",
        pid=12345,
        started_at="2026-08-14T23:30:00",
        updated_at="2026-08-14T23:32:00",
        finished_at="2026-08-14T23:32:00",
        summary="수집 완료",
    )
    monkeypatch.setattr(
        "src.dashboard_background_refresh_ui.read_dashboard_refresh_progress",
        lambda: finished_progress,
    )

    calls = []
    fake_st = SimpleNamespace(
        title=lambda text: calls.append(("title", text)),
        caption=lambda text: calls.append(("caption", text)),
        fragment=lambda run_every=None: lambda fn: (lambda *args, **kwargs: fn(*args, **kwargs)),
        rerun=lambda *args, **kwargs: calls.append(("rerun", kwargs)),
        session_state={},
    )

    render_lightweight_refresh_dashboard(fake_st)

    assert any(item[0] == "rerun" for item in calls)
    assert fake_st.session_state.get("trend_refresh_flash", {}).get("summary") == "수집 완료"
