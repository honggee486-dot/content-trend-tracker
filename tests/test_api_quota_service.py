from datetime import datetime
from pathlib import Path

import pytest

from src.database import connect_database, init_database
from src.services.api_quota_service import (
    ApiQuotaExceededError,
    NAVER_SEARCH_OFFICIAL_DAILY_LIMIT,
    NAVER_SEARCH_OFFICIAL_MONTHLY_LIMIT,
    clamp_naver_search_daily_limit,
    clamp_naver_search_monthly_limit,
    ensure_naver_search_capacity,
    get_local_api_usage,
    get_naver_search_usage,
    record_local_api_calls,
    record_naver_search_call,
)


def test_naver_search_usage_counts_day_and_month(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    with connect_database(db_path) as con:
        usage = get_naver_search_usage(
            con,
            daily_limit=25_000,
            monthly_limit=775_000,
            now=now,
        )
        assert usage.daily_used == 0
        assert usage.monthly_used == 0

        record_naver_search_call(con, now=now)
        record_naver_search_call(con, now=now)
        usage = get_naver_search_usage(
            con,
            daily_limit=25_000,
            monthly_limit=775_000,
            now=now,
        )
        assert usage.daily_used == 2
        assert usage.monthly_used == 2
        assert usage.daily_remaining == 24_998
        assert usage.monthly_remaining == 774_998

        rows = con.execute(
            """
            SELECT period_type, period_key, call_count
            FROM api_usage_counters
            WHERE provider = 'naver' AND api_name = 'search_api'
            ORDER BY period_type
            """
        ).fetchall()
        assert rows == [
            ("day", "2026-07-15", 2),
            ("month", "2026-07", 2),
        ]


def test_quota_blocks_before_crossing_daily_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    with connect_database(db_path) as con:
        for _ in range(4):
            record_naver_search_call(con, now=now)

        ensure_naver_search_capacity(
            con,
            planned_calls=1,
            daily_limit=5,
            monthly_limit=100,
            now=now,
        )
        with pytest.raises(ApiQuotaExceededError) as exc_info:
            ensure_naver_search_capacity(
                con,
                planned_calls=2,
                daily_limit=5,
                monthly_limit=100,
                now=now,
            )
        assert "일간 안전 한도" in str(exc_info.value)


def test_quota_blocks_before_crossing_monthly_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    with connect_database(db_path) as con:
        for _ in range(4):
            record_naver_search_call(con, now=now)

        with pytest.raises(ApiQuotaExceededError) as exc_info:
            ensure_naver_search_capacity(
                con,
                planned_calls=2,
                daily_limit=100,
                monthly_limit=5,
                now=now,
            )
        assert "월간 안전 한도" in str(exc_info.value)


def test_limits_never_exceed_official_boundaries() -> None:
    assert clamp_naver_search_daily_limit(999_999) == NAVER_SEARCH_OFFICIAL_DAILY_LIMIT
    assert clamp_naver_search_daily_limit(1_000) == 1_000
    assert (
        clamp_naver_search_monthly_limit(9_999_999)
        == NAVER_SEARCH_OFFICIAL_MONTHLY_LIMIT
    )
    assert clamp_naver_search_monthly_limit(20_000) == 20_000


def test_generic_local_usage_supports_future_sources(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    with connect_database(db_path) as con:
        record_local_api_calls(
            con,
            provider="google",
            api_name="trends_rss",
            count=3,
            now=now,
        )
        usage = get_local_api_usage(
            con,
            provider="google",
            api_name="trends_rss",
            now=now,
        )
        assert usage.daily_used == 3
        assert usage.monthly_used == 3


def test_kakao_daum_usage_counts_and_limits(tmp_path: Path) -> None:
    from src.services.api_quota_service import (
        KAKAO_ALL_API_OFFICIAL_MONTHLY_LIMIT,
        KAKAO_DAUM_OFFICIAL_DAILY_LIMIT,
        clamp_kakao_daum_daily_limit,
        clamp_kakao_daum_monthly_limit,
        ensure_kakao_daum_capacity,
        get_kakao_daum_usage,
        record_kakao_daum_call,
    )

    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    with connect_database(db_path) as con:
        record_kakao_daum_call(con, now=now)
        usage = get_kakao_daum_usage(
            con,
            daily_limit=50_000,
            monthly_limit=3_000_000,
            now=now,
        )
        assert usage.daily_used == 1
        assert usage.monthly_used == 1
        ensure_kakao_daum_capacity(
            con,
            planned_calls=1,
            daily_limit=2,
            monthly_limit=10,
            now=now,
        )
        with pytest.raises(ApiQuotaExceededError):
            ensure_kakao_daum_capacity(
                con,
                planned_calls=2,
                daily_limit=2,
                monthly_limit=10,
                now=now,
            )

    assert clamp_kakao_daum_daily_limit(999_999) == KAKAO_DAUM_OFFICIAL_DAILY_LIMIT
    assert clamp_kakao_daum_monthly_limit(9_999_999) == KAKAO_ALL_API_OFFICIAL_MONTHLY_LIMIT
