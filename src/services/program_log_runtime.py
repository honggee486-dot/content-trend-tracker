from __future__ import annotations

import json
from functools import wraps
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Mapping

from src.config import DEFAULT_DB_PATH
from src.services.program_log_service import feature_label, record_program_event

_VIEW_LABELS = {
    "title": "제목 기준",
    "event": "주체·대상·행동 기준",
    "identity": "날짜·회차·제품·방향 기준",
    "existing": "기존 군집 연결 기준",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    data = getattr(value, "__dict__", None)
    return data if isinstance(data, Mapping) else {}


def _value(value: Any, *names: str, default: Any = None) -> Any:
    data = _mapping(value)
    for name in names:
        if name in data:
            return data.get(name)
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _count_sequence(value: Any, *names: str) -> int:
    for name in names:
        candidate = _value(value, name)
        if isinstance(candidate, (list, tuple, set, dict)):
            return len(candidate)
        try:
            if candidate is not None:
                return max(0, int(candidate))
        except (TypeError, ValueError):
            continue
    return 0


def _db_path_from_call(args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> Path:
    candidate = kwargs.get("db_path")
    if candidate is None and args and isinstance(args[0], (str, Path)):
        candidate = args[0]
    return Path(candidate or DEFAULT_DB_PATH).resolve()


def _json_request_payload(request_text: str) -> Mapping[str, Any]:
    text = str(request_text or "")
    starts: list[int] = []
    for marker in ("\n\n{", "\n{"):
        position = text.find(marker)
        if position >= 0:
            starts.append(position + len(marker) - 1)
    direct = text.find("{")
    if direct >= 0:
        starts.append(direct)
    for start in sorted(set(starts)):
        try:
            parsed = json.loads(text[start:])
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, Mapping):
            return parsed
    return {}


def _request_context(request_text: str) -> tuple[str, int]:
    payload = _json_request_payload(request_text)
    view = str(payload.get("view") or "")
    for key in ("candidates", "clusters", "topics", "items"):
        values = payload.get(key)
        if isinstance(values, (list, tuple)):
            return view, len(values)
    for value in payload.values():
        if not isinstance(value, Mapping):
            continue
        for key in ("candidates", "clusters", "topics", "items"):
            values = value.get(key)
            if isinstance(values, (list, tuple)):
                return view, len(values)
    return view, 0


def _install_gemini_call_logging() -> None:
    from src.services import gemini_service

    original = getattr(gemini_service, "call_gemini_structured_output", None)
    if not callable(original) or getattr(original, "_program_log_runtime", False):
        return

    @wraps(original)
    def wrapped(config, request_text, request_hash, *args, **kwargs):
        feature_id = str(kwargs.get("feature_id") or "")
        view, item_count = _request_context(str(request_text or ""))
        view_label = _VIEW_LABELS.get(view, "")
        base_label = feature_label(feature_id)
        action = (
            f"{base_label} · {view_label} API 전송"
            if view_label
            else f"{base_label} API 전송"
        )
        model_name = str(getattr(config, "model", "") or "")
        request_chars = len(str(request_text or ""))
        correlation_id = str(request_hash or "")[:24]
        detail = f"모델 {model_name or '-'} · 요청 문자 {request_chars:,}자"
        if item_count:
            detail += f" · 요청 항목 {item_count:,}개"
        record_program_event(
            event_type="api",
            status="started",
            source="gemini_service",
            action=action,
            detail=detail,
            item_count=item_count,
            correlation_id=correlation_id,
        )
        started = perf_counter()
        try:
            result = original(config, request_text, request_hash, *args, **kwargs)
        except Exception as exc:
            record_program_event(
                event_type="api",
                status="failed",
                source="gemini_service",
                action=action,
                detail=f"{detail} · {type(exc).__name__}: {str(exc)[:700]}",
                item_count=item_count,
                duration_ms=round((perf_counter() - started) * 1000),
                correlation_id=correlation_id,
            )
            raise
        input_tokens = int(result[1] or 0) if len(result) > 1 else 0
        output_tokens = int(result[2] or 0) if len(result) > 2 else 0
        thought_tokens = int(result[3] or 0) if len(result) > 3 else 0
        total_tokens = int(result[4] or 0) if len(result) > 4 else 0
        finish_reason = str(result[5] or "") if len(result) > 5 else ""
        record_program_event(
            event_type="api",
            status="completed",
            source="gemini_service",
            action=action,
            detail=(
                f"{detail} · 입력 {input_tokens:,} · 출력 {output_tokens:,} · "
                f"사고 {thought_tokens:,} · 총 {total_tokens:,}토큰"
                + (f" · 종료 {finish_reason}" if finish_reason else "")
            ),
            item_count=item_count,
            duration_ms=round((perf_counter() - started) * 1000),
            correlation_id=correlation_id,
        )
        return result

    wrapped._program_log_runtime = True  # type: ignore[attr-defined]
    gemini_service.call_gemini_structured_output = wrapped


def _install_cleanup_logging() -> None:
    from src.services import data_maintenance_service as module

    original = getattr(module, "run_automatic_cleanup_if_due", None)
    if not callable(original) or getattr(original, "_program_log_runtime", False):
        return

    @wraps(original)
    def wrapped(con, *args, **kwargs):
        action = "저장 자료 자동 정리"
        record_program_event(
            event_type="stage",
            status="started",
            source="data_maintenance",
            action=action,
            detail="자동 정리 활성화 여부·정리 주기·보존기간 확인",
            con=con,
        )
        started = perf_counter()
        try:
            result = original(con, *args, **kwargs)
        except Exception as exc:
            record_program_event(
                event_type="stage",
                status="failed",
                source="data_maintenance",
                action=action,
                detail=f"{type(exc).__name__}: {str(exc)[:700]}",
                duration_ms=round((perf_counter() - started) * 1000),
                con=con,
            )
            raise
        if result is None:
            status = "skipped"
            detail = "자동 정리 비활성 또는 정리 주기 미도래"
            total = 0
        else:
            status = "completed"
            source_rows = int(_value(result, "source_items_deleted", default=0) or 0)
            sync_rows = int(_value(result, "sync_runs_deleted", default=0) or 0)
            collection_rows = int(_value(result, "collection_runs_deleted", default=0) or 0)
            api_rows = int(_value(result, "api_usage_rows_deleted", default=0) or 0)
            total = int(_value(result, "total_rows_deleted", default=0) or 0)
            detail = (
                f"원본 {source_rows:,} · 출처 실행 {sync_rows:,} · "
                f"전체 이력 {collection_rows:,} · API 기록 {api_rows:,}개 삭제"
            )
        record_program_event(
            event_type="stage",
            status=status,
            source="data_maintenance",
            action=action,
            detail=detail,
            item_count=total,
            duration_ms=round((perf_counter() - started) * 1000),
            con=con,
        )
        return result

    wrapped._program_log_runtime = True  # type: ignore[attr-defined]
    module.run_automatic_cleanup_if_due = wrapped


def _refresh_result_detail(result: Any) -> tuple[int, str, str]:
    data = _mapping(result)
    source_parts: list[str] = []
    total = 0
    for key, label in (
        ("youtube", "YouTube"),
        ("naver", "NAVER"),
        ("daum", "Daum"),
        ("google_trends", "Google Trends"),
        ("wikipedia", "위키백과"),
    ):
        row = data.get(key)
        if not isinstance(row, Mapping):
            continue
        count = int(row.get("items_read") or 0)
        total += count
        source_parts.append(f"{label} {count:,}개")
    errors = data.get("errors")
    status = "partial" if isinstance(errors, Mapping) and errors else "completed"
    ranking = data.get("ranking") if isinstance(data.get("ranking"), Mapping) else {}
    clusters = int(ranking.get("clusters") or 0)
    if clusters:
        source_parts.append(f"통합 주제 {clusters:,}개")
    return total, " · ".join(source_parts) or "수집 결과 저장 완료", status


def _install_refresh_logging() -> None:
    from src.services import trend_discovery_service as module

    original = getattr(module, "refresh_trend_sources_short_connections", None)
    if callable(original) and not getattr(original, "_program_log_runtime", False):

        @wraps(original)
        def refresh_wrapped(*args, **kwargs):
            db_path = _db_path_from_call(args, kwargs)
            action = "최신 데이터 수집·분석"
            record_program_event(
                event_type="task",
                status="started",
                source="trend_discovery",
                action=action,
                detail="출처별 최신 자료 수집 시작",
                db_path=db_path,
            )
            started = perf_counter()
            try:
                result = original(*args, **kwargs)
            except Exception as exc:
                record_program_event(
                    event_type="task",
                    status="failed",
                    source="trend_discovery",
                    action=action,
                    detail=f"{type(exc).__name__}: {str(exc)[:700]}",
                    duration_ms=round((perf_counter() - started) * 1000),
                    db_path=db_path,
                )
                raise
            total, detail, status = _refresh_result_detail(result)
            record_program_event(
                event_type="task",
                status=status,
                source="trend_discovery",
                action=action,
                detail=detail,
                item_count=total,
                duration_ms=round((perf_counter() - started) * 1000),
                db_path=db_path,
            )
            return result

        refresh_wrapped._program_log_runtime = True  # type: ignore[attr-defined]
        module.refresh_trend_sources_short_connections = refresh_wrapped

    for function_name, action in (
        ("prepare_trend_ranking_rebuild", "저장 자료·순위 계산 대상 준비"),
        ("calculate_prepared_trend_rankings", "군집·통합 순위 계산"),
        ("finalize_prepared_trend_rankings", "군집·통합 순위 저장"),
    ):
        _install_generic_stage(module, function_name, action)


def _install_generic_stage(module: Any, function_name: str, action: str) -> None:
    original = getattr(module, function_name, None)
    if not callable(original) or getattr(original, "_program_log_runtime", False):
        return

    @wraps(original)
    def wrapped(*args, **kwargs):
        con = args[0] if args and hasattr(args[0], "execute") else None
        db_path = _db_path_from_call(args, kwargs)
        record_program_event(
            event_type="stage",
            status="started",
            source=module.__name__.rsplit(".", 1)[-1],
            action=action,
            con=con,
            db_path=db_path,
        )
        started = perf_counter()
        try:
            result = original(*args, **kwargs)
        except Exception as exc:
            record_program_event(
                event_type="stage",
                status="failed",
                source=module.__name__.rsplit(".", 1)[-1],
                action=action,
                detail=f"{type(exc).__name__}: {str(exc)[:700]}",
                duration_ms=round((perf_counter() - started) * 1000),
                con=con,
                db_path=db_path,
            )
            raise
        item_count = _count_sequence(
            result,
            "clusters",
            "candidates",
            "items",
            "source_items",
            "processed_items",
        )
        result_data = _mapping(result)
        detail_parts = []
        for key, label in (
            ("items", "신호"),
            ("clusters", "통합 주제"),
            ("processed_items", "처리"),
            ("remaining_items", "남은 미처리"),
        ):
            value = result_data.get(key)
            if isinstance(value, (int, float)):
                detail_parts.append(f"{label} {int(value):,}개")
        record_program_event(
            event_type="stage",
            status="completed",
            source=module.__name__.rsplit(".", 1)[-1],
            action=action,
            detail=" · ".join(detail_parts),
            item_count=item_count,
            duration_ms=round((perf_counter() - started) * 1000),
            con=con,
            db_path=db_path,
        )
        return result

    wrapped._program_log_runtime = True  # type: ignore[attr-defined]
    setattr(module, function_name, wrapped)


def _install_topic_angle_logging() -> None:
    from src.services import topic_angle_ai_service as module

    original_prepare = getattr(module, "prepare_missing_topic_angles", None)
    if callable(original_prepare) and not getattr(
        original_prepare, "_program_log_runtime", False
    ):

        @wraps(original_prepare)
        def prepare_wrapped(con, *args, **kwargs):
            action = "주제 방향 자동 생성 대상 집계"
            record_program_event(
                event_type="stage",
                status="started",
                source="topic_angle_ai",
                action=action,
                con=con,
            )
            started = perf_counter()
            try:
                result = original_prepare(con, *args, **kwargs)
            except Exception as exc:
                record_program_event(
                    event_type="stage",
                    status="failed",
                    source="topic_angle_ai",
                    action=action,
                    detail=f"{type(exc).__name__}: {str(exc)[:700]}",
                    duration_ms=round((perf_counter() - started) * 1000),
                    con=con,
                )
                raise
            cluster_count = _count_sequence(result, "clusters")
            batch_count = _count_sequence(result, "batches")
            record_program_event(
                event_type="stage",
                status="completed",
                source="topic_angle_ai",
                action=action,
                detail=f"대상 {cluster_count:,}개 · 요청 묶음 {batch_count:,}개",
                item_count=cluster_count,
                duration_ms=round((perf_counter() - started) * 1000),
                con=con,
            )
            return result

        prepare_wrapped._program_log_runtime = True  # type: ignore[attr-defined]
        module.prepare_missing_topic_angles = prepare_wrapped

    original_execute = getattr(module, "execute_prepared_topic_angles", None)
    if callable(original_execute) and not getattr(
        original_execute, "_program_log_runtime", False
    ):

        @wraps(original_execute)
        def execute_wrapped(preparation, *args, **kwargs):
            cluster_count = _count_sequence(preparation, "clusters")
            batch_count = _count_sequence(preparation, "batches")
            action = "주제 방향 Gemini 요청 처리"
            record_program_event(
                event_type="task",
                status="started",
                source="topic_angle_ai",
                action=action,
                detail=f"대상 {cluster_count:,}개 · 요청 묶음 {batch_count:,}개",
                item_count=cluster_count,
            )
            started = perf_counter()
            try:
                result = original_execute(preparation, *args, **kwargs)
            except Exception as exc:
                record_program_event(
                    event_type="task",
                    status="failed",
                    source="topic_angle_ai",
                    action=action,
                    detail=f"{type(exc).__name__}: {str(exc)[:700]}",
                    item_count=cluster_count,
                    duration_ms=round((perf_counter() - started) * 1000),
                )
                raise
            completed = _count_sequence(result, "results")
            record_program_event(
                event_type="task",
                status="completed",
                source="topic_angle_ai",
                action=action,
                detail=f"요청 묶음 결과 {completed:,}개 수신",
                item_count=cluster_count,
                duration_ms=round((perf_counter() - started) * 1000),
            )
            return result

        execute_wrapped._program_log_runtime = True  # type: ignore[attr-defined]
        module.execute_prepared_topic_angles = execute_wrapped

    original_finalize = getattr(module, "finalize_prepared_topic_angles", None)
    if callable(original_finalize) and not getattr(
        original_finalize, "_program_log_runtime", False
    ):

        @wraps(original_finalize)
        def finalize_wrapped(con, *args, **kwargs):
            action = "주제 방향·요약 저장"
            record_program_event(
                event_type="stage",
                status="started",
                source="topic_angle_ai",
                action=action,
                con=con,
            )
            started = perf_counter()
            try:
                result = original_finalize(con, *args, **kwargs)
            except Exception as exc:
                record_program_event(
                    event_type="stage",
                    status="failed",
                    source="topic_angle_ai",
                    action=action,
                    detail=f"{type(exc).__name__}: {str(exc)[:700]}",
                    duration_ms=round((perf_counter() - started) * 1000),
                    con=con,
                )
                raise
            requested = int(_value(result, "requested_clusters", default=0) or 0)
            generated_clusters = int(
                _value(result, "generated_clusters", default=0) or 0
            )
            generated_angles = int(_value(result, "generated_angles", default=0) or 0)
            status = str(_value(result, "status", default="completed") or "completed")
            event_status = "completed" if status in {"success", "completed"} else status
            record_program_event(
                event_type="stage",
                status=event_status,
                source="topic_angle_ai",
                action=action,
                detail=(
                    f"대상 {requested:,}개 · 저장 글감 {generated_clusters:,}개 · "
                    f"방향 {generated_angles:,}개"
                ),
                item_count=generated_clusters,
                duration_ms=round((perf_counter() - started) * 1000),
                con=con,
            )
            return result

        finalize_wrapped._program_log_runtime = True  # type: ignore[attr-defined]
        module.finalize_prepared_topic_angles = finalize_wrapped


def _install_collection_run_logging() -> None:
    from src.services import collection_history_service as module

    original_start = getattr(module, "start_collection_run", None)
    if callable(original_start) and not getattr(original_start, "_program_log_runtime", False):

        @wraps(original_start)
        def start_wrapped(con, run_type, *args, **kwargs):
            run_id = original_start(con, run_type, *args, **kwargs)
            record_program_event(
                event_type="task",
                status="started",
                source="collection_history",
                action=f"실행 기록 · {str(run_type or '작업')}",
                detail=f"실행 ID {str(run_id)[:80]}",
                correlation_id=str(run_id),
                con=con,
            )
            return run_id

        start_wrapped._program_log_runtime = True  # type: ignore[attr-defined]
        module.start_collection_run = start_wrapped

    original_finish = getattr(module, "finish_collection_run", None)
    if callable(original_finish) and not getattr(original_finish, "_program_log_runtime", False):

        @wraps(original_finish)
        def finish_wrapped(con, run_id, *args, **kwargs):
            error = kwargs.get("error")
            if error is None and len(args) >= 2:
                error = args[1]
            result = original_finish(con, run_id, *args, **kwargs)
            record_program_event(
                event_type="task",
                status="failed" if error else "completed",
                source="collection_history",
                action="실행 기록 종료",
                detail=(
                    f"실행 ID {str(run_id)[:80]}"
                    + (f" · {type(error).__name__}: {str(error)[:700]}" if error else "")
                ),
                correlation_id=str(run_id),
                con=con,
            )
            return result

        finish_wrapped._program_log_runtime = True  # type: ignore[attr-defined]
        module.finish_collection_run = finish_wrapped


def install_program_logging_contract() -> None:
    """앱·예약 수집·백그라운드 군집의 주요 단계와 모든 Gemini 전송을 기록합니다."""
    _install_gemini_call_logging()
    _install_cleanup_logging()
    _install_refresh_logging()
    _install_topic_angle_logging()
    _install_collection_run_logging()
