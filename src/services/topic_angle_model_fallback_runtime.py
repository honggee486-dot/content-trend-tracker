from __future__ import annotations

from dataclasses import replace
from functools import wraps
from typing import Any


PROTECTED_TOPIC_ANGLE_MODEL = "gemini-3.7-flash"
TOPIC_ANGLE_FALLBACK_MODEL = "gemini-3.6-flash"
_TRANSIENT_FALLBACK_ERRORS = frozenset({"service_unavailable", "request_timeout"})
_FALLBACK_TO_PREFIX = "fallback_to:"
_ATTEMPT_MODEL_PREFIX = "model:"


def _normalized_model(value: object) -> str:
    model = str(value or "").strip().casefold()
    if model.startswith("models/"):
        model = model[7:]
    return model


def _one_shot_config(config: Any, *, model: str | None = None) -> Any:
    """Return an internal config that cannot retry the same provider request.

    The base topic-angle executor retries while accumulated wait is within
    retry_max_wait_seconds. A negative internal sentinel disables that loop without
    changing the public/environment configuration contract.
    """
    changes: dict[str, Any] = {"retry_max_wait_seconds": -1.0}
    if model is not None:
        changes["model"] = model
    return replace(config, **changes)


def _fallback_allowed(config: Any, result: Any) -> bool:
    return (
        _normalized_model(getattr(config, "model", "")) == PROTECTED_TOPIC_ANGLE_MODEL
        and not bool(getattr(result, "enrichments", None))
        and str(getattr(result, "error_type", "") or "").strip().casefold()
        in _TRANSIENT_FALLBACK_ERRORS
    )


def _attempt_model(config: Any, attempt: Any) -> str:
    reason = str(getattr(attempt, "retry_reason", "") or "")
    for part in reason.split("|"):
        clean = part.strip()
        if clean.startswith(_ATTEMPT_MODEL_PREFIX):
            model = clean[len(_ATTEMPT_MODEL_PREFIX) :].strip()
            if model:
                return model
    return str(getattr(config, "model", "") or "").strip()


def _has_fallback_metadata(result: Any) -> bool:
    return any(
        _FALLBACK_TO_PREFIX in str(getattr(item, "retry_reason", "") or "")
        or _ATTEMPT_MODEL_PREFIX in str(getattr(item, "retry_reason", "") or "")
        for item in tuple(getattr(result, "attempts", ()) or ())
    )


def install_topic_angle_model_fallback_contract() -> None:
    """Protect Gemini 3.7 Flash RPD for topic-angle generation.

    Gemini 3.7 Flash gets exactly one provider attempt per prepared batch. Only a
    transient service/timeout failure may fall back once to Gemini 3.6 Flash. 400,
    authentication, permission, model, quota and validation errors are surfaced as-is.
    A successful-but-partial 3.7 response is preserved without an automatic 3.7
    recovery request; missing IDs remain pending for a later run. Other models retain
    the existing retry and partial-recovery policies.
    """
    from src.services import topic_angle_ai_service as ai_module
    from src.services import topic_angle_partial_recovery_runtime as recovery_module

    original_execute = getattr(ai_module, "_execute_batch_request", None)
    original_record = getattr(ai_module, "_record_batch_attempts", None)
    original_save = getattr(ai_module, "_save_batch_enrichments", None)
    original_recover = getattr(
        recovery_module,
        "recover_partial_topic_angle_execution",
        None,
    )
    if not all(
        callable(item)
        for item in (original_execute, original_record, original_save, original_recover)
    ):
        return
    if getattr(original_execute, "_topic_angle_model_fallback_contract", False):
        return

    @wraps(original_execute)
    def protected_execute(
        *,
        batch_number,
        clusters,
        start_delay_seconds,
        config,
        event_queue,
        sleep_func,
    ):
        if _normalized_model(getattr(config, "model", "")) != PROTECTED_TOPIC_ANGLE_MODEL:
            return original_execute(
                batch_number=batch_number,
                clusters=clusters,
                start_delay_seconds=start_delay_seconds,
                config=config,
                event_queue=event_queue,
                sleep_func=sleep_func,
            )

        primary = original_execute(
            batch_number=batch_number,
            clusters=clusters,
            start_delay_seconds=start_delay_seconds,
            config=_one_shot_config(config),
            event_queue=event_queue,
            sleep_func=sleep_func,
        )
        if not _fallback_allowed(config, primary):
            return primary

        primary_attempts = list(tuple(getattr(primary, "attempts", ()) or ()))
        if primary_attempts:
            last = primary_attempts[-1]
            primary_attempts[-1] = replace(
                last,
                status="fallback",
                retry_reason=f"{_FALLBACK_TO_PREFIX}{TOPIC_ANGLE_FALLBACK_MODEL}",
                retry_wait_seconds=0,
            )

        fallback_config = _one_shot_config(config, model=TOPIC_ANGLE_FALLBACK_MODEL)
        fallback = original_execute(
            batch_number=batch_number,
            clusters=clusters,
            start_delay_seconds=0.0,
            config=fallback_config,
            event_queue=event_queue,
            sleep_func=sleep_func,
        )
        fallback_attempts = []
        next_attempt = len(primary_attempts)
        for offset, attempt in enumerate(
            tuple(getattr(fallback, "attempts", ()) or ()),
            start=1,
        ):
            reason = str(getattr(attempt, "retry_reason", "") or "").strip()
            model_marker = f"{_ATTEMPT_MODEL_PREFIX}{TOPIC_ANGLE_FALLBACK_MODEL}"
            fallback_attempts.append(
                replace(
                    attempt,
                    attempt_number=next_attempt + offset,
                    status=(
                        "success_after_fallback"
                        if bool(getattr(fallback, "enrichments", None))
                        and str(getattr(attempt, "status", ""))
                        in {"success", "success_after_retry"}
                        else getattr(attempt, "status", "")
                    ),
                    retry_reason="|".join(
                        value for value in (reason, model_marker) if value
                    ),
                )
            )

        merged_status = (
            "success_after_fallback"
            if bool(getattr(fallback, "enrichments", None))
            else str(getattr(fallback, "status", "") or "")
        )
        return replace(
            fallback,
            batch_number=batch_number,
            attempts=tuple((*primary_attempts, *fallback_attempts)),
            status=merged_status,
        )

    @wraps(original_record)
    def protected_record(con, *, config, result):
        if not _has_fallback_metadata(result):
            return original_record(con, config=config, result=result)

        clusters = list(tuple(getattr(result, "clusters", ()) or ()))
        attempts = tuple(getattr(result, "attempts", ()) or ())
        last_attempt_number = attempts[-1].attempt_number if attempts else 0
        for attempt in attempts:
            actual_model = _attempt_model(config, attempt)
            actual_config = replace(config, model=actual_model)
            _request_text, actual_hash = ai_module._build_request(actual_config, clusters)
            single_result = replace(
                result,
                request_hash=actual_hash,
                attempts=(attempt,),
                response_text=(
                    str(getattr(result, "response_text", "") or "")
                    if attempt.attempt_number == last_attempt_number
                    else ""
                ),
            )
            original_record(con, config=actual_config, result=single_result)
        return None

    @wraps(original_save)
    def protected_save(con, *, config, result):
        attempts = tuple(getattr(result, "attempts", ()) or ())
        actual_model = _attempt_model(config, attempts[-1]) if attempts else config.model
        actual_config = replace(config, model=actual_model)
        return original_save(con, config=actual_config, result=result)

    @wraps(original_recover)
    def protected_recover(execution, *, config, **kwargs):
        if _normalized_model(getattr(config, "model", "")) == PROTECTED_TOPIC_ANGLE_MODEL:
            return execution
        return original_recover(execution, config=config, **kwargs)

    protected_execute._topic_angle_model_fallback_contract = True  # type: ignore[attr-defined]
    protected_record._topic_angle_model_fallback_contract = True  # type: ignore[attr-defined]
    protected_save._topic_angle_model_fallback_contract = True  # type: ignore[attr-defined]
    protected_recover._topic_angle_model_fallback_contract = True  # type: ignore[attr-defined]
    ai_module._execute_batch_request = protected_execute
    ai_module._record_batch_attempts = protected_record
    ai_module._save_batch_enrichments = protected_save
    recovery_module.recover_partial_topic_angle_execution = protected_recover
