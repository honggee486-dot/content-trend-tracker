from __future__ import annotations

from datetime import datetime, timedelta

from src.services.scheduler_quota_analysis_service import (
    analyze_actual_quota_usage,
    build_actual_quota_assessment,
)


NOW = datetime(2026, 7, 16, 20, 0, 0)


def _rows(count: int, *, calls: int = 200, retries: int = 0):
    rows = []
    for index in range(count):
        started_at = NOW - timedelta(hours=index * 2)
        for source_name in ("naver", "daum"):
            rows.append(
                (
                    f"run_{index}",
                    "background_refresh",
                    "success",
                    started_at,
                    source_name,
                    "success",
                    calls,
                    retries,
                )
            )
    return rows


def test_no_history_uses_theoretical_fallback_message() -> None:
    result = build_actual_quota_assessment(
        [],
        interval_minutes=30,
        naver_daily_limit=25000,
        kakao_daily_limit=50000,
        now=NOW,
    )

    assert result.sample_level == "insufficient"
    assert result.sample_count == 0
    assert result.status_label == "실제 기록 표본 부족"
    assert result.recommended_min_interval_minutes is None


def test_high_recent_actual_usage_warns_even_without_forecast_sample() -> None:
    rows = [
        (
            "failed_run",
            "background_refresh",
            "failure",
            NOW - timedelta(hours=1),
            "naver",
            "failure",
            23000,
            2,
        )
    ]
    result = build_actual_quota_assessment(
        rows,
        interval_minutes=30,
        naver_daily_limit=25000,
        kakao_daily_limit=50000,
        now=NOW,
    )

    assert result.sample_count == 0
    assert result.status_level == "danger"
    assert result.status_label == "최근 24시간 한도 사용 주의"


def test_less_than_six_runs_remains_insufficient() -> None:
    result = build_actual_quota_assessment(
        _rows(5),
        interval_minutes=30,
        naver_daily_limit=25000,
        kakao_daily_limit=50000,
        now=NOW,
    )

    assert result.sample_level == "insufficient"
    assert result.sample_count == 5


def test_six_runs_are_reference_and_thirty_minutes_is_safe() -> None:
    result = build_actual_quota_assessment(
        _rows(6),
        interval_minutes=30,
        naver_daily_limit=25000,
        kakao_daily_limit=50000,
        now=NOW,
    )

    assert result.sample_level == "reference"
    assert result.status_level == "safe"
    assert result.status_label == "30분 유지 가능"
    naver = result.portals[0]
    assert naver.estimated_calls_per_day == 9600
    assert naver.estimated_usage_percent == 38.4
    assert naver.conservative_min_interval_minutes == 12


def test_twenty_four_runs_are_stable_sample() -> None:
    result = build_actual_quota_assessment(
        _rows(24, calls=180),
        interval_minutes=30,
        naver_daily_limit=25000,
        kakao_daily_limit=50000,
        now=NOW,
    )

    assert result.sample_level == "stable"
    assert result.sample_count == 24
    assert result.portals[0].average_calls_per_run == 180


def test_request_count_already_includes_retries_and_is_not_double_counted() -> None:
    result = build_actual_quota_assessment(
        _rows(6, calls=210, retries=10),
        interval_minutes=60,
        naver_daily_limit=25000,
        kakao_daily_limit=50000,
        now=NOW,
    )

    naver = result.portals[0]
    assert naver.average_calls_per_run == 210
    assert naver.estimated_calls_per_day == 5040
    assert round(naver.retry_rate_percent, 2) == 4.76


def test_high_observed_calls_recommend_longer_interval() -> None:
    result = build_actual_quota_assessment(
        _rows(6, calls=600),
        interval_minutes=30,
        naver_daily_limit=25000,
        kakao_daily_limit=50000,
        now=NOW,
    )

    assert result.status_level == "danger"
    assert result.recommended_min_interval_minutes == 40
    assert result.status_label == "40분 이상으로 변경 권장"




def test_failed_requests_count_in_recent_actual_but_not_forecast_sample() -> None:
    rows = _rows(6, calls=200)
    rows.append(
        (
            "failed_run",
            "background_refresh",
            "failure",
            NOW - timedelta(hours=1),
            "naver",
            "failure",
            50,
            2,
        )
    )
    result = build_actual_quota_assessment(
        rows,
        interval_minutes=30,
        naver_daily_limit=25000,
        kakao_daily_limit=50000,
        now=NOW,
    )

    naver = result.portals[0]
    assert naver.sample_count == 6
    assert naver.average_calls_per_run == 200
    assert naver.calls_24h == 1250

class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = []

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return _Cursor(self.rows)


def test_history_query_excludes_ranking_failure_and_zero_request_rows_in_sql() -> None:
    con = _FakeConnection(_rows(6))
    result = analyze_actual_quota_usage(
        con,
        interval_minutes=30,
        naver_daily_limit=25000,
        kakao_daily_limit=50000,
        now=NOW,
    )

    assert result.sample_count == 6
    assert "ranking_rebuild" not in con.sql
    assert "cr.status IN ('success', 'partial_success', 'failure')" in con.sql
    assert "crs.request_count > 0" in con.sql
    assert con.params == [NOW - timedelta(days=7)]
