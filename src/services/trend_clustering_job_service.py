from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from typing import Any
from uuid import uuid4

import duckdb

from src.config import DEFAULT_DB_PATH, PROJECT_ROOT
from src.database import connect_database, get_setting
from src.services.gemini_model_service import (
    MODEL_PURPOSE_DATA_REVIEW,
    get_selected_gemini_model,
)
from src.services.trend_clustering_lock_service import acquire_trend_clustering_lock
from src.services.trend_discovery_service import (
    AI_CLUSTERING_MAX_ITEMS_SETTING,
    DEFAULT_AI_CLUSTERING_MAX_ITEMS,
    calculate_prepared_trend_rankings,
    finalize_prepared_trend_rankings,
    prepare_trend_ranking_rebuild,
)

ACTIVE_STATUSES = frozenset({"queued", "running"})
FINAL_STATUSES = frozenset({"success", "partial", "failed", "skipped_overlap"})
JOB_STALE_AFTER_MINUTES = 20
CLUSTERING_JOB_BATCH_SIZE = 300
CLUSTERING_JOB_MAX_BATCHES = 20


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = int(default)
    return max(minimum, min(parsed, maximum))


def get_clustering_job_settings(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    return {
        "model_name": get_selected_gemini_model(con, MODEL_PURPOSE_DATA_REVIEW),
        "scan_limit": _bounded_int(
            get_setting(
                con,
                AI_CLUSTERING_MAX_ITEMS_SETTING,
                str(DEFAULT_AI_CLUSTERING_MAX_ITEMS),
            ),
            default=DEFAULT_AI_CLUSTERING_MAX_ITEMS,
            minimum=200,
            maximum=10000,
        ),
        "batch_size": CLUSTERING_JOB_BATCH_SIZE,
        "max_batches": CLUSTERING_JOB_MAX_BATCHES,
    }


def create_clustering_job(
    con: duckdb.DuckDBPyConnection,
    *,
    launcher: str,
) -> dict[str, Any]:
    stale_before = datetime.now() - timedelta(minutes=JOB_STALE_AFTER_MINUTES)
    active = con.execute(
        """
        SELECT job_id, status, created_at
        FROM trend_clustering_jobs
        WHERE status IN ('queued', 'running')
          AND COALESCE(heartbeat_at, created_at) >= ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [stale_before],
    ).fetchone()
    if active:
        return {
            "created": False,
            "job_id": str(active[0]),
            "status": str(active[1]),
            "message": "이미 군집 처리 작업이 실행 중입니다.",
        }
    settings = get_clustering_job_settings(con)
    job_id = f"cluster_job_{uuid4().hex}"
    now = datetime.now()
    con.execute(
        """
        INSERT INTO trend_clustering_jobs(
            job_id, status, launcher, model_name, scan_limit, batch_size,
            max_batches, created_at, heartbeat_at
        ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            job_id,
            str(launcher or "dashboard"),
            settings["model_name"],
            settings["scan_limit"],
            settings["batch_size"],
            settings["max_batches"],
            now,
            now,
        ],
    )
    return {
        "created": True,
        "job_id": job_id,
        "status": "queued",
        "message": (
            f"2차 군집 작업을 시작했습니다. 요청당 최대 {settings['batch_size']:,}개 · "
            f"최대 {settings['max_batches']:,}회 처리하며 미처리 자료가 없으면 즉시 종료합니다."
        ),
        **settings,
    }


def launch_clustering_job(
    job_id: str,
    *,
    project_root: str | Path = PROJECT_ROOT,
    db_path: str | Path = DEFAULT_DB_PATH,
    lookback_hours: int = 72,
) -> int:
    root = Path(project_root).resolve()
    script = root / "scripts" / "process_cluster_backlog.py"
    command = [
        sys.executable,
        str(script),
        "--job-id",
        str(job_id),
        "--db-path",
        str(Path(db_path).resolve()),
        "--lookback-hours",
        str(max(6, int(lookback_hours))),
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    process = subprocess.Popen(command, **kwargs)
    return int(process.pid)


def _update_job_status(
    con: duckdb.DuckDBPyConnection,
    job_id: str,
    *,
    status: str,
    error_message: str = "",
    finished: bool = False,
) -> None:
    now = datetime.now()
    con.execute(
        """
        UPDATE trend_clustering_jobs
        SET status = ?, heartbeat_at = ?, error_message = ?,
            started_at = COALESCE(started_at, ?),
            finished_at = CASE WHEN ? THEN ? ELSE finished_at END
        WHERE job_id = ?
        """,
        [status, now, str(error_message or "")[:2000], now, finished, now, job_id],
    )


def _mark_batch_started(
    con: duckdb.DuckDBPyConnection,
    job_id: str,
    *,
    pending_items: int,
) -> None:
    now = datetime.now()
    con.execute(
        """
        UPDATE trend_clustering_jobs
        SET status = 'running', remaining_items = ?, heartbeat_at = ?,
            started_at = COALESCE(started_at, ?)
        WHERE job_id = ?
        """,
        [max(0, int(pending_items)), now, now, job_id],
    )


def _apply_job_limits(preparation: Any, *, batch_size: int, max_batches: int) -> Any:
    values = {
        "ai_clustering_batch_size": int(batch_size),
        "ai_clustering_max_batches": int(max_batches),
    }
    try:
        return replace(preparation, **values)
    except TypeError:
        for name, value in values.items():
            setattr(preparation, name, value)
        return preparation


def _record_batch(
    con: duckdb.DuckDBPyConnection,
    job_id: str,
    batch_number: int,
    *,
    result: dict[str, Any],
    started_at: datetime,
    duration_ms: int,
) -> None:
    detail = dict(result.get("batch_log") or {})
    con.execute(
        """
        INSERT INTO trend_clustering_job_batches(
            job_id, batch_number, status, scanned_pending_items,
            first_stage_units, all_first_stage_units, source_items,
            url_merged_items, url_conflict_splits, title_merged_groups,
            existing_candidate_refs, deferred_units, processed_units,
            processed_source_items, existing_links, new_clusters,
            uncertain_units, conflict_units, needs_review_items, input_tokens,
            output_tokens, thought_tokens, total_tokens, duration_ms,
            error_message, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id, batch_number) DO UPDATE SET
            status = EXCLUDED.status,
            scanned_pending_items = EXCLUDED.scanned_pending_items,
            first_stage_units = EXCLUDED.first_stage_units,
            all_first_stage_units = EXCLUDED.all_first_stage_units,
            source_items = EXCLUDED.source_items,
            url_merged_items = EXCLUDED.url_merged_items,
            url_conflict_splits = EXCLUDED.url_conflict_splits,
            title_merged_groups = EXCLUDED.title_merged_groups,
            existing_candidate_refs = EXCLUDED.existing_candidate_refs,
            deferred_units = EXCLUDED.deferred_units,
            processed_units = EXCLUDED.processed_units,
            processed_source_items = EXCLUDED.processed_source_items,
            existing_links = EXCLUDED.existing_links,
            new_clusters = EXCLUDED.new_clusters,
            uncertain_units = EXCLUDED.uncertain_units,
            conflict_units = EXCLUDED.conflict_units,
            needs_review_items = EXCLUDED.needs_review_items,
            input_tokens = EXCLUDED.input_tokens,
            output_tokens = EXCLUDED.output_tokens,
            thought_tokens = EXCLUDED.thought_tokens,
            total_tokens = EXCLUDED.total_tokens,
            duration_ms = EXCLUDED.duration_ms,
            error_message = EXCLUDED.error_message,
            finished_at = EXCLUDED.finished_at
        """,
        [
            job_id,
            int(batch_number),
            str(detail.get("status") or result.get("ai_clustering", {}).get("status") or "unknown"),
            int(detail.get("scanned_pending_items") or 0),
            int(detail.get("first_stage_units") or 0),
            int(detail.get("all_first_stage_units") or 0),
            int(detail.get("source_items") or 0),
            int(detail.get("url_merged_items") or 0),
            int(detail.get("url_conflict_splits") or 0),
            int(detail.get("title_merged_groups") or 0),
            int(detail.get("existing_candidate_refs") or 0),
            int(detail.get("deferred_units") or 0),
            int(detail.get("processed_units") or 0),
            int(detail.get("processed_source_items") or 0),
            int(detail.get("existing_links") or 0),
            int(detail.get("new_clusters") or 0),
            int(detail.get("uncertain_units") or 0),
            int(detail.get("conflict_units") or 0),
            int(detail.get("needs_review_items") or 0),
            int(detail.get("input_tokens") or 0),
            int(detail.get("output_tokens") or 0),
            int(detail.get("thought_tokens") or 0),
            int(detail.get("total_tokens") or 0),
            int(duration_ms),
            str(detail.get("error_message") or "")[:2000],
            started_at,
            datetime.now(),
        ],
    )
    clustering = dict(result.get("ai_clustering") or {})
    con.execute(
        """
        UPDATE trend_clustering_jobs
        SET completed_batches = completed_batches + 1,
            processed_units = processed_units + ?,
            processed_source_items = processed_source_items + ?,
            remaining_items = ?,
            existing_links = existing_links + ?,
            new_clusters = new_clusters + ?,
            uncertain_units = uncertain_units + ?,
            conflict_units = conflict_units + ?,
            needs_review_items = needs_review_items + ?,
            input_tokens = input_tokens + ?,
            output_tokens = output_tokens + ?,
            thought_tokens = thought_tokens + ?,
            total_tokens = total_tokens + ?,
            heartbeat_at = ?,
            error_message = CASE
                WHEN ? <> '' THEN ?
                ELSE error_message
            END
        WHERE job_id = ?
        """,
        [
            int(detail.get("processed_units") or 0),
            int(detail.get("processed_source_items") or 0),
            int(clustering.get("remaining_items") or 0),
            int(detail.get("existing_links") or 0),
            int(detail.get("new_clusters") or 0),
            int(detail.get("uncertain_units") or 0),
            int(detail.get("conflict_units") or 0),
            int(detail.get("needs_review_items") or 0),
            int(detail.get("input_tokens") or 0),
            int(detail.get("output_tokens") or 0),
            int(detail.get("thought_tokens") or 0),
            int(detail.get("total_tokens") or 0),
            datetime.now(),
            str(detail.get("error_message") or ""),
            str(detail.get("error_message") or "")[:2000],
            job_id,
        ],
    )


def run_clustering_job(
    job_id: str,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    project_root: str | Path = PROJECT_ROOT,
    lookback_hours: int = 72,
) -> int:
    attempt = acquire_trend_clustering_lock(
        data_directory=Path(db_path).resolve().parent,
        launcher=f"clustering-job:{job_id}",
    )
    if not attempt.acquired or attempt.lock is None:
        with connect_database(db_path) as con:
            _update_job_status(
                con,
                job_id,
                status="skipped_overlap",
                error_message=attempt.message,
                finished=True,
            )
        return 0

    try:
        with connect_database(db_path) as con:
            row = con.execute(
                """
                SELECT batch_size, max_batches
                FROM trend_clustering_jobs
                WHERE job_id = ?
                """,
                [job_id],
            ).fetchone()
            if row is None:
                raise ValueError("군집 작업 이력이 없습니다.")
            batch_size = max(
                20,
                min(int(row[0] or CLUSTERING_JOB_BATCH_SIZE), CLUSTERING_JOB_BATCH_SIZE),
            )
            max_batches = max(1, min(int(row[1] or 1), CLUSTERING_JOB_MAX_BATCHES))
            _update_job_status(con, job_id, status="running")

        completed = 0
        remaining = 0
        final_error = ""
        for batch_number in range(1, max_batches + 1):
            started_at = datetime.now()
            started = perf_counter()
            with connect_database(db_path) as con:
                preparation = prepare_trend_ranking_rebuild(
                    con,
                    lookback_hours=lookback_hours,
                )
            if preparation.status == "reused" or preparation.pending_item_count <= 0:
                remaining = 0
                break

            preparation = _apply_job_limits(
                preparation,
                batch_size=batch_size,
                max_batches=max_batches,
            )
            with connect_database(db_path) as con:
                _mark_batch_started(
                    con,
                    job_id,
                    pending_items=int(preparation.pending_item_count or 0),
                )

            calculation = calculate_prepared_trend_rankings(preparation)
            with connect_database(db_path) as con:
                result = finalize_prepared_trend_rankings(con, calculation)
                _record_batch(
                    con,
                    job_id,
                    batch_number,
                    result=result,
                    started_at=started_at,
                    duration_ms=int((perf_counter() - started) * 1000),
                )
            completed += 1
            clustering = dict(result.get("ai_clustering") or {})
            remaining = int(clustering.get("remaining_items") or 0)
            status = str(clustering.get("status") or "")
            final_error = str(clustering.get("error_message") or final_error)
            progressed = int(clustering.get("processed_items") or 0) + int(
                clustering.get("needs_review_items") or 0
            )
            if remaining <= 0:
                break
            if progressed <= 0 or status in {"missing_api_key", "disabled", "failed_pending"}:
                break

        final_status = "success" if remaining <= 0 else "partial"
        if completed <= 0 and remaining > 0:
            final_status = "failed" if final_error else "partial"
        with connect_database(db_path) as con:
            _update_job_status(
                con,
                job_id,
                status=final_status,
                error_message=final_error,
                finished=True,
            )
        return 0 if final_status in {"success", "partial"} else 1
    except Exception as exc:
        try:
            with connect_database(db_path) as con:
                _update_job_status(
                    con,
                    job_id,
                    status="failed",
                    error_message=str(exc),
                    finished=True,
                )
        except Exception:
            pass
        return 1
    finally:
        attempt.lock.release()


def get_latest_clustering_job(
    con: duckdb.DuckDBPyConnection,
    *,
    batch_limit: int = 20,
    active_only: bool = False,
    result_only: bool = False,
) -> dict[str, Any] | None:
    params: list[Any] = []
    conditions: list[str] = []
    if active_only:
        conditions.append(
            "status IN ('queued', 'running') "
            "AND COALESCE(heartbeat_at, created_at) >= ?"
        )
        params.append(datetime.now() - timedelta(minutes=JOB_STALE_AFTER_MINUTES))
    if result_only:
        conditions.append("status IN ('success', 'partial', 'failed')")
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    row = con.execute(
        f"""
        SELECT *
        FROM trend_clustering_jobs
        {where_clause}
        ORDER BY created_at DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if row is None:
        return None
    columns = [str(item[0]) for item in con.description]
    job = dict(zip(columns, row))
    batch_rows = con.execute(
        """
        SELECT batch_number, status, scanned_pending_items, first_stage_units,
               all_first_stage_units, source_items, url_merged_items,
               url_conflict_splits, title_merged_groups, existing_candidate_refs,
               deferred_units, processed_units, processed_source_items,
               existing_links, new_clusters, uncertain_units, conflict_units,
               needs_review_items, input_tokens, output_tokens, thought_tokens,
               total_tokens, duration_ms, error_message, started_at, finished_at
        FROM trend_clustering_job_batches
        WHERE job_id = ?
        ORDER BY batch_number DESC
        LIMIT ?
        """,
        [str(job.get("job_id") or ""), max(1, min(int(batch_limit), 50))],
    ).fetchall()
    batch_columns = [str(item[0]) for item in con.description]
    job["batches"] = [dict(zip(batch_columns, batch_row)) for batch_row in batch_rows]

    status = str(job.get("status") or "")
    heartbeat_at = job.get("heartbeat_at")
    created_at = job.get("created_at")
    activity_at = heartbeat_at if isinstance(heartbeat_at, datetime) else created_at
    completed_batches = max(0, int(job.get("completed_batches") or 0))
    max_batches = max(1, int(job.get("max_batches") or 1))
    progress_percent = min(100, round(completed_batches * 100 / max_batches))
    current_batch = min(max_batches, completed_batches + 1)
    job["progress_percent"] = progress_percent
    job["current_batch"] = current_batch

    if (
        status in ACTIVE_STATUSES
        and isinstance(activity_at, datetime)
        and activity_at < datetime.now() - timedelta(minutes=JOB_STALE_AFTER_MINUTES)
    ):
        job["display_status"] = "stale"
    elif status == "queued":
        job["display_status"] = f"대기 · {current_batch}/{max_batches}차 준비"
    elif status == "running":
        job["display_status"] = (
            f"실행 중 · {current_batch}/{max_batches}차 · {progress_percent}% 완료"
        )
    else:
        job["display_status"] = status
    return job


def get_active_clustering_job(
    con: duckdb.DuckDBPyConnection,
    *,
    batch_limit: int = 20,
) -> dict[str, Any] | None:
    """최근 heartbeat가 유효한 queued/running backlog 작업을 조회합니다."""
    return get_latest_clustering_job(
        con,
        batch_limit=batch_limit,
        active_only=True,
    )


def get_latest_clustering_attempt(
    con: duckdb.DuckDBPyConnection,
    *,
    batch_limit: int = 20,
) -> dict[str, Any] | None:
    """성공·실패·중복 생략을 포함한 가장 최근 실행 시도를 조회합니다."""
    return get_latest_clustering_job(con, batch_limit=batch_limit)


def get_latest_clustering_result(
    con: duckdb.DuckDBPyConnection,
    *,
    batch_limit: int = 20,
) -> dict[str, Any] | None:
    """중복 생략을 제외하고 실제로 실행된 가장 최근 최종 결과를 조회합니다."""
    return get_latest_clustering_job(
        con,
        batch_limit=batch_limit,
        result_only=True,
    )


def get_representative_clustering_job(
    con: duckdb.DuckDBPyConnection,
    *,
    batch_limit: int = 20,
) -> dict[str, Any] | None:
    """화면 주 상태로 활성 작업을 우선하고 없으면 최근 실제 결과를 반환합니다."""
    return get_active_clustering_job(con, batch_limit=batch_limit) or (
        get_latest_clustering_result(con, batch_limit=batch_limit)
    )
