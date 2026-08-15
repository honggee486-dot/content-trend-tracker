from __future__ import annotations

from datetime import datetime
from functools import wraps
from threading import local
from time import perf_counter
from typing import Any
from uuid import uuid4


_STATE = local()
_LAUNCHER = "trend-refresh-ranking"


def _batch_has_clustering_work(result: dict[str, Any]) -> bool:
    if bool(result.get("reused")):
        return False
    detail = dict(result.get("batch_log") or {})
    if not detail:
        return False
    return any(
        int(detail.get(field) or 0) > 0
        for field in (
            "scanned_pending_items",
            "first_stage_units",
            "processed_units",
            "processed_source_items",
            "uncertain_units",
            "needs_review_items",
        )
    )


def _record_refresh_clustering_job(
    con: Any,
    *,
    preparation: Any,
    result: dict[str, Any],
    started_at: datetime,
    duration_ms: int,
) -> str | None:
    """수집 경로에서 직접 수행한 2단계 군집도 기존 작업 원장에 남깁니다."""
    if not _batch_has_clustering_work(result):
        return None

    from src.services import trend_clustering_job_service as job_service

    detail = dict(result.get("batch_log") or {})
    clustering = dict(result.get("ai_clustering") or {})
    job_id = f"cluster_job_{uuid4().hex}"
    model_name = str(
        getattr(preparation, "ai_clustering_model", "")
        or clustering.get("model")
        or ""
    )
    scan_limit = max(
        1,
        int(
            getattr(preparation, "ai_clustering_max_items", 0)
            or detail.get("scanned_pending_items")
            or detail.get("source_items")
            or 1
        ),
    )
    batch_size = max(
        1,
        int(
            getattr(preparation, "ai_clustering_batch_size", 0)
            or detail.get("first_stage_units")
            or 1
        ),
    )
    max_batches = max(
        1,
        int(getattr(preparation, "ai_clustering_max_batches", 0) or 1),
    )
    pending_items = max(
        0,
        int(
            getattr(preparation, "pending_item_count", 0)
            or detail.get("scanned_pending_items")
            or 0
        ),
    )
    final_error = str(clustering.get("error_message") or "")
    remaining = max(0, int(clustering.get("remaining_items") or 0))
    final_status = "success" if remaining <= 0 else "partial"

    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            """
            INSERT INTO trend_clustering_jobs(
                job_id, status, launcher, model_name, scan_limit, batch_size,
                max_batches, remaining_items, created_at, started_at, heartbeat_at
            ) VALUES (?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                job_id,
                _LAUNCHER,
                model_name,
                scan_limit,
                batch_size,
                max_batches,
                pending_items,
                started_at,
                started_at,
                started_at,
            ],
        )
        job_service._record_batch(
            con,
            job_id,
            1,
            result=result,
            started_at=started_at,
            duration_ms=max(0, int(duration_ms)),
        )
        job_service._update_job_status(
            con,
            job_id,
            status=final_status,
            error_message=final_error,
            finished=True,
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return job_id


def _attach_job_id(result: dict[str, Any], job_id: str) -> None:
    clustering = dict(result.get("ai_clustering") or {})
    clustering["job_id"] = job_id
    result["ai_clustering"] = clustering
    if "ai_review" in result:
        result["ai_review"] = clustering


def install_refresh_clustering_job_history_contract(
    discovery_module: Any | None = None,
) -> None:
    """최신 데이터 수집이 직접 수행한 군집 결과를 job/batch 원장과 연결합니다."""
    if discovery_module is None:
        from src.services import trend_discovery_service as discovery_module

    original_refresh = getattr(
        discovery_module,
        "refresh_trend_sources_short_connections",
        None,
    )
    original_calculate = getattr(
        discovery_module,
        "calculate_prepared_trend_rankings",
        None,
    )
    original_finalize = getattr(
        discovery_module,
        "finalize_prepared_trend_rankings",
        None,
    )
    if not all(callable(item) for item in (original_refresh, original_calculate, original_finalize)):
        return
    if getattr(original_refresh, "_refresh_clustering_job_history_contract", False):
        return

    @wraps(original_refresh)
    def refresh_with_job_history(*args, **kwargs):
        previous_active = bool(getattr(_STATE, "active", False))
        previous_started_at = getattr(_STATE, "started_at", None)
        previous_started_perf = getattr(_STATE, "started_perf", None)
        _STATE.active = True
        _STATE.started_at = None
        _STATE.started_perf = None
        try:
            return original_refresh(*args, **kwargs)
        finally:
            _STATE.active = previous_active
            _STATE.started_at = previous_started_at
            _STATE.started_perf = previous_started_perf

    @wraps(original_calculate)
    def calculate_with_job_history(*args, **kwargs):
        if bool(getattr(_STATE, "active", False)):
            _STATE.started_at = datetime.now()
            _STATE.started_perf = perf_counter()
        return original_calculate(*args, **kwargs)

    @wraps(original_finalize)
    def finalize_with_job_history(con, calculation, *args, **kwargs):
        result = dict(original_finalize(con, calculation, *args, **kwargs))
        started_at = getattr(_STATE, "started_at", None)
        started_perf = getattr(_STATE, "started_perf", None)
        if (
            bool(getattr(_STATE, "active", False))
            and isinstance(started_at, datetime)
            and isinstance(started_perf, (int, float))
        ):
            job_id = _record_refresh_clustering_job(
                con,
                preparation=getattr(calculation, "preparation", None),
                result=result,
                started_at=started_at,
                duration_ms=int((perf_counter() - float(started_perf)) * 1000),
            )
            if job_id:
                _attach_job_id(result, job_id)
            _STATE.started_at = None
            _STATE.started_perf = None
        return result

    refresh_with_job_history._refresh_clustering_job_history_contract = True  # type: ignore[attr-defined]
    calculate_with_job_history._refresh_clustering_job_history_contract = True  # type: ignore[attr-defined]
    finalize_with_job_history._refresh_clustering_job_history_contract = True  # type: ignore[attr-defined]
    discovery_module.refresh_trend_sources_short_connections = refresh_with_job_history
    discovery_module.calculate_prepared_trend_rankings = calculate_with_job_history
    discovery_module.finalize_prepared_trend_rankings = finalize_with_job_history
