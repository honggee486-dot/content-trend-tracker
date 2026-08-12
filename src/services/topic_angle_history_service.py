"""Gemini 글감 분석 저장 결과를 기존 수집 이력에서 집계합니다."""

from __future__ import annotations

from typing import Any

import duckdb


def get_topic_angle_history_summary(
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    """최근 Gemini 글감 분석의 저장률과 누락 건수를 반환합니다.

    별도 테이블이나 마이그레이션을 만들지 않고 기존
    ``collection_runs``·``collection_run_sources`` 기록만 사용합니다.
    """

    bounded_limit = max(1, min(int(limit), 100))
    rows = con.execute(
        """
        SELECT cr.started_at, crs.status, crs.duration_ms,
               crs.request_count, crs.retry_count,
               crs.updated_count, crs.skipped_count,
               crs.newly_saved_count
        FROM collection_run_sources crs
        JOIN collection_runs cr ON cr.run_id = crs.run_id
        WHERE crs.source_name = 'topic_angles'
        ORDER BY cr.started_at DESC, cr.run_id DESC
        LIMIT ?
        """,
        [bounded_limit],
    ).fetchall()

    if not rows:
        return {
            "history_count": 0,
            "last_started_at": None,
            "last_status": "",
            "last_requested_clusters": 0,
            "last_saved_clusters": 0,
            "last_missing_clusters": 0,
            "last_generated_angles": 0,
            "last_duration_ms": None,
            "last_request_count": 0,
            "last_retry_count": 0,
            "total_requested_clusters": 0,
            "total_saved_clusters": 0,
            "total_missing_clusters": 0,
            "save_rate_percent": None,
            "problem_run_count": 0,
            "average_duration_ms": None,
        }

    normalized: list[dict[str, Any]] = []
    for row in rows:
        saved = max(0, int(row[5] or 0))
        missing = max(0, int(row[6] or 0))
        normalized.append(
            {
                "started_at": row[0],
                "status": str(row[1] or ""),
                "duration_ms": None if row[2] is None else max(0, int(row[2] or 0)),
                "request_count": max(0, int(row[3] or 0)),
                "retry_count": max(0, int(row[4] or 0)),
                "saved_clusters": saved,
                "missing_clusters": missing,
                "requested_clusters": saved + missing,
                "generated_angles": max(0, int(row[7] or 0)),
            }
        )

    last = normalized[0]
    total_requested = sum(item["requested_clusters"] for item in normalized)
    total_saved = sum(item["saved_clusters"] for item in normalized)
    durations = [
        int(item["duration_ms"])
        for item in normalized
        if item["duration_ms"] is not None
    ]
    save_rate = (
        round((total_saved / total_requested) * 100, 1)
        if total_requested > 0
        else None
    )

    return {
        "history_count": len(normalized),
        "last_started_at": last["started_at"],
        "last_status": last["status"],
        "last_requested_clusters": last["requested_clusters"],
        "last_saved_clusters": last["saved_clusters"],
        "last_missing_clusters": last["missing_clusters"],
        "last_generated_angles": last["generated_angles"],
        "last_duration_ms": last["duration_ms"],
        "last_request_count": last["request_count"],
        "last_retry_count": last["retry_count"],
        "total_requested_clusters": total_requested,
        "total_saved_clusters": total_saved,
        "total_missing_clusters": sum(
            item["missing_clusters"] for item in normalized
        ),
        "save_rate_percent": save_rate,
        "problem_run_count": sum(
            1
            for item in normalized
            if item["status"] in {"partial_success", "failure"}
        ),
        "average_duration_ms": (
            int(round(sum(durations) / len(durations))) if durations else None
        ),
    }
