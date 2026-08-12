"""예약 Gemini 글감 응답의 누락·검증 탈락 ID를 명시적으로 기록합니다."""

from __future__ import annotations

from dataclasses import is_dataclass, replace
from typing import Any


RESPONSE_PARTIAL_ERROR_TYPE = "response_partial"


def _cluster_ids(clusters: object) -> set[str]:
    result: set[str] = set()
    for item in clusters if isinstance(clusters, (list, tuple)) else ():
        if not isinstance(item, dict):
            continue
        cluster_id = str(item.get("cluster_id") or "").strip()
        if cluster_id:
            result.add(cluster_id)
    return result


def _missing_message(missing_ids: tuple[str, ...]) -> str:
    preview = ", ".join(missing_ids[:5])
    suffix = f" 외 {len(missing_ids) - 5}개" if len(missing_ids) > 5 else ""
    return (
        f"Gemini 유효 결과에서 요청한 cluster_id {len(missing_ids)}개가 "
        f"누락 또는 검증 탈락했습니다: {preview}{suffix}"
    )


def annotate_missing_topic_angle_ids(execution: Any) -> Any:
    """Return a frozen execution copy whose last attempts expose omitted IDs.

    기존 응답·유효 결과는 바꾸지 않고, 일부 ID가 유효 결과에서 빠진 배치의
    상태와 오류 메시지만 보강합니다. 이후 기존 finalize 함수가 유효 결과를
    저장하고 누락·검증 탈락 항목은 다음 실행 대상으로 남깁니다.

    기존 호출부·테스트가 사용하는 단순 호환 객체는 dataclass가 아니므로
    수정하지 않고 그대로 통과시킵니다.
    """
    if not is_dataclass(execution):
        return execution

    updated_results = []
    for result in tuple(getattr(execution, "results", ()) or ()):
        requested_ids = _cluster_ids(getattr(result, "clusters", ()))
        returned_ids = {
            str(value).strip()
            for value in dict(getattr(result, "enrichments", {}) or {})
            if str(value).strip()
        }
        missing_ids = tuple(sorted(requested_ids - returned_ids))
        if not missing_ids or not returned_ids:
            updated_results.append(result)
            continue

        message = _missing_message(missing_ids)
        validation_errors = tuple(getattr(result, "validation_errors", ()) or ())
        if message not in validation_errors:
            validation_errors = (*validation_errors, message)

        attempts = list(getattr(result, "attempts", ()) or ())
        if attempts:
            last = attempts[-1]
            attempts[-1] = replace(
                last,
                status=RESPONSE_PARTIAL_ERROR_TYPE,
                error_type=RESPONSE_PARTIAL_ERROR_TYPE,
                error_message=message,
            )

        updated_results.append(
            replace(
                result,
                validation_errors=validation_errors,
                attempts=tuple(attempts),
                status=RESPONSE_PARTIAL_ERROR_TYPE,
                error_type=RESPONSE_PARTIAL_ERROR_TYPE,
                error_message=message,
            )
        )
    return replace(execution, results=tuple(updated_results))


def topic_angle_integrity_message(execution: Any) -> str:
    messages: list[str] = []
    for result in tuple(getattr(execution, "results", ()) or ()):
        if str(getattr(result, "error_type", "") or "") != RESPONSE_PARTIAL_ERROR_TYPE:
            continue
        message = str(getattr(result, "error_message", "") or "").strip()
        if message and message not in messages:
            messages.append(message)
    return "; ".join(messages[:8])


def apply_integrity_to_batch_result(batch_result: Any, execution: Any) -> Any:
    """Preserve normal saving while surfacing a precise partial-success reason."""
    message = topic_angle_integrity_message(execution)
    if not message:
        return batch_result
    existing_message = str(getattr(batch_result, "error_message", "") or "").strip()
    combined = "; ".join(value for value in (message, existing_message) if value)
    existing_error_type = str(getattr(batch_result, "error_type", "") or "").strip()
    generated_clusters = int(getattr(batch_result, "generated_clusters", 0) or 0)
    if existing_error_type and existing_error_type != RESPONSE_PARTIAL_ERROR_TYPE:
        return replace(batch_result, error_message=combined)
    if generated_clusters <= 0:
        return replace(batch_result, error_message=combined)
    return replace(
        batch_result,
        status="partial_success",
        error_type=RESPONSE_PARTIAL_ERROR_TYPE,
        error_message=combined,
    )
