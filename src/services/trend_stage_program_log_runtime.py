from __future__ import annotations

from functools import wraps
import re
from time import perf_counter
from typing import Any, Callable

from src.services.program_log_service import record_program_event


_COUNT_PATTERN = re.compile(r"([0-9][0-9,]*)\s*개")


def _stage_from_message(message: object) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    if "1차 군집" in text:
        return "1차 군집 구성"
    if "순위 점수" in text or "계산 완료" in text:
        return "통합 순위 점수 계산"
    if "2차 군집" in text or "Flash-Lite" in text or "Gemini" in text:
        return "2차 군집 Gemini 처리"
    return ""


def _item_count(message: object) -> int:
    match = _COUNT_PATTERN.search(str(message or ""))
    if not match:
        return 0
    try:
        return max(0, int(match.group(1).replace(",", "")))
    except ValueError:
        return 0


def install_precise_trend_stage_logging() -> None:
    """순위 계산 내부의 1차·2차 군집과 점수 계산을 별도 단계로 기록합니다."""
    from src.services import trend_discovery_service as module

    original = getattr(module, "calculate_prepared_trend_rankings", None)
    if not callable(original) or getattr(original, "_precise_trend_stage_log", False):
        return

    @wraps(original)
    def wrapped(*args: Any, **kwargs: Any):
        previous_callback = kwargs.get("progress_callback")
        active_action = ""
        active_detail = ""
        active_count = 0
        active_started = 0.0

        def close_active(status: str, *, error: Exception | None = None) -> None:
            nonlocal active_action, active_detail, active_count, active_started
            if not active_action:
                return
            detail = active_detail
            if error is not None:
                detail = (
                    f"{detail} · {type(error).__name__}: {str(error)[:500]}"
                    if detail
                    else f"{type(error).__name__}: {str(error)[:500]}"
                )
            record_program_event(
                event_type="stage",
                status=status,
                source="trend_ranking_stage",
                action=active_action,
                detail=detail,
                item_count=active_count,
                duration_ms=max(0, round((perf_counter() - active_started) * 1000)),
            )
            active_action = ""
            active_detail = ""
            active_count = 0
            active_started = 0.0

        def tracked_progress(value: float, message: str) -> None:
            nonlocal active_action, active_detail, active_count, active_started
            action = _stage_from_message(message)
            detail = str(message or "").strip()
            if action and action != active_action:
                close_active("completed")
                active_action = action
                active_detail = detail
                active_count = _item_count(detail)
                active_started = perf_counter()
                record_program_event(
                    event_type="stage",
                    status="started",
                    source="trend_ranking_stage",
                    action=active_action,
                    detail=active_detail,
                    item_count=active_count,
                )
            elif action:
                active_detail = detail
                active_count = _item_count(detail) or active_count
            if callable(previous_callback):
                previous_callback(value, message)

        call_kwargs = dict(kwargs)
        call_kwargs["progress_callback"] = tracked_progress
        try:
            result = original(*args, **call_kwargs)
        except Exception as exc:
            close_active("failed", error=exc)
            raise
        close_active("completed")
        return result

    wrapped._precise_trend_stage_log = True  # type: ignore[attr-defined]
    module.calculate_prepared_trend_rankings = wrapped
