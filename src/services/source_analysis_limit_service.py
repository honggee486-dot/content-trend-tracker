"""최근 원문 중 출처별 분석 입력 상한 초과 추정치를 읽기 전용으로 계산합니다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import duckdb

from src.database import get_setting


@dataclass(frozen=True)
class SourceAnalysisLimitRow:
    source_group: str
    source_label: str
    source_types: tuple[str, ...]
    collected_item_count: int
    configured_limit: int
    estimated_excluded_count: int


@dataclass(frozen=True)
class SourceAnalysisLimitReport:
    requested_lookback_hours: int
    effective_lookback_hours: int
    generated_at: datetime
    rows: tuple[SourceAnalysisLimitRow, ...]
    estimated_excluded_count: int


_LIMIT_SPECS = (
    (
        "youtube",
        "YouTube",
        ("youtube",),
        "trend_analysis_youtube_limit",
        2000,
    ),
    (
        "naver",
        "NAVER 뉴스·블로그",
        ("naver_news", "naver_blog"),
        "trend_analysis_naver_limit",
        4000,
    ),
    (
        "daum",
        "Daum 웹문서·카페",
        ("daum_web", "daum_cafe"),
        "trend_analysis_daum_limit",
        4000,
    ),
    (
        "google_trends",
        "Google Trends",
        ("google_trends",),
        "trend_analysis_google_limit",
        500,
    ),
    (
        "wikipedia",
        "위키백과",
        ("wikipedia_pageviews",),
        "trend_analysis_wikipedia_limit",
        500,
    ),
)


def _setting_limit(
    con: duckdb.DuckDBPyConnection,
    key: str,
    default: int,
) -> int:
    try:
        value = int(get_setting(con, key, str(default)) or default)
    except (TypeError, ValueError, duckdb.Error):
        value = int(default)
    return max(1, value)


def analyze_source_analysis_limits(
    con: duckdb.DuckDBPyConnection,
    *,
    lookback_hours: int = 72,
    now: datetime | None = None,
) -> SourceAnalysisLimitReport:
    requested_lookback = max(1, min(int(lookback_hours), 24 * 30))
    ranking_lookback = _setting_limit(con, "trend_lookback_hours", 72)
    effective_lookback = min(requested_lookback, ranking_lookback)
    generated_at = now or datetime.now()
    since = generated_at - timedelta(hours=effective_lookback)
    counts = {
        str(source_type): int(item_count or 0)
        for source_type, item_count in con.execute(
            """
            SELECT source_type, COUNT(DISTINCT source_item_id)
            FROM source_items
            WHERE COALESCE(
                published_at,
                observed_at,
                last_imported_at,
                imported_at
            ) >= ?
            GROUP BY source_type
            """,
            [since],
        ).fetchall()
    }

    rows: list[SourceAnalysisLimitRow] = []
    for group, label, source_types, setting_key, default in _LIMIT_SPECS:
        collected = sum(counts.get(source_type, 0) for source_type in source_types)
        configured_limit = _setting_limit(con, setting_key, default)
        rows.append(
            SourceAnalysisLimitRow(
                source_group=group,
                source_label=label,
                source_types=source_types,
                collected_item_count=collected,
                configured_limit=configured_limit,
                estimated_excluded_count=max(0, collected - configured_limit),
            )
        )

    return SourceAnalysisLimitReport(
        requested_lookback_hours=requested_lookback,
        effective_lookback_hours=effective_lookback,
        generated_at=generated_at,
        rows=tuple(rows),
        estimated_excluded_count=sum(
            row.estimated_excluded_count for row in rows
        ),
    )
