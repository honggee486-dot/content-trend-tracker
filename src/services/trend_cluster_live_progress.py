from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Iterable

from src.config import DEFAULT_DB_PATH
from src.database import connect_database
from src.services.gemini_service import call_gemini_structured_output
from src.services.program_log_service import record_program_event
from src.services.trend_cluster_token_runtime import GLOBAL_TOKEN_ESTIMATOR

_VIEW_LABELS = {
    "title": "제목 기준",
    "event": "주체·대상·행동 기준",
    "identity": "날짜·회차·제품·방향 기준",
    "existing": "기존 군집 연결 기준",
}
_PHASE_LABELS = {
    "preparing": "1차 후보 정리 중",
    "planning": "요청 분할 준비 중",
    "calling": "군집 비교 호출 중",
    "validating": "응답 검증 중",
    "aggregating": "관점별 결과 통합 중",
    "ranking": "군집 반영·점수 계산 중",
    "completed": "작업 종료",
    "failed": "호출 오류 확인 중",
    "skipped_overlap": "실행 전 중복 차단",
}


@dataclass
class _LiveContext:
    job_id: str
    db_path: Path
    request_number: int = 0
    stage_topic_count: int = 0


_CONTEXT = threading.local()


def _active_context() -> _LiveContext | None:
    value = getattr(_CONTEXT, "value", None)
    return value if isinstance(value, _LiveContext) else None


def _set_context(job_id: str, db_path: str | Path) -> _LiveContext:
    value = _LiveContext(str(job_id), Path(db_path).resolve())
    _CONTEXT.value = value
    return value


def _clear_context() -> None:
    if hasattr(_CONTEXT, "value"):
        delattr(_CONTEXT, "value")


def _record_cluster_event(
    *,
    event_type: str,
    status: str,
    action: str,
    detail: str = "",
    item_count: int = 0,
    duration_ms: int = 0,
) -> None:
    context = _active_context()
    if context is None:
        return
    record_program_event(
        event_type=event_type,
        status=status,
        source="trend_clustering_job",
        action=action,
        detail=detail,
        item_count=item_count,
        duration_ms=duration_ms,
        correlation_id=context.job_id,
        db_path=context.db_path,
    )


def _ensure_progress_table(con: Any) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS trend_clustering_job_progress (
            job_id VARCHAR PRIMARY KEY,
            phase VARCHAR NOT NULL DEFAULT '',
            analysis_view VARCHAR NOT NULL DEFAULT '',
            analysis_view_label VARCHAR NOT NULL DEFAULT '',
            request_number INTEGER NOT NULL DEFAULT 0,
            topic_count INTEGER NOT NULL DEFAULT 0,
            stage_topic_count INTEGER NOT NULL DEFAULT 0,
            estimated_input_tokens BIGINT NOT NULL DEFAULT 0,
            message VARCHAR NOT NULL DEFAULT '',
            updated_at TIMESTAMP NOT NULL
        )
        """
    )


def record_live_progress(
    *,
    phase: str,
    analysis_view: str = "",
    request_number: int = 0,
    topic_count: int = 0,
    stage_topic_count: int = 0,
    estimated_input_tokens: int = 0,
    message: str = "",
) -> None:
    """실제 API 호출 중에도 읽을 수 있도록 짧은 연결로 진행 상태만 저장합니다."""
    context = _active_context()
    if context is None:
        return
    try:
        with connect_database(context.db_path) as con:
            _ensure_progress_table(con)
            con.execute(
                """
                INSERT INTO trend_clustering_job_progress(
                    job_id, phase, analysis_view, analysis_view_label,
                    request_number, topic_count, stage_topic_count,
                    estimated_input_tokens, message, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    phase = EXCLUDED.phase,
                    analysis_view = EXCLUDED.analysis_view,
                    analysis_view_label = EXCLUDED.analysis_view_label,
                    request_number = EXCLUDED.request_number,
                    topic_count = EXCLUDED.topic_count,
                    stage_topic_count = EXCLUDED.stage_topic_count,
                    estimated_input_tokens = EXCLUDED.estimated_input_tokens,
                    message = EXCLUDED.message,
                    updated_at = EXCLUDED.updated_at
                """,
                [
                    context.job_id,
                    str(phase or ""),
                    str(analysis_view or ""),
                    _VIEW_LABELS.get(str(analysis_view or ""), ""),
                    max(0, int(request_number or 0)),
                    max(0, int(topic_count or 0)),
                    max(0, int(stage_topic_count or context.stage_topic_count or 0)),
                    max(0, int(estimated_input_tokens or 0)),
                    str(message or "")[:500],
                    datetime.now(),
                ],
            )
    except Exception:
        # 진행 표시 실패가 실제 군집 작업을 취소하면 안 됩니다.
        return


def _request_progress(request_text: str) -> tuple[str, int]:
    try:
        payload = json.loads(str(request_text).split("\n\n", 1)[1])
    except (IndexError, TypeError, ValueError, json.JSONDecodeError):
        return "", 0
    if not isinstance(payload, dict):
        return "", 0
    candidates = payload.get("candidates")
    return (
        str(payload.get("view") or ""),
        len(candidates) if isinstance(candidates, list) else 0,
    )


def _progress_api_call(base_api_call: Callable[..., tuple[Any, ...]]):
    @wraps(base_api_call)
    def wrapped(config, request_text, request_hash, *args, **kwargs):
        context = _active_context()
        view, topic_count = _request_progress(request_text)
        request_number = 0
        if context is not None:
            context.request_number += 1
            request_number = context.request_number
        estimated_tokens = GLOBAL_TOKEN_ESTIMATOR.estimate_text(str(request_text or ""))
        record_live_progress(
            phase="calling",
            analysis_view=view,
            request_number=request_number,
            topic_count=topic_count,
            estimated_input_tokens=estimated_tokens,
        )
        try:
            result = base_api_call(
                config,
                request_text,
                request_hash,
                *args,
                **kwargs,
            )
        except Exception:
            record_live_progress(
                phase="failed",
                analysis_view=view,
                request_number=request_number,
                topic_count=topic_count,
                estimated_input_tokens=estimated_tokens,
            )
            raise
        record_live_progress(
            phase="validating",
            analysis_view=view,
            request_number=request_number,
            topic_count=topic_count,
            estimated_input_tokens=estimated_tokens,
        )
        return result

    return wrapped


def classify_with_live_progress(
    classifier: Callable[..., Any],
    config: Any,
    candidates: Iterable[dict[str, Any]],
    *args: Any,
    **kwargs: Any,
) -> Any:
    candidate_rows = [dict(candidate) for candidate in candidates]
    context = _active_context()
    if context is not None:
        context.stage_topic_count = len(candidate_rows)
        context.request_number = 0
    _record_cluster_event(
        event_type="stage",
        status="completed",
        action="2차 군집 전체 스냅샷 집계",
        detail=(
            f"미처리 후보 {len(candidate_rows):,}개를 한 번 고정하고 "
            "제목·사건·식별·기존 군집 관점 순서로 처리"
        ),
        item_count=len(candidate_rows),
    )
    record_live_progress(
        phase="planning",
        stage_topic_count=len(candidate_rows),
        message=f"2차 비교 대상 {len(candidate_rows):,}개 주제 정렬·분할 중",
    )
    _record_cluster_event(
        event_type="stage",
        status="started",
        action="2차 군집 관점별 순차 처리",
        detail=(
            f"전체 {len(candidate_rows):,}개 · 제목 → 주체·대상·행동 → "
            "날짜·회차·제품·방향 → 기존 군집 연결"
        ),
        item_count=len(candidate_rows),
    )
    base_api_call = kwargs.pop("api_call", call_gemini_structured_output)
    started = perf_counter()
    try:
        result = classifier(
            config,
            candidate_rows,
            *args,
            api_call=_progress_api_call(base_api_call),
            **kwargs,
        )
    except Exception as exc:
        _record_cluster_event(
            event_type="stage",
            status="failed",
            action="2차 군집 관점별 순차 처리",
            detail=f"{type(exc).__name__}: {str(exc)[:700]}",
            item_count=len(candidate_rows),
            duration_ms=round((perf_counter() - started) * 1000),
        )
        raise
    record_live_progress(
        phase="aggregating",
        stage_topic_count=len(candidate_rows),
        message="제목·사건·식별·기존 군집 관점 결과 통합 중",
    )
    _record_cluster_event(
        event_type="stage",
        status="completed",
        action="2차 군집 관점별 순차 처리",
        detail="모든 관점 요청과 응답 검증 완료 · 결과 통합 시작",
        item_count=len(candidate_rows),
        duration_ms=round((perf_counter() - started) * 1000),
    )
    return result


def _load_progress(con: Any, job_id: str) -> dict[str, Any]:
    try:
        _ensure_progress_table(con)
        row = con.execute(
            """
            SELECT phase, analysis_view, analysis_view_label, request_number,
                   topic_count, stage_topic_count, estimated_input_tokens,
                   message, updated_at
            FROM trend_clustering_job_progress
            WHERE job_id = ?
            """,
            [str(job_id)],
        ).fetchone()
    except Exception:
        return {}
    if row is None:
        return {}
    columns = (
        "phase",
        "analysis_view",
        "analysis_view_label",
        "request_number",
        "topic_count",
        "stage_topic_count",
        "estimated_input_tokens",
        "message",
        "progress_updated_at",
    )
    return dict(zip(columns, row))


def build_live_display_status(job: dict[str, Any], progress: dict[str, Any]) -> str:
    completed = max(0, int(job.get("completed_batches") or 0))
    maximum = max(1, int(job.get("max_batches") or 1))
    current = min(maximum, completed + 1)
    view_label = str(progress.get("analysis_view_label") or "")
    phase = str(progress.get("phase") or "")
    phase_label = _PHASE_LABELS.get(phase, str(progress.get("message") or "").strip())
    if view_label and phase in {"calling", "validating"}:
        phase_label = f"{view_label} {phase_label}"
    parts = [f"실행 중 · {current}/{maximum}차"]
    if phase_label:
        parts.append(phase_label)
    topic_count = max(0, int(progress.get("topic_count") or 0))
    stage_topic_count = max(0, int(progress.get("stage_topic_count") or 0))
    if topic_count:
        parts.append(f"{topic_count:,}개 주제")
    elif stage_topic_count:
        parts.append(f"전체 {stage_topic_count:,}개 주제")
    request_number = max(0, int(progress.get("request_number") or 0))
    if request_number:
        parts.append(f"{request_number}번째 요청")
    return " · ".join(parts)


def _final_progress_state(status: str, *, exit_code: int) -> dict[str, str]:
    if str(status or "") == "skipped_overlap":
        return {
            "phase": "skipped_overlap",
            "message": "기존 작업 진행 중으로 생략 · Gemini 호출 및 DB 반영 없음",
            "event_status": "skipped",
            "event_detail": "실행 전 중복 차단 · Gemini 호출 및 DB 반영 없음",
        }
    success = int(exit_code or 0) == 0
    return {
        "phase": "completed" if success else "failed",
        "message": "2차 군집 작업 종료",
        "event_status": "completed" if success else "failed",
        "event_detail": f"작업 종료 코드 {int(exit_code or 0)}",
    }


def _load_final_job_status(db_path: str | Path, job_id: str) -> str:
    try:
        with connect_database(db_path, read_only=True) as con:
            row = con.execute(
                "SELECT status FROM trend_clustering_jobs WHERE job_id = ?",
                [str(job_id)],
            ).fetchone()
    except Exception:
        return ""
    return str(row[0] or "") if row is not None else ""


def install_job_progress_contract(job_module: Any) -> None:
    """작업자 호출 단계와 대시보드 조회 결과에 실시간 관점 정보를 연결합니다."""
    original_latest = getattr(job_module, "get_latest_clustering_job", None)
    if callable(original_latest) and not getattr(
        original_latest,
        "_trend_cluster_live_progress",
        False,
    ):

        @wraps(original_latest)
        def latest_with_progress(con, *args, **kwargs):
            job = original_latest(con, *args, **kwargs)
            if not isinstance(job, dict):
                return job
            progress = _load_progress(con, str(job.get("job_id") or ""))
            if progress:
                job.update(progress)
                if str(job.get("status") or "") == "running":
                    job["display_status"] = build_live_display_status(job, progress)
            return job

        latest_with_progress._trend_cluster_live_progress = True  # type: ignore[attr-defined]
        job_module.get_latest_clustering_job = latest_with_progress

    original_run = getattr(job_module, "run_clustering_job", None)
    if not callable(original_run) or getattr(
        original_run,
        "_trend_cluster_live_progress",
        False,
    ):
        return

    @wraps(original_run)
    def run_with_progress(job_id: str, *args: Any, **kwargs: Any):
        db_path = kwargs.get("db_path", DEFAULT_DB_PATH)
        _set_context(job_id, db_path)
        record_live_progress(phase="preparing", message="미처리 자료와 1차 후보 확인 중")
        _record_cluster_event(
            event_type="task",
            status="started",
            action="2차 군집 작업",
            detail="미처리 자료와 보수적 1차 후보 준비 시작",
        )
        job_started = perf_counter()

        original_calculate = getattr(job_module, "calculate_prepared_trend_rankings")

        @wraps(original_calculate)
        def calculate_with_progress(preparation, *calc_args, **calc_kwargs):
            from src.services import trend_discovery_service as discovery

            original_classifier = discovery.classify_cluster_batch

            @wraps(original_classifier)
            def classifier(config, candidates, *classifier_args, **classifier_kwargs):
                return classify_with_live_progress(
                    original_classifier,
                    config,
                    candidates,
                    *classifier_args,
                    **classifier_kwargs,
                )

            discovery.classify_cluster_batch = classifier
            try:
                result = original_calculate(preparation, *calc_args, **calc_kwargs)
            finally:
                discovery.classify_cluster_batch = original_classifier
            record_live_progress(
                phase="ranking",
                message="군집 결과 반영과 순위 점수 계산 중",
            )
            _record_cluster_event(
                event_type="stage",
                status="started",
                action="2차 군집 결과 반영·순위 계산",
                detail="관점 통합 결과를 군집과 점수에 반영",
            )
            return result

        job_module.calculate_prepared_trend_rankings = calculate_with_progress
        exit_code = 1
        try:
            exit_code = original_run(job_id, *args, **kwargs)
            final_status = _load_final_job_status(db_path, job_id)
            final_progress = _final_progress_state(
                final_status,
                exit_code=int(exit_code or 0),
            )
            record_live_progress(
                phase=final_progress["phase"],
                message=final_progress["message"],
            )
            _record_cluster_event(
                event_type="task",
                status=final_progress["event_status"],
                action="2차 군집 작업",
                detail=final_progress["event_detail"],
                duration_ms=round((perf_counter() - job_started) * 1000),
            )
            return exit_code
        except Exception as exc:
            _record_cluster_event(
                event_type="task",
                status="failed",
                action="2차 군집 작업",
                detail=f"{type(exc).__name__}: {str(exc)[:700]}",
                duration_ms=round((perf_counter() - job_started) * 1000),
            )
            raise
        finally:
            job_module.calculate_prepared_trend_rankings = original_calculate
            _clear_context()

    run_with_progress._trend_cluster_live_progress = True  # type: ignore[attr-defined]
    job_module.run_clustering_job = run_with_progress
