from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from src.services import trend_clustering_stale_display_runtime as runtime


def _running_job(*, now: datetime, age_hours: float) -> dict[str, object]:
    return {
        "job_id": "old-running",
        "status": "running",
        "display_status": "stale",
        "completed_batches": 2,
        "max_batches": 20,
        "progress_percent": 10,
        "created_at": now - timedelta(hours=age_hours),
        "heartbeat_at": now - timedelta(hours=age_hours),
    }


def test_stale_display_requires_six_hours_and_known_inactive_locks() -> None:
    now = datetime(2026, 8, 9, 5, 0, 0)
    old = _running_job(now=now, age_hours=7)
    recent = _running_job(now=now, age_hours=5)

    unknown = runtime.apply_clustering_stale_display_policy(
        old,
        {"known": False, "refresh_active": False, "clustering_active": False},
        now=now,
    )
    refresh_active = runtime.apply_clustering_stale_display_policy(
        old,
        {"known": True, "refresh_active": True, "clustering_active": False},
        now=now,
    )
    clustering_active = runtime.apply_clustering_stale_display_policy(
        old,
        {"known": True, "refresh_active": False, "clustering_active": True},
        now=now,
    )
    recent_inactive = runtime.apply_clustering_stale_display_policy(
        recent,
        {"known": True, "refresh_active": False, "clustering_active": False},
        now=now,
    )
    old_inactive = runtime.apply_clustering_stale_display_policy(
        old,
        {"known": True, "refresh_active": False, "clustering_active": False},
        now=now,
    )

    expected_running = "실행 중 · 3/20차 · 10% 완료"
    assert unknown["display_status"] == expected_running
    assert refresh_active["display_status"] == expected_running
    assert clustering_active["display_status"] == expected_running
    assert recent_inactive["display_status"] == expected_running
    assert old_inactive["display_status"] == "stale"
    assert old["display_status"] == "stale"


def test_lock_state_inspection_uses_both_read_only_inspectors(tmp_path: Path) -> None:
    calls: list[tuple[str, Path]] = []

    def refresh_inspector(root):
        calls.append(("refresh", Path(root)))
        return SimpleNamespace(active=False)

    def clustering_inspector(root):
        calls.append(("clustering", Path(root)))
        return SimpleNamespace(active=True)

    state = runtime.inspect_clustering_display_lock_state(
        tmp_path,
        refresh_inspector=refresh_inspector,
        clustering_inspector=clustering_inspector,
    )

    assert state == {
        "known": True,
        "refresh_active": False,
        "clustering_active": True,
    }
    assert calls == [
        ("refresh", tmp_path.resolve()),
        ("clustering", tmp_path.resolve()),
    ]

    def failed_inspector(root):
        raise OSError("잠금 상태 확인 실패")

    unknown = runtime.inspect_clustering_display_lock_state(
        tmp_path,
        refresh_inspector=failed_inspector,
        clustering_inspector=clustering_inspector,
    )
    assert unknown == {
        "known": False,
        "refresh_active": False,
        "clustering_active": False,
    }


def test_installer_keeps_latest_old_running_attempt_as_representative(monkeypatch) -> None:
    now = datetime.now()
    old_running = _running_job(now=now, age_hours=7)
    previous_success = {
        "job_id": "previous-success",
        "status": "success",
        "display_status": "success",
        "created_at": now - timedelta(hours=8),
    }
    module = SimpleNamespace()

    def latest(con, *, batch_limit=20, active_only=False, result_only=False):
        if active_only:
            return None
        if result_only:
            return dict(previous_success)
        return dict(old_running)

    module.get_latest_clustering_job = latest
    module.get_active_clustering_job = (
        lambda con, batch_limit=20: module.get_latest_clustering_job(
            con,
            batch_limit=batch_limit,
            active_only=True,
        )
    )
    module.get_latest_clustering_attempt = (
        lambda con, batch_limit=20: module.get_latest_clustering_job(
            con,
            batch_limit=batch_limit,
        )
    )
    module.get_latest_clustering_result = (
        lambda con, batch_limit=20: module.get_latest_clustering_job(
            con,
            batch_limit=batch_limit,
            result_only=True,
        )
    )
    module.get_representative_clustering_job = (
        lambda con, batch_limit=20: module.get_active_clustering_job(
            con,
            batch_limit=batch_limit,
        )
        or module.get_latest_clustering_result(con, batch_limit=batch_limit)
    )

    monkeypatch.setattr(
        runtime,
        "inspect_clustering_display_lock_state",
        lambda: {
            "known": False,
            "refresh_active": False,
            "clustering_active": False,
        },
    )
    runtime.install_clustering_stale_display_contract(module)
    first_latest = module.get_latest_clustering_job
    first_representative = module.get_representative_clustering_job
    runtime.install_clustering_stale_display_contract(module)

    representative = module.get_representative_clustering_job(object())

    assert module.get_latest_clustering_job is first_latest
    assert module.get_representative_clustering_job is first_representative
    assert representative["job_id"] == "old-running"
    assert representative["status"] == "running"
    assert representative["display_status"].startswith("실행 중")

    monkeypatch.setattr(
        runtime,
        "inspect_clustering_display_lock_state",
        lambda: {
            "known": True,
            "refresh_active": False,
            "clustering_active": False,
        },
    )
    reviewed = module.get_representative_clustering_job(object())
    assert reviewed["job_id"] == "old-running"
    assert reviewed["display_status"] == "stale"


def test_terminal_results_are_not_changed_by_display_policy() -> None:
    result = runtime.apply_clustering_stale_display_policy(
        {"job_id": "done", "status": "success", "display_status": "success"},
        {"known": True, "refresh_active": False, "clustering_active": False},
        now=datetime(2026, 8, 9, 5, 0, 0),
    )

    assert result["status"] == "success"
    assert result["display_status"] == "success"
