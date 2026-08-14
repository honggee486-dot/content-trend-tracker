from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Mapping, Sequence

import pandas as pd


_REQUIRED_COLUMNS = frozenset(
    {
        "전체 1차 군집",
        "요청 1차 군집",
        "입력 토큰",
        "총 토큰",
        "시간(ms)",
    }
)
_COLUMN_RENAMES = {
    "전체 1차 군집": "전체 1차 후보",
    "요청 1차 군집": "2차 검토 후보",
}
_COUNT_COLUMNS = (
    "검색 미처리",
    "전체 1차 후보",
    "2차 검토 후보",
    "요청 원문",
    "URL 중복 절감",
    "URL 충돌 분리",
    "동일 제목 병합",
    "다음 배치 대기",
    "기존 후보",
    "처리",
    "기존 연결",
    "새 군집",
    "불확실",
    "충돌",
    "입력 토큰",
    "출력 토큰",
    "사고 토큰",
    "총 토큰",
)
_CLUSTERING_FEATURE_ID = "trend_cluster_grouping_v3"
_VIEW_LABELS = {
    "title": "제목",
    "event": "사건",
    "identity": "식별",
    "existing": "기존 군집",
}
_DETAIL_RENDERED_ATTR = "_clustering_request_detail_rendered"

BATCH_LOG_HELP = (
    "전체 1차 후보는 확실한 규칙만 적용해 만든 후보 수입니다. "
    "2차 검토 후보는 제목·사건·식별 정보·기존 군집 관점으로 반복 검토한 고유 후보 수이며, "
    "실제 Gemini 요청은 주제순으로 정렬한 뒤 예상 입력 225,000토큰 이하로 자동 분할됩니다. "
    "표의 입력·출력·총 토큰은 해당 배치 안의 여러 요청을 합산한 값입니다. "
    "아래 요청별 상세 표에서 각 Gemini 호출의 토큰·대기·처리시간을 직접 비교할 수 있습니다."
)
REQUEST_DETAIL_HELP = (
    "같은 2차 군집 작업에 포함된 실제 Gemini 호출을 요청 단위로 보여줍니다. "
    "관점·후보 수·예상/실제 입력 토큰과 출력·사고·총 토큰, TPM 입력 대기, "
    "API 처리시간, HTTP·오류 상태를 비교할 수 있습니다."
)


def _format_integer(value: Any) -> str:
    try:
        if pd.isna(value):
            return "0"
        return f"{int(value):,}"
    except (TypeError, ValueError, OverflowError):
        return str(value or "")


def _format_decimal(value: Any) -> str:
    try:
        if pd.isna(value):
            return "0.0"
        return f"{float(value):,.1f}"
    except (TypeError, ValueError, OverflowError):
        return "0.0"


def _format_seconds_from_milliseconds(value: Any) -> str:
    try:
        if pd.isna(value):
            return "0.0"
        return f"{float(value) / 1000.0:,.1f}"
    except (TypeError, ValueError, OverflowError):
        return "0.0"


def _format_timestamp(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    formatter = getattr(value, "strftime", None)
    if callable(formatter):
        return formatter("%H:%M:%S")
    return str(value)


def format_clustering_batch_log_frame(data: Any) -> tuple[Any, bool]:
    """군집 배치 로그 표만 읽기 쉬운 표시 문자열로 변환합니다."""
    if not isinstance(data, pd.DataFrame) or not _REQUIRED_COLUMNS.issubset(data.columns):
        return data, False

    frame = data.copy().rename(columns=_COLUMN_RENAMES)
    duration = frame.pop("시간(ms)")
    insert_at = min(len(frame.columns), max(0, len(frame.columns) - 1))
    frame.insert(
        insert_at,
        "시간(초)",
        duration.map(_format_seconds_from_milliseconds),
    )
    for column in _COUNT_COLUMNS:
        if column in frame.columns:
            frame[column] = frame[column].map(_format_integer)
    return frame, True


def load_clustering_request_detail_rows(
    con: Any,
    *,
    job_id: str,
) -> list[dict[str, Any]]:
    """완료된 군집 배치 시간 구간과 Gemini 호출 로그를 연결합니다."""
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        return []
    try:
        rows = con.execute(
            """
            SELECT
                b.batch_number,
                ROW_NUMBER() OVER (
                    PARTITION BY b.batch_number
                    ORDER BY g.created_at, g.attempt_number, g.request_hash
                ) AS request_number,
                g.created_at,
                g.model_name,
                COALESCE(m.analysis_view, '') AS analysis_view,
                COALESCE(g.requested_item_count, m.requested_item_count, 0) AS requested_item_count,
                COALESCE(m.estimated_input_tokens, 0) AS estimated_input_tokens,
                COALESCE(g.input_tokens, m.actual_input_tokens, 0) AS input_tokens,
                COALESCE(g.output_tokens, 0) AS output_tokens,
                COALESCE(g.thought_tokens, 0) AS thought_tokens,
                COALESCE(g.total_tokens, 0) AS total_tokens,
                COALESCE(m.tpm_wait_seconds, 0.0) AS tpm_wait_seconds,
                COALESCE(g.duration_ms, m.duration_ms, 0) AS duration_ms,
                g.http_status,
                COALESCE(g.status, '') AS status,
                COALESCE(g.error_type, '') AS error_type,
                COALESCE(g.finish_reason, '') AS finish_reason
            FROM trend_clustering_job_batches b
            JOIN gemini_api_calls g
              ON g.created_at >= b.started_at
             AND g.created_at <= COALESCE(b.finished_at, CURRENT_TIMESTAMP)
             AND g.feature_id = ?
            LEFT JOIN trend_clustering_request_metrics m
              ON m.request_hash = g.request_hash
             AND m.feature_id = g.feature_id
            WHERE b.job_id = ?
            ORDER BY b.batch_number, g.created_at, g.attempt_number, g.request_hash
            """,
            [_CLUSTERING_FEATURE_ID, normalized_job_id],
        ).fetchall()
    except Exception:
        return []
    columns = [str(item[0]) for item in con.description]
    return [dict(zip(columns, row)) for row in rows]


def format_clustering_request_detail_frame(
    rows: Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    """요청별 저장 지표를 사람이 바로 비교할 수 있는 표로 바꿉니다."""
    formatted: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        analysis_view = str(row.get("analysis_view") or "").strip()
        status = str(row.get("status") or "").strip() or "unknown"
        error_type = str(row.get("error_type") or "").strip()
        formatted.append(
            {
                "배치": int(row.get("batch_number") or 0),
                "요청": int(row.get("request_number") or 0),
                "시각": _format_timestamp(row.get("created_at")),
                "관점": _VIEW_LABELS.get(analysis_view, analysis_view or "-") ,
                "후보": _format_integer(row.get("requested_item_count")),
                "예상 입력": _format_integer(row.get("estimated_input_tokens")),
                "실제 입력": _format_integer(row.get("input_tokens")),
                "출력": _format_integer(row.get("output_tokens")),
                "사고": _format_integer(row.get("thought_tokens")),
                "총 토큰": _format_integer(row.get("total_tokens")),
                "TPM 대기(초)": _format_decimal(row.get("tpm_wait_seconds")),
                "API 시간(초)": _format_seconds_from_milliseconds(row.get("duration_ms")),
                "HTTP": "-" if row.get("http_status") in (None, 0, "") else str(row.get("http_status")),
                "상태": f"{status} · {error_type}" if error_type else status,
                "종료": str(row.get("finish_reason") or "").strip() or "-",
            }
        )
    return pd.DataFrame(formatted)


def _load_latest_clustering_request_details() -> list[dict[str, Any]]:
    from src.config import DEFAULT_DB_PATH
    from src.database import connect_database
    from src.services.trend_clustering_job_service import get_representative_clustering_job

    with connect_database(DEFAULT_DB_PATH) as con:
        job = get_representative_clustering_job(con)
        if not isinstance(job, dict):
            return []
        return load_clustering_request_detail_rows(
            con,
            job_id=str(job.get("job_id") or ""),
        )


def install_clustering_batch_log_ui(
    st_module: Any,
    *,
    detail_loader: Callable[[], Sequence[Mapping[str, Any]]] | None = None,
) -> None:
    """군집 배치 합계와 같은 작업의 실제 Gemini 요청별 비교표를 표시합니다."""
    original = getattr(st_module, "dataframe", None)
    if not callable(original) or getattr(original, "_clustering_batch_log_wrapper", False):
        return
    active_detail_loader = detail_loader or _load_latest_clustering_request_details
    setattr(st_module, _DETAIL_RENDERED_ATTR, False)

    @wraps(original)
    def wrapped(data: Any = None, *args: Any, **kwargs: Any):
        formatted, matched = format_clustering_batch_log_frame(data)
        if matched:
            st_module.caption(BATCH_LOG_HELP)
        result = original(formatted, *args, **kwargs)
        if not matched or bool(getattr(st_module, _DETAIL_RENDERED_ATTR, False)):
            return result

        setattr(st_module, _DETAIL_RENDERED_ATTR, True)
        try:
            rows = list(active_detail_loader() or ())
        except Exception as exc:
            st_module.caption(f"Gemini 요청별 상세 로그를 불러오지 못했습니다: {exc}")
            return result
        if not rows:
            st_module.caption(
                "이 군집 실행에는 연결할 수 있는 Gemini 요청별 상세 지표가 없습니다. "
                "새 실행부터 요청 단위 비교가 표시됩니다."
            )
            return result

        detail_frame = format_clustering_request_detail_frame(rows)
        st_module.markdown("**실제 Gemini 요청별 토큰·시간 비교**")
        st_module.caption(REQUEST_DETAIL_HELP)
        original(
            detail_frame,
            hide_index=True,
            width="stretch",
            height=min(520, 74 + len(detail_frame) * 35),
        )
        return result

    wrapped._clustering_batch_log_wrapper = True  # type: ignore[attr-defined]
    st_module.dataframe = wrapped
