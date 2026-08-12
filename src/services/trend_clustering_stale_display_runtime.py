from __future__ import annotations

from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from src.config import PROJECT_ROOT
from src.services.trend_clustering_lock_service import inspect_trend_clustering_lock
from src.services.trend_refresh_lock_service import inspect_trend_refresh_lock


CLUSTERING_DISPLAY_REVIEW_AFTER_HOURS = 6
_ACTIVE_STATUSES = frozenset({"queued", "running"})


def inspect_clustering_display_lock_state(
    project_root: str | Path = PROJECT_ROOT,
    *,
    refresh_inspector: Callable[..., Any] = inspect_trend_refresh_lock,
    clustering_inspector: Callable[..., Any] = inspect_trend_clustering_lock,
) -> dict[str, bool]:
    """수집·군집 잠금을 정리하지 않고 화면 판정에 필요한 활성 상태만 읽습니다."""
    root = Path(project_root).resolve()
    try:
        refresh_status = refresh_inspector(root)
        clustering_status = clustering_inspector(root)
    except Exception:
        return {
            "known": False,
            "refresh_active": False,
            "clustering_active": False,
        }
    return {
        "known": True,
        "refresh_active": bool(getattr(refresh_status, "active", False)),
        "clustering_active": bool(getattr(clustering_status, "active", False)),
    }


def _activity_at(job: dict[str, Any]) -> datetime | None:
    heartbeat_at = job.get("heartbeat_at")
    if isinstance(heartbeat_at, datetime):
        return heartbeat_at
    created_at = job.get("created_at")
    return created_at if isinstance(created_at, datetime) else None


def _review_required(
    job: dict[str, Any],
    lock_state: dict[str, bool],
    *,
    now: datetime | None = None,
    review_after_hours: int = CLUSTERING_DISPLAY_REVIEW_AFTER_HOURS,
) -> bool:
    if str(job.get("status") or "") not in _ACTIVE_STATUSES:
        return False
    if not bool(lock_state.get("known")):
        return False
    if bool(lock_state.get("refresh_active") or lock_state.get("clustering_active")):
        return False
    activity_at = _activity_at(job)
    if activity_at is None:
        return False
    current = now or datetime.now()
    try:
        return current - activity_at >= timedelta(hours=max(1, int(review_after_hours)))
    except TypeError:
        return False


def _active_display_fallback(job: dict[str, Any]) -> str:
    status = str(job.get("status") or "")
    completed = max(0, int(job.get("completed_batches") or 0))
    maximum = max(1, int(job.get("max_batches") or 1))
    current = min(maximum, completed + 1)
    progress = max(0, min(100, int(job.get("progress_percent") or 0)))
    if status == "queued":
        return f"대기 · {current}/{maximum}차 준비"
    return f"실행 중 · {current}/{maximum}차 · {progress}% 완료"


def apply_clustering_stale_display_policy(
    job: dict[str, Any],
    lock_state: dict[str, bool],
    *,
    now: datetime | None = None,
    review_after_hours: int = CLUSTERING_DISPLAY_REVIEW_AFTER_HOURS,
) -> dict[str, Any]:
    """DB 원본은 유지하고 오래된 active 이력의 화면 표시만 보수적으로 결정합니다."""
    result = dict(job)
    if str(result.get("status") or "") not in _ACTIVE_STATUSES:
        return result
    if _review_required(
        result,
        lock_state,
        now=now,
        review_after_hours=review_after_hours,
    ):
        result["display_status"] = "stale"
        return result
    if str(result.get("display_status") or "") == "stale":
        result["display_status"] = _active_display_fallback(result)
    return result


def install_clustering_stale_display_contract(job_module: Any) -> None:
    """최신 이력 조회와 대표 카드에 잠금 근거 기반 stale 표시 정책을 설치합니다."""
    original_latest = getattr(job_module, "get_latest_clustering_job", None)
    if callable(original_latest) and not getattr(
        original_latest,
        "_clustering_stale_display_contract",
        False,
    ):

        @wraps(original_latest)
        def latest_with_lock_evidence(con, *args, **kwargs):
            job = original_latest(con, *args, **kwargs)
            if not isinstance(job, dict):
                return job
            lock_state = inspect_clustering_display_lock_state()
            return apply_clustering_stale_display_policy(job, lock_state)

        latest_with_lock_evidence._clustering_stale_display_contract = True  # type: ignore[attr-defined]
        job_module.get_latest_clustering_job = latest_with_lock_evidence

    original_representative = getattr(job_module, "get_representative_clustering_job", None)
    if not callable(original_representative) or getattr(
        original_representative,
        "_clustering_stale_display_contract",
        False,
    ):
        return

    @wraps(original_representative)
    def representative_with_stale_history(con, *args, **kwargs):
        primary = original_representative(con, *args, **kwargs)
        batch_limit = max(1, min(int(kwargs.get("batch_limit") or 20), 50))
        latest_attempt = job_module.get_latest_clustering_attempt(
            con,
            batch_limit=batch_limit,
        )
        if (
            isinstance(latest_attempt, dict)
            and str(latest_attempt.get("status") or "") in _ACTIVE_STATUSES
        ):
            return latest_attempt
        return primary

    representative_with_stale_history._clustering_stale_display_contract = True  # type: ignore[attr-defined]
    job_module.get_representative_clustering_job = representative_with_stale_history
