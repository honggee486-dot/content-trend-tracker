"""실제 저장된 트렌드 출처가 현재 글감 목록까지 도달하는지 읽기 전용으로 진단합니다."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import duckdb


_SOURCE_GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "youtube": ("YouTube", ("youtube",)),
    "naver": ("NAVER", ("naver_news", "naver_blog")),
    "daum": ("Daum", ("daum_web", "daum_cafe")),
    "google_trends": ("Google Trends", ("google_trends",)),
    "wikipedia": ("위키백과", ("wikipedia_pageviews",)),
}
_REQUIRED_TABLES = ("source_items", "trend_clusters", "trend_cluster_items")
_DIAGNOSIS_LABELS = {
    "visible": "기본 목록 노출 후보 있음",
    "hidden_by_score": "추천·검토 후보가 점수 기준 아래에 있음",
    "held_by_policy": "현재 군집이 모두 보류 상태",
    "unclustered_or_stale": "최근 원문이 현재 군집에 연결되지 않음",
    "no_current_clusters": "현재 군집 없음",
    "no_recent_items": "최근 분석 범위 원문 없음",
    "no_visible_candidate": "기본 목록 노출 후보 없음",
}


def _table_names(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}


def _bounded_score(value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = 30.0
    return max(0.0, min(parsed, 100.0))


def _diagnosis(
    *,
    recent_items: int,
    recent_unclustered_items: int,
    cluster_count: int,
    recommended_count: int,
    review_count: int,
    hold_count: int,
    default_visible_count: int,
    eligible_below_score_count: int,
) -> str:
    if recent_items <= 0:
        return "no_recent_items"
    if cluster_count <= 0:
        return "unclustered_or_stale" if recent_unclustered_items else "no_current_clusters"
    if default_visible_count > 0:
        return "visible"
    if eligible_below_score_count > 0:
        return "hidden_by_score"
    if recommended_count + review_count <= 0 and hold_count > 0:
        return "held_by_policy"
    if recent_unclustered_items > 0:
        return "unclustered_or_stale"
    return "no_visible_candidate"


def _unavailable(
    *,
    lookback_hours: int,
    minimum_score: float,
    missing_tables: list[str] | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "lookback_hours": int(lookback_hours),
        "minimum_score": float(minimum_score),
        "total_clusters": 0,
        "default_visible_clusters": 0,
        "groups": {},
        "missing_tables": list(missing_tables or []),
        "error_type": type(error).__name__ if error is not None else "",
        "error_message": str(error)[:500] if error is not None else "",
        "overlap_note": (
            "한 군집에 여러 출처가 있으면 출처별 군집 수에는 중복으로 집계됩니다."
        ),
    }


def _group_metrics(
    con: duckdb.DuckDBPyConnection,
    *,
    group_name: str,
    label: str,
    source_types: tuple[str, ...],
    cutoff: datetime,
    minimum_score: float,
    example_limit: int,
) -> dict[str, Any]:
    placeholders = ", ".join("?" for _ in source_types)
    recent = con.execute(
        f"""
        SELECT
            COUNT(*) AS recent_items,
            COUNT(*) FILTER (
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM trend_cluster_items tci
                    WHERE tci.source_item_id = s.source_item_id
                )
            ) AS recent_unclustered_items
        FROM source_items s
        WHERE s.source_type IN ({placeholders})
          AND COALESCE(s.published_at, s.observed_at, s.imported_at) >= ?
        """,
        [*source_types, cutoff],
    ).fetchone()
    recent_items = int((recent or (0, 0))[0] or 0)
    recent_unclustered_items = int((recent or (0, 0))[1] or 0)

    cluster_row = con.execute(
        f"""
        WITH matching_clusters AS (
            SELECT DISTINCT tc.cluster_id, tc.recommendation_status,
                   tc.trend_score, tc.opportunity_score
            FROM trend_clusters tc
            JOIN trend_cluster_items tci ON tci.cluster_id = tc.cluster_id
            JOIN source_items s ON s.source_item_id = tci.source_item_id
            WHERE s.source_type IN ({placeholders})
        )
        SELECT
            COUNT(*) AS cluster_count,
            COUNT(*) FILTER (WHERE recommendation_status = 'recommended') AS recommended_count,
            COUNT(*) FILTER (WHERE recommendation_status = 'review') AS review_count,
            COUNT(*) FILTER (WHERE recommendation_status = 'hold') AS hold_count,
            COUNT(*) FILTER (
                WHERE COALESCE(recommendation_status, 'review') IN ('recommended', 'review')
                  AND COALESCE(trend_score, 0) >= ?
            ) AS default_visible_count,
            COUNT(*) FILTER (
                WHERE COALESCE(recommendation_status, 'review') IN ('recommended', 'review')
                  AND COALESCE(trend_score, 0) < ?
            ) AS eligible_below_score_count,
            MAX(COALESCE(trend_score, 0)) AS highest_trend_score
        FROM matching_clusters
        """,
        [*source_types, minimum_score, minimum_score],
    ).fetchone()
    values = list(cluster_row or (0, 0, 0, 0, 0, 0, 0.0))
    (
        cluster_count,
        recommended_count,
        review_count,
        hold_count,
        default_visible_count,
        eligible_below_score_count,
        highest_trend_score,
    ) = (
        int(values[0] or 0),
        int(values[1] or 0),
        int(values[2] or 0),
        int(values[3] or 0),
        int(values[4] or 0),
        int(values[5] or 0),
        float(values[6] or 0.0),
    )

    example_rows = con.execute(
        f"""
        SELECT DISTINCT
            tc.cluster_id,
            tc.canonical_title,
            COALESCE(tc.recommendation_status, 'review') AS recommendation_status,
            COALESCE(tc.trend_score, 0) AS trend_score,
            COALESCE(tc.opportunity_score, 0) AS opportunity_score
        FROM trend_clusters tc
        JOIN trend_cluster_items tci ON tci.cluster_id = tc.cluster_id
        JOIN source_items s ON s.source_item_id = tci.source_item_id
        WHERE s.source_type IN ({placeholders})
        ORDER BY
            CASE COALESCE(tc.recommendation_status, 'review')
                WHEN 'recommended' THEN 0
                WHEN 'review' THEN 1
                ELSE 2
            END,
            COALESCE(tc.trend_score, 0) DESC,
            COALESCE(tc.opportunity_score, 0) DESC,
            tc.canonical_title
        LIMIT ?
        """,
        [*source_types, max(1, min(int(example_limit), 20))],
    ).fetchall()
    examples = [
        {
            "cluster_id": str(row[0]),
            "title": str(row[1] or ""),
            "recommendation_status": str(row[2] or "review"),
            "trend_score": float(row[3] or 0.0),
            "opportunity_score": float(row[4] or 0.0),
        }
        for row in example_rows
    ]

    diagnosis = _diagnosis(
        recent_items=recent_items,
        recent_unclustered_items=recent_unclustered_items,
        cluster_count=cluster_count,
        recommended_count=recommended_count,
        review_count=review_count,
        hold_count=hold_count,
        default_visible_count=default_visible_count,
        eligible_below_score_count=eligible_below_score_count,
    )
    return {
        "source_group": group_name,
        "label": label,
        "source_types": list(source_types),
        "recent_items": recent_items,
        "recent_clustered_items": max(0, recent_items - recent_unclustered_items),
        "recent_unclustered_items": recent_unclustered_items,
        "cluster_count": cluster_count,
        "recommended_count": recommended_count,
        "review_count": review_count,
        "hold_count": hold_count,
        "default_visible_count": default_visible_count,
        "eligible_below_score_count": eligible_below_score_count,
        "highest_trend_score": round(highest_trend_score, 1),
        "diagnosis": diagnosis,
        "diagnosis_label": _DIAGNOSIS_LABELS[diagnosis],
        "examples": examples,
    }


def build_trend_source_visibility_diagnostic(
    con: duckdb.DuckDBPyConnection,
    *,
    lookback_hours: int = 72,
    minimum_score: float = 30.0,
    now: datetime | None = None,
    example_limit: int = 5,
) -> dict[str, Any]:
    """출처별 원문→현재 군집→기본 추천·검토 목록 도달 상태를 읽기 전용으로 집계합니다."""
    bounded_hours = max(6, int(lookback_hours))
    bounded_score = _bounded_score(minimum_score)
    missing_tables = [name for name in _REQUIRED_TABLES if name not in _table_names(con)]
    if missing_tables:
        return _unavailable(
            lookback_hours=bounded_hours,
            minimum_score=bounded_score,
            missing_tables=missing_tables,
        )

    cutoff = (now or datetime.now()) - timedelta(hours=bounded_hours)
    try:
        groups = {
            group_name: _group_metrics(
                con,
                group_name=group_name,
                label=label,
                source_types=source_types,
                cutoff=cutoff,
                minimum_score=bounded_score,
                example_limit=example_limit,
            )
            for group_name, (label, source_types) in _SOURCE_GROUPS.items()
        }
        totals = con.execute(
            """
            SELECT
                COUNT(*) AS total_clusters,
                COUNT(*) FILTER (
                    WHERE COALESCE(recommendation_status, 'review') IN ('recommended', 'review')
                      AND COALESCE(trend_score, 0) >= ?
                ) AS default_visible_clusters
            FROM trend_clusters
            """,
            [bounded_score],
        ).fetchone()
    except Exception as exc:
        return _unavailable(
            lookback_hours=bounded_hours,
            minimum_score=bounded_score,
            error=exc,
        )

    return {
        "available": True,
        "lookback_hours": bounded_hours,
        "minimum_score": bounded_score,
        "total_clusters": int((totals or (0, 0))[0] or 0),
        "default_visible_clusters": int((totals or (0, 0))[1] or 0),
        "groups": groups,
        "missing_tables": [],
        "error_type": "",
        "error_message": "",
        "overlap_note": (
            "한 군집에 여러 출처가 있으면 출처별 군집 수에는 중복으로 집계됩니다."
        ),
    }
