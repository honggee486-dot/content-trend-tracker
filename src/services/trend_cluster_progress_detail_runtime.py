from __future__ import annotations

from datetime import datetime
from functools import wraps
from time import perf_counter
from typing import Any, Callable

from src.services.program_log_service import PROGRAM_LOG_TABLE, status_label

_VIEW_LABELS = {
    "title": "제목 기준",
    "event": "주체·대상·행동 기준",
    "identity": "날짜·회차·제품·방향 기준",
    "existing": "기존 군집 연결 기준",
}
_VIEW_ORDER = {"title": 0, "event": 1, "identity": 2, "existing": 3}
_PHASE_LABELS = {
    "preparing": "1차 후보 정리 중",
    "planning": "2차 요청 분할 준비 중",
    "calling": "Gemini 호출 중",
    "validating": "응답 검증 중",
    "aggregating": "관점별 결과 통합 중",
    "ranking": "군집 반영·점수 계산 중",
    "completed": "작업 종료",
    "failed": "호출 오류 확인 중",
    "skipped_overlap": "실행 전 중복 차단",
}
PROGRESS_FLOW_TEXT = (
    "진행 순서: 미처리 자료 확인 → 보수적 1차 후보 정리 → "
    "2차 주제 군집(제목 → 주체·대상·행동 → 날짜·회차·제품·방향 → 기존 군집 연결) "
    "→ 결과 통합 → 군집 반영·순위 계산"
)
_PROGRESS_LOG_LIMIT = 16


def _detailed_api_call_factory(live_module: Any, base_api_call: Callable[..., Any]):
    @wraps(base_api_call)
    def wrapped(config, request_text, request_hash, *args, **kwargs):
        context = live_module._active_context()
        view, topic_count = live_module._request_progress(request_text)
        view_label = _VIEW_LABELS.get(view, view or "관점")
        request_number = 0
        if context is not None:
            context.request_number += 1
            request_number = context.request_number
        estimated_tokens = live_module.GLOBAL_TOKEN_ESTIMATOR.estimate_text(
            str(request_text or "")
        )
        action = f"2차 주제 군집 · {view_label}"
        detail = (
            f"Gemini {request_number}번째 요청 · {topic_count:,}개 주제 · "
            f"예상 입력 {estimated_tokens:,}토큰"
        )
        started = perf_counter()
        live_module.record_live_progress(
            phase="calling",
            analysis_view=view,
            request_number=request_number,
            topic_count=topic_count,
            estimated_input_tokens=estimated_tokens,
        )
        live_module._record_cluster_event(
            event_type="api",
            status="started",
            action=action,
            detail=detail,
            item_count=topic_count,
        )
        try:
            result = base_api_call(
                config,
                request_text,
                request_hash,
                *args,
                **kwargs,
            )
        except Exception as exc:
            duration_ms = round((perf_counter() - started) * 1000)
            live_module.record_live_progress(
                phase="failed",
                analysis_view=view,
                request_number=request_number,
                topic_count=topic_count,
                estimated_input_tokens=estimated_tokens,
            )
            live_module._record_cluster_event(
                event_type="api",
                status="failed",
                action=action,
                detail=f"{detail} · {type(exc).__name__}: {str(exc)[:500]}",
                item_count=topic_count,
                duration_ms=duration_ms,
            )
            raise
        duration_ms = round((perf_counter() - started) * 1000)
        live_module.record_live_progress(
            phase="validating",
            analysis_view=view,
            request_number=request_number,
            topic_count=topic_count,
            estimated_input_tokens=estimated_tokens,
        )
        live_module._record_cluster_event(
            event_type="api",
            status="completed",
            action=action,
            detail=f"{detail} · 응답 수신 후 검증 단계로 이동",
            item_count=topic_count,
            duration_ms=duration_ms,
        )
        return result

    wrapped._cluster_progress_detail = True  # type: ignore[attr-defined]
    return wrapped


def build_detailed_display_status(job: dict[str, Any], progress: dict[str, Any]) -> str:
    completed = max(0, int(job.get("completed_batches") or 0))
    maximum = max(1, int(job.get("max_batches") or 1))
    current = min(maximum, completed + 1)
    phase = str(progress.get("phase") or "")
    view_label = str(progress.get("analysis_view_label") or "")
    parts = ["2차 주제 군집 중", f"{current}/{maximum}차 작업"]
    if view_label:
        parts.append(f"{view_label} 비교")
    request_number = max(0, int(progress.get("request_number") or 0))
    if request_number:
        parts.append(f"Gemini {request_number}번째 요청")
    topic_count = max(0, int(progress.get("topic_count") or 0))
    stage_topic_count = max(0, int(progress.get("stage_topic_count") or 0))
    if topic_count:
        parts.append(f"{topic_count:,}개 주제")
    elif stage_topic_count:
        parts.append(f"전체 {stage_topic_count:,}개 주제")
    phase_label = _PHASE_LABELS.get(
        phase,
        str(progress.get("message") or "").strip(),
    )
    if phase_label:
        parts.append(phase_label)
    return " · ".join(parts)


def build_stage_label(progress: dict[str, Any]) -> str:
    phase = str(progress.get("phase") or "")
    view_label = str(progress.get("analysis_view_label") or "")
    phase_label = _PHASE_LABELS.get(
        phase,
        str(progress.get("message") or "").strip(),
    )
    if view_label and phase in {"calling", "validating"}:
        return f"2차 주제 군집 · {view_label} · {phase_label}"
    return phase_label or "2차 군집 상태 확인 중"


def progress_percent(job: dict[str, Any], progress: dict[str, Any]) -> int:
    status = str(job.get("status") or "")
    if status == "skipped_overlap":
        return 0
    if status in {"success", "partial"}:
        return 100
    completed = max(0, int(job.get("completed_batches") or 0))
    maximum = max(1, int(job.get("max_batches") or 1))
    phase = str(progress.get("phase") or "")
    view = str(progress.get("analysis_view") or "")
    fraction = {
        "preparing": 0.06,
        "planning": 0.14,
        "aggregating": 0.86,
        "ranking": 0.94,
        "completed": 1.0,
        "failed": 0.0,
    }.get(phase, 0.0)
    if phase in {"calling", "validating"}:
        base = 0.20 + _VIEW_ORDER.get(view, 0) * 0.15
        fraction = base + (0.08 if phase == "validating" else 0.0)
    value = round((completed + min(0.99, max(0.0, fraction))) * 100 / maximum)
    return max(0, min(100, int(value)))


def _elapsed_seconds(event_time: Any, started_at: Any) -> float:
    if not isinstance(event_time, datetime) or not isinstance(started_at, datetime):
        return 0.0
    return max(0.0, (event_time - started_at).total_seconds())


def load_progress_log(
    con: Any,
    job_id: str,
    *,
    started_at: Any = None,
    limit: int = _PROGRESS_LOG_LIMIT,
) -> list[dict[str, str]]:
    try:
        rows = con.execute(
            f"""
            SELECT event_time, status, action, detail, item_count, duration_ms
            FROM {PROGRAM_LOG_TABLE}
            WHERE correlation_id = ? AND source = 'trend_clustering_job'
            ORDER BY event_time DESC, event_id DESC
            LIMIT ?
            """,
            [str(job_id), max(1, min(int(limit or _PROGRESS_LOG_LIMIT), 50))],
        ).fetchall()
    except Exception:
        return []

    result: list[dict[str, str]] = []
    for event_time, status, action, detail, item_count, duration_ms in reversed(rows):
        elapsed = _elapsed_seconds(event_time, started_at)
        duration = max(0, int(duration_ms or 0)) / 1000.0
        detail_text = str(detail or "").strip()
        if not detail_text and int(item_count or 0):
            detail_text = f"대상 {int(item_count):,}개"
        if duration > 0:
            detail_text = f"{detail_text} · 단계 소요 {duration:,.2f}초".strip(" ·")
        result.append(
            {
                "시각": (
                    event_time.strftime("%Y-%m-%d %H:%M:%S")
                    if isinstance(event_time, datetime)
                    else str(event_time or "-")
                ),
                "경과(초)": f"{elapsed:,.2f}",
                "상태": status_label(status),
                "단계": str(action or ""),
                "내용": detail_text,
            }
        )
    return result


def enrich_clustering_job(con: Any, job: dict[str, Any]) -> dict[str, Any]:
    progress = {
        key: job.get(key)
        for key in (
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
        if key in job
    }
    status = str(job.get("status") or "")
    stale_display = str(job.get("display_status") or "") == "stale"
    if stale_display:
        job["current_stage_label"] = "2차 군집 상태 확인 필요"
        job["display_status"] = "상태 확인 필요"
        job["progress_notice"] = (
            "저장된 작업 heartbeat가 오래되어 현재 실행 중이라고 단정하지 않습니다."
        )
    elif status == "skipped_overlap":
        job["current_stage_label"] = "실행 전 중복 차단"
        job["progress_notice"] = (
            "기존 작업 진행 중으로 생략 · Gemini 호출 및 DB 반영 없음"
        )
        job["display_status"] = "skipped_overlap"
    elif progress:
        job["current_stage_label"] = build_stage_label(progress)
        if status == "running":
            job["display_status"] = build_detailed_display_status(job, progress)
    else:
        job["current_stage_label"] = {
            "queued": "2차 군집 실행 대기",
            "running": "2차 군집 상태 확인 중",
            "success": "2차 군집 작업 완료",
            "partial": "2차 군집 시험 범위 완료",
            "failed": "2차 군집 작업 실패",
            "skipped_overlap": "중복 작업 생략",
            "stale": "2차 군집 상태 재확인 필요",
        }.get(status, "2차 군집 상태 확인 중")
    job["progress_percent"] = 0 if stale_display else progress_percent(job, progress)
    job["progress_flow_text"] = PROGRESS_FLOW_TEXT
    job["progress_log_rows"] = load_progress_log(
        con,
        str(job.get("job_id") or ""),
        started_at=job.get("started_at"),
    )
    return job


def install_cluster_progress_detail_contract(job_module: Any) -> None:
    """기존 군집 실행은 보존하고 요청별 단계 로그와 화면 표시만 보강합니다."""
    from src.services import trend_cluster_live_progress as live_module

    current_factory = getattr(live_module, "_progress_api_call", None)
    if callable(current_factory) and not getattr(
        current_factory,
        "_cluster_progress_detail",
        False,
    ):

        def detailed_factory(base_api_call: Callable[..., Any]):
            return _detailed_api_call_factory(live_module, base_api_call)

        detailed_factory._cluster_progress_detail = True  # type: ignore[attr-defined]
        live_module._progress_api_call = detailed_factory
        live_module.build_live_display_status = build_detailed_display_status

    original_latest = getattr(job_module, "get_latest_clustering_job", None)
    if not callable(original_latest) or getattr(
        original_latest,
        "_cluster_progress_detail",
        False,
    ):
        return

    @wraps(original_latest)
    def latest_with_details(con, *args, **kwargs):
        job = original_latest(con, *args, **kwargs)
        if not isinstance(job, dict):
            return job
        return enrich_clustering_job(con, job)

    latest_with_details._cluster_progress_detail = True  # type: ignore[attr-defined]
    job_module.get_latest_clustering_job = latest_with_details
