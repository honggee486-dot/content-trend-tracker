from __future__ import annotations

from typing import Any, Mapping


def cleanup_preflight_progress_message(cleanup_result: Any) -> str:
    """자동 정리 조건 확인과 실제 삭제 실행 시점을 구분해 표시합니다."""
    if cleanup_result is None:
        return "자동 정리 대상 없음 · 출처 수집 시작"

    executed = getattr(cleanup_result, "executed", None)
    if executed is False:
        return "자동 정리 예약 완료 · 출처 저장 후 순위 준비 직전에 실행"

    return "자동 정리 실행 확인 완료 · 출처 수집 시작"


def _cleanup_deleted_rows(cleanup_result: Any) -> int:
    try:
        return max(0, int(getattr(cleanup_result, "total_rows_deleted", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def post_collection_cleanup_progress_message(
    cleanup_result: Any,
    refresh_result: Any,
) -> str:
    """출처 저장 뒤 정리·순위 처리 결과를 사용자에게 보수적으로 설명합니다."""
    ranking = (
        refresh_result.get("ranking")
        if isinstance(refresh_result, Mapping)
        else None
    )
    ranking_status = (
        str(ranking.get("status") or "").strip()
        if isinstance(ranking, Mapping)
        else ""
    )

    if ranking_status == "skipped_source_failure":
        if cleanup_result is not None and getattr(cleanup_result, "executed", None) is False:
            return "전체 출처 실패 · 예약된 자동 정리 보류 및 기존 순위·보존 자료 유지"
        return "전체 출처 실패 · 기존 순위·보존 자료 유지"

    if ranking_status == "skipped_overlap":
        if cleanup_result is None:
            return "출처 저장 완료 · 기존 군집 작업으로 순위 재계산 보류"
        if getattr(cleanup_result, "executed", None) is False:
            return "출처 저장 완료 · 자동 정리 실행 상태 확인 필요 · 순위 재계산 보류"
        deleted = _cleanup_deleted_rows(cleanup_result)
        return f"출처 저장 후 자동 정리 {deleted:,}건 · 기존 군집 작업으로 순위 재계산 보류"

    if cleanup_result is None:
        return "출처 저장 및 군집·순위 처리 완료 · 자동 정리 대상 없음"

    executed = getattr(cleanup_result, "executed", None)
    if executed is False:
        return "출처 저장 완료 · 자동 정리 실행 상태 확인 필요 · 군집·순위 처리 완료"

    deleted = _cleanup_deleted_rows(cleanup_result)
    return f"출처 저장 후 자동 정리 {deleted:,}건 · 군집·순위 처리 완료"
