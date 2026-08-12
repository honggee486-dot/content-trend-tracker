"""출처별 수집 신선도와 백그라운드 스케줄러 상태를 읽기 전용으로 계산합니다."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterable

import duckdb

from src.database import get_setting
from src.services.collection_history_service import SOURCE_LABELS

HEALTHY_SOURCE_STATUSES = {"success", "skipped"}
PROBLEM_SOURCE_STATUSES = {"partial_success", "failure"}
REFRESH_RUN_TYPES = ("background_refresh", "manual_refresh")
REFRESH_SOURCE_NAMES = tuple(
    source_name
    for source_name in SOURCE_LABELS
    if source_name not in {"ranking", "topic_angles"}
)


def _coerce_interval_minutes(value: object) -> int:
    try:
        parsed = int(str(value or "240").strip())
    except (TypeError, ValueError):
        parsed = 240
    return max(15, min(parsed, 10_080))


def _elapsed_minutes(now: datetime, value: object) -> int | None:
    if not isinstance(value, datetime):
        return None
    return max(0, int((now - value).total_seconds() // 60))


def _cursor_rows(cursor) -> list[dict[str, Any]]:
    columns = [str(column[0]) for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def build_source_freshness_diagnostics(
    source_rows: Iterable[dict[str, Any]],
    background_rows: Iterable[dict[str, Any]],
    *,
    interval_minutes: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """저장된 수집 이력 행을 화면용 상태로 변환합니다."""
    current = now or datetime.now()
    interval = _coerce_interval_minutes(interval_minutes)
    stale_minutes = interval * 2

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    observed_source_names: list[str] = []
    for row in source_rows:
        source_name = str(row.get("source_name") or "").strip()
        if not source_name or source_name in {"ranking", "topic_angles"}:
            continue
        grouped[source_name].append(dict(row))
        if source_name not in observed_source_names:
            observed_source_names.append(source_name)

    ordered_source_names = list(REFRESH_SOURCE_NAMES)
    ordered_source_names.extend(
        source_name
        for source_name in observed_source_names
        if source_name not in ordered_source_names
    )

    source_states: list[dict[str, Any]] = []
    for source_name in ordered_source_names:
        rows = sorted(
            grouped.get(source_name, ()),
            key=lambda row: row.get("started_at") or datetime.min,
            reverse=True,
        )
        latest = rows[0] if rows else {}
        latest_status = str(latest.get("status") or "")
        latest_at = latest.get("started_at")
        latest_elapsed = _elapsed_minutes(current, latest_at)

        healthy_row = next(
            (
                row
                for row in rows
                if str(row.get("status") or "") in HEALTHY_SOURCE_STATUSES
            ),
            None,
        )
        last_healthy_at = healthy_row.get("started_at") if healthy_row else None
        healthy_elapsed = _elapsed_minutes(current, last_healthy_at)
        last_new_row = next(
            (row for row in rows if int(row.get("newly_saved_count") or 0) > 0),
            None,
        )
        last_new_at = last_new_row.get("started_at") if last_new_row else None

        consecutive_problem_count = 0
        for row in rows:
            status = str(row.get("status") or "")
            if status in HEALTHY_SOURCE_STATUSES:
                break
            if status in PROBLEM_SOURCE_STATUSES or status:
                consecutive_problem_count += 1

        if not rows:
            state = "no_history"
        elif latest_status == "failure":
            state = "failure"
        elif healthy_elapsed is None:
            state = "stale" if (latest_elapsed or 0) > stale_minutes else "warning"
        elif healthy_elapsed > stale_minutes:
            state = "stale"
        elif latest_status == "partial_success" or consecutive_problem_count > 0:
            state = "warning"
        elif healthy_elapsed > interval:
            state = "warning"
        else:
            state = "healthy"

        source_states.append(
            {
                "source_name": source_name,
                "state": state,
                "latest_status": latest_status or None,
                "latest_at": latest_at,
                "last_healthy_at": last_healthy_at,
                "last_new_at": last_new_at,
                "latest_elapsed_minutes": latest_elapsed,
                "healthy_elapsed_minutes": healthy_elapsed,
                "consecutive_problem_count": consecutive_problem_count,
                "newly_saved_count": int(latest.get("newly_saved_count") or 0),
                "updated_count": int(latest.get("updated_count") or 0),
                "error_message": str(latest.get("error_message") or ""),
            }
        )

    backgrounds = sorted(
        (dict(row) for row in background_rows),
        key=lambda row: row.get("started_at") or datetime.min,
        reverse=True,
    )
    latest_background = backgrounds[0] if backgrounds else {}
    last_background_at = latest_background.get("started_at")
    background_elapsed = _elapsed_minutes(current, last_background_at)
    latest_background_status = str(latest_background.get("status") or "") or None
    last_full_success = next(
        (row for row in backgrounds if str(row.get("status") or "") == "success"),
        None,
    )

    if not backgrounds:
        scheduler_state = "no_history"
    elif (background_elapsed or 0) > stale_minutes:
        scheduler_state = "overdue"
    elif latest_background_status in {"failure", "partial_success"}:
        scheduler_state = "warning"
    elif (background_elapsed or 0) > interval:
        scheduler_state = "warning"
    else:
        scheduler_state = "healthy"

    next_expected_at = (
        last_background_at + timedelta(minutes=interval)
        if isinstance(last_background_at, datetime)
        else None
    )
    stale_after_at = (
        last_background_at + timedelta(minutes=stale_minutes)
        if isinstance(last_background_at, datetime)
        else None
    )

    return {
        "interval_minutes": interval,
        "stale_minutes": stale_minutes,
        "scheduler_state": scheduler_state,
        "latest_background_status": latest_background_status,
        "last_background_at": last_background_at,
        "last_background_success_at": (
            last_full_success.get("started_at") if last_full_success else None
        ),
        "background_elapsed_minutes": background_elapsed,
        "next_expected_at": next_expected_at,
        "stale_after_at": stale_after_at,
        "background_error_message": str(
            latest_background.get("error_message") or ""
        ),
        "source_rows": source_states,
        "attention_source_count": sum(
            1
            for row in source_states
            if row["state"] in {"warning", "stale", "failure"}
        ),
        "stale_source_count": sum(
            1 for row in source_states if row["state"] == "stale"
        ),
        "failed_source_count": sum(
            1 for row in source_states if row["state"] == "failure"
        ),
        "no_history_source_count": sum(
            1 for row in source_states if row["state"] == "no_history"
        ),
    }


def get_source_freshness_diagnostics(
    con: duckdb.DuckDBPyConnection,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """기존 수집 이력과 저장된 스케줄 주기만 읽어 상태를 계산합니다."""
    interval_minutes = _coerce_interval_minutes(
        get_setting(con, "trend_refresh_interval_minutes", "240")
    )
    placeholders = ", ".join("?" for _ in REFRESH_SOURCE_NAMES)
    source_rows = _cursor_rows(
        con.execute(
            f"""
            SELECT s.source_name, s.status, s.newly_saved_count, s.updated_count,
                   s.error_message, r.started_at, r.finished_at, r.run_type
            FROM collection_run_sources s
            JOIN collection_runs r ON r.run_id = s.run_id
            WHERE r.run_type IN (?, ?)
              AND r.status <> 'running'
              AND s.source_name IN ({placeholders})
            ORDER BY s.source_name, r.started_at DESC
            """,
            [*REFRESH_RUN_TYPES, *REFRESH_SOURCE_NAMES],
        )
    )
    background_rows = _cursor_rows(
        con.execute(
            """
            SELECT started_at, finished_at, status, error_message
            FROM collection_runs
            WHERE run_type = 'background_refresh'
              AND status <> 'running'
            ORDER BY started_at DESC
            LIMIT 100
            """
        )
    )
    return build_source_freshness_diagnostics(
        source_rows,
        background_rows,
        interval_minutes=interval_minutes,
        now=now,
    )
