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
    "visible": "기본 목록에 실제 표시 후보 있음",
    "ranked_out": "추천·검토 후보가 기본 목록 표시 한도 밖에 있음",
    "hidden_by_score": "추천·검토 후보가 점수 기준 아래에 있음",
    "held_by_policy": "현재 군집이 모두 보류 상태",
    "unclustered_or_stale": "최근 원문이 현재 군집에 연결되지 않음",
    "no_current_clusters": "현재 군집 없음",
    "no_recent_items": "최근 분석 범위 원문 없음",
    "no_visible_candidate": "기본 목록 노출 후보 없음",
}
_DEFAULT_LOOKBACK_HOURS = 72
_DEFAULT_DISPLAY_LIMIT = 100
_DEFAULT_SORT_BY = "opportunity"
_ALLOWED_SORTS = frozenset({"opportunity", "trend", "quality", "recent"})


def _table_names(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}


def _bounded_lookback_hours(value: object) -> int:
    try:
        parsed = int(value) if value is not None else _DEFAULT_LOOKBACK_HOURS
    except (TypeError, ValueError, OverflowError):
        parsed = _DEFAULT_LOOKBACK_HOURS
    return max(6, parsed)


def _bounded_score(value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = 30.0
    return max(0.0, min(parsed, 100.0))


def _bounded_display_limit(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = _DEFAULT_DISPLAY_LIMIT
    return max(1, min(parsed, 500))


def _normalized_sort_by(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    return normalized if normalized in _ALLOWED_SORTS else _DEFAULT_SORT_BY


def _diagnosis(
    *,
    recent_items: int,
    recent_unclustered_items: int,
    cluster_count: int,
    recommended_count: int,
    review_count: int,
    hold_count: int,
    default_visible_count: int,
    eligible_at_or_above_score_count: int,
    eligible_below_score_count: int,
) -> str:
    if recent_items <= 0:
        return "no_recent_items"
    if cluster_count <= 0:
        return "unclustered_or_stale" if recent_unclustered_items else "no_current_clusters"
    if default_visible_count > 0:
        return "visible"
    if eligible_at_or_above_score_count > 0:
        return "ranked_out"
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
    display_limit: int,
    sort_by: str,
    missing_tables: list[str] | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "lookback_hours": int(lookback_hours),
        "minimum_score": float(minimum_score),
        "display_limit": int(display_limit),
        "sort_by": str(sort_by),
        "total_clusters": 0,
        "eligible_clusters": 0,
        "default_visible_clusters": 0,
        "groups": {},
        "missing_tables": list(missing_tables or []),
        "error_type": type(error).__name__ if error is not None else "",
        "error_message": str(error)[:500] if error is not None else "",
        "overlap_note": (
            "한 군집에 여러 출처가 있으면 출처별 군집 수에는 중복으로 집계됩니다."
        ),
        "scope_note": (
            "출처별 군집은 최근 분석 범위 원문이 현재 군집에 연결된 경우만 집계합니다."
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
    visible_cluster_ids: frozenset[str],
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

    cluster_rows = con.execute(
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
          AND COALESCE(s.published_at, s.observed_at, s.imported_at) >= ?
        """,
        [*source_types, cutoff],
    ).fetchall()

    normalized_rows = [
        {
            "cluster_id": str(row[0]),
            "title": str(row[1] or ""),
            "recommendation_status": str(row[2] or "review"),
            "trend_score": float(row[3] or 0.0),
            "opportunity_score": float(row[4] or 0.0),
        }
        for row in cluster_rows
    ]
    recommended_count = sum(
        1 for row in normalized_rows if row["recommendation_status"] == "recommended"
    )
    review_count = sum(
        1 for row in normalized_rows if row["recommendation_status"] == "review"
    )
    hold_count = sum(
        1 for row in normalized_rows if row["recommendation_status"] == "hold"
    )
    eligible_at_or_above_score_count = sum(
        1
        for row in normalized_rows
        if row["recommendation_status"] in {"recommended", "review"}
        and row["trend_score"] >= minimum_score
    )
    eligible_below_score_count = sum(
        1
        for row in normalized_rows
        if row["recommendation_status"] in {"recommended", "review"}
        and row["trend_score"] < minimum_score
    )
    default_visible_count = sum(
        1 for row in normalized_rows if row["cluster_id"] in visible_cluster_ids
    )
    ranked_out_count = max(
        0,
        eligible_at_or_above_score_count - default_visible_count,
    )
    highest_trend_score = max(
        (row["trend_score"] for row in normalized_rows),
        default=0.0,
    )
    highest_opportunity_score = max(
        (row["opportunity_score"] for row in normalized_rows),
        default=0.0,
    )

    status_order = {"recommended": 0, "review": 1, "hold": 2}
    example_rows = sorted(
        normalized_rows,
        key=lambda row: (
            status_order.get(row["recommendation_status"], 3),
            -row["trend_score"],
            -row["opportunity_score"],
            row["title"],
        ),
    )[: max(1, min(int(example_limit), 20))]
    examples = [
        {
            **row,
            "in_default_list": row["cluster_id"] in visible_cluster_ids,
        }
        for row in example_rows
    ]

    diagnosis = _diagnosis(
        recent_items=recent_items,
        recent_unclustered_items=recent_unclustered_items,
        cluster_count=len(normalized_rows),
        recommended_count=recommended_count,
        review_count=review_count,
        hold_count=hold_count,
        default_visible_count=default_visible_count,
        eligible_at_or_above_score_count=eligible_at_or_above_score_count,
        eligible_below_score_count=eligible_below_score_count,
    )
    return {
        "source_group": group_name,
        "label": label,
        "source_types": list(source_types),
        "recent_items": recent_items,
        "recent_clustered_items": max(0, recent_items - recent_unclustered_items),
        "recent_unclustered_items": recent_unclustered_items,
        "cluster_count": len(normalized_rows),
        "recommended_count": recommended_count,
        "review_count": review_count,
        "hold_count": hold_count,
        "default_visible_count": default_visible_count,
        "eligible_at_or_above_score_count": eligible_at_or_above_score_count,
        "ranked_out_count": ranked_out_count,
        "eligible_below_score_count": eligible_below_score_count,
        "highest_trend_score": round(highest_trend_score, 1),
        "highest_opportunity_score": round(highest_opportunity_score, 1),
        "diagnosis": diagnosis,
        "diagnosis_label": _DIAGNOSIS_LABELS[diagnosis],
        "examples": examples,
    }


def build_trend_source_visibility_diagnostic(
    con: duckdb.DuckDBPyConnection,
    *,
    lookback_hours: int | None = None,
    minimum_score: float = 30.0,
    display_limit: int = _DEFAULT_DISPLAY_LIMIT,
    sort_by: str = _DEFAULT_SORT_BY,
    now: datetime | None = None,
    example_limit: int = 5,
) -> dict[str, Any]:
    """출처별 최근 원문이 실제 기본 후보 목록까지 도달하는지 읽기 전용으로 집계합니다."""
    table_names = _table_names(con)
    configured_lookback: object = None
    if lookback_hours is None and "app_settings" in table_names:
        row = con.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key = 'trend_lookback_hours'"
        ).fetchone()
        configured_lookback = row[0] if row is not None else None
    bounded_hours = _bounded_lookback_hours(
        lookback_hours if lookback_hours is not None else configured_lookback
    )
    bounded_score = _bounded_score(minimum_score)
    bounded_display_limit = _bounded_display_limit(display_limit)
    normalized_sort_by = _normalized_sort_by(sort_by)
    missing_tables = [name for name in _REQUIRED_TABLES if name not in table_names]
    if missing_tables:
        return _unavailable(
            lookback_hours=bounded_hours,
            minimum_score=bounded_score,
            display_limit=bounded_display_limit,
            sort_by=normalized_sort_by,
            missing_tables=missing_tables,
        )

    cutoff = (now or datetime.now()) - timedelta(hours=bounded_hours)
    try:
        from src.services.trend_discovery_service import list_ranked_trends

        visible_frame = list_ranked_trends(
            con,
            limit=bounded_display_limit,
            minimum_score=bounded_score,
            recommendation_statuses=("recommended", "review"),
            sort_by=normalized_sort_by,
        )
        visible_cluster_ids = frozenset(
            str(value)
            for value in (
                visible_frame["cluster_id"].tolist()
                if "cluster_id" in visible_frame.columns
                else ()
            )
        )
        eligible_clusters = (
            int(visible_frame["matched_count"].iloc[0] or 0)
            if not visible_frame.empty and "matched_count" in visible_frame.columns
            else 0
        )
        groups = {
            group_name: _group_metrics(
                con,
                group_name=group_name,
                label=label,
                source_types=source_types,
                cutoff=cutoff,
                minimum_score=bounded_score,
                visible_cluster_ids=visible_cluster_ids,
                example_limit=example_limit,
            )
            for group_name, (label, source_types) in _SOURCE_GROUPS.items()
        }
        total_clusters = int(
            con.execute("SELECT COUNT(*) FROM trend_clusters").fetchone()[0] or 0
        )
    except Exception as exc:
        return _unavailable(
            lookback_hours=bounded_hours,
            minimum_score=bounded_score,
            display_limit=bounded_display_limit,
            sort_by=normalized_sort_by,
            error=exc,
        )

    return {
        "available": True,
        "lookback_hours": bounded_hours,
        "minimum_score": bounded_score,
        "display_limit": bounded_display_limit,
        "sort_by": normalized_sort_by,
        "total_clusters": total_clusters,
        "eligible_clusters": eligible_clusters,
        "default_visible_clusters": len(visible_cluster_ids),
        "groups": groups,
        "missing_tables": [],
        "error_type": "",
        "error_message": "",
        "overlap_note": (
            "한 군집에 여러 출처가 있으면 출처별 군집 수에는 중복으로 집계됩니다."
        ),
        "scope_note": (
            "출처별 군집은 최근 분석 범위 원문이 현재 군집에 연결된 경우만 집계합니다."
        ),
    }
