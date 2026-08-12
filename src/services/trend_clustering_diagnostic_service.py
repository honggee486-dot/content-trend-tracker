"""2단계 군집 시험 이력을 읽기 전용으로 집계합니다."""

from __future__ import annotations

from typing import Any

import duckdb


_JOB_TABLE = "trend_clustering_jobs"
_BATCH_TABLE = "trend_clustering_job_batches"
_MAX_CONFIGURED_BATCHES = 20
_REQUIRED_JOB_COLUMNS = {
    "job_id",
    "status",
    "launcher",
    "model_name",
    "scan_limit",
    "batch_size",
    "max_batches",
    "completed_batches",
    "remaining_items",
    "error_message",
    "created_at",
    "started_at",
    "finished_at",
}
_REQUIRED_BATCH_COLUMNS = {
    "job_id",
    "batch_number",
    "status",
    "first_stage_units",
    "processed_units",
    "processed_source_items",
    "existing_links",
    "new_clusters",
    "uncertain_units",
    "conflict_units",
    "needs_review_items",
    "input_tokens",
    "output_tokens",
    "thought_tokens",
    "total_tokens",
    "duration_ms",
    "error_message",
    "started_at",
    "finished_at",
}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat(sep=" ", timespec="seconds"))
    return str(value)


def _milliseconds_between(later: Any, earlier: Any) -> int | None:
    if later is None or earlier is None:
        return None
    try:
        return int(round((later - earlier).total_seconds() * 1000))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _table_exists(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
) -> bool:
    rows = con.execute("SHOW TABLES").fetchall()
    return str(table_name) in {str(row[0]) for row in rows}


def _table_columns(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
) -> set[str]:
    return {
        str(row[1])
        for row in con.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    }


def _unavailable(
    *,
    reason: str,
    missing_columns: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "missing_columns": list(missing_columns or []),
        "sample_available": False,
        "status": "집계 불가",
        "job_id": None,
        "job_status": "",
        "model_name": "",
        "scan_limit": 0,
        "configured_batch_size": 0,
        "configured_max_batches": 0,
        "completed_batches": 0,
        "remaining_items": 0,
        "trial_mode": False,
        "completed_within_configured_limit": False,
        "batch_size_contract_ok": False,
        "batch_timing_complete": False,
        "sequential_execution_ok": False,
        "invalid_batch_interval_count": 0,
        "overlapping_batch_count": 0,
        "minimum_inter_batch_gap_ms": None,
        "maximum_inter_batch_gap_ms": None,
        "trial_contract_ok": False,
        "batch_count": 0,
        "maximum_batch_number": 0,
        "maximum_first_stage_units": 0,
        "processed_units": 0,
        "processed_source_items": 0,
        "existing_links": 0,
        "new_clusters": 0,
        "uncertain_units": 0,
        "conflict_units": 0,
        "needs_review_items": 0,
        "review_signal_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "thought_tokens": 0,
        "total_tokens": 0,
        "estimated_total_tokens_per_1000_units": None,
        "average_duration_ms": 0,
        "maximum_duration_ms": 0,
        "error_message": "",
        "created_at": None,
        "started_at": None,
        "finished_at": None,
        "batches": [],
    }


def build_trend_clustering_trial_diagnostic(
    con: duckdb.DuckDBPyConnection,
) -> dict[str, Any]:
    """최신 군집 작업과 배치 이력을 변경 없이 요약합니다."""
    missing_tables = [
        table_name
        for table_name in (_JOB_TABLE, _BATCH_TABLE)
        if not _table_exists(con, table_name)
    ]
    if missing_tables:
        return _unavailable(
            reason="missing_tables",
            missing_columns=missing_tables,
        )

    job_columns = _table_columns(con, _JOB_TABLE)
    batch_columns = _table_columns(con, _BATCH_TABLE)
    missing_columns = sorted(
        {
            f"{_JOB_TABLE}.{column}"
            for column in _REQUIRED_JOB_COLUMNS - job_columns
        }
        | {
            f"{_BATCH_TABLE}.{column}"
            for column in _REQUIRED_BATCH_COLUMNS - batch_columns
        }
    )
    if missing_columns:
        return _unavailable(
            reason="missing_columns",
            missing_columns=missing_columns,
        )

    cursor = con.execute(
        """
        SELECT job_id, status, launcher, model_name, scan_limit, batch_size,
               max_batches, completed_batches, remaining_items, error_message,
               created_at, started_at, finished_at
        FROM trend_clustering_jobs
        ORDER BY created_at DESC, job_id DESC
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    if row is None:
        result = _unavailable(reason="job_not_found")
        result.update(
            {
                "available": True,
                "reason": "job_not_found",
                "status": "실행 기록 없음",
            }
        )
        return result

    columns = [str(item[0]) for item in cursor.description]
    job = dict(zip(columns, row))
    job_id = str(job.get("job_id") or "")

    batch_cursor = con.execute(
        """
        SELECT batch_number, status, first_stage_units, processed_units,
               processed_source_items, existing_links, new_clusters,
               uncertain_units, conflict_units, needs_review_items,
               input_tokens, output_tokens, thought_tokens, total_tokens,
               duration_ms, error_message, started_at, finished_at
        FROM trend_clustering_job_batches
        WHERE job_id = ?
        ORDER BY batch_number
        """,
        [job_id],
    )
    batch_columns = [str(item[0]) for item in batch_cursor.description]
    batches: list[dict[str, Any]] = []
    batch_timings: list[tuple[int, Any, Any]] = []
    for values in batch_cursor.fetchall():
        raw = dict(zip(batch_columns, values))
        batch_number = int(raw.get("batch_number") or 0)
        started_at = raw.get("started_at")
        finished_at = raw.get("finished_at")
        batch_timings.append((batch_number, started_at, finished_at))
        batches.append(
            {
                "batch_number": batch_number,
                "status": str(raw.get("status") or ""),
                "first_stage_units": int(raw.get("first_stage_units") or 0),
                "processed_units": int(raw.get("processed_units") or 0),
                "processed_source_items": int(
                    raw.get("processed_source_items") or 0
                ),
                "existing_links": int(raw.get("existing_links") or 0),
                "new_clusters": int(raw.get("new_clusters") or 0),
                "uncertain_units": int(raw.get("uncertain_units") or 0),
                "conflict_units": int(raw.get("conflict_units") or 0),
                "needs_review_items": int(
                    raw.get("needs_review_items") or 0
                ),
                "input_tokens": int(raw.get("input_tokens") or 0),
                "output_tokens": int(raw.get("output_tokens") or 0),
                "thought_tokens": int(raw.get("thought_tokens") or 0),
                "total_tokens": int(raw.get("total_tokens") or 0),
                "duration_ms": int(raw.get("duration_ms") or 0),
                "error_message": str(raw.get("error_message") or ""),
                "started_at": _iso(started_at),
                "finished_at": _iso(finished_at),
            }
        )

    def total(field: str) -> int:
        return sum(int(item.get(field) or 0) for item in batches)

    batch_count = len(batches)
    maximum_batch_number = max(
        (int(item["batch_number"]) for item in batches),
        default=0,
    )
    maximum_first_stage_units = max(
        (int(item["first_stage_units"]) for item in batches),
        default=0,
    )
    configured_batch_size = max(0, int(job.get("batch_size") or 0))
    configured_max_batches = max(0, int(job.get("max_batches") or 0))
    completed_batches = max(0, int(job.get("completed_batches") or 0))
    processed_units = total("processed_units")
    total_tokens = total("total_tokens")

    batch_timing_complete = batch_count > 0 and all(
        started_at is not None and finished_at is not None
        for _, started_at, finished_at in batch_timings
    )
    invalid_batch_interval_count = 0
    inter_batch_gaps: list[int] = []
    if batch_timing_complete:
        for _, started_at, finished_at in batch_timings:
            interval_ms = _milliseconds_between(finished_at, started_at)
            if interval_ms is None:
                batch_timing_complete = False
                break
            if interval_ms < 0:
                invalid_batch_interval_count += 1

    if batch_timing_complete:
        for previous, current in zip(batch_timings, batch_timings[1:]):
            gap_ms = _milliseconds_between(current[1], previous[2])
            if gap_ms is None:
                batch_timing_complete = False
                inter_batch_gaps = []
                break
            inter_batch_gaps.append(gap_ms)

    overlapping_batch_count = sum(gap_ms < 0 for gap_ms in inter_batch_gaps)
    sequential_execution_ok = (
        batch_timing_complete
        and invalid_batch_interval_count == 0
        and overlapping_batch_count == 0
    )
    minimum_inter_batch_gap_ms = min(inter_batch_gaps, default=None)
    maximum_inter_batch_gap_ms = max(inter_batch_gaps, default=None)

    completed_within_configured_limit = (
        0 < configured_max_batches <= _MAX_CONFIGURED_BATCHES
        and completed_batches <= configured_max_batches
        and batch_count <= configured_max_batches
        and maximum_batch_number <= configured_max_batches
    )
    batch_size_contract_ok = (
        0 < configured_batch_size <= 300
        and maximum_first_stage_units <= configured_batch_size
        and maximum_first_stage_units <= 300
    )
    trial_contract_ok = (
        completed_within_configured_limit
        and batch_size_contract_ok
        and sequential_execution_ok
    )
    trial_mode = 0 < configured_max_batches <= 5

    if batch_count <= 0:
        status = "실행 기록 없음"
    elif not sequential_execution_ok:
        status = "순차 실행 점검"
    elif not trial_contract_ok:
        status = "시험 계약 점검"
    elif trial_mode:
        status = "5배치 시험 확인"
    else:
        status = "확대 설정 관찰"

    uncertain_units = total("uncertain_units")
    conflict_units = total("conflict_units")
    needs_review_items = total("needs_review_items")
    durations = [int(item["duration_ms"]) for item in batches]
    estimated_tokens = (
        int(round(total_tokens / processed_units * 1000))
        if processed_units > 0 and total_tokens > 0
        else None
    )

    return {
        "available": True,
        "reason": "",
        "missing_columns": [],
        "sample_available": batch_count > 0,
        "status": status,
        "job_id": job_id,
        "job_status": str(job.get("status") or ""),
        "launcher": str(job.get("launcher") or ""),
        "model_name": str(job.get("model_name") or ""),
        "scan_limit": int(job.get("scan_limit") or 0),
        "configured_batch_size": configured_batch_size,
        "configured_max_batches": configured_max_batches,
        "completed_batches": completed_batches,
        "remaining_items": int(job.get("remaining_items") or 0),
        "trial_mode": trial_mode,
        "completed_within_configured_limit": (
            completed_within_configured_limit
        ),
        "batch_size_contract_ok": batch_size_contract_ok,
        "batch_timing_complete": batch_timing_complete,
        "sequential_execution_ok": sequential_execution_ok,
        "invalid_batch_interval_count": invalid_batch_interval_count,
        "overlapping_batch_count": overlapping_batch_count,
        "minimum_inter_batch_gap_ms": minimum_inter_batch_gap_ms,
        "maximum_inter_batch_gap_ms": maximum_inter_batch_gap_ms,
        "trial_contract_ok": trial_contract_ok,
        "batch_count": batch_count,
        "maximum_batch_number": maximum_batch_number,
        "maximum_first_stage_units": maximum_first_stage_units,
        "processed_units": processed_units,
        "processed_source_items": total("processed_source_items"),
        "existing_links": total("existing_links"),
        "new_clusters": total("new_clusters"),
        "uncertain_units": uncertain_units,
        "conflict_units": conflict_units,
        "needs_review_items": needs_review_items,
        "review_signal_count": (
            uncertain_units + conflict_units + needs_review_items
        ),
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "thought_tokens": total("thought_tokens"),
        "total_tokens": total_tokens,
        "estimated_total_tokens_per_1000_units": estimated_tokens,
        "average_duration_ms": (
            int(round(sum(durations) / len(durations))) if durations else 0
        ),
        "maximum_duration_ms": max(durations, default=0),
        "error_message": str(job.get("error_message") or ""),
        "created_at": _iso(job.get("created_at")),
        "started_at": _iso(job.get("started_at")),
        "finished_at": _iso(job.get("finished_at")),
        "batches": batches,
    }
