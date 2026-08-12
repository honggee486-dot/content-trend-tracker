"""전체 수집·순위 실행과 출처별 결과를 짧은 DB 작업으로 기록합니다."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import duckdb

from src.services.portal_request_ledger_service import (
    activate_portal_request_capture,
    discard_portal_request_capture,
    flush_portal_request_capture,
)
from src.services.portal_request_schema_service import ensure_portal_request_ledger_schema


RUN_TYPE_LABELS = {
    "background_refresh": "예약·백그라운드 수집",
    "manual_refresh": "화면 수동 수집",
    "ranking_rebuild": "저장 데이터 순위 재계산",
    "topic_angle_generation": "Gemini 주제 방향 자동 생성",
}

RUN_STATUS_LABELS = {
    "running": "실행 중",
    "success": "전체 성공",
    "partial_success": "부분 성공",
    "skipped_overlap": "중복 실행 생략",
    "failure": "실패",
}

SOURCE_LABELS = {
    "youtube": "YouTube",
    "google_trends": "Google Trends",
    "wikipedia": "위키백과",
    "naver": "NAVER 뉴스·블로그",
    "daum": "Daum 웹문서·카페",
    "ranking": "통합 군집·순위 계산",
    "topic_angles": "Gemini 주제 방향",
}

_REFRESH_SOURCE_KEYS = tuple(
    key for key in SOURCE_LABELS if key not in {"ranking", "topic_angles"}
)
_REFRESH_RUN_TYPES = {"background_refresh", "manual_refresh"}
_VALID_RUN_TYPES = frozenset(RUN_TYPE_LABELS)
_STALE_RUNNING_REASON = "이전 실행이 정상적으로 종료되지 않은 것으로 추정합니다."
_MAX_MESSAGE_LENGTH = 1000


def run_type_for_dashboard_action(action: str) -> str:
    mapping = {
        "refresh": "manual_refresh",
        "rebuild": "ranking_rebuild",
        "angles": "topic_angle_generation",
    }
    try:
        return mapping[str(action or "").strip()]
    except KeyError as exc:
        raise ValueError(f"지원하지 않는 화면 실행 작업입니다: {action}") from exc


def _short_message(value: Any) -> str:
    return str(value or "").strip()[:_MAX_MESSAGE_LENGTH]


def _int_value(mapping: dict[str, Any], key: str) -> int:
    try:
        return max(0, int(mapping.get(key, 0) or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _duration_ms(seconds: Any) -> int | None:
    try:
        return max(0, int(round(float(seconds or 0.0) * 1000)))
    except (TypeError, ValueError, OverflowError):
        return None


def _source_error(result: dict[str, Any], source_key: str) -> str:
    errors = result.get("errors") or {}
    warnings = result.get("warnings") or {}
    return _short_message(errors.get(source_key) or warnings.get(source_key))


def build_refresh_source_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """현재 수집 결과 의미를 유지한 채 이력용 정규화 행을 만듭니다."""
    rows: list[dict[str, Any]] = []
    timings = result.get("timings") or {}
    errors = result.get("errors") or {}
    warnings = result.get("warnings") or {}

    for source_key in _REFRESH_SOURCE_KEYS:
        source_result = result.get(source_key)
        error_message = _source_error(result, source_key)
        if not isinstance(source_result, dict):
            if source_key not in errors and source_key not in warnings:
                continue
            raw_status = "failure" if source_key in errors else "partial_success"
            source_result = {}
        else:
            status = str(source_result.get("status") or "success")
            raw_status = {
                "success": "success",
                "partial": "partial_success",
                "failed": "failure",
                "skipped": "skipped",
            }.get(status, "success")
            if source_key in errors:
                raw_status = "failure"
            elif source_key in warnings and raw_status == "success":
                raw_status = "partial_success"

        rows.append(
            {
                "source_name": source_key,
                "status": raw_status,
                "duration_ms": _duration_ms(timings.get(source_key)),
                "request_count": _int_value(source_result, "request_count"),
                "retry_count": _int_value(source_result, "retry_count"),
                "newly_saved_count": _int_value(source_result, "items_added"),
                "updated_count": _int_value(source_result, "items_updated"),
                "skipped_count": _int_value(source_result, "items_skipped"),
                "error_message": error_message,
            }
        )
    return rows


def build_ranking_source_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    timings = result.get("timings") or {}
    return [
        {
            "source_name": "ranking",
            "status": "success",
            "duration_ms": _duration_ms(timings.get("total")),
            "request_count": 0,
            "retry_count": 0,
            "newly_saved_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "error_message": "",
        }
    ]


def build_topic_angle_source_rows(
    result: dict[str, Any],
    *,
    optional: bool = False,
) -> list[dict[str, Any]]:
    """Gemini 글감 분석 결과를 수집 이력의 전용 행으로 정규화합니다."""
    requested = _int_value(result, "requested_clusters")
    generated_clusters = _int_value(result, "generated_clusters")
    generated_angles = _int_value(result, "generated_angles")
    attempts = _int_value(result, "attempts")
    requested_batches = _int_value(result, "requested_batches")
    raw_status = str(result.get("status") or "failure")

    if raw_status == "nothing_to_generate":
        status = "skipped"
    elif raw_status == "missing_api_key" and optional:
        status = "skipped"
    elif generated_clusters > 0 and generated_clusters < requested:
        status = "partial_success"
    elif raw_status in {"success", "success_after_retry"}:
        status = "success"
    elif generated_clusters > 0:
        status = "partial_success"
    else:
        status = "failure"

    request_count = attempts or requested_batches
    retry_count = (
        max(0, attempts - requested_batches)
        if requested_batches > 0
        else max(0, attempts - 1)
    )
    return [
        {
            "source_name": "topic_angles",
            "status": status,
            "duration_ms": _duration_ms(result.get("duration_seconds")),
            "request_count": request_count,
            "retry_count": retry_count,
            "newly_saved_count": generated_angles,
            "updated_count": generated_clusters,
            "skipped_count": max(0, requested - generated_clusters),
            "error_message": _short_message(result.get("error_message")),
        }
    ]


def _aggregate_source_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    succeeded = sum(
        1 for row in rows if row["status"] in {"success", "partial_success", "skipped"}
    )
    failed = sum(1 for row in rows if row["status"] in {"partial_success", "failure"})
    return {
        "source_count": len(rows),
        "succeeded_source_count": succeeded,
        "failed_source_count": failed,
        "request_count": sum(_int_value(row, "request_count") for row in rows),
        "retry_count": sum(_int_value(row, "retry_count") for row in rows),
        "newly_saved_count": sum(_int_value(row, "newly_saved_count") for row in rows),
        "updated_count": sum(_int_value(row, "updated_count") for row in rows),
        "skipped_count": sum(_int_value(row, "skipped_count") for row in rows),
    }


def _overall_status(rows: list[dict[str, Any]]) -> str:
    has_useful_result = any(
        row["status"] in {"success", "partial_success", "skipped"} for row in rows
    )
    has_failure = any(row["status"] in {"partial_success", "failure"} for row in rows)
    if has_failure and has_useful_result:
        return "partial_success"
    if has_failure:
        return "failure"
    return "success"


def start_collection_run(
    con: duckdb.DuckDBPyConnection,
    run_type: str,
    *,
    started_at: datetime | None = None,
    stale_after_hours: int = 6,
) -> str:
    if run_type not in _VALID_RUN_TYPES:
        raise ValueError(f"지원하지 않는 실행 유형입니다: {run_type}")
    current = started_at or datetime.now()
    # 하위 호환을 위해 인수는 유지하지만 과거 running 이력을 시작 시 자동 변경하지 않습니다.
    _ = stale_after_hours
    run_id = f"collection_{uuid4().hex}"
    con.execute(
        """
        INSERT INTO collection_runs(run_id, run_type, status, started_at, created_at)
        VALUES (?, ?, 'running', ?, ?)
        """,
        [run_id, run_type, current, current],
    )
    if run_type in _REFRESH_RUN_TYPES:
        ensure_portal_request_ledger_schema(con)
        activate_portal_request_capture(run_id)
    return run_id


def finish_collection_run(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    *,
    result: dict[str, Any] | None = None,
    error: BaseException | str | None = None,
    finished_at: datetime | None = None,
) -> str:
    run = con.execute(
        "SELECT run_type, status, started_at FROM collection_runs WHERE run_id = ?",
        [run_id],
    ).fetchone()
    if run is None:
        discard_portal_request_capture(run_id)
        raise ValueError(f"실행 기록을 찾을 수 없습니다: {run_id}")
    run_type, current_status, started_at = str(run[0]), str(run[1]), run[2]
    if current_status != "running":
        discard_portal_request_capture(run_id)
        return current_status

    if run_type in _REFRESH_RUN_TYPES:
        ensure_portal_request_ledger_schema(con)
        flush_portal_request_capture(con, run_id)

    current = finished_at or datetime.now()
    if error is not None:
        rows: list[dict[str, Any]] = []
        aggregate_rows = rows
        status = "failure"
        error_message = _short_message(error)
        summary = "실행 중 복구할 수 없는 오류가 발생했습니다."
    elif run_type == "ranking_rebuild":
        rows = build_ranking_source_rows(result or {})
        aggregate_rows = rows
        status = "success"
        error_message = ""
        ranking_result = result or {}
        summary = (
            f"신호 {_int_value(ranking_result, 'items'):,}개 · "
            f"통합 주제 {_int_value(ranking_result, 'clusters'):,}개"
        )
    elif run_type == "topic_angle_generation":
        angle_result = result or {}
        rows = build_topic_angle_source_rows(angle_result)
        aggregate_rows = rows
        status = _overall_status(rows)
        error_message = _short_message(angle_result.get("error_message"))
        summary = (
            f"요청 글감 {_int_value(angle_result, 'requested_clusters'):,}개 · "
            f"저장 글감 {_int_value(angle_result, 'generated_clusters'):,}개 · "
            f"방향 {_int_value(angle_result, 'generated_angles'):,}개"
        )
    else:
        refresh_result = result or {}
        refresh_rows = build_refresh_source_rows(refresh_result)
        rows = list(refresh_rows)
        aggregate_rows = refresh_rows
        status = _overall_status(refresh_rows)
        errors = refresh_result.get("errors") or {}
        warnings = refresh_result.get("warnings") or {}
        error_parts = [str(value) for value in [*errors.values(), *warnings.values()] if value]
        refresh_totals = _aggregate_source_rows(refresh_rows)
        summary = (
            f"출처 {len(refresh_rows)}개 · 성공 {refresh_totals['succeeded_source_count']}개 · "
            f"실패 {refresh_totals['failed_source_count']}개"
        )

        angle_result = refresh_result.get("topic_angles")
        if isinstance(angle_result, dict):
            rows.extend(build_topic_angle_source_rows(angle_result, optional=True))
            angle_status = str(angle_result.get("status") or "")
            requested = _int_value(angle_result, "requested_clusters")
            generated = _int_value(angle_result, "generated_clusters")
            directions = _int_value(angle_result, "generated_angles")
            if angle_status == "missing_api_key":
                angle_summary = "Gemini API 키 없음"
            elif angle_status == "nothing_to_generate":
                angle_summary = "Gemini 새 분석 없음"
            else:
                angle_summary = (
                    f"Gemini 글감 {generated:,}/{requested:,}개 · 방향 {directions:,}개"
                )
            summary += f" · {angle_summary}"
            if angle_result.get("error_message"):
                error_parts.append(f"Gemini: {angle_result['error_message']}")

        error_message = _short_message(" | ".join(error_parts))

    totals = _aggregate_source_rows(aggregate_rows)
    duration_ms = max(0, int(round((current - started_at).total_seconds() * 1000)))
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            """
            UPDATE collection_runs
            SET status = ?, finished_at = ?, duration_ms = ?, source_count = ?,
                succeeded_source_count = ?, failed_source_count = ?, request_count = ?,
                retry_count = ?, newly_saved_count = ?, updated_count = ?, skipped_count = ?,
                summary = ?, error_message = ?
            WHERE run_id = ? AND status = 'running'
            """,
            [
                status,
                current,
                duration_ms,
                totals["source_count"],
                totals["succeeded_source_count"],
                totals["failed_source_count"],
                totals["request_count"],
                totals["retry_count"],
                totals["newly_saved_count"],
                totals["updated_count"],
                totals["skipped_count"],
                _short_message(summary),
                error_message,
                run_id,
            ],
        )
        if rows:
            con.executemany(
                """
                INSERT INTO collection_run_sources(
                    run_id, source_name, status, duration_ms, request_count, retry_count,
                    newly_saved_count, updated_count, skipped_count, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, source_name) DO UPDATE SET
                    status = EXCLUDED.status,
                    duration_ms = EXCLUDED.duration_ms,
                    request_count = EXCLUDED.request_count,
                    retry_count = EXCLUDED.retry_count,
                    newly_saved_count = EXCLUDED.newly_saved_count,
                    updated_count = EXCLUDED.updated_count,
                    skipped_count = EXCLUDED.skipped_count,
                    error_message = EXCLUDED.error_message
                """,
                [
                    [
                        run_id,
                        row["source_name"],
                        row["status"],
                        row["duration_ms"],
                        row["request_count"],
                        row["retry_count"],
                        row["newly_saved_count"],
                        row["updated_count"],
                        row["skipped_count"],
                        row["error_message"],
                    ]
                    for row in rows
                ],
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return status


def record_skipped_overlap(
    con: duckdb.DuckDBPyConnection,
    run_type: str,
    *,
    summary: str = "다른 수집 또는 순위 작업이 실행 중이어서 생략했습니다.",
    recorded_at: datetime | None = None,
) -> str:
    if run_type not in _VALID_RUN_TYPES:
        raise ValueError(f"지원하지 않는 실행 유형입니다: {run_type}")
    current = recorded_at or datetime.now()
    run_id = f"collection_{uuid4().hex}"
    con.execute(
        """
        INSERT INTO collection_runs(
            run_id, run_type, status, started_at, finished_at, duration_ms,
            summary, created_at
        ) VALUES (?, ?, 'skipped_overlap', ?, ?, 0, ?, ?)
        """,
        [run_id, run_type, current, current, _short_message(summary), current],
    )
    return run_id


def list_recent_collection_runs(
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int = 30,
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 50))
    cursor = con.execute(
        """
        SELECT run_id, run_type, status, started_at, finished_at, duration_ms,
               source_count, succeeded_source_count, failed_source_count,
               request_count, retry_count, newly_saved_count, updated_count,
               skipped_count, summary, error_message, created_at
        FROM collection_runs
        ORDER BY started_at DESC, created_at DESC
        LIMIT ?
        """,
        [bounded_limit],
    )
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def list_collection_run_sources(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
) -> list[dict[str, Any]]:
    cursor = con.execute(
        """
        SELECT source_name, status, duration_ms, request_count, retry_count,
               newly_saved_count, updated_count, skipped_count, error_message
        FROM collection_run_sources
        WHERE run_id = ?
        ORDER BY source_name
        """,
        [run_id],
    )
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def get_collection_history_summary(
    con: duckdb.DuckDBPyConnection,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now()
    last_background = con.execute(
        """
        SELECT started_at, status
        FROM collection_runs
        WHERE run_type = 'background_refresh'
        ORDER BY started_at DESC LIMIT 1
        """
    ).fetchone()
    last_success = con.execute(
        """
        SELECT finished_at
        FROM collection_runs
        WHERE status = 'success'
        ORDER BY finished_at DESC LIMIT 1
        """
    ).fetchone()
    recent_problem_count = int(
        con.execute(
            """
            SELECT COUNT(*) FROM collection_runs
            WHERE started_at >= ? AND status IN ('partial_success', 'failure')
            """,
            [current - timedelta(hours=24)],
        ).fetchone()[0]
        or 0
    )
    statuses = con.execute(
        """
        SELECT status FROM collection_runs
        WHERE status NOT IN ('running', 'skipped_overlap')
        ORDER BY started_at DESC LIMIT 500
        """
    ).fetchall()
    consecutive_successes = 0
    for (status,) in statuses:
        if str(status) != "success":
            break
        consecutive_successes += 1
    last_success_at = last_success[0] if last_success else None
    return {
        "last_background_at": last_background[0] if last_background else None,
        "last_background_status": str(last_background[1]) if last_background else None,
        "last_success_at": last_success_at,
        "elapsed_since_success": current - last_success_at if last_success_at else None,
        "consecutive_success_count": consecutive_successes,
        "recent_problem_count": recent_problem_count,
    }


def cleanup_collection_history(
    con: duckdb.DuckDBPyConnection,
    *,
    retention_days: int = 90,
    now: datetime | None = None,
) -> int:
    cutoff = (now or datetime.now()) - timedelta(days=max(1, int(retention_days)))
    deleted = int(
        con.execute(
            "SELECT COUNT(*) FROM collection_runs WHERE started_at < ?",
            [cutoff],
        ).fetchone()[0]
        or 0
    )
    ensure_portal_request_ledger_schema(con)
    con.execute(
        """
        DELETE FROM collection_query_requests
        WHERE run_id IN (SELECT run_id FROM collection_runs WHERE started_at < ?)
        """,
        [cutoff],
    )
    con.execute(
        """
        DELETE FROM collection_query_discoveries
        WHERE run_id IN (SELECT run_id FROM collection_runs WHERE started_at < ?)
        """,
        [cutoff],
    )
    con.execute(
        """
        DELETE FROM collection_run_sources
        WHERE run_id IN (SELECT run_id FROM collection_runs WHERE started_at < ?)
        """,
        [cutoff],
    )
    con.execute("DELETE FROM collection_runs WHERE started_at < ?", [cutoff])
    return deleted
