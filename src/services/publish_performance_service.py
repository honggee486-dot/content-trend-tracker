from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any
from uuid import uuid4

import duckdb


STANDARD_OBSERVATION_WINDOWS = (7, 30, 90)
MIN_PROFILE_SAMPLE = 3


@dataclass(frozen=True)
class PublishPerformanceComparison:
    window_days: int
    status: str
    severity: str
    summary: str
    comparison_ready: bool
    recommendation_action: str
    profile_rows: tuple[dict[str, Any], ...]
    view_leader: str
    engagement_leader: str
    next_step: str


def ensure_publish_performance_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS publish_performance_snapshots(
            snapshot_id VARCHAR PRIMARY KEY,
            publish_id VARCHAR NOT NULL,
            observation_window_days INTEGER NOT NULL,
            observed_at TIMESTAMP NOT NULL,
            views BIGINT NOT NULL DEFAULT 0,
            search_visits BIGINT NOT NULL DEFAULT 0,
            likes BIGINT NOT NULL DEFAULT 0,
            comments BIGINT NOT NULL DEFAULT 0,
            shares BIGINT NOT NULL DEFAULT 0,
            memo VARCHAR NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_publish_performance_publish_window
        ON publish_performance_snapshots(publish_id, observation_window_days, observed_at)
        """
    )


def _non_negative_int(value: Any, *, label: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}은 0 이상의 정수여야 합니다.") from exc
    if normalized < 0:
        raise ValueError(f"{label}은 0 이상의 정수여야 합니다.")
    return normalized


def _normalize_window_days(value: Any) -> int:
    normalized = _non_negative_int(value, label="관찰 구간")
    if normalized < 1 or normalized > 3650:
        raise ValueError("관찰 구간은 1일 이상 3650일 이하여야 합니다.")
    return normalized


def save_publish_performance_snapshot(
    con: duckdb.DuckDBPyConnection,
    *,
    publish_id: str,
    observation_window_days: int,
    observed_at: datetime,
    views: int,
    search_visits: int = 0,
    likes: int = 0,
    comments: int = 0,
    shares: int = 0,
    memo: str = "",
) -> str:
    ensure_publish_performance_schema(con)
    normalized_publish_id = str(publish_id or "").strip()
    if not normalized_publish_id:
        raise ValueError("발행 기록을 선택하세요.")
    if not isinstance(observed_at, datetime):
        raise ValueError("올바른 성과 확인 시각을 입력하세요.")

    record = con.execute(
        """
        SELECT publish_status, published_at, created_at, archived_at
        FROM publish_records
        WHERE publish_id = ?
        """,
        [normalized_publish_id],
    ).fetchone()
    if record is None:
        raise ValueError("발행 기록을 찾을 수 없습니다.")
    if record[3] is not None:
        raise ValueError("보관된 발행 기록에는 새 성과를 입력할 수 없습니다.")
    if str(record[0] or "").strip().casefold() != "published":
        raise ValueError("발행 완료 기록에만 성과를 입력할 수 있습니다.")
    published_at = record[1] or record[2]
    if isinstance(published_at, datetime) and observed_at < published_at:
        raise ValueError("성과 확인 시각은 발행 시각보다 빠를 수 없습니다.")

    snapshot_id = f"perf_{uuid4().hex}"
    now = datetime.now()
    con.execute(
        """
        INSERT INTO publish_performance_snapshots(
            snapshot_id, publish_id, observation_window_days, observed_at,
            views, search_visits, likes, comments, shares, memo, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            snapshot_id,
            normalized_publish_id,
            _normalize_window_days(observation_window_days),
            observed_at.replace(microsecond=0),
            _non_negative_int(views, label="조회수"),
            _non_negative_int(search_visits, label="검색 유입"),
            _non_negative_int(likes, label="좋아요"),
            _non_negative_int(comments, label="댓글"),
            _non_negative_int(shares, label="공유"),
            str(memo or "").strip(),
            now,
        ],
    )
    return snapshot_id


def _decorate_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    views = int(row.get("views") or 0)
    search_visits = int(row.get("search_visits") or 0)
    interactions = (
        int(row.get("likes") or 0)
        + int(row.get("comments") or 0)
        + int(row.get("shares") or 0)
    )
    row["interactions"] = interactions
    row["search_share"] = (search_visits / views) if views > 0 else None
    row["engagement_rate"] = (interactions / views) if views > 0 else None
    return row


def list_publish_performance_snapshots(
    con: duckdb.DuckDBPyConnection,
    *,
    publish_id: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    ensure_publish_performance_schema(con)
    normalized_publish_id = str(publish_id or "").strip()
    rows = con.execute(
        """
        SELECT ps.snapshot_id, ps.publish_id, ps.observation_window_days,
               ps.observed_at, ps.views, ps.search_visits, ps.likes,
               ps.comments, ps.shares, ps.memo, ps.created_at,
               pr.platform, pr.blog_profile_id, pr.published_at,
               d.title AS draft_title,
               bp.profile_name AS blog_profile_name
        FROM publish_performance_snapshots ps
        JOIN publish_records pr ON pr.publish_id = ps.publish_id
        JOIN drafts d ON d.draft_id = pr.draft_id
        LEFT JOIN blog_profiles bp ON bp.blog_profile_id = pr.blog_profile_id
        WHERE (? = '' OR ps.publish_id = ?)
        ORDER BY ps.observed_at DESC, ps.created_at DESC
        LIMIT ?
        """,
        [
            normalized_publish_id,
            normalized_publish_id,
            max(1, min(int(limit), 2000)),
        ],
    ).fetchall()
    columns = [str(column[0]) for column in con.description]
    return [_decorate_snapshot(dict(zip(columns, row))) for row in rows]


def list_latest_publish_performance(
    con: duckdb.DuckDBPyConnection,
    *,
    observation_window_days: int,
) -> list[dict[str, Any]]:
    ensure_publish_performance_schema(con)
    window_days = _normalize_window_days(observation_window_days)
    rows = con.execute(
        """
        WITH ranked AS (
            SELECT ps.snapshot_id, ps.publish_id, ps.observation_window_days,
                   ps.observed_at, ps.views, ps.search_visits, ps.likes,
                   ps.comments, ps.shares, ps.memo, ps.created_at,
                   ROW_NUMBER() OVER(
                       PARTITION BY ps.publish_id, ps.observation_window_days
                       ORDER BY ps.observed_at DESC, ps.created_at DESC,
                                ps.snapshot_id DESC
                   ) AS row_number
            FROM publish_performance_snapshots ps
            WHERE ps.observation_window_days = ?
        )
        SELECT ranked.snapshot_id, ranked.publish_id,
               ranked.observation_window_days, ranked.observed_at,
               ranked.views, ranked.search_visits, ranked.likes,
               ranked.comments, ranked.shares, ranked.memo,
               ranked.created_at, pr.platform, pr.blog_profile_id,
               pr.published_at, d.title AS draft_title,
               bp.profile_name AS blog_profile_name
        FROM ranked
        JOIN publish_records pr ON pr.publish_id = ranked.publish_id
        JOIN drafts d ON d.draft_id = pr.draft_id
        LEFT JOIN blog_profiles bp ON bp.blog_profile_id = pr.blog_profile_id
        WHERE ranked.row_number = 1
          AND pr.publish_status = 'published'
          AND pr.archived_at IS NULL
        ORDER BY ranked.observed_at DESC, ranked.publish_id
        """,
        [window_days],
    ).fetchall()
    columns = [str(column[0]) for column in con.description]
    return [_decorate_snapshot(dict(zip(columns, row))) for row in rows]


def _average(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def build_publish_performance_comparison(
    con: duckdb.DuckDBPyConnection,
    *,
    observation_window_days: int,
    minimum_profile_sample: int = MIN_PROFILE_SAMPLE,
) -> PublishPerformanceComparison:
    window_days = _normalize_window_days(observation_window_days)
    minimum_sample = max(1, int(minimum_profile_sample))
    rows = list_latest_publish_performance(
        con,
        observation_window_days=window_days,
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    labels: dict[str, str] = {}
    platforms: dict[str, str] = {}
    for row in rows:
        profile_id = str(row.get("blog_profile_id") or "").strip()
        platform = str(row.get("platform") or "unknown").strip()
        group_key = profile_id or f"platform:{platform}"
        grouped.setdefault(group_key, []).append(row)
        labels[group_key] = str(row.get("blog_profile_name") or platform or "알 수 없음")
        platforms[group_key] = platform

    profile_rows: list[dict[str, Any]] = []
    for group_key, items in grouped.items():
        views = [int(item.get("views") or 0) for item in items]
        search_shares = [
            float(item["search_share"])
            for item in items
            if item.get("search_share") is not None
        ]
        engagement_rates = [
            float(item["engagement_rate"])
            for item in items
            if item.get("engagement_rate") is not None
        ]
        profile_rows.append(
            {
                "group_key": group_key,
                "blog_profile_id": group_key if not group_key.startswith("platform:") else "",
                "profile_name": labels[group_key],
                "platform": platforms[group_key],
                "measured_posts": len(items),
                "sample_sufficient": len(items) >= minimum_sample,
                "average_views": round(sum(views) / len(views), 1) if views else 0.0,
                "median_views": round(float(median(views)), 1) if views else 0.0,
                "average_search_share": _average(search_shares),
                "average_engagement_rate": _average(engagement_rates),
            }
        )
    profile_rows.sort(
        key=lambda item: (
            bool(item["sample_sufficient"]),
            float(item["average_views"]),
            int(item["measured_posts"]),
        ),
        reverse=True,
    )

    sufficient = [item for item in profile_rows if item["sample_sufficient"]]
    if not rows:
        status = "성과 기록 없음"
        severity = "info"
        summary = f"{window_days}일 성과 기록이 아직 없습니다."
        next_step = "발행 후 같은 관찰 구간의 조회·검색 유입·반응 수치를 입력합니다."
    elif len(sufficient) < 2:
        status = "표본 부족"
        severity = "info"
        summary = (
            f"{window_days}일 기준으로 프로필당 최소 {minimum_sample}건이 쌓인 "
            "발행처가 2개 미만입니다."
        )
        next_step = (
            "현재 발행처 추천 규칙을 유지하고 동일 관찰 구간의 표본을 더 기록합니다."
        )
    else:
        status = "비교 가능"
        severity = "success"
        summary = (
            f"{window_days}일 기준으로 {len(sufficient)}개 발행처가 "
            f"프로필당 최소 {minimum_sample}건 표본을 충족했습니다."
        )
        next_step = (
            "조회수와 반응률 우위가 여러 표본에서 반복되는지 확인한 뒤 "
            "별도 작업에서 추천 규칙 한 축만 조정합니다."
        )

    view_leader = ""
    engagement_leader = ""
    if sufficient:
        view_leader = max(sufficient, key=lambda item: float(item["average_views"]))[
            "profile_name"
        ]
        engagement_candidates = [
            item for item in sufficient if item.get("average_engagement_rate") is not None
        ]
        if engagement_candidates:
            engagement_leader = max(
                engagement_candidates,
                key=lambda item: float(item["average_engagement_rate"] or 0.0),
            )["profile_name"]

    return PublishPerformanceComparison(
        window_days=window_days,
        status=status,
        severity=severity,
        summary=summary,
        comparison_ready=len(sufficient) >= 2,
        recommendation_action="keep_current_rules",
        profile_rows=tuple(profile_rows),
        view_leader=str(view_leader),
        engagement_leader=str(engagement_leader),
        next_step=next_step,
    )
