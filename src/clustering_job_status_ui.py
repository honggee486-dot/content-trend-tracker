from __future__ import annotations

from functools import wraps
import re
from typing import Any, Callable

import pandas as pd

from src.config import DEFAULT_DB_PATH
from src.database import connect_database

_CLUSTERING_JOB_HEADING = "#### 최근 2단계 군집 작업"
_LEGACY_REFRESH_LABEL = "군집 작업 상태 새로고침"
_LEGACY_BATCH_METRIC = "완료 배치"
_SNAPSHOT_METRIC = "처리 스냅샷"
_LIVE_PROGRESS_RENDERED = "_clustering_live_progress_rendered"
_LEGACY_REQUEST_PREFIX = re.compile(r"^요청\s+(\d+)회(?=\s*\|)")


def _snapshot_value(value: Any) -> str:
    text = str(value or "").strip()
    match = re.match(r"^(\d+)\s*/\s*\d+$", text)
    if match:
        return "1/1" if int(match.group(1)) > 0 else "0/1"
    try:
        return "1/1" if int(value or 0) > 0 else "0/1"
    except (TypeError, ValueError):
        return text


def build_clustering_metric_values(job: dict[str, Any]) -> dict[str, str]:
    """DB 원본을 바꾸지 않고 작업 상태에 맞는 화면용 처리량을 만듭니다."""
    if str(job.get("status") or "") == "skipped_overlap":
        return {
            "snapshot": "미실행",
            "processed_units": "미실행",
            "processed_source_items": "미실행",
            "remaining_items": "확인 안 함",
            "total_tokens": "미호출",
        }
    return {
        "snapshot": (
            f"{int(job.get('completed_batches') or 0):,}/"
            f"{int(job.get('max_batches') or 0):,}"
        ),
        "processed_units": f"{int(job.get('processed_units') or 0):,}개",
        "processed_source_items": (
            f"{int(job.get('processed_source_items') or 0):,}개"
        ),
        "remaining_items": f"{int(job.get('remaining_items') or 0):,}개",
        "total_tokens": f"{int(job.get('total_tokens') or 0):,}",
    }


def build_recent_clustering_attempt_notice(
    primary_job: dict[str, Any] | None,
    latest_attempt: dict[str, Any] | None,
) -> str:
    if not isinstance(latest_attempt, dict):
        return ""
    if str(latest_attempt.get("status") or "") != "skipped_overlap":
        return ""
    if (
        isinstance(primary_job, dict)
        and str(primary_job.get("job_id") or "")
        == str(latest_attempt.get("job_id") or "")
    ):
        return ""
    timestamp = str(
        latest_attempt.get("finished_at")
        or latest_attempt.get("created_at")
        or ""
    ).strip()
    timestamp = timestamp.replace("T", " ")[:19]
    when = f"{timestamp} 당시" if timestamp else "당시"
    return (
        f"최근 실행 시도 · {when} 기존 군집 작업이 실행 중이어서 "
        "새 요청을 시작하지 않았습니다."
    )


def render_clustering_job_error(st_module: Any, job: dict[str, Any]) -> None:
    """과거 중복 생략 문구를 현재 오류 경고로 다시 표시하지 않습니다."""
    if str(job.get("status") or "") == "skipped_overlap":
        return
    error_message = str(job.get("error_message") or "").strip()
    if error_message:
        st_module.warning(error_message)


def _rewrite_job_message(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if "실제 Gemini 요청" in value or "분석 관점" in value:
        return value
    return _LEGACY_REQUEST_PREFIX.sub(
        lambda match: f"실제 Gemini 요청 {match.group(1)}회",
        value,
        count=1,
    )


def _wrap_metric_method() -> None:
    try:
        from streamlit.delta_generator import DeltaGenerator
    except Exception:
        return
    original: Callable[..., Any] | None = getattr(DeltaGenerator, "metric", None)
    if not callable(original) or getattr(original, "_clustering_snapshot_metric", False):
        return

    @wraps(original)
    def wrapped(self, label: Any, value: Any, *args: Any, **kwargs: Any):
        if str(label or "").strip() == _LEGACY_BATCH_METRIC:
            return original(
                self,
                _SNAPSHOT_METRIC,
                _snapshot_value(value),
                *args,
                **kwargs,
            )
        return original(self, label, value, *args, **kwargs)

    wrapped._clustering_snapshot_metric = True  # type: ignore[attr-defined]
    DeltaGenerator.metric = wrapped


def _render_live_progress(st_module: Any) -> None:
    try:
        from src.services.trend_clustering_job_service import (
            get_representative_clustering_job,
        )

        # Streamlit의 기본 연결과 같은 DuckDB 구성으로 짧게 읽습니다. 동일 DB 파일을
        # read_only=True로 다시 열면 기존 read/write 연결과 configuration 충돌이 납니다.
        with connect_database(DEFAULT_DB_PATH) as con:
            job = get_representative_clustering_job(con)
    except Exception as exc:
        st_module.caption(f"2차 군집 진행 상태를 불러오지 못했습니다: {exc}")
        return
    if not isinstance(job, dict):
        return

    flow_text = str(job.get("progress_flow_text") or "").strip()
    if flow_text:
        st_module.caption(flow_text)
    stage_label = str(job.get("current_stage_label") or "상태 확인 중").strip()
    st_module.markdown(f"**현재 단계:** {stage_label}")
    progress_value = max(0, min(100, int(job.get("progress_percent") or 0)))
    progress_notice = str(job.get("progress_notice") or "").strip()
    if progress_notice:
        st_module.info(progress_notice)
    if str(job.get("status") or "") == "running":
        progress_text = str(
            job.get("display_status")
            or job.get("current_stage_label")
            or "2차 군집 상태 확인 중"
        )
    else:
        progress_text = str(
            job.get("current_stage_label")
            or job.get("display_status")
            or job.get("status")
            or "2차 군집 상태 확인 중"
        )
    st_module.progress(progress_value, text=progress_text)

    rows = list(job.get("progress_log_rows") or ())
    with st_module.container(border=True):
        st_module.markdown("**진행 로그 · 최근 단계**")
        if rows:
            st_module.dataframe(
                pd.DataFrame(rows),
                hide_index=True,
                width="stretch",
                height=min(420, 74 + len(rows) * 35),
            )
        else:
            st_module.caption(
                "아직 저장된 단계 로그가 없습니다. 다음 요청·응답 단계부터 시각과 누적 경과가 표시됩니다."
            )


def _render_live_progress_once(st_module: Any) -> None:
    if bool(getattr(st_module, _LIVE_PROGRESS_RENDERED, False)):
        return
    _render_live_progress(st_module)
    setattr(st_module, _LIVE_PROGRESS_RENDERED, True)


def install_clustering_job_status_ui(st_module: Any) -> None:
    """군집 제목 옆 새로고침과 현재 단계·진행 로그를 설치합니다."""
    original_markdown = getattr(st_module, "markdown", None)
    original_button = getattr(st_module, "button", None)
    original_warning = getattr(st_module, "warning", None)
    if (
        not callable(original_markdown)
        or not callable(original_button)
        or getattr(st_module, "_clustering_job_status_ui_installed", False)
    ):
        return

    st_module._clustering_job_status_ui_installed = True
    st_module._clustering_job_refresh_rendered = False
    setattr(st_module, _LIVE_PROGRESS_RENDERED, False)
    _wrap_metric_method()

    @wraps(original_markdown)
    def wrapped_markdown(value: Any, *args: Any, **kwargs: Any):
        if str(value or "").strip() != _CLUSTERING_JOB_HEADING:
            return original_markdown(value, *args, **kwargs)

        columns = st_module.columns(
            [1.65, 1.05, 7.30],
            gap="small",
            vertical_alignment="center",
        )
        columns[0].markdown(value, *args, **kwargs)
        st_module._clustering_job_refresh_rendered = True
        setattr(st_module, _LIVE_PROGRESS_RENDERED, False)
        clicked = columns[1].button(
            "상태 새로고침",
            key="refresh_clustering_job_status",
            width="stretch",
        )
        if clicked:
            st_module.rerun()
        return None

    @wraps(original_button)
    def wrapped_button(label: Any, *args: Any, **kwargs: Any):
        if (
            str(label or "").strip() == _LEGACY_REFRESH_LABEL
            and bool(getattr(st_module, "_clustering_job_refresh_rendered", False))
        ):
            _render_live_progress_once(st_module)
            return False
        return original_button(label, *args, **kwargs)

    st_module.markdown = wrapped_markdown
    st_module.button = wrapped_button

    if callable(original_warning):

        @wraps(original_warning)
        def wrapped_warning(value: Any, *args: Any, **kwargs: Any):
            return original_warning(_rewrite_job_message(value), *args, **kwargs)

        st_module.warning = wrapped_warning
