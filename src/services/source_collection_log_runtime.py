from __future__ import annotations

from datetime import datetime
from functools import wraps
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping

from src.config import DEFAULT_DB_PATH
from src.services.program_log_service import record_program_event

_SOURCE_SPECS = (
    ("youtube_adapter", "youtube", "YouTube 교환 파일 읽기", "file"),
    ("naver_adapter", "naver", "NAVER 검색 API", "api"),
    ("daum_adapter", "daum", "Daum 검색 API", "api"),
    ("google_trends_adapter", "google_trends", "Google Trends RSS", "api"),
    ("wikipedia_adapter", "wikipedia", "Wikimedia Pageviews API", "api"),
)


def _db_path(args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> Path:
    value = kwargs.get("db_path")
    if value is None and args and isinstance(args[0], (str, Path)):
        value = args[0]
    return Path(value or DEFAULT_DB_PATH).resolve()


def _source_result(result: Any, key: str) -> Mapping[str, Any]:
    if not isinstance(result, Mapping):
        return {}
    value = result.get(key)
    return value if isinstance(value, Mapping) else {}


def _source_duration_ms(result: Any, key: str) -> int:
    if not isinstance(result, Mapping):
        return 0
    timings = result.get("timings")
    if not isinstance(timings, Mapping):
        return 0
    try:
        return max(0, round(float(timings.get(key) or 0.0) * 1000))
    except (TypeError, ValueError, OverflowError):
        return 0


def _elapsed_ms(started_at: datetime, finished_at: datetime) -> int:
    try:
        return max(0, round((finished_at - started_at).total_seconds() * 1000))
    except (TypeError, ValueError, OverflowError):
        return 0


def _progress_boundaries(message: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    text = str(message or "")
    starts: list[str] = []
    completes: list[str] = []
    if "YouTube" in text:
        starts.append("youtube")
    if "Google Trends" in text:
        completes.append("youtube")
        starts.append("google_trends")
    if "위키백과" in text:
        completes.append("google_trends")
        starts.append("wikipedia")
    if "포털 탐색어" in text or "NAVER·Daum 동시 수집 준비" in text:
        completes.append("wikipedia")
    if "NAVER·Daum 네트워크" in text:
        starts.extend(("naver", "daum"))
    if "통합 군집" in text or "통합 순위" in text:
        completes.extend(("naver", "daum"))
    if "최신 데이터 분석 완료" in text:
        completes.extend(("youtube", "google_trends", "wikipedia", "naver", "daum"))
    return tuple(dict.fromkeys(starts)), tuple(dict.fromkeys(completes))


def install_source_collection_logging() -> None:
    """출처별 실제 시작·완료 시각과 요청·재시도 결과를 기록합니다."""
    from src.services import trend_discovery_service as module

    original = getattr(module, "refresh_trend_sources_short_connections", None)
    if not callable(original) or getattr(original, "_source_collection_program_log", False):
        return

    @wraps(original)
    def wrapped(*args, **kwargs):
        database = _db_path(args, kwargs)
        correlation_id = str(kwargs.get("collection_run_id") or "")
        enabled = {
            result_key: (label, kind)
            for argument, result_key, label, kind in _SOURCE_SPECS
            if kwargs.get(argument) is not None
        }
        started_at: dict[str, datetime] = {}
        completed_at: dict[str, datetime] = {}
        original_progress = kwargs.get("progress_callback")

        def mark_started(source_key: str) -> None:
            if source_key not in enabled or source_key in started_at:
                return
            current = datetime.now()
            started_at[source_key] = current
            label, kind = enabled[source_key]
            record_program_event(
                event_type="api" if kind == "api" else "stage",
                status="started",
                source="trend_source_collection",
                action=label,
                detail="출처 수집 시작",
                correlation_id=correlation_id,
                event_time=current,
                db_path=database,
            )

        def mark_completed(source_key: str) -> None:
            if source_key not in enabled or source_key in completed_at:
                return
            if source_key not in started_at:
                mark_started(source_key)
            completed_at[source_key] = datetime.now()

        def tracked_progress(value: float, message: str) -> None:
            starts, completes = _progress_boundaries(message)
            for source_key in completes:
                mark_completed(source_key)
            for source_key in starts:
                mark_started(source_key)
            if callable(original_progress):
                original_progress(value, message)

        call_kwargs = dict(kwargs)
        call_kwargs["progress_callback"] = tracked_progress
        overall_started = perf_counter()
        try:
            result = original(*args, **call_kwargs)
        except Exception as exc:
            current = datetime.now()
            for source_key, (label, kind) in enabled.items():
                if source_key not in started_at:
                    continue
                source_finished = completed_at.get(source_key)
                if source_finished is not None:
                    status = "completed"
                    detail = (
                        "출처 수집 단계 완료 · 후속 군집·순위 처리에서 전체 작업 중단 · "
                        f"{type(exc).__name__}: {str(exc)[:700]}"
                    )
                    event_time = source_finished
                else:
                    status = "failed"
                    detail = (
                        "출처 수집 중 전체 작업 중단 · "
                        f"{type(exc).__name__}: {str(exc)[:700]}"
                    )
                    event_time = current
                record_program_event(
                    event_type="api" if kind == "api" else "stage",
                    status=status,
                    source="trend_source_collection",
                    action=label,
                    detail=detail,
                    duration_ms=_elapsed_ms(started_at[source_key], event_time),
                    correlation_id=correlation_id,
                    event_time=event_time,
                    db_path=database,
                )
            raise

        current = datetime.now()
        for source_key in enabled:
            if source_key not in started_at:
                mark_started(source_key)
            completed_at.setdefault(source_key, current)

        errors = result.get("errors") if isinstance(result, Mapping) else {}
        warnings = result.get("warnings") if isinstance(result, Mapping) else {}
        errors = errors if isinstance(errors, Mapping) else {}
        warnings = warnings if isinstance(warnings, Mapping) else {}
        for result_key, (label, kind) in enabled.items():
            row = _source_result(result, result_key)
            raw_status = str(row.get("status") or "success").casefold()
            error_message = str(errors.get(result_key) or warnings.get(result_key) or "")
            if raw_status in {"failed", "error"} or result_key in errors:
                status = "failed"
            elif raw_status in {"partial", "partial_success"} or result_key in warnings:
                status = "partial"
            elif raw_status == "skipped":
                status = "skipped"
            else:
                status = "completed"
            items_read = int(row.get("items_read") or 0)
            request_count = int(row.get("request_count") or 0)
            planned_count = int(row.get("planned_request_count") or 0)
            success_count = int(row.get("successful_requests") or 0)
            failed_count = int(row.get("failed_requests") or 0)
            skipped_count = int(row.get("skipped_requests") or 0)
            retry_count = int(row.get("retry_count") or 0)
            detail = (
                f"수집 {items_read:,}개 · 실제 요청 {request_count:,}회"
                + (f"/계획 {planned_count:,}회" if planned_count else "")
                + f" · 성공 {success_count:,} · 실패 {failed_count:,}"
                + f" · 생략 {skipped_count:,} · 재시도 {retry_count:,}"
            )
            if error_message:
                detail += f" · {error_message[:700]}"
            record_program_event(
                event_type="api" if kind == "api" else "stage",
                status=status,
                source="trend_source_collection",
                action=label,
                detail=detail,
                item_count=items_read,
                duration_ms=_source_duration_ms(result, result_key),
                correlation_id=correlation_id,
                event_time=completed_at[result_key],
                db_path=database,
            )
        return result

    wrapped._source_collection_program_log = True  # type: ignore[attr-defined]
    module.refresh_trend_sources_short_connections = wrapped
