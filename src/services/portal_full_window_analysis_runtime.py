from __future__ import annotations

from functools import wraps
from typing import Any


PORTAL_FULL_WINDOW_GROUPS = frozenset({"naver", "daum"})
PORTAL_FULL_WINDOW_SETTING_VALUE = 0
PORTAL_LEGACY_USER_LIMIT_MIN = 500
# DuckDB의 partitioned TopN 최적화는 n < 1,000,000을 요구합니다. 전체 기간 모드에서는
# 이 엔진 경계 바로 아래 값을 방어 상한으로 사용하며, 실제 운영 범위는 lookback_hours가
# 먼저 제한합니다. 현재 72시간 포털 원문 규모보다 충분히 큰 값입니다.
PORTAL_FULL_WINDOW_QUERY_LIMIT = 999_999


def portal_limit_uses_full_window(value: object) -> bool:
    """0 또는 과거 UI에서 저장하던 포털 상한은 전체 기간 모드로 해석합니다."""
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return True
    return normalized <= 0 or normalized >= PORTAL_LEGACY_USER_LIMIT_MIN


def install_portal_full_window_analysis_contract(
    discovery_module: Any | None = None,
) -> None:
    """NAVER·Daum은 별도 사용자 개수 상한 없이 최근 분석 시간 범위 전체를 사용합니다.

    기존 DB의 500개 이상 포털 상한 값은 삭제하지 않고 호환 입력으로 보존하되,
    런타임에서는 전체 기간 모드로 해석합니다. 10~499의 작은 명시값은 테스트와
    내부 진단용 제한으로 계속 지원합니다.
    """
    if discovery_module is None:
        from src.services import trend_discovery_service as discovery_module

    original_limits = getattr(discovery_module, "_normalized_analysis_limits", None)
    if callable(original_limits) and not getattr(
        original_limits,
        "_portal_full_window_analysis_contract",
        False,
    ):

        @wraps(original_limits)
        def full_window_limits(source_limits=None):
            limits = dict(original_limits(source_limits))
            defaults = dict(
                getattr(discovery_module, "DEFAULT_ANALYSIS_SOURCE_LIMITS", {}) or {}
            )
            for group_name in PORTAL_FULL_WINDOW_GROUPS:
                raw_value = (
                    source_limits.get(group_name)
                    if isinstance(source_limits, dict) and group_name in source_limits
                    else defaults.get(group_name, PORTAL_FULL_WINDOW_SETTING_VALUE)
                )
                if portal_limit_uses_full_window(raw_value):
                    limits[group_name] = PORTAL_FULL_WINDOW_QUERY_LIMIT
            return limits

        full_window_limits._portal_full_window_analysis_contract = True  # type: ignore[attr-defined]
        discovery_module._normalized_analysis_limits = full_window_limits

    original_candidate_limit = getattr(
        discovery_module,
        "_balanced_candidate_limit",
        None,
    )
    if callable(original_candidate_limit) and not getattr(
        original_candidate_limit,
        "_portal_full_window_analysis_contract",
        False,
    ):

        @wraps(original_candidate_limit)
        def full_window_candidate_limit(limit: int) -> int:
            if int(limit) >= PORTAL_FULL_WINDOW_QUERY_LIMIT:
                return PORTAL_FULL_WINDOW_QUERY_LIMIT
            return int(original_candidate_limit(limit))

        full_window_candidate_limit._portal_full_window_analysis_contract = True  # type: ignore[attr-defined]
        discovery_module._balanced_candidate_limit = full_window_candidate_limit

    defaults = getattr(discovery_module, "DEFAULT_ANALYSIS_SOURCE_LIMITS", None)
    if isinstance(defaults, dict):
        for group_name in PORTAL_FULL_WINDOW_GROUPS:
            defaults[group_name] = PORTAL_FULL_WINDOW_SETTING_VALUE


def install_portal_full_window_streamlit_contract(st_module: Any) -> None:
    """기존 포털 상한 입력을 최근 분석 범위 전체 안내로 바꿉니다."""
    if getattr(st_module, "_portal_full_window_analysis_contract", False):
        return

    original_number_input = st_module.number_input
    original_text_input = st_module.text_input
    original_markdown = st_module.markdown
    original_caption = st_module.caption

    @wraps(original_number_input)
    def number_input(label, *args, **kwargs):
        is_portal_analysis_limit = (
            str(label or "") in {"NAVER", "Daum"}
            and int(kwargs.get("min_value", -1) or -1) == 500
            and int(kwargs.get("max_value", -1) or -1) == 20000
        )
        if not is_portal_analysis_limit:
            return original_number_input(label, *args, **kwargs)

        help_text = (
            "NAVER·Daum은 별도 개수 상한을 두지 않고 설정한 순위 분석 시간 범위의 "
            "원문 전체를 사용합니다. 기본 분석 범위는 최근 72시간입니다."
        )
        original_text_input(
            label,
            value="최근 분석 범위 전체",
            disabled=True,
            help=help_text,
            key=f"portal_full_window_{str(label).casefold()}_analysis",
        )
        return PORTAL_FULL_WINDOW_SETTING_VALUE

    @wraps(original_markdown)
    def markdown(value, *args, **kwargs):
        text = str(value or "")
        if text == "##### 순위 계산 시 출처별 최대 분석량":
            value = "##### 순위 계산 시 출처별 분석 범위"
        return original_markdown(value, *args, **kwargs)

    @wraps(original_caption)
    def caption(value, *args, **kwargs):
        text = str(value or "")
        if text == (
            "최근 분석 범위 안에서도 한 출처가 문서량만으로 다른 출처를 밀어내지 않도록 "
            "출처별 상한을 적용합니다."
        ):
            value = (
                "NAVER·Daum은 최근 분석 시간 범위 전체를 사용합니다. "
                "YouTube·Google Trends·위키백과는 기존 출처별 분석량 제한을 유지합니다."
            )
        return original_caption(value, *args, **kwargs)

    number_input._portal_full_window_analysis_contract = True  # type: ignore[attr-defined]
    st_module.number_input = number_input
    st_module.markdown = markdown
    st_module.caption = caption
    st_module._portal_full_window_analysis_contract = True
