"""외부 데이터 호출량을 로컬 DB에 기록하고 NAVER 검색 한도를 제한합니다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import duckdb

NAVER_SEARCH_PROVIDER = "naver"
NAVER_SEARCH_API = "search_api"
GOOGLE_TRENDS_PROVIDER = "google"
GOOGLE_TRENDS_API = "trends_rss"
WIKIMEDIA_PROVIDER = "wikimedia"
WIKIMEDIA_API = "pageviews_top"
KAKAO_DAUM_PROVIDER = "kakao"
KAKAO_DAUM_API = "daum_search"

NAVER_SEARCH_OFFICIAL_DAILY_LIMIT = 25_000
NAVER_SEARCH_OFFICIAL_MONTHLY_LIMIT = 775_000
NAVER_SEARCH_OFFICIAL_RPS_LIMIT = 50
KAKAO_DAUM_OFFICIAL_DAILY_LIMIT = 50_000
KAKAO_ALL_API_OFFICIAL_MONTHLY_LIMIT = 3_000_000


class ApiQuotaExceededError(RuntimeError):
    """설정한 일간 또는 월간 안전 한도를 넘을 때 발생합니다."""


@dataclass(frozen=True)
class LocalApiUsage:
    daily_used: int
    monthly_used: int


@dataclass(frozen=True)
class ApiQuotaUsage(LocalApiUsage):
    daily_limit: int
    monthly_limit: int

    @property
    def daily_remaining(self) -> int:
        return max(0, self.daily_limit - self.daily_used)

    @property
    def monthly_remaining(self) -> int:
        return max(0, self.monthly_limit - self.monthly_used)


def clamp_naver_search_daily_limit(daily_limit: int) -> int:
    """사용자 설정값이 검색 API 공식 일간 한도를 넘지 않도록 제한합니다."""
    return max(1, min(int(daily_limit), NAVER_SEARCH_OFFICIAL_DAILY_LIMIT))


def clamp_naver_search_monthly_limit(monthly_limit: int) -> int:
    """사용자 설정값이 검색 API 공식 월간 한도를 넘지 않도록 제한합니다."""
    return max(1, min(int(monthly_limit), NAVER_SEARCH_OFFICIAL_MONTHLY_LIMIT))


def clamp_kakao_daum_daily_limit(daily_limit: int) -> int:
    """사용자 설정값이 Daum 검색 공식 일간 무료 한도를 넘지 않도록 제한합니다."""
    return max(1, min(int(daily_limit), KAKAO_DAUM_OFFICIAL_DAILY_LIMIT))


def clamp_kakao_daum_monthly_limit(monthly_limit: int) -> int:
    """카카오 앱 전체 API 월간 무료 한도보다 높게 설정하지 않도록 제한합니다."""
    return max(1, min(int(monthly_limit), KAKAO_ALL_API_OFFICIAL_MONTHLY_LIMIT))


# 이전 이름을 사용하는 외부 코드와의 호환용 별칭입니다.
def clamp_naver_search_limit(monthly_limit: int) -> int:
    return clamp_naver_search_monthly_limit(monthly_limit)


def _day_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _month_key(now: datetime) -> str:
    return now.strftime("%Y-%m")


def _get_count(
    con: duckdb.DuckDBPyConnection,
    *,
    provider: str,
    api_name: str,
    period_type: str,
    period_key: str,
) -> int:
    row = con.execute(
        """
        SELECT call_count
        FROM api_usage_counters
        WHERE provider = ? AND api_name = ? AND period_type = ? AND period_key = ?
        """,
        [provider, api_name, period_type, period_key],
    ).fetchone()
    return 0 if row is None else int(row[0] or 0)


def get_local_api_usage(
    con: duckdb.DuckDBPyConnection,
    *,
    provider: str,
    api_name: str,
    now: datetime | None = None,
) -> LocalApiUsage:
    """현재 일·월의 로컬 요청 횟수를 제공자 구분 없이 조회합니다."""
    current = now or datetime.now()
    return LocalApiUsage(
        daily_used=_get_count(
            con,
            provider=provider,
            api_name=api_name,
            period_type="day",
            period_key=_day_key(current),
        ),
        monthly_used=_get_count(
            con,
            provider=provider,
            api_name=api_name,
            period_type="month",
            period_key=_month_key(current),
        ),
    )


def get_naver_search_usage(
    con: duckdb.DuckDBPyConnection,
    *,
    daily_limit: int,
    monthly_limit: int,
    now: datetime | None = None,
) -> ApiQuotaUsage:
    local = get_local_api_usage(
        con,
        provider=NAVER_SEARCH_PROVIDER,
        api_name=NAVER_SEARCH_API,
        now=now,
    )
    return ApiQuotaUsage(
        daily_used=local.daily_used,
        monthly_used=local.monthly_used,
        daily_limit=clamp_naver_search_daily_limit(daily_limit),
        monthly_limit=clamp_naver_search_monthly_limit(monthly_limit),
    )


def get_kakao_daum_usage(
    con: duckdb.DuckDBPyConnection,
    *,
    daily_limit: int,
    monthly_limit: int,
    now: datetime | None = None,
) -> ApiQuotaUsage:
    local = get_local_api_usage(
        con,
        provider=KAKAO_DAUM_PROVIDER,
        api_name=KAKAO_DAUM_API,
        now=now,
    )
    return ApiQuotaUsage(
        daily_used=local.daily_used,
        monthly_used=local.monthly_used,
        daily_limit=clamp_kakao_daum_daily_limit(daily_limit),
        monthly_limit=clamp_kakao_daum_monthly_limit(monthly_limit),
    )


def ensure_kakao_daum_capacity(
    con: duckdb.DuckDBPyConnection,
    *,
    planned_calls: int,
    daily_limit: int,
    monthly_limit: int,
    now: datetime | None = None,
) -> ApiQuotaUsage:
    planned = max(0, int(planned_calls))
    usage = get_kakao_daum_usage(
        con,
        daily_limit=daily_limit,
        monthly_limit=monthly_limit,
        now=now,
    )
    if usage.daily_used + planned > usage.daily_limit:
        raise ApiQuotaExceededError(
            "Daum 검색 API의 프로그램 일간 안전 한도에 도달했습니다. "
            f"현재 {usage.daily_used:,}회, 이번 실행 예정 {planned:,}회, "
            f"설정 한도 {usage.daily_limit:,}회입니다. 다른 출처만 반영합니다."
        )
    if usage.monthly_used + planned > usage.monthly_limit:
        raise ApiQuotaExceededError(
            "카카오 앱 전체 API의 프로그램 월간 안전 한도에 도달했습니다. "
            f"현재 이 프로그램 기록 {usage.monthly_used:,}회, 이번 실행 예정 {planned:,}회, "
            f"설정 한도 {usage.monthly_limit:,}회입니다. 카카오 콘솔의 앱 전체 사용량도 확인하세요."
        )
    return usage


def ensure_naver_search_capacity(
    con: duckdb.DuckDBPyConnection,
    *,
    planned_calls: int,
    daily_limit: int,
    monthly_limit: int,
    now: datetime | None = None,
) -> ApiQuotaUsage:
    planned = max(0, int(planned_calls))
    usage = get_naver_search_usage(
        con,
        daily_limit=daily_limit,
        monthly_limit=monthly_limit,
        now=now,
    )
    if usage.daily_used + planned > usage.daily_limit:
        raise ApiQuotaExceededError(
            "네이버 검색 API의 프로그램 일간 안전 한도에 도달했습니다. "
            f"현재 {usage.daily_used:,}회, 이번 실행 예정 {planned:,}회, "
            f"설정 한도 {usage.daily_limit:,}회입니다. 다른 무료 데이터만 반영합니다."
        )
    if usage.monthly_used + planned > usage.monthly_limit:
        raise ApiQuotaExceededError(
            "네이버 검색 API의 프로그램 월간 안전 한도에 도달했습니다. "
            f"현재 {usage.monthly_used:,}회, 이번 실행 예정 {planned:,}회, "
            f"설정 한도 {usage.monthly_limit:,}회입니다. 다른 무료 데이터만 반영합니다."
        )
    return usage


def _increment_counter(
    con: duckdb.DuckDBPyConnection,
    *,
    provider: str,
    api_name: str,
    period_type: str,
    period_key: str,
    count: int,
    current: datetime,
) -> None:
    con.execute(
        """
        INSERT INTO api_usage_counters(
            provider, api_name, period_type, period_key, call_count, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, api_name, period_type, period_key) DO UPDATE SET
            call_count = api_usage_counters.call_count + EXCLUDED.call_count,
            updated_at = EXCLUDED.updated_at
        """,
        [provider, api_name, period_type, period_key, count, current],
    )


def record_local_api_calls(
    con: duckdb.DuckDBPyConnection,
    *,
    provider: str,
    api_name: str,
    count: int = 1,
    now: datetime | None = None,
) -> None:
    """실제 요청 시도 횟수를 일·월 카운터에 함께 기록합니다."""
    increment = max(0, int(count))
    if increment == 0:
        return
    current = now or datetime.now()
    _increment_counter(
        con,
        provider=provider,
        api_name=api_name,
        period_type="day",
        period_key=_day_key(current),
        count=increment,
        current=current,
    )
    _increment_counter(
        con,
        provider=provider,
        api_name=api_name,
        period_type="month",
        period_key=_month_key(current),
        count=increment,
        current=current,
    )


def record_naver_search_call(
    con: duckdb.DuckDBPyConnection,
    *,
    now: datetime | None = None,
) -> None:
    """실제 NAVER 요청 시도 직전에 해당 일·월 카운터를 1회 증가시킵니다."""
    record_local_api_calls(
        con,
        provider=NAVER_SEARCH_PROVIDER,
        api_name=NAVER_SEARCH_API,
        count=1,
        now=now,
    )

def record_kakao_daum_call(
    con: duckdb.DuckDBPyConnection,
    *,
    now: datetime | None = None,
) -> None:
    """실제 Daum 검색 요청 시도 직전에 해당 일·월 카운터를 1회 증가시킵니다."""
    record_local_api_calls(
        con,
        provider=KAKAO_DAUM_PROVIDER,
        api_name=KAKAO_DAUM_API,
        count=1,
        now=now,
    )
