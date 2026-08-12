"""수집 이력 화면용 필터와 Gemini 결과 요약을 제공합니다."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from src.services.collection_history_service import RUN_STATUS_LABELS
from src.services.trend_clustering_lock_service import inspect_trend_clustering_lock
from src.services.trend_refresh_lock_service import inspect_trend_refresh_lock


GEMINI_STATE_LABELS = {
    "": "전체",
    "complete": "저장 완료",
    "problem": "부분 저장·실패",
    "skipped": "새 분석 없음·API 키 없음",
    "missing": "Gemini 기록 없음",
}
RUN_DISPLAY_STATUS_REVIEW = "needs_review"
RUN_DISPLAY_STATUS_LABELS = {
    **RUN_STATUS_LABELS,
    RUN_DISPLAY_STATUS_REVIEW: "상태 확인 필요",
}
RUNNING_REVIEW_AFTER_HOURS = 6


def inspect_collection_history_lock_state(
    project_root: str | Path,
    *,
    refresh_inspector: Callable[..., Any] = inspect_trend_refresh_lock,
    clustering_inspector: Callable[..., Any] = inspect_trend_clustering_lock,
) -> dict[str, bool]:
    """수집·군집 잠금을 정리하지 않고 heartbeat 기반 활성 상태만 읽습니다."""
    try:
        refresh_status = refresh_inspector(project_root)
        clustering_status = clustering_inspector(project_root)
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


def annotate_collection_run_display_statuses(
    runs: Iterable[dict[str, Any]],
    *,
    lock_state: dict[str, bool],
    now: datetime | None = None,
    review_after_hours: int = RUNNING_REVIEW_AFTER_HOURS,
) -> list[dict[str, Any]]:
    """DB 상태를 바꾸지 않고 오래된 running 이력의 화면 표시 상태만 보정합니다."""
    current = now or datetime.now()
    threshold = timedelta(hours=max(1, int(review_after_hours)))
    lock_state_known = bool(lock_state.get("known"))
    active_operation = bool(
        lock_state.get("refresh_active") or lock_state.get("clustering_active")
    )

    annotated: list[dict[str, Any]] = []
    for run in runs:
        item = dict(run)
        stored_status = str(item.get("status") or "")
        display_status = stored_status
        started_at = item.get("started_at")
        if (
            stored_status == "running"
            and lock_state_known
            and not active_operation
            and isinstance(started_at, datetime)
        ):
            try:
                is_old = current - started_at >= threshold
            except TypeError:
                is_old = False
            if is_old:
                display_status = RUN_DISPLAY_STATUS_REVIEW
        item["display_status"] = display_status
        annotated.append(item)
    return annotated


def list_collection_run_source_map(
    con,
    run_ids: Iterable[object],
) -> dict[str, list[dict[str, Any]]]:
    """여러 실행의 출처별 결과를 한 번의 조회로 묶어 반환합니다."""
    normalized_ids = list(
        dict.fromkeys(str(value or "").strip() for value in run_ids if str(value or "").strip())
    )[:50]
    if not normalized_ids:
        return {}

    placeholders = ", ".join("?" for _ in normalized_ids)
    cursor = con.execute(
        f"""
        SELECT run_id, source_name, status, duration_ms, request_count, retry_count,
               newly_saved_count, updated_count, skipped_count, error_message
        FROM collection_run_sources
        WHERE run_id IN ({placeholders})
        ORDER BY run_id, source_name
        """,
        normalized_ids,
    )
    columns = [str(item[0]) for item in cursor.description]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in cursor.fetchall():
        item = dict(zip(columns, row))
        grouped[str(item.pop("run_id"))].append(item)
    return dict(grouped)


def topic_angle_run_summary(source_rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """한 실행의 Gemini 글감 저장 상태를 필터와 표시에 쓸 형태로 정리합니다."""
    topic_row = next(
        (
            row
            for row in source_rows
            if str(row.get("source_name") or "") == "topic_angles"
        ),
        None,
    )
    if topic_row is None:
        return {
            "category": "missing",
            "label": "기록 없음",
            "saved_clusters": 0,
            "requested_clusters": 0,
            "missing_clusters": 0,
        }

    status = str(topic_row.get("status") or "failure")
    saved = max(0, int(topic_row.get("updated_count") or 0))
    missing = max(0, int(topic_row.get("skipped_count") or 0))
    requested = saved + missing
    error_message = str(topic_row.get("error_message") or "").strip()

    if status == "skipped":
        label = "API 키 없음" if "키" in error_message else "새 분석 없음"
        category = "skipped"
    elif status == "success" and missing == 0:
        label = f"완료 {saved:,}/{requested:,}개"
        category = "complete"
    elif status == "failure":
        label = f"실패 {saved:,}/{requested:,}개"
        category = "problem"
    else:
        label = f"부분 {saved:,}/{requested:,}개"
        category = "problem"

    return {
        "category": category,
        "label": label,
        "saved_clusters": saved,
        "requested_clusters": requested,
        "missing_clusters": missing,
    }


def filter_collection_runs(
    runs: Iterable[dict[str, Any]],
    source_map: dict[str, list[dict[str, Any]]],
    *,
    run_type: str = "",
    run_status: str = "",
    gemini_state: str = "",
) -> list[dict[str, Any]]:
    """화면에서 선택한 조건을 모두 만족하는 실행만 원래 순서대로 반환합니다."""
    normalized_type = str(run_type or "").strip()
    normalized_status = str(run_status or "").strip()
    normalized_gemini = str(gemini_state or "").strip()
    if normalized_gemini not in GEMINI_STATE_LABELS:
        normalized_gemini = ""

    filtered: list[dict[str, Any]] = []
    for run in runs:
        if normalized_type and str(run.get("run_type") or "") != normalized_type:
            continue
        display_status = str(run.get("display_status") or run.get("status") or "")
        if normalized_status and display_status != normalized_status:
            continue
        if normalized_gemini:
            run_id = str(run.get("run_id") or "")
            category = topic_angle_run_summary(source_map.get(run_id, ()))['category']
            if category != normalized_gemini:
                continue
        filtered.append(run)
    return filtered
