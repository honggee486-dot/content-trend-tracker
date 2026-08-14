from __future__ import annotations

from functools import wraps
from typing import Any, MutableMapping, cast

import pandas as pd

from src.services.dashboard_refresh_progress_service import (
    DashboardRefreshProgress,
    is_dashboard_refresh_active,
    read_dashboard_refresh_progress,
)


_PANEL_MARKER = "__content_trend_dashboard_operation_status__"
_LAST_PROGRESS_KEY = "trend_dashboard_last_operation_progress"
_TARGET_ACTIONS = {
    "최신 데이터 수집·분석",
    "저장 자료 정리·순위 다시 계산",
    "주제 방향 자동 생성",
}
_LAST_PRIMARY_JOB: dict[str, Any] | None = None
_LAST_ATTEMPT_NOTICE = ""


def _state(st_module: Any) -> MutableMapping[str, Any] | None:
    value = getattr(st_module, "session_state", None)
    if value is None:
        return None
    if not all(hasattr(value, name) for name in ("get", "__setitem__")):
        return None
    return cast(MutableMapping[str, Any], value)


def _remember_progress(st_module: Any) -> DashboardRefreshProgress | None:
    progress = read_dashboard_refresh_progress()
    state = _state(st_module)
    if progress is not None and state is not None:
        state[_LAST_PROGRESS_KEY] = progress
        return progress
    if state is None:
        return progress
    remembered = state.get(_LAST_PROGRESS_KEY)
    return remembered if isinstance(remembered, DashboardRefreshProgress) else progress


def _format_time(value: object) -> str:
    return str(value or "-").replace("T", " ")[:19]


def _format_elapsed(value: object) -> str:
    try:
        seconds = max(0.0, float(value or 0.0))
    except (TypeError, ValueError, OverflowError):
        seconds = 0.0
    if seconds >= 3600:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        remainder = seconds % 60
        return f"{hours:,}시간 {minutes:02d}분 {remainder:05.2f}초"
    if seconds >= 60:
        minutes = int(seconds // 60)
        remainder = seconds % 60
        return f"{minutes:,}분 {remainder:05.2f}초"
    return f"{seconds:,.2f}초"


def _refresh_rows(progress: DashboardRefreshProgress) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for step in reversed(progress.steps):
        rows.append(
            {
                "시간": _format_time(step.get("time")),
                "경과": _format_elapsed(step.get("elapsed_seconds")),
                "진행률": f"{int(step.get('value') or 0):,}%",
                "내용": str(step.get("message") or "").strip(),
            }
        )
    if not rows:
        rows.append(
            {
                "시간": _format_time(progress.updated_at),
                "경과": "-",
                "진행률": f"{progress.value:,}%",
                "내용": progress.message or "상태 확인 중",
            }
        )
    return rows


def _clustering_rows(job: dict[str, Any]) -> list[dict[str, str]]:
    source_rows = list(job.get("progress_log_rows") or ())
    result: list[dict[str, str]] = []
    current_progress = max(0, min(100, int(job.get("progress_percent") or 0)))
    for index, row in enumerate(reversed(source_rows)):
        detail = str(row.get("내용") or "").strip()
        stage = str(row.get("단계") or "").strip()
        content = " · ".join(part for part in (stage, detail) if part)
        result.append(
            {
                "시간": str(row.get("시각") or "-"),
                "경과": _format_elapsed(row.get("경과(초)")),
                "진행률": f"{current_progress:,}%" if index == 0 else "-",
                "내용": content or str(row.get("상태") or "상태 확인 중"),
            }
        )
    return result


def _active_clustering(job: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(job, dict)
        and str(job.get("status") or "") in {"queued", "running"}
        and str(job.get("display_status") or "") != "stale"
    )


def _panel_label(
    progress: DashboardRefreshProgress | None,
    job: dict[str, Any] | None,
    notice: str,
) -> tuple[str, bool]:
    if (
        progress is not None
        and progress.active
        and is_dashboard_refresh_active(progress)
    ):
        message = progress.message or "최신 데이터 수집·분석 중"
        return f"실행 현황 · 진행 중 {progress.value:,}% · {message}", True
    if _active_clustering(job):
        percent = max(0, min(100, int((job or {}).get("progress_percent") or 0)))
        stage = str(
            (job or {}).get("current_stage_label")
            or (job or {}).get("display_status")
            or "2차 군집 처리 중"
        ).strip()
        return f"실행 현황 · 2차 군집 진행 중 {percent:,}% · {stage}", True
    if progress is not None:
        timestamp = progress.finished_at or progress.updated_at
        message = progress.message or progress.summary or "최근 실행 종료"
        return f"최근 실행 시도 · {_format_time(timestamp)} · {message}", False
    if notice:
        return notice, False
    return "최근 실행 시도", False


def _render_panel(st_module: Any) -> None:
    progress = _remember_progress(st_module)
    job = _LAST_PRIMARY_JOB
    notice = _LAST_ATTEMPT_NOTICE
    active_refresh = bool(
        progress is not None
        and progress.active
        and is_dashboard_refresh_active(progress)
    )
    active_cluster = _active_clustering(job)
    label, expanded = _panel_label(progress, job, notice)
    with st_module.expander(label, expanded=expanded):
        if active_refresh and progress is not None:
            metadata = [f"시작 {_format_time(progress.started_at)}"]
            if progress.pid:
                metadata.append(f"PID {progress.pid:,}")
            if progress.run_id:
                metadata.append(f"실행 ID {progress.run_id}")
            st_module.caption(" · ".join(metadata))
            rows = _refresh_rows(progress)
        elif active_cluster and isinstance(job, dict):
            metadata = [
                f"상태 {str(job.get('display_status') or job.get('status') or '확인 중')}"
            ]
            started_at = job.get("started_at") or job.get("created_at")
            if started_at:
                metadata.append(f"시작 {_format_time(started_at)}")
            st_module.caption(" · ".join(metadata))
            rows = _clustering_rows(job)
        elif progress is not None:
            metadata = [f"시작 {_format_time(progress.started_at)}"]
            if progress.finished_at:
                metadata.append(f"종료 {_format_time(progress.finished_at)}")
            if progress.pid:
                metadata.append(f"PID {progress.pid:,}")
            st_module.caption(" · ".join(metadata))
            rows = _refresh_rows(progress)
        elif isinstance(job, dict):
            rows = _clustering_rows(job)
        else:
            rows = []

        if rows:
            st_module.dataframe(
                pd.DataFrame(rows),
                hide_index=True,
                width="stretch",
                height=min(420, 74 + len(rows) * 35),
            )
        if notice and not active_refresh and not active_cluster:
            st_module.caption(notice)
        if not rows and not notice:
            st_module.caption("표시할 최근 실행 단계가 없습니다.")


def _install_recent_attempt_marker(st_module: Any) -> None:
    from src import clustering_job_status_ui as module

    original = getattr(module, "build_recent_clustering_attempt_notice", None)
    if not callable(original) or getattr(original, "_dashboard_operation_panel", False):
        return

    @wraps(original)
    def wrapped(primary_job: Any, latest_attempt: Any) -> str:
        global _LAST_PRIMARY_JOB, _LAST_ATTEMPT_NOTICE
        _LAST_PRIMARY_JOB = primary_job if isinstance(primary_job, dict) else None
        _LAST_ATTEMPT_NOTICE = str(original(primary_job, latest_attempt) or "")
        progress = _remember_progress(st_module)
        if progress is not None or _active_clustering(_LAST_PRIMARY_JOB) or _LAST_ATTEMPT_NOTICE:
            return _PANEL_MARKER
        return ""

    wrapped._dashboard_operation_panel = True  # type: ignore[attr-defined]
    module.build_recent_clustering_attempt_notice = wrapped


def _install_info_panel(st_module: Any) -> None:
    original = getattr(st_module, "info", None)
    if not callable(original) or getattr(original, "_dashboard_operation_panel", False):
        return

    @wraps(original)
    def wrapped(value: Any, *args: Any, **kwargs: Any):
        if str(value or "") == _PANEL_MARKER:
            _render_panel(st_module)
            return None
        return original(value, *args, **kwargs)

    wrapped._dashboard_operation_panel = True  # type: ignore[attr-defined]
    st_module.info = wrapped


def _install_progress_memory(st_module: Any) -> None:
    for method_name in ("set_page_config", "rerun"):
        original = getattr(st_module, method_name, None)
        if not callable(original) or getattr(original, "_dashboard_operation_memory", False):
            continue

        @wraps(original)
        def wrapped(*args: Any, __original=original, **kwargs: Any):
            _remember_progress(st_module)
            return __original(*args, **kwargs)

        wrapped._dashboard_operation_memory = True  # type: ignore[attr-defined]
        setattr(st_module, method_name, wrapped)


def _install_action_button_guard(st_module: Any) -> None:
    try:
        from streamlit.delta_generator import DeltaGenerator
    except Exception:
        return
    original = getattr(DeltaGenerator, "button", None)
    if not callable(original) or getattr(original, "_dashboard_operation_guard", False):
        return

    @wraps(original)
    def wrapped(self: Any, label: Any, *args: Any, **kwargs: Any):
        clean_label = str(label or "").strip()
        progress = _remember_progress(st_module)
        if (
            clean_label in _TARGET_ACTIONS
            and progress is not None
            and progress.active
            and is_dashboard_refresh_active(progress)
        ):
            call_kwargs = dict(kwargs)
            call_kwargs["disabled"] = True
            call_kwargs["help"] = (
                f"최신 데이터 수집·분석이 진행 중입니다. "
                f"진행률 {progress.value:,}% · {progress.message or '상태 확인 중'}"
            )
            return original(self, label, *args, **call_kwargs)
        return original(self, label, *args, **kwargs)

    wrapped._dashboard_operation_guard = True  # type: ignore[attr-defined]
    DeltaGenerator.button = wrapped


def install_dashboard_operation_status_ui(st_module: Any) -> None:
    """실행 중 버튼 차단과 접이식 최신 단계 이력을 한곳에 설치합니다."""
    if getattr(st_module, "_dashboard_operation_status_ui_installed", False):
        return
    st_module._dashboard_operation_status_ui_installed = True
    _install_recent_attempt_marker(st_module)
    _install_info_panel(st_module)
    _install_progress_memory(st_module)
    _install_action_button_guard(st_module)
