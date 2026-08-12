from __future__ import annotations

from functools import wraps
import math
import re
from typing import Any

import pandas as pd


_DURATION_COLUMNS = {
    "시간(ms)": ("시간(초)", "milliseconds"),
    "시간(초)": ("시간(초)", "seconds"),
    "소요 시간": ("소요 시간(초)", "display"),
    "소요 시간(초)": ("소요 시간(초)", "seconds"),
    "평균 소요": ("평균 소요(초)", "display"),
    "평균 소요(초)": ("평균 소요(초)", "seconds"),
    "소요": ("소요(초)", "display"),
    "소요(초)": ("소요(초)", "seconds"),
    "제한 시간": ("제한 시간(초)", "display"),
    "제한 시간(초)": ("제한 시간(초)", "seconds"),
}
_METRIC_DURATION_TERMS = ("소요", "대기 시간", "누적 대기", "제한 시간")
_QUANTITY_TERMS = (
    "토큰",
    "항목",
    "요청",
    "시도",
    "호출",
    "재시도",
    "성공",
    "실패",
    "신규",
    "갱신",
    "생략",
    "결과",
    "처리",
    "발견",
    "원문",
    "건수",
    "횟수",
    "상한",
    "글자",
    "후보",
    "군집",
    "배치",
    "대기",
    "연결",
    "충돌",
    "미처리",
    "페이지",
)
_QUANTITY_EXCLUSIONS = (
    "비율",
    "오류율",
    "신규율",
    "저장률",
    "순위",
    "점수",
    "시간",
    "시각",
    "날짜",
    "경과",
    "HTTP",
    "버전",
)
_LOG_CONTEXT_COLUMNS = {
    "기간",
    "시각",
    "시간",
    "모델",
    "기능",
    "실행",
    "종류",
    "작업",
    "오류",
    "포털",
    "검색어",
    "출처",
    "출처·단계",
    "종료 사유",
    "마지막 요청",
    "최근 호출",
}
_MISSING_TEXT = {"", "-", "기록 없음", "이전 기록", "계산 불가", "확인 불가"}
_INTEGER_TOKEN_PATTERN = re.compile(r"(?<![\d.,-])\d{4,}(?![\d.,])")
_NUMBER_PATTERN = re.compile(r"^[+-]?[\d,]+(?:\.\d+)?$")
_MILLISECONDS_PATTERN = re.compile(r"^([\d,]+(?:\.\d+)?)\s*ms$", re.IGNORECASE)
_SECONDS_PATTERN = re.compile(r"^([\d,]+(?:\.\d+)?)\s*초$")
_MINUTES_SECONDS_PATTERN = re.compile(
    r"^(\d[\d,]*)\s*분(?:\s*([\d,]+(?:\.\d+)?)\s*초)?$"
)
_DATE_TIME_PATTERN = re.compile(
    r"(?:^|\s)\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?(?:\s|$)"
)
_VERSION_PATTERN = re.compile(r"(?:^|\s)[vV]?\d+\.\d+(?:\.\d+)+(?:\s|$)")
_RATIO_PATTERN = re.compile(r"^[+-]?[\d,]+(?:\.\d+)?\s*/\s*[+-]?[\d,]+(?:\.\d+)?(?:\D.*)?$")
_PERCENT_PATTERN = re.compile(r"^[+-]?[\d,]+(?:\.\d+)?\s*%$")
_ELAPSED_TEXT_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(?:초|분|시간|일)\s*전")


def format_log_integer(value: Any, *, missing: str = "-") -> str:
    if value is None:
        return missing
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return str(value)
    if not math.isfinite(numeric):
        return missing
    if numeric.is_integer():
        return f"{int(numeric):,}"
    return f"{numeric:,.2f}".rstrip("0").rstrip(".")


def format_log_seconds_from_milliseconds(
    value: Any,
    *,
    missing: str = "-",
    suffix: bool = True,
    zero_as_missing: bool = False,
) -> str:
    if value is None:
        return missing
    try:
        milliseconds = max(0.0, float(value))
    except (TypeError, ValueError, OverflowError):
        return missing
    if not math.isfinite(milliseconds) or (zero_as_missing and milliseconds <= 0):
        return missing
    text = f"{milliseconds / 1000.0:,.2f}"
    return f"{text}초" if suffix else text


def _parse_display_seconds(value: Any, *, unit: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        return max(0.0, numeric / 1000.0 if unit == "milliseconds" else numeric)

    text = str(value or "").strip()
    if text in _MISSING_TEXT:
        return None
    match = _MILLISECONDS_PATTERN.fullmatch(text)
    if match:
        return max(0.0, float(match.group(1).replace(",", "")) / 1000.0)
    match = _SECONDS_PATTERN.fullmatch(text)
    if match:
        return max(0.0, float(match.group(1).replace(",", "")))
    match = _MINUTES_SECONDS_PATTERN.fullmatch(text)
    if match:
        minutes = float(match.group(1).replace(",", ""))
        seconds = float((match.group(2) or "0").replace(",", ""))
        return max(0.0, minutes * 60.0 + seconds)
    if _NUMBER_PATTERN.fullmatch(text):
        numeric = float(text.replace(",", ""))
        return max(0.0, numeric / 1000.0 if unit == "milliseconds" else numeric)
    return None


def _format_duration_cell(value: Any, *, unit: str) -> str:
    seconds = _parse_display_seconds(value, unit=unit)
    if seconds is None:
        return str(value if value is not None else "-")
    return f"{seconds:,.2f}"


def _is_quantity_column(column: object) -> bool:
    name = str(column or "")
    if any(term in name for term in _QUANTITY_EXCLUSIONS):
        return False
    return any(term in name for term in _QUANTITY_TERMS)


def _format_quantity_cell(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return format_log_integer(value)
    text = str(value)
    stripped = text.strip()
    if stripped in _MISSING_TEXT:
        return text
    if _NUMBER_PATTERN.fullmatch(stripped):
        return format_log_integer(stripped.replace(",", ""))

    def replace(match: re.Match[str]) -> str:
        return f"{int(match.group(0)):,}"

    return _INTEGER_TOKEN_PATTERN.sub(replace, text)


def _looks_like_log_frame(frame: pd.DataFrame) -> bool:
    columns = [str(column) for column in frame.columns]
    column_set = set(columns)
    has_duration = any(column in _DURATION_COLUMNS for column in columns)
    has_quantity = any(_is_quantity_column(column) for column in columns)
    has_token = any("토큰" in column for column in columns)
    has_status = "상태" in column_set
    has_time_context = any(
        column in {"시각", "시간", "최근 호출", "마지막 요청"}
        or column.endswith("시각")
        for column in columns
    )
    has_period_context = "기간" in column_set and has_duration
    has_model_context = "모델" in column_set and (
        "기능" in column_set or has_status or has_time_context
    )
    has_portal_context = "포털" in column_set and "검색어" in column_set
    has_source_context = (
        "출처" in column_set or "출처·단계" in column_set
    ) and (has_status or has_duration or has_time_context)

    if has_duration and (
        has_status
        or has_time_context
        or has_period_context
        or has_model_context
        or has_portal_context
        or has_source_context
    ):
        return True
    if has_token and (has_status or has_time_context or has_model_context):
        return True
    if has_quantity and (has_time_context or has_portal_context):
        return True
    return False


def format_log_dataframe(data: Any) -> tuple[Any, bool]:
    if not isinstance(data, pd.DataFrame) or not _looks_like_log_frame(data):
        return data, False

    frame = data.copy()
    renamed: dict[object, str] = {}
    for column in list(frame.columns):
        name = str(column)
        duration_spec = _DURATION_COLUMNS.get(name)
        if duration_spec is not None:
            display_name, unit = duration_spec
            frame[column] = frame[column].map(
                lambda value, unit=unit: _format_duration_cell(value, unit=unit)
            )
            renamed[column] = display_name
            continue
        if _is_quantity_column(name):
            frame[column] = frame[column].map(_format_quantity_cell)
    if renamed:
        frame = frame.rename(columns=renamed)
    return frame, True


def _preserve_metric_value(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return False
    text = str(value).strip()
    if not text or text in _MISSING_TEXT:
        return True
    return bool(
        _DATE_TIME_PATTERN.search(text)
        or _VERSION_PATTERN.search(text)
        or _RATIO_PATTERN.fullmatch(text)
        or _PERCENT_PATTERN.fullmatch(text)
        or _ELAPSED_TEXT_PATTERN.search(text)
    )


def format_log_metric_value(label: Any, value: Any) -> Any:
    name = str(label or "")
    if any(term in name for term in _METRIC_DURATION_TERMS):
        seconds = _parse_display_seconds(value, unit="display")
        if seconds is not None:
            return f"{seconds:,.2f}초"
    if _is_quantity_column(name) and not _preserve_metric_value(value):
        return _format_quantity_cell(value)
    return value


def _format_metric_delta(label: Any, delta: Any) -> Any:
    if delta is None:
        return None
    if _is_quantity_column(label) and not _preserve_metric_value(delta):
        return _format_quantity_cell(delta)
    return delta


def _wrapper_chain_has_marker(callable_value: Any, marker: str) -> bool:
    current = callable_value
    seen: set[int] = set()
    for _ in range(32):
        if not callable(current):
            return False
        identity = id(current)
        if identity in seen:
            return False
        seen.add(identity)
        if bool(getattr(current, marker, False)):
            return True
        current = getattr(current, "__wrapped__", None)
    return False


def _wrap_metric_callable(original: Any, *, method: bool) -> Any:
    if not callable(original) or _wrapper_chain_has_marker(
        original,
        "_log_display_formatting",
    ):
        return original

    if method:

        @wraps(original)
        def wrapped(self, label: Any, value: Any, *args: Any, **kwargs: Any):
            formatted_args = list(args)
            if formatted_args:
                formatted_args[0] = _format_metric_delta(label, formatted_args[0])
            elif "delta" in kwargs:
                kwargs = dict(kwargs)
                kwargs["delta"] = _format_metric_delta(label, kwargs.get("delta"))
            return original(
                self,
                label,
                format_log_metric_value(label, value),
                *formatted_args,
                **kwargs,
            )

    else:

        @wraps(original)
        def wrapped(label: Any, value: Any, *args: Any, **kwargs: Any):
            formatted_args = list(args)
            if formatted_args:
                formatted_args[0] = _format_metric_delta(label, formatted_args[0])
            elif "delta" in kwargs:
                kwargs = dict(kwargs)
                kwargs["delta"] = _format_metric_delta(label, kwargs.get("delta"))
            return original(
                label,
                format_log_metric_value(label, value),
                *formatted_args,
                **kwargs,
            )

    wrapped._log_display_formatting = True  # type: ignore[attr-defined]
    return wrapped


def _install_metric_formatting(st_module: Any) -> None:
    module_metric = getattr(st_module, "metric", None)
    wrapped_module_metric = _wrap_metric_callable(module_metric, method=False)
    if wrapped_module_metric is not module_metric:
        st_module.metric = wrapped_module_metric

    if str(getattr(st_module, "__name__", "")) != "streamlit":
        return
    try:
        from streamlit.delta_generator import DeltaGenerator
    except Exception:
        return
    class_metric = getattr(DeltaGenerator, "metric", None)
    wrapped_class_metric = _wrap_metric_callable(class_metric, method=True)
    if wrapped_class_metric is not class_metric:
        DeltaGenerator.metric = wrapped_class_metric


def _install_duration_helper_contract() -> None:
    from src import collection_history_ui
    from src import gemini_stability_ui
    from src import query_discovery_diagnostics_ui

    collection_history_ui._format_duration = (  # type: ignore[attr-defined]
        lambda value: format_log_seconds_from_milliseconds(value, suffix=True)
    )
    gemini_stability_ui._duration = (  # type: ignore[attr-defined]
        lambda value: format_log_seconds_from_milliseconds(
            value,
            missing="기록 없음",
            suffix=True,
            zero_as_missing=True,
        )
    )
    query_discovery_diagnostics_ui._format_duration_ms = (  # type: ignore[attr-defined]
        lambda value: format_log_seconds_from_milliseconds(
            value,
            missing="기록 없음",
            suffix=True,
        )
    )


def install_log_display_formatting(st_module: Any) -> None:
    """로그·이력의 수량과 토큰은 쉼표, 소요 시간은 초 단위로 통일합니다."""
    _install_duration_helper_contract()
    _install_metric_formatting(st_module)
    original = getattr(st_module, "dataframe", None)
    if not callable(original) or _wrapper_chain_has_marker(
        original,
        "_log_display_formatting",
    ):
        return

    @wraps(original)
    def wrapped(data: Any = None, *args: Any, **kwargs: Any):
        formatted, _matched = format_log_dataframe(data)
        return original(formatted, *args, **kwargs)

    wrapped._log_display_formatting = True  # type: ignore[attr-defined]
    st_module.dataframe = wrapped
