"""실행 이력의 실제 포털 요청량으로 자동 수집 간격의 여유를 계산합니다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from typing import Any, Iterable


PORTAL_LABELS = {
    "naver": "NAVER",
    "daum": "Daum",
}

_SAMPLE_INSUFFICIENT = 6
_SAMPLE_STABLE = 24


@dataclass(frozen=True)
class PortalActualUsage:
    source_name: str
    sample_count: int
    run_count_24h: int
    calls_24h: int
    retries_24h: int
    calls_lookback: int
    retries_lookback: int
    average_calls_per_run: float
    max_calls_per_run: int
    estimated_calls_per_day: int
    daily_limit: int
    estimated_usage_percent: float
    observed_usage_percent_24h: float
    retry_rate_percent: float
    average_min_interval_minutes: int
    conservative_min_interval_minutes: int


@dataclass(frozen=True)
class ActualQuotaAssessment:
    lookback_days: int
    interval_minutes: int
    runs_per_day: int
    sample_count: int
    sample_level: str
    sample_label: str
    status_level: str
    status_label: str
    message: str
    recommended_min_interval_minutes: int | None
    portals: tuple[PortalActualUsage, ...]


def analyze_actual_quota_usage(
    con,
    *,
    interval_minutes: int,
    naver_daily_limit: int,
    kakao_daily_limit: int,
    now: datetime | None = None,
    lookback_days: int = 7,
) -> ActualQuotaAssessment:
    current = now or datetime.now()
    days = max(1, int(lookback_days))
    cutoff = current - timedelta(days=days)
    rows = con.execute(
        """
        SELECT cr.run_id, cr.run_type, cr.status, cr.started_at, crs.source_name,
               crs.status, crs.request_count, crs.retry_count
        FROM collection_runs AS cr
        JOIN collection_run_sources AS crs ON crs.run_id = cr.run_id
        WHERE cr.started_at >= ?
          AND cr.run_type IN ('background_refresh', 'manual_refresh')
          AND cr.status IN ('success', 'partial_success', 'failure')
          AND crs.source_name IN ('naver', 'daum')
          AND crs.status IN ('success', 'partial_success', 'failure')
          AND crs.request_count > 0
        ORDER BY cr.started_at ASC, cr.run_id ASC, crs.source_name ASC
        """,
        [cutoff],
    ).fetchall()
    return build_actual_quota_assessment(
        rows,
        interval_minutes=interval_minutes,
        naver_daily_limit=naver_daily_limit,
        kakao_daily_limit=kakao_daily_limit,
        now=current,
        lookback_days=days,
    )


def build_actual_quota_assessment(
    rows: Iterable[tuple[Any, ...] | dict[str, Any]],
    *,
    interval_minutes: int,
    naver_daily_limit: int,
    kakao_daily_limit: int,
    now: datetime | None = None,
    lookback_days: int = 7,
) -> ActualQuotaAssessment:
    current = now or datetime.now()
    interval = max(1, int(interval_minutes))
    days = max(1, int(lookback_days))
    normalized_rows = [_normalize_row(row) for row in rows]
    runs_per_day = ceil(1440 / interval)
    daily_limits = {
        "naver": max(0, int(naver_daily_limit)),
        "daum": max(0, int(kakao_daily_limit)),
    }

    portals = tuple(
        _build_portal_usage(
            source_name=source_name,
            rows=[row for row in normalized_rows if row["source_name"] == source_name],
            current=current,
            runs_per_day=runs_per_day,
            daily_limit=daily_limits[source_name],
        )
        for source_name in ("naver", "daum")
    )
    sample_count = min((portal.sample_count for portal in portals), default=0)
    sample_level, sample_label = _sample_quality(sample_count)
    recommended = max(
        (portal.conservative_min_interval_minutes for portal in portals if portal.sample_count),
        default=0,
    )
    recommended = _round_up_to_five(recommended) if recommended else None

    max_estimated_percent = max((portal.estimated_usage_percent for portal in portals), default=0.0)
    max_observed_percent = max((portal.observed_usage_percent_24h for portal in portals), default=0.0)
    max_retry_percent = max((portal.retry_rate_percent for portal in portals), default=0.0)

    if max_observed_percent >= 90:
        status_level = "danger"
        status_label = "최근 24시간 한도 사용 주의"
        message = (
            "최근 24시간 실제 요청량이 일일 한도의 90% 이상입니다. "
            "자동 수집 간격을 늘리고 제공자 콘솔의 실제 사용량을 확인하세요."
        )
    elif sample_level == "insufficient":
        status_level = "insufficient"
        status_label = "실제 기록 표본 부족"
        message = (
            f"출처별 실제 수집 기록이 최소 {sample_count}회라 아직 운영 판단 표본이 부족합니다. "
            "현재는 위의 설정 기준 최대 호출량 안내를 우선 적용합니다."
        )
    elif recommended is not None and interval < recommended:
        status_level = "danger"
        status_label = f"{recommended}분 이상으로 변경 권장"
        message = (
            f"최근 실제 실행의 최대 관측 요청량을 기준으로 현재 {interval}분 주기는 "
            f"일일 한도 여유가 부족할 수 있습니다. 최소 {recommended}분 이상을 권장합니다."
        )
    elif max_estimated_percent >= 85:
        status_level = "danger"
        status_label = "일일 한도 여유 부족"
        message = (
            "최근 실제 요청량이 일일 한도에 근접했습니다. 수동 수집을 줄이거나 자동 수집 간격을 늘리는 편이 안전합니다."
        )
    elif max_estimated_percent >= 70 or max_observed_percent >= 75 or max_retry_percent >= 10:
        status_level = "warning"
        status_label = f"{interval}분 유지 가능 · 사용량 주의"
        message = (
            f"현재 {interval}분 주기는 평균 실제 사용량 기준으로 유지 가능하지만, "
            "호출량 또는 재시도가 증가하는지 최근 실행 이력을 함께 확인하세요."
        )
    else:
        status_level = "safe"
        status_label = f"{interval}분 유지 가능"
        message = (
            f"최근 실제 사용량 기준으로 현재 {interval}분 자동 수집을 유지해도 일일 한도에 충분한 여유가 있습니다."
        )

    return ActualQuotaAssessment(
        lookback_days=days,
        interval_minutes=interval,
        runs_per_day=runs_per_day,
        sample_count=sample_count,
        sample_level=sample_level,
        sample_label=sample_label,
        status_level=status_level,
        status_label=status_label,
        message=message,
        recommended_min_interval_minutes=recommended,
        portals=portals,
    )


def _build_portal_usage(
    *,
    source_name: str,
    rows: list[dict[str, Any]],
    current: datetime,
    runs_per_day: int,
    daily_limit: int,
) -> PortalActualUsage:
    recent_cutoff = current - timedelta(hours=24)
    sample_rows = [
        row
        for row in rows
        if row["run_status"] in {"success", "partial_success"}
        and row["source_status"] in {"success", "partial_success"}
    ]
    sample_count = len(sample_rows)
    calls = [max(0, int(row["request_count"])) for row in sample_rows]
    retries = [max(0, int(row["retry_count"])) for row in sample_rows]
    calls_lookback = sum(calls)
    retries_lookback = sum(retries)
    average_calls = calls_lookback / sample_count if sample_count else 0.0
    max_calls = max(calls, default=0)
    recent_rows = [row for row in rows if row["started_at"] >= recent_cutoff]
    calls_24h = sum(max(0, int(row["request_count"])) for row in recent_rows)
    retries_24h = sum(max(0, int(row["retry_count"])) for row in recent_rows)
    estimated_calls = int(ceil(average_calls * runs_per_day)) if sample_count else 0
    estimated_percent = _percent(estimated_calls, daily_limit)
    observed_percent = _percent(calls_24h, daily_limit)
    retry_percent = _percent(retries_lookback, calls_lookback)
    average_min_interval = _minimum_interval_for_calls(average_calls, daily_limit)
    conservative_min_interval = _minimum_interval_for_calls(max_calls, daily_limit)
    return PortalActualUsage(
        source_name=source_name,
        sample_count=sample_count,
        run_count_24h=len({str(row["run_id"]) for row in recent_rows}),
        calls_24h=calls_24h,
        retries_24h=retries_24h,
        calls_lookback=calls_lookback,
        retries_lookback=retries_lookback,
        average_calls_per_run=average_calls,
        max_calls_per_run=max_calls,
        estimated_calls_per_day=estimated_calls,
        daily_limit=daily_limit,
        estimated_usage_percent=estimated_percent,
        observed_usage_percent_24h=observed_percent,
        retry_rate_percent=retry_percent,
        average_min_interval_minutes=average_min_interval,
        conservative_min_interval_minutes=conservative_min_interval,
    )


def _normalize_row(row: tuple[Any, ...] | dict[str, Any]) -> dict[str, Any]:
    if isinstance(row, dict):
        values = row
    else:
        columns = (
            "run_id",
            "run_type",
            "run_status",
            "started_at",
            "source_name",
            "source_status",
            "request_count",
            "retry_count",
        )
        if len(row) == 6:
            columns = (
                "run_id",
                "run_type",
                "started_at",
                "source_name",
                "request_count",
                "retry_count",
            )
        values = dict(zip(columns, row))
    return {
        "run_id": str(values.get("run_id") or ""),
        "run_type": str(values.get("run_type") or ""),
        "run_status": str(values.get("run_status") or "success"),
        "started_at": values.get("started_at") or datetime.min,
        "source_name": str(values.get("source_name") or "").strip().lower(),
        "source_status": str(values.get("source_status") or "success"),
        "request_count": max(0, int(values.get("request_count") or 0)),
        "retry_count": max(0, int(values.get("retry_count") or 0)),
    }


def _sample_quality(sample_count: int) -> tuple[str, str]:
    if sample_count < _SAMPLE_INSUFFICIENT:
        return "insufficient", f"표본 부족 · {sample_count}회"
    if sample_count < _SAMPLE_STABLE:
        return "reference", f"참고 가능 · {sample_count}회"
    return "stable", f"안정적 표본 · {sample_count}회"


def _minimum_interval_for_calls(calls_per_run: float, daily_limit: int) -> int:
    if calls_per_run <= 0 or daily_limit <= 0:
        return 0
    max_runs = int(daily_limit // calls_per_run)
    if max_runs <= 0:
        return 1439
    return max(1, min(1439, ceil(1440 / max_runs)))


def _round_up_to_five(value: int) -> int:
    number = max(1, int(value))
    return min(1439, int(ceil(number / 5) * 5))


def _percent(value: int | float, total: int | float) -> float:
    if total <= 0:
        return 0.0
    return max(0.0, float(value) / float(total) * 100.0)
