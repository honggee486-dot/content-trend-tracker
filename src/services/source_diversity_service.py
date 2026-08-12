"""최근 수집 원문과 현재 군집의 출처 다양성을 읽기 전용으로 진단합니다."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import combinations
from typing import Any

import duckdb


SOURCE_LABELS = {
    "youtube": "YouTube",
    "naver_news": "NAVER 뉴스",
    "naver_blog": "NAVER 블로그",
    "daum_web": "Daum 웹문서",
    "daum_cafe": "Daum 카페",
    "google_trends": "Google Trends",
    "wikipedia_pageviews": "위키백과 조회수",
}

LOOKBACK_OPTIONS = {
    24: "최근 24시간",
    72: "최근 72시간",
    168: "최근 7일",
}

MIN_CLUSTER_SAMPLE = 20
CRITICAL_MULTI_SOURCE_RATIO = 0.05
LOW_MULTI_SOURCE_RATIO = 0.15
HEALTHY_MULTI_SOURCE_RATIO = 0.30


@dataclass(frozen=True)
class SourceDiversitySourceRow:
    source_type: str
    source_label: str
    collected_item_count: int
    clustered_item_count: int
    cluster_count: int
    multi_source_cluster_count: int
    cluster_coverage: float
    cross_source_rate: float
    cluster_share: float


@dataclass(frozen=True)
class SourceDiversityPairRow:
    left_source_type: str
    right_source_type: str
    pair_label: str
    cluster_count: int
    multi_source_share: float


@dataclass(frozen=True)
class SourceDiversityIssue:
    severity: str
    code: str
    message: str
    recommendation: str


@dataclass(frozen=True)
class SourceDiversityReport:
    lookback_hours: int
    generated_at: datetime
    collected_item_count: int
    clustered_item_count: int
    unclustered_item_count: int
    cluster_coverage: float
    cluster_count: int
    single_source_cluster_count: int
    multi_source_cluster_count: int
    three_plus_source_cluster_count: int
    multi_source_ratio: float
    status: str
    status_label: str
    source_rows: tuple[SourceDiversitySourceRow, ...]
    pair_rows: tuple[SourceDiversityPairRow, ...]
    issues: tuple[SourceDiversityIssue, ...]

    @property
    def lookback_label(self) -> str:
        return LOOKBACK_OPTIONS.get(self.lookback_hours, f"최근 {self.lookback_hours}시간")



def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0



def _status_for(cluster_count: int, multi_source_ratio: float) -> tuple[str, str]:
    if cluster_count <= 0:
        return "empty", "데이터 없음"
    if cluster_count < MIN_CLUSTER_SAMPLE:
        return "sample_low", "표본 부족"
    if multi_source_ratio < CRITICAL_MULTI_SOURCE_RATIO:
        return "critical", "매우 낮음"
    if multi_source_ratio < LOW_MULTI_SOURCE_RATIO:
        return "needs_improvement", "개선 필요"
    if multi_source_ratio < HEALTHY_MULTI_SOURCE_RATIO:
        return "watch", "관찰"
    return "healthy", "양호"



def _source_label(source_type: str) -> str:
    return SOURCE_LABELS.get(source_type, source_type or "기타")



def _source_item_counts(
    con: duckdb.DuckDBPyConnection,
    *,
    since: datetime,
) -> dict[str, int]:
    rows = con.execute(
        """
        SELECT source_type, COUNT(DISTINCT source_item_id) AS item_count
        FROM source_items
        WHERE COALESCE(published_at, observed_at, last_imported_at, imported_at) >= ?
        GROUP BY source_type
        """,
        [since],
    ).fetchall()
    return {str(source_type): int(item_count or 0) for source_type, item_count in rows}



def _clustered_source_rows(
    con: duckdb.DuckDBPyConnection,
    *,
    since: datetime,
) -> list[tuple[str, str, str]]:
    rows = con.execute(
        """
        SELECT ci.cluster_id, si.source_item_id, si.source_type
        FROM trend_cluster_items ci
        JOIN trend_clusters c ON c.cluster_id = ci.cluster_id
        JOIN source_items si ON si.source_item_id = ci.source_item_id
        WHERE COALESCE(si.published_at, si.observed_at, si.last_imported_at, si.imported_at) >= ?
        ORDER BY ci.cluster_id, si.source_type, si.source_item_id
        """,
        [since],
    ).fetchall()
    return [
        (str(cluster_id), str(source_item_id), str(source_type))
        for cluster_id, source_item_id, source_type in rows
    ]



def _build_issues(
    *,
    cluster_count: int,
    collected_item_count: int,
    cluster_coverage: float,
    multi_source_ratio: float,
    source_rows: list[SourceDiversitySourceRow],
) -> list[SourceDiversityIssue]:
    issues: list[SourceDiversityIssue] = []

    if cluster_count <= 0:
        issues.append(
            SourceDiversityIssue(
                severity="warning",
                code="no_clusters",
                message="선택한 기간에 현재 순위 군집으로 연결된 원문이 없습니다.",
                recommendation=(
                    "최근 수집 성공 여부와 순위 다시 계산 시각을 확인하세요. "
                    "이 진단은 수집이나 재계산을 자동 실행하지 않습니다."
                ),
            )
        )
        return issues

    if cluster_count < MIN_CLUSTER_SAMPLE:
        issues.append(
            SourceDiversityIssue(
                severity="info",
                code="low_sample",
                message=(
                    f"군집이 {cluster_count:,}개로 운영 판단 기준인 "
                    f"{MIN_CLUSTER_SAMPLE:,}개보다 적습니다."
                ),
                recommendation="기간을 72시간 또는 7일로 넓혀 다시 확인하세요.",
            )
        )
    elif multi_source_ratio < CRITICAL_MULTI_SOURCE_RATIO:
        issues.append(
            SourceDiversityIssue(
                severity="error",
                code="critical_multi_source_ratio",
                message=(
                    "두 종류 이상 출처가 함께 묶인 군집 비율이 "
                    f"{multi_source_ratio * 100:.1f}%로 매우 낮습니다."
                ),
                recommendation=(
                    "출처별 제목 정규화, 핵심어 추출, 군집 유사도 기준과 분석 대상 제한을 "
                    "순서대로 점검하세요. 자동으로 기준을 완화하지는 않습니다."
                ),
            )
        )
    elif multi_source_ratio < LOW_MULTI_SOURCE_RATIO:
        issues.append(
            SourceDiversityIssue(
                severity="warning",
                code="low_multi_source_ratio",
                message=(
                    "두 종류 이상 출처가 함께 묶인 군집 비율이 "
                    f"{multi_source_ratio * 100:.1f}%로 낮습니다."
                ),
                recommendation=(
                    "아래 출처별 교차 연결률과 출처 조합을 확인해 가장 약한 연결부터 "
                    "정규화·군집 기준을 점검하세요."
                ),
            )
        )
    elif multi_source_ratio < HEALTHY_MULTI_SOURCE_RATIO:
        issues.append(
            SourceDiversityIssue(
                severity="info",
                code="watch_multi_source_ratio",
                message=(
                    "다중 출처 군집 비율이 "
                    f"{multi_source_ratio * 100:.1f}%입니다."
                ),
                recommendation=(
                    "즉시 기준을 바꾸기보다 7일 추세와 출처별 교차 연결률을 함께 관찰하세요."
                ),
            )
        )

    if collected_item_count >= 50 and cluster_coverage < 0.20:
        issues.append(
            SourceDiversityIssue(
                severity="warning",
                code="low_cluster_coverage",
                message=(
                    "최근 수집 원문 중 현재 순위 군집에 연결된 비율이 "
                    f"{cluster_coverage * 100:.1f}%입니다."
                ),
                recommendation=(
                    "분석 출처별 입력 상한, 오래된 게시물 제외, 일반 제목·탐색 페이지 제외와 "
                    "군집 전 필터를 확인하세요. 낮은 연결률이 곧 수집 실패를 뜻하지는 않습니다."
                ),
            )
        )

    populated = [row for row in source_rows if row.cluster_count > 0]
    if cluster_count >= MIN_CLUSTER_SAMPLE and populated:
        dominant = max(populated, key=lambda row: row.cluster_share)
        if dominant.cluster_share >= 0.70:
            issues.append(
                SourceDiversityIssue(
                    severity="warning",
                    code="dominant_source",
                    message=(
                        f"{dominant.source_label}가 전체 군집의 "
                        f"{dominant.cluster_share * 100:.1f}%에 포함돼 편중되어 있습니다."
                    ),
                    recommendation=(
                        "해당 출처를 줄이기 전에 다른 출처의 최근 수집 성공·분석 입력 상한과 "
                        "제목 품질을 먼저 확인하세요."
                    ),
                )
            )

    weak_cross_sources = [
        row
        for row in source_rows
        if row.collected_item_count >= 20
        and row.cluster_count >= 10
        and row.cross_source_rate < 0.05
    ]
    weak_cross_sources.sort(key=lambda row: (row.cross_source_rate, -row.cluster_count))
    for row in weak_cross_sources[:3]:
        recommendation = (
            "검색어형 제목과 기사·영상 제목의 핵심어 연결, 날짜·수식어 제거와 군집 유사도 "
            "기준을 점검하세요."
            if row.source_type == "google_trends"
            else "해당 출처의 제목 정규화와 다른 출처와 공유되는 핵심어 추출을 점검하세요."
        )
        issues.append(
            SourceDiversityIssue(
                severity="warning",
                code=f"weak_cross_source:{row.source_type}",
                message=(
                    f"{row.source_label} 군집의 교차 출처 연결률이 "
                    f"{row.cross_source_rate * 100:.1f}%입니다."
                ),
                recommendation=recommendation,
            )
        )

    missing_labels = [
        row.source_label for row in source_rows if row.collected_item_count <= 0
    ]
    if missing_labels:
        issues.append(
            SourceDiversityIssue(
                severity="info",
                code="sources_without_recent_items",
                message="최근 데이터가 없는 출처: " + ", ".join(missing_labels),
                recommendation=(
                    "사용하지 않도록 설정한 출처라면 정상입니다. 사용하는 출처라면 최근 수집 이력과 "
                    "API·교환 파일 상태를 확인하세요."
                ),
            )
        )

    if not issues:
        issues.append(
            SourceDiversityIssue(
                severity="success",
                code="healthy",
                message="현재 운영 진단 기준에서 뚜렷한 출처 다양성 경고가 없습니다.",
                recommendation="자동 설정 변경 없이 7일 추세와 실제 글감 품질을 계속 확인하세요.",
            )
        )
    return issues



def analyze_source_diversity(
    con: duckdb.DuckDBPyConnection,
    *,
    lookback_hours: int = 72,
    now: datetime | None = None,
) -> SourceDiversityReport:
    """Return a read-only diversity report for recent items and current clusters."""
    lookback = max(1, min(int(lookback_hours), 24 * 30))
    generated_at = now or datetime.now()
    since = generated_at - timedelta(hours=lookback)

    collected_by_source = _source_item_counts(con, since=since)
    linked_rows = _clustered_source_rows(con, since=since)

    cluster_sources: dict[str, set[str]] = defaultdict(set)
    cluster_items: dict[str, set[str]] = defaultdict(set)
    clustered_items_by_source: dict[str, set[str]] = defaultdict(set)
    all_clustered_items: set[str] = set()

    for cluster_id, source_item_id, source_type in linked_rows:
        cluster_sources[cluster_id].add(source_type)
        cluster_items[cluster_id].add(source_item_id)
        clustered_items_by_source[source_type].add(source_item_id)
        all_clustered_items.add(source_item_id)

    cluster_count = len(cluster_sources)
    single_source_cluster_count = sum(
        1 for source_types in cluster_sources.values() if len(source_types) == 1
    )
    multi_source_cluster_count = sum(
        1 for source_types in cluster_sources.values() if len(source_types) >= 2
    )
    three_plus_source_cluster_count = sum(
        1 for source_types in cluster_sources.values() if len(source_types) >= 3
    )

    cluster_count_by_source: Counter[str] = Counter()
    multi_cluster_count_by_source: Counter[str] = Counter()
    pair_counts: Counter[tuple[str, str]] = Counter()

    for source_types in cluster_sources.values():
        ordered_types = sorted(source_types)
        for source_type in ordered_types:
            cluster_count_by_source[source_type] += 1
            if len(ordered_types) >= 2:
                multi_cluster_count_by_source[source_type] += 1
        for left_source, right_source in combinations(ordered_types, 2):
            pair_counts[(left_source, right_source)] += 1

    known_source_types = list(SOURCE_LABELS)
    extra_source_types = sorted(
        (set(collected_by_source) | set(cluster_count_by_source)) - set(known_source_types)
    )
    source_rows: list[SourceDiversitySourceRow] = []
    for source_type in [*known_source_types, *extra_source_types]:
        collected = int(collected_by_source.get(source_type, 0))
        clustered = len(clustered_items_by_source.get(source_type, set()))
        source_cluster_count = int(cluster_count_by_source.get(source_type, 0))
        multi_cluster_count = int(multi_cluster_count_by_source.get(source_type, 0))
        source_rows.append(
            SourceDiversitySourceRow(
                source_type=source_type,
                source_label=_source_label(source_type),
                collected_item_count=collected,
                clustered_item_count=clustered,
                cluster_count=source_cluster_count,
                multi_source_cluster_count=multi_cluster_count,
                cluster_coverage=_safe_ratio(clustered, collected),
                cross_source_rate=_safe_ratio(multi_cluster_count, source_cluster_count),
                cluster_share=_safe_ratio(source_cluster_count, cluster_count),
            )
        )

    source_rows.sort(
        key=lambda row: (-row.cluster_count, -row.collected_item_count, row.source_label)
    )

    pair_rows = [
        SourceDiversityPairRow(
            left_source_type=left_source,
            right_source_type=right_source,
            pair_label=f"{_source_label(left_source)} + {_source_label(right_source)}",
            cluster_count=int(pair_count),
            multi_source_share=_safe_ratio(pair_count, multi_source_cluster_count),
        )
        for (left_source, right_source), pair_count in pair_counts.most_common()
    ]

    collected_item_count = sum(collected_by_source.values())
    clustered_item_count = len(all_clustered_items)
    unclustered_item_count = max(0, collected_item_count - clustered_item_count)
    cluster_coverage = _safe_ratio(clustered_item_count, collected_item_count)
    multi_source_ratio = _safe_ratio(multi_source_cluster_count, cluster_count)
    status, status_label = _status_for(cluster_count, multi_source_ratio)
    issues = _build_issues(
        cluster_count=cluster_count,
        collected_item_count=collected_item_count,
        cluster_coverage=cluster_coverage,
        multi_source_ratio=multi_source_ratio,
        source_rows=source_rows,
    )

    return SourceDiversityReport(
        lookback_hours=lookback,
        generated_at=generated_at,
        collected_item_count=collected_item_count,
        clustered_item_count=clustered_item_count,
        unclustered_item_count=unclustered_item_count,
        cluster_coverage=cluster_coverage,
        cluster_count=cluster_count,
        single_source_cluster_count=single_source_cluster_count,
        multi_source_cluster_count=multi_source_cluster_count,
        three_plus_source_cluster_count=three_plus_source_cluster_count,
        multi_source_ratio=multi_source_ratio,
        status=status,
        status_label=status_label,
        source_rows=tuple(source_rows),
        pair_rows=tuple(pair_rows),
        issues=tuple(issues),
    )
