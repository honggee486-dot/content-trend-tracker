from __future__ import annotations

import time
from dataclasses import dataclass, replace
from functools import wraps
from queue import Queue
from typing import Any, Callable


@dataclass(frozen=True)
class TopicAngleRecoveryExecution:
    """기존 실행 결과와 누락 ID 전용 보강 요청을 함께 보존합니다."""

    preparation: Any
    results: tuple[Any, ...]
    recovery_results: tuple[Any, ...] = ()


def _cluster_id(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("cluster_id") or "").strip()


def _missing_clusters(result: Any) -> tuple[dict[str, Any], ...]:
    returned_ids = {
        str(cluster_id).strip()
        for cluster_id in dict(getattr(result, "enrichments", {}) or {})
        if str(cluster_id).strip()
    }
    if not returned_ids:
        return ()
    return tuple(
        item
        for item in tuple(getattr(result, "clusters", ()) or ())
        if isinstance(item, dict)
        and _cluster_id(item)
        and _cluster_id(item) not in returned_ids
    )


def recover_partial_topic_angle_execution(
    execution: Any,
    *,
    config: Any,
    sleep_func: Callable[[float], None] = time.sleep,
    progress_callback: Callable[[float, str], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    batch_request_runner: Callable[..., Any] | None = None,
) -> Any:
    """유효 결과가 있는 부분 응답만 누락 ID 대상으로 한 번 보강합니다.

    원래 유효 결과를 다시 요청하지 않고, 각 원본 배치마다 누락·검증 탈락 ID만
    최대 한 번의 보강 배치로 보냅니다. 보강 배치 내부의 공급자 재시도 정책은
    기존 `_execute_batch_request` 계약을 그대로 사용합니다.
    """
    from src.services import topic_angle_ai_service as ai_module

    results = tuple(getattr(execution, "results", ()) or ())
    if not results:
        return execution

    runner = batch_request_runner or ai_module._execute_batch_request
    updated_results: list[Any] = []
    recovery_results: list[Any] = []
    original_batch_count = len(results)

    for result in results:
        missing = _missing_clusters(result)
        if not missing:
            updated_results.append(result)
            continue

        message = (
            f"Gemini 부분 응답에서 누락·검증 탈락 {len(missing):,}개만 "
            "한 번 보강 요청합니다."
        )
        ai_module._emit_progress(
            progress_callback,
            status_callback,
            0.93,
            message,
        )
        recovery = runner(
            batch_number=original_batch_count + len(recovery_results) + 1,
            clusters=list(missing),
            start_delay_seconds=0.0,
            config=config,
            event_queue=Queue(),
            sleep_func=sleep_func,
        )
        recovery_results.append(recovery)

        merged = dict(getattr(result, "enrichments", {}) or {})
        merged.update(dict(getattr(recovery, "enrichments", {}) or {}))
        requested_ids = {
            _cluster_id(item)
            for item in tuple(getattr(result, "clusters", ()) or ())
            if _cluster_id(item)
        }
        remaining_ids = requested_ids - set(merged)
        if not remaining_ids:
            updated_results.append(
                replace(
                    result,
                    enrichments=merged,
                    validation_errors=(),
                    status="success_after_retry",
                    error_type="",
                    error_message="",
                )
            )
        elif len(merged) > len(dict(getattr(result, "enrichments", {}) or {})):
            updated_results.append(replace(result, enrichments=merged))
        else:
            updated_results.append(result)

    if not recovery_results:
        return execution
    return TopicAngleRecoveryExecution(
        preparation=getattr(execution, "preparation", None),
        results=tuple(updated_results),
        recovery_results=tuple(recovery_results),
    )


def _recovery_attempt_count(execution: Any) -> int:
    return sum(
        len(tuple(getattr(result, "attempts", ()) or ()))
        for result in tuple(getattr(execution, "recovery_results", ()) or ())
    )


def install_topic_angle_partial_recovery_contract() -> None:
    """모든 주제 방향 실행 경로에 누락 ID 전용 보강을 설치합니다."""
    from src.services import topic_angle_ai_service as ai_module

    original_execute = getattr(ai_module, "execute_prepared_topic_angles", None)
    original_finalize = getattr(ai_module, "finalize_prepared_topic_angles", None)
    if not callable(original_execute) or not callable(original_finalize):
        return
    if getattr(original_execute, "_topic_angle_partial_recovery_contract", False):
        return

    @wraps(original_execute)
    def recovery_execute(
        preparation,
        *,
        config,
        status_callback=None,
        progress_callback=None,
        sleep_func=time.sleep,
        poll_interval_seconds=0.25,
    ):
        execution = original_execute(
            preparation,
            config=config,
            status_callback=status_callback,
            progress_callback=progress_callback,
            sleep_func=sleep_func,
            poll_interval_seconds=poll_interval_seconds,
        )
        return recover_partial_topic_angle_execution(
            execution,
            config=config,
            sleep_func=sleep_func,
            progress_callback=progress_callback,
            status_callback=status_callback,
        )

    @wraps(original_finalize)
    def recovery_finalize(
        con,
        *,
        config,
        execution,
        status_callback=None,
        progress_callback=None,
    ):
        batch_result = original_finalize(
            con,
            config=config,
            execution=execution,
            status_callback=status_callback,
            progress_callback=progress_callback,
        )
        recovery_results = tuple(
            getattr(execution, "recovery_results", ()) or ()
        )
        if not recovery_results:
            return batch_result

        for recovery in recovery_results:
            ai_module._record_batch_attempts(con, config=config, result=recovery)

        attempts = int(getattr(batch_result, "attempts", 0) or 0) + _recovery_attempt_count(
            execution
        )
        status = str(getattr(batch_result, "status", "") or "")
        requested = int(getattr(batch_result, "requested_clusters", 0) or 0)
        generated = int(getattr(batch_result, "generated_clusters", 0) or 0)
        failed_batches = int(getattr(batch_result, "failed_batches", 0) or 0)
        if requested > 0 and generated == requested and failed_batches == 0:
            status = "success_after_retry"
        return replace(batch_result, attempts=attempts, status=status)

    recovery_execute._topic_angle_partial_recovery_contract = True  # type: ignore[attr-defined]
    recovery_finalize._topic_angle_partial_recovery_contract = True  # type: ignore[attr-defined]
    ai_module.execute_prepared_topic_angles = recovery_execute
    ai_module.finalize_prepared_topic_angles = recovery_finalize
