"""예약 수집의 실제 DB 실행 상태를 읽기 전용으로 요약합니다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import duckdb


@dataclass(frozen=True)
class BackgroundRefreshSnapshot:
    available: bool
    diagnostic_status: str = "missing"
    run_id: str = ""
    run_status: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int = 0
    request_count: int = 0
    retry_count: int = 0
    newly_saved_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    summary: str = ""
    error_message: str = ""
    source_problem_count: int = 0
    topic_angle_status: str = ""
    sources: tuple[dict[str, Any], ...] = ()

    @property
    def changed_count(self) -> int:
        return self.newly_saved_count + self.updated_count


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def get_latest_background_refresh_snapshot(
    con: duckdb.DuckDBPyConnection,
    *,
    expected_interval_minutes: int = 180,
    now: datetime | None = None,
) -> BackgroundRefreshSnapshot:
    row = con.execute(
        """
        SELECT run_id, status, started_at, finished_at, duration_ms,
               request_count, retry_count, newly_saved_count, updated_count,
               skipped_count, summary, error_message
        FROM collection_runs
        WHERE run_type = 'background_refresh'
        ORDER BY started_at DESC, created_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return BackgroundRefreshSnapshot(available=False)

    columns = [
        "run_id",
        "status",
        "started_at",
        "finished_at",
        "duration_ms",
        "request_count",
        "retry_count",
        "newly_saved_count",
        "updated_count",
        "skipped_count",
        "summary",
        "error_message",
    ]
    run = dict(zip(columns, row))
    source_cursor = con.execute(
        """
        SELECT source_name, status, duration_ms, request_count, retry_count,
               newly_saved_count, updated_count, skipped_count, error_message
        FROM collection_run_sources
        WHERE run_id = ?
        ORDER BY source_name
        """,
        [str(run["run_id"])],
    )
    source_columns = [str(item[0]) for item in source_cursor.description]
    sources = tuple(dict(zip(source_columns, item)) for item in source_cursor.fetchall())

    run_status = str(run.get("status") or "")
    source_problem_count = sum(
        1
        for item in sources
        if str(item.get("status") or "") in {"partial_success", "failure"}
    )
    topic_angle = next(
        (item for item in sources if str(item.get("source_name") or "") == "topic_angles"),
        None,
    )
    started_at = run.get("started_at")
    current = now or datetime.now()
    stale_minutes = max(60, int(expected_interval_minutes) * 2 + 30)
    stale = bool(
        isinstance(started_at, datetime)
        and current - started_at > timedelta(minutes=stale_minutes)
    )
    changed_count = _as_int(run.get("newly_saved_count")) + _as_int(
        run.get("updated_count")
    )

    if run_status == "running":
        diagnostic_status = "running"
    elif run_status == "failure":
        diagnostic_status = "failure"
    elif run_status == "skipped_overlap":
        diagnostic_status = "skipped_overlap"
    elif source_problem_count:
        diagnostic_status = "partial_success"
    elif stale:
        diagnostic_status = "stale"
    elif changed_count == 0:
        diagnostic_status = "no_change"
    else:
        diagnostic_status = "success"

    return BackgroundRefreshSnapshot(
        available=True,
        diagnostic_status=diagnostic_status,
        run_id=str(run.get("run_id") or ""),
        run_status=run_status,
        started_at=started_at if isinstance(started_at, datetime) else None,
        finished_at=run.get("finished_at") if isinstance(run.get("finished_at"), datetime) else None,
        duration_ms=_as_int(run.get("duration_ms")),
        request_count=_as_int(run.get("request_count")),
        retry_count=_as_int(run.get("retry_count")),
        newly_saved_count=_as_int(run.get("newly_saved_count")),
        updated_count=_as_int(run.get("updated_count")),
        skipped_count=_as_int(run.get("skipped_count")),
        summary=str(run.get("summary") or ""),
        error_message=str(run.get("error_message") or ""),
        source_problem_count=source_problem_count,
        topic_angle_status=str((topic_angle or {}).get("status") or ""),
        sources=sources,
    )
