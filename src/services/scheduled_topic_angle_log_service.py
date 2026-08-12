from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from src.config import DEFAULT_DB_PATH
from src.services.program_log_service import record_program_event

SCHEDULED_TOPIC_ANGLE_ACTION = "예약 수집 후 주제 방향 자동 생성"

_FAILED_STATUSES = {"unexpected_error", "failed", "error"}
_SKIPPED_STATUSES = {
    "deferred_for_clustering_backlog",
    "missing_api_key",
    "nothing_to_generate",
}

RefreshBody = Callable[[str | None], tuple[int, dict[str, object]]]
OutcomeRecorder = Callable[..., bool]


def _int_value(payload: Mapping[str, Any], key: str) -> int:
    return max(0, int(payload.get(key, 0) or 0))


def _float_value(payload: Mapping[str, Any], key: str) -> float:
    return max(0.0, float(payload.get(key, 0.0) or 0.0))


def build_scheduled_topic_angle_event(
    topic_angle_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """예약 수집의 주제 방향 결과를 운영 로그 한 행으로 정규화합니다."""
    if not isinstance(topic_angle_result, Mapping):
        return {
            "status": "failed",
            "detail": "주제 방향 실행 결과가 반환되지 않았습니다.",
            "item_count": 0,
            "duration_ms": 0,
            "metadata": {"topic_angle_status": "missing_result"},
        }

    status = str(topic_angle_result.get("status") or "unknown").strip()
    requested_clusters = _int_value(topic_angle_result, "requested_clusters")
    generated_clusters = _int_value(topic_angle_result, "generated_clusters")
    generated_angles = _int_value(topic_angle_result, "generated_angles")
    remaining_items = _int_value(topic_angle_result, "remaining_items")
    clustering_remaining_items = _int_value(
        topic_angle_result,
        "clustering_remaining_items",
    )
    resumed_with_backlog = bool(
        topic_angle_result.get("resumed_with_clustering_backlog")
    )
    duration_ms = round(_float_value(topic_angle_result, "duration_seconds") * 1000)
    warning = str(topic_angle_result.get("error_message") or "").strip()

    if status == "deferred_for_clustering_backlog":
        event_status = "skipped"
        detail = (
            f"군집 미처리 {remaining_items:,}개가 남아 "
            "주제 방향 생성을 보류했습니다."
        )
    elif status == "nothing_to_generate":
        event_status = "skipped"
        detail = "새로 생성할 주제 방향 대상이 없습니다."
    elif status == "missing_api_key":
        event_status = "skipped"
        detail = "Gemini API 키가 없어 주제 방향 생성을 시작하지 않았습니다."
    elif status in _FAILED_STATUSES:
        event_status = "failed"
        detail = warning or f"주제 방향 생성이 {status} 상태로 종료됐습니다."
    else:
        event_status = "completed"
        detail = (
            f"대상 {requested_clusters:,}개 · 글감 {generated_clusters:,}개 · "
            f"방향 {generated_angles:,}개 저장"
        )
        if resumed_with_backlog and clustering_remaining_items > 0:
            detail += (
                f" · 군집 미처리 {clustering_remaining_items:,}개는 "
                "다음 실행에서 계속 처리"
            )
        if warning:
            detail += f" · 주의: {warning}"

    return {
        "status": event_status,
        "detail": detail,
        "item_count": generated_clusters,
        "duration_ms": duration_ms,
        "metadata": {
            "topic_angle_status": status,
            "requested_clusters": requested_clusters,
            "generated_clusters": generated_clusters,
            "generated_angles": generated_angles,
            "remaining_items": remaining_items,
            "clustering_remaining_items": clustering_remaining_items,
            "resumed_with_clustering_backlog": resumed_with_backlog,
        },
    }


def record_scheduled_topic_angle_outcome(
    refresh_result: Mapping[str, Any] | None,
    *,
    collection_run_id: str | None,
    db_path: str | Path = DEFAULT_DB_PATH,
    recorder: OutcomeRecorder = record_program_event,
) -> bool:
    """예약 수집마다 주제 방향 완료·생략·보류·실패 중 하나를 기록합니다."""
    topic_angle_result = (
        refresh_result.get("topic_angles")
        if isinstance(refresh_result, Mapping)
        else None
    )
    event = build_scheduled_topic_angle_event(
        topic_angle_result if isinstance(topic_angle_result, Mapping) else None
    )
    return bool(
        recorder(
            event_type="task",
            status=str(event["status"]),
            source="scheduled_topic_angles",
            action=SCHEDULED_TOPIC_ANGLE_ACTION,
            detail=str(event["detail"]),
            item_count=int(event["item_count"]),
            duration_ms=int(event["duration_ms"]),
            correlation_id=str(collection_run_id or ""),
            metadata=dict(event["metadata"]),
            db_path=db_path,
        )
    )


def run_refresh_body_with_topic_angle_log(
    runner: RefreshBody,
    collection_run_id: str | None = None,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    outcome_recorder: Callable[..., bool] = record_scheduled_topic_angle_outcome,
) -> tuple[int, dict[str, object]]:
    """예약 수집 본문 결과를 반환하면서 주제 방향 결과를 한 번 기록합니다."""
    exit_code, result = runner(collection_run_id)
    outcome_recorder(
        result,
        collection_run_id=collection_run_id,
        db_path=db_path,
    )
    return exit_code, result
