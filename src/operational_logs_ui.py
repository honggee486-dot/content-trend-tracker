from __future__ import annotations

from collections import defaultdict
from functools import wraps
import re
from typing import Any, Callable, Mapping

import pandas as pd

from src.config import DEFAULT_DB_PATH
from src.database import connect_database
from src.services.program_log_service import (
    event_type_label,
    feature_label,
    list_recent_gemini_calls,
    list_recent_program_events,
    status_label,
)

_BATCH_LOG_LABEL = "배치별 토큰·품질 로그"
_CANDIDATE_HEADING = "글감 후보"
_DASHBOARD_INTRO_MARKER = '<div class="trend-intro-copy">'
_RENDERED_MARKER = "_operational_logs_rendered_for_dashboard"
_GEMINI_LOG_LIMIT = 100
_PROGRAM_LOG_MAX_LIMIT = 500
_PROGRAM_LIMIT_OPTIONS = (100, 300, 500)
_PROGRAM_TYPE_OPTIONS = ("전체", "작업 중심", "API만")
_PROGRAM_ORDER_OPTIONS = ("최신순", "실행 흐름")
_RUN_TYPE_LABELS = {
    "background_refresh": "예약 데이터 수집",
    "manual_refresh": "최신 데이터 수집",
    "ranking_rebuild": "저장 자료 정리·순위 재계산",
    "topic_angle_generation": "주제 방향 자동 생성",
}
_START_STATUSES = {"started", "clicked", "queued", "running"}
_EXECUTION_ID_PATTERN = re.compile(r"(?:^|\s*·\s*)실행 ID\s+\S+")


def _format_time(value: Any) -> str:
    if value is None:
        return "-"
    formatter = getattr(value, "strftime", None)
    if callable(formatter):
        try:
            return formatter("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return str(value)


def _format_seconds(duration_ms: Any, *, status: Any = "") -> str:
    if str(status or "").casefold() in _START_STATUSES:
        return "-"
    try:
        return f"{max(0, int(duration_ms or 0)) / 1000.0:,.2f}"
    except (TypeError, ValueError):
        return "-"


def _format_item_count(value: Any, *, status: Any = "") -> str:
    if str(status or "").casefold() in _START_STATUSES:
        return "-"
    try:
        return f"{max(0, int(value or 0)):,}"
    except (TypeError, ValueError):
        return "-"


def _gemini_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "시간": _format_time(row.get("created_at")),
                "상태": (
                    "캐시"
                    if bool(row.get("cache_hit"))
                    else status_label(row.get("status"))
                ),
                "모델": str(row.get("model_name") or ""),
                "기능": feature_label(row.get("feature_id")),
                "기능 버전": str(row.get("feature_version") or ""),
                "시도": int(row.get("attempt_number") or 0),
                "요청 항목": int(row.get("requested_item_count") or 0),
                "입력 토큰": int(row.get("input_tokens") or 0),
                "출력 토큰": int(row.get("output_tokens") or 0),
                "사고 토큰": int(row.get("thought_tokens") or 0),
                "총 토큰": int(row.get("total_tokens") or 0),
                "HTTP": (
                    ""
                    if row.get("http_status") is None
                    else int(row.get("http_status") or 0)
                ),
                "종료 사유": str(row.get("finish_reason") or ""),
                "시간(초)": _format_seconds(row.get("duration_ms")),
                "오류": " · ".join(
                    value
                    for value in (
                        str(row.get("error_type") or "").strip(),
                        str(row.get("error_message") or "").strip(),
                    )
                    if value
                ),
            }
            for row in rows
        ]
    )


def _collection_run_types(con: Any, rows: list[dict[str, Any]]) -> dict[str, str]:
    run_ids = sorted(
        {
            str(row.get("correlation_id") or "")
            for row in rows
            if str(row.get("correlation_id") or "").startswith("collection_")
        }
    )
    if not run_ids:
        return {}
    placeholders = ", ".join("?" for _ in run_ids)
    try:
        values = con.execute(
            f"SELECT run_id, run_type FROM collection_runs WHERE run_id IN ({placeholders})",
            run_ids,
        ).fetchall()
    except Exception:
        return {}
    return {str(run_id): str(run_type) for run_id, run_type in values}


def _execution_label(
    correlation_id: Any,
    run_types: Mapping[str, str],
) -> str:
    value = str(correlation_id or "").strip()
    if not value:
        return "개별"
    run_type = str(run_types.get(value) or "")
    if run_type:
        return f"{_RUN_TYPE_LABELS.get(run_type, run_type)} · {value[-8:]}"
    if value.startswith(("cluster_", "clustering_", "job_")):
        return f"2단계 군집 · {value[-8:]}"
    return value if len(value) <= 16 else f"…{value[-12:]}"


def _display_action(
    row: Mapping[str, Any],
    run_types: Mapping[str, str],
) -> str:
    action = str(row.get("action") or "").strip()
    correlation_id = str(row.get("correlation_id") or "")
    run_type = str(run_types.get(correlation_id) or "")
    run_label = _RUN_TYPE_LABELS.get(run_type, run_type)
    if action == "실행 기록 종료" and run_label:
        return f"{run_label} 실행 종료"
    if action.startswith("실행 기록 · "):
        suffix = action.split("·", 1)[1].strip()
        return f"{_RUN_TYPE_LABELS.get(suffix, suffix)} 실행 시작"
    return action


def _display_detail(value: Any) -> str:
    text = _EXECUTION_ID_PATTERN.sub("", str(value or "")).strip(" ·")
    return text


def filter_program_rows(
    rows: list[dict[str, Any]],
    *,
    type_filter: str,
) -> list[dict[str, Any]]:
    if type_filter == "API만":
        allowed = {"api"}
    elif type_filter == "작업 중심":
        allowed = {"button", "task", "stage", "system"}
    else:
        return list(rows)
    return [
        row for row in rows if str(row.get("event_type") or "").casefold() in allowed
    ]


def order_program_rows(
    rows: list[dict[str, Any]],
    *,
    flow_order: bool,
) -> list[dict[str, Any]]:
    if not flow_order:
        return list(rows)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        correlation_id = str(row.get("correlation_id") or "").strip()
        key = correlation_id or str(row.get("event_id") or "")
        groups[key].append(row)

    def event_value(row: Mapping[str, Any]) -> Any:
        return row.get("event_time")

    ordered_groups = sorted(
        groups.values(),
        key=lambda values: max(event_value(row) for row in values),
        reverse=True,
    )
    flattened: list[dict[str, Any]] = []
    for values in ordered_groups:
        flattened.extend(sorted(values, key=event_value))
    return flattened


def _program_frame(
    rows: list[dict[str, Any]],
    *,
    run_types: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    labels = dict(run_types or {})
    return pd.DataFrame(
        [
            {
                "시간": _format_time(row.get("event_time")),
                "실행": _execution_label(row.get("correlation_id"), labels),
                "종류": event_type_label(row.get("event_type")),
                "상태": status_label(row.get("status")),
                "작업": _display_action(row, labels),
                "항목": _format_item_count(
                    row.get("item_count"),
                    status=row.get("status"),
                ),
                "시간(초)": _format_seconds(
                    row.get("duration_ms"),
                    status=row.get("status"),
                ),
                "상세": _display_detail(row.get("detail")),
                "실행 위치": str(row.get("source") or ""),
            }
            for row in rows
        ]
    )


def render_operational_logs(st_module: Any) -> None:
    try:
        with connect_database(DEFAULT_DB_PATH) as con:
            gemini_rows = list_recent_gemini_calls(con, limit=_GEMINI_LOG_LIMIT)
            all_program_rows = list_recent_program_events(
                con,
                limit=_PROGRAM_LOG_MAX_LIMIT,
            )
            run_types = _collection_run_types(con, all_program_rows)
    except Exception as exc:
        st_module.warning(f"운영 로그를 불러오지 못했습니다: {exc}")
        return

    with st_module.expander("Gemini 로그 · 최근 100건", expanded=False):
        st_module.caption(
            "실제 gemini_api_calls 기록의 최신 100건입니다. 요청 본문과 API 키는 표시하거나 프로그램 로그에 복사하지 않습니다."
        )
        if gemini_rows:
            st_module.dataframe(
                _gemini_frame(gemini_rows),
                hide_index=True,
                width="stretch",
                height=420,
            )
        else:
            st_module.info("저장된 Gemini 호출 기록이 없습니다.")

    with st_module.expander("프로그램 로그", expanded=False):
        st_module.caption(
            "버튼과 작업, 출처별 수집, 정리, 군집 관점별 집계, 주제 방향 처리와 실제 API 전송을 확인합니다."
        )
        limit = int(
            st_module.selectbox(
                "조회 범위",
                _PROGRAM_LIMIT_OPTIONS,
                index=0,
                format_func=lambda value: f"최근 {int(value):,}건",
                key="program_log_limit",
            )
        )
        type_filter = st_module.selectbox(
            "로그 종류",
            _PROGRAM_TYPE_OPTIONS,
            index=0,
            key="program_log_type_filter",
        )
        order_mode = st_module.selectbox(
            "표시 순서",
            _PROGRAM_ORDER_OPTIONS,
            index=0,
            key="program_log_order_mode",
        )
        selected_rows = filter_program_rows(
            all_program_rows,
            type_filter=str(type_filter),
        )[:limit]
        selected_rows = order_program_rows(
            selected_rows,
            flow_order=str(order_mode) == "실행 흐름",
        )
        if selected_rows:
            st_module.dataframe(
                _program_frame(selected_rows, run_types=run_types),
                hide_index=True,
                width="stretch",
                height=520,
            )
        else:
            st_module.info("선택한 조건에 해당하는 프로그램 로그가 없습니다.")


def _render_once(st_module: Any) -> None:
    if bool(getattr(st_module, _RENDERED_MARKER, False)):
        return
    render_operational_logs(st_module)
    setattr(st_module, _RENDERED_MARKER, True)


class _ExpanderWithOperationalLogs:
    def __init__(self, base_context: Any, renderer: Callable[[], None]) -> None:
        self._base_context = base_context
        self._renderer = renderer

    def __enter__(self):
        return self._base_context.__enter__()

    def __exit__(self, exc_type, exc, traceback):
        result = self._base_context.__exit__(exc_type, exc, traceback)
        if exc_type is None:
            self._renderer()
        return result


def install_operational_logs_ui(st_module: Any) -> None:
    """배치 로그 바로 아래에 Gemini·프로그램 로그 접이식을 이어 붙입니다."""
    original_expander = getattr(st_module, "expander", None)
    if not callable(original_expander) or getattr(
        st_module,
        "_operational_logs_ui_installed",
        False,
    ):
        return
    st_module._operational_logs_ui_installed = True

    @wraps(original_expander)
    def wrapped_expander(label: Any, *args: Any, **kwargs: Any):
        context = original_expander(label, *args, **kwargs)
        if str(label or "").strip() != _BATCH_LOG_LABEL:
            return context
        return _ExpanderWithOperationalLogs(
            context,
            lambda: _render_once(st_module),
        )

    wrapped_expander._operational_logs_ui = True  # type: ignore[attr-defined]
    st_module.expander = wrapped_expander

    original_markdown = getattr(st_module, "markdown", None)
    if callable(original_markdown):

        @wraps(original_markdown)
        def wrapped_markdown(value: Any, *args: Any, **kwargs: Any):
            if isinstance(value, str) and _DASHBOARD_INTRO_MARKER in value:
                setattr(st_module, _RENDERED_MARKER, False)
            return original_markdown(value, *args, **kwargs)

        st_module.markdown = wrapped_markdown

    original_subheader = getattr(st_module, "subheader", None)
    if callable(original_subheader):

        @wraps(original_subheader)
        def wrapped_subheader(value: Any, *args: Any, **kwargs: Any):
            if str(value or "").strip() == _CANDIDATE_HEADING:
                _render_once(st_module)
            return original_subheader(value, *args, **kwargs)

        st_module.subheader = wrapped_subheader
