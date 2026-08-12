from __future__ import annotations

from functools import wraps
from typing import Any

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
BATCH_LOG_HELP = (
    "전체 1차 후보는 확실한 규칙만 적용해 만든 후보 수입니다. "
    "2차 검토 후보는 제목·사건·식별 정보·기존 군집 관점으로 반복 검토한 고유 후보 수이며, "
    "실제 Gemini 요청은 주제순으로 정렬한 뒤 예상 입력 225,000토큰 이하로 자동 분할됩니다. "
    "표의 입력·출력·총 토큰은 해당 배치 안의 여러 요청을 합산한 값입니다. "
    "요청별 예상·실제 입력 토큰, TPM 대기시간과 종료 사유는 군집 요청 지표 로그에 별도로 저장됩니다."
)


def _format_integer(value: Any) -> str:
    try:
        if pd.isna(value):
            return "0"
        return f"{int(value):,}"
    except (TypeError, ValueError, OverflowError):
        return str(value or "")


def _format_seconds_from_milliseconds(value: Any) -> str:
    try:
        if pd.isna(value):
            return "0.0"
        return f"{float(value) / 1000.0:,.1f}"
    except (TypeError, ValueError, OverflowError):
        return "0.0"


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


def install_clustering_batch_log_ui(st_module: Any) -> None:
    """Streamlit의 군집 배치 로그 표에만 표시 형식을 적용합니다."""
    original = getattr(st_module, "dataframe", None)
    if not callable(original) or getattr(original, "_clustering_batch_log_wrapper", False):
        return

    @wraps(original)
    def wrapped(data: Any = None, *args: Any, **kwargs: Any):
        formatted, matched = format_clustering_batch_log_frame(data)
        if matched:
            st_module.caption(BATCH_LOG_HELP)
        return original(formatted, *args, **kwargs)

    wrapped._clustering_batch_log_wrapper = True  # type: ignore[attr-defined]
    st_module.dataframe = wrapped
