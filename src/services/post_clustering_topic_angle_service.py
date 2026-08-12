from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, ContextManager

from src.config import DEFAULT_DB_PATH
from src.database import connect_database
from src.services.program_log_context import program_log_correlation
from src.services.program_log_service import record_program_event

TopicAngleRunner = Callable[[str | Path], tuple[dict[str, object], str]]
ConnectionFactory = Callable[[str | Path], ContextManager[Any]]


def _default_topic_angle_runner(
    db_path: str | Path,
) -> tuple[dict[str, object], str]:
    # 예약 수집과 동일한 짧은 DB 연결·Gemini 실행·무결성 검사 경로를 재사용합니다.
    from scripts.refresh_trends import _run_background_topic_angles

    return _run_background_topic_angles(db_path)


def run_topic_angles_after_clustering(
    job_id: str,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    runner: TopicAngleRunner | None = None,
    connection_factory: ConnectionFactory = connect_database,
) -> dict[str, object]:
    """군집 결과가 저장된 success·partial 작업 뒤 누락된 주제 방향 한 묶음을 생성합니다."""
    database = Path(db_path).resolve()
    with connection_factory(database) as con:
        row = con.execute(
            """
            SELECT status, remaining_items
            FROM trend_clustering_jobs
            WHERE job_id = ?
            """,
            [str(job_id)],
        ).fetchone()

    if row is None:
        return {
            "status": "job_not_found",
            "generated_clusters": 0,
            "generated_angles": 0,
        }

    job_status = str(row[0] or "")
    remaining_items = max(0, int(row[1] or 0))
    if job_status not in {"success", "partial"}:
        return {
            "status": "deferred_for_clustering_backlog",
            "job_status": job_status,
            "remaining_items": remaining_items,
            "generated_clusters": 0,
            "generated_angles": 0,
        }

    active_runner = runner or _default_topic_angle_runner
    with program_log_correlation(job_id):
        backlog_detail = (
            f"군집 미처리 {remaining_items:,}개 남음 · "
            "현재 저장 완료 군집의 누락 방향 생성"
            if remaining_items > 0
            else "군집 미처리 0개 확인 · 누락된 주제 방향 생성"
        )
        record_program_event(
            event_type="task",
            status="started",
            source="post_clustering_topic_angles",
            action="2단계 군집 후 주제 방향 자동 생성",
            detail=backlog_detail,
            correlation_id=job_id,
            db_path=database,
        )
        try:
            payload, warning = active_runner(database)
        except Exception as exc:
            payload = {
                "status": "unexpected_error",
                "generated_clusters": 0,
                "generated_angles": 0,
                "error_message": str(exc),
            }
            warning = str(exc)

        result = dict(payload or {})
        result.setdefault("status", "unknown")
        result.setdefault("generated_clusters", 0)
        result.setdefault("generated_angles", 0)
        result["clustering_remaining_items"] = remaining_items
        result["resumed_with_clustering_backlog"] = remaining_items > 0
        status = str(result.get("status") or "unknown")
        failed = status in {"unexpected_error", "failed", "error"}
        generated_clusters = max(0, int(result.get("generated_clusters") or 0))
        generated_angles = max(0, int(result.get("generated_angles") or 0))
        detail = (
            f"상태 {status} · 글감 {generated_clusters:,}개 · "
            f"방향 {generated_angles:,}개"
        )
        if remaining_items > 0:
            detail += f" · 군집 미처리 {remaining_items:,}개는 다음 작업에서 계속 처리"
        if warning:
            detail += f" · {str(warning)[:700]}"
        record_program_event(
            event_type="task",
            status="failed" if failed else "completed",
            source="post_clustering_topic_angles",
            action="2단계 군집 후 주제 방향 자동 생성",
            detail=detail,
            item_count=generated_clusters,
            correlation_id=job_id,
            db_path=database,
        )
        return result
