from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

import duckdb


STATUS_HOT = "hot"
STATUS_EARLY = "early"
STATUS_OPPORTUNITY = "opportunity"
STATUS_SATURATED = "saturated"

ACTION_WRITE_NOW = "write_now"
ACTION_WATCH = "watch"
ACTION_CLOSE = "close"

STATUS_LABELS = {
    STATUS_HOT: "🔥 급상승",
    STATUS_EARLY: "🟠 초기 신호",
    STATUS_OPPORTUNITY: "🟢 정보성 기회",
    STATUS_SATURATED: "⚪ 포화/종료",
}

ACTION_LABELS = {
    ACTION_WRITE_NOW: "지금 작성",
    ACTION_WATCH: "계속 관찰",
    ACTION_CLOSE: "종료",
}

DEMAND_SOURCE_TYPES = {"youtube", "google_trends", "wikipedia_pageviews"}
SUPPLY_SOURCE_TYPES = {"naver_news", "naver_blog", "daum_web", "daum_cafe"}

_BREAKING_TERMS = {
    "속보", "실시간", "경기", "결과", "스코어", "태풍", "호우", "날씨", "예보",
}
_DURABLE_INFORMATIONAL_TERMS = {
    "방법", "신청", "대상", "조건", "기준", "혜택", "사용법", "비교", "지원",
    "서류", "절차", "정책", "개정", "시행",
}
_TIME_SENSITIVE_TERMS = {
    "오늘", "내일", "이번주", "마감", "출시", "발표", "가격", "요금", "환율",
    "금리", "일정", "예정", "확정",
}


@dataclass(frozen=True)
class OpportunityRadarSnapshot:
    snapshot_id: str
    cluster_id: str
    canonical_title: str
    observed_at: datetime
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    trend_score: float
    base_opportunity_score: float
    demand_score: float
    supply_score: float
    saturation_score: float
    velocity: float
    acceleration: float
    expected_lifetime: str
    radar_status: str
    recommended_action: str
    item_count: int
    source_type_count: int
    publisher_count: int
    source_spread: tuple[dict[str, str], ...]
    source_counts: dict[str, int]
    reasons: tuple[str, ...]


def ensure_opportunity_radar_schema(con: duckdb.DuckDBPyConnection) -> None:
    """기존 DB를 삭제/변환하지 않고 레이더용 이력과 최신 Watchlist만 추가합니다."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS trend_opportunity_snapshots (
            snapshot_id VARCHAR PRIMARY KEY,
            cluster_id VARCHAR NOT NULL,
            canonical_title VARCHAR NOT NULL,
            observed_at TIMESTAMP NOT NULL,
            first_seen_at TIMESTAMP,
            last_seen_at TIMESTAMP,
            trend_score DOUBLE NOT NULL,
            base_opportunity_score DOUBLE NOT NULL,
            demand_score DOUBLE NOT NULL DEFAULT 0,
            supply_score DOUBLE NOT NULL DEFAULT 0,
            saturation_score DOUBLE NOT NULL DEFAULT 0,
            velocity DOUBLE NOT NULL DEFAULT 0,
            acceleration DOUBLE NOT NULL DEFAULT 0,
            expected_lifetime VARCHAR NOT NULL DEFAULT 'unknown',
            radar_status VARCHAR NOT NULL DEFAULT 'early',
            recommended_action VARCHAR NOT NULL DEFAULT 'watch',
            item_count INTEGER NOT NULL DEFAULT 0,
            source_type_count INTEGER NOT NULL DEFAULT 0,
            publisher_count INTEGER NOT NULL DEFAULT 0,
            source_spread_json VARCHAR NOT NULL DEFAULT '[]',
            source_counts_json VARCHAR NOT NULL DEFAULT '{}',
            reasons_json VARCHAR NOT NULL DEFAULT '[]',
            UNIQUE(cluster_id, observed_at)
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trend_opportunity_snapshots_cluster_time
        ON trend_opportunity_snapshots(cluster_id, observed_at DESC)
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trend_opportunity_snapshots_status_time
        ON trend_opportunity_snapshots(radar_status, observed_at DESC)
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS trend_opportunity_watchlist (
            cluster_id VARCHAR PRIMARY KEY,
            canonical_title VARCHAR NOT NULL,
            first_seen_at TIMESTAMP,
            last_seen_at TIMESTAMP,
            first_tracked_at TIMESTAMP NOT NULL,
            last_evaluated_at TIMESTAMP NOT NULL,
            radar_status VARCHAR NOT NULL,
            recommended_action VARCHAR NOT NULL,
            demand_score DOUBLE NOT NULL DEFAULT 0,
            supply_score DOUBLE NOT NULL DEFAULT 0,
            saturation_score DOUBLE NOT NULL DEFAULT 0,
            velocity DOUBLE NOT NULL DEFAULT 0,
            acceleration DOUBLE NOT NULL DEFAULT 0,
            expected_lifetime VARCHAR NOT NULL DEFAULT 'unknown',
            source_spread_json VARCHAR NOT NULL DEFAULT '[]',
            source_counts_json VARCHAR NOT NULL DEFAULT '{}',
            status_changed_at TIMESTAMP NOT NULL,
            latest_snapshot_id VARCHAR NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trend_opportunity_watchlist_status
        ON trend_opportunity_watchlist(radar_status, recommended_action, last_evaluated_at DESC)
        """
    )


def _bounded(value: Any, minimum: float = 0.0, maximum: float = 100.0) -> float:
    try:
        numeric = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        numeric = 0.0
    if not math.isfinite(numeric):
        numeric = 0.0
    return max(minimum, min(maximum, numeric))


def _source_first_seen(item: dict[str, Any]) -> datetime | None:
    for key in ("first_imported_at", "imported_at", "observed_at", "published_at"):
        value = item.get(key)
        if isinstance(value, datetime):
            return value
    return None


def _load_current_clusters(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT tc.cluster_id, tc.canonical_title, tc.trend_score,
               tc.opportunity_score, tc.item_count, tc.source_type_count,
               tc.publisher_count, tc.first_seen_at, tc.last_seen_at,
               tc.calculated_at,
               s.source_item_id, s.source_type, s.signal_value,
               s.first_imported_at, s.last_imported_at, s.imported_at,
               s.observed_at, s.published_at
        FROM trend_clusters tc
        LEFT JOIN trend_cluster_items tci ON tci.cluster_id = tc.cluster_id
        LEFT JOIN source_items s ON s.source_item_id = tci.source_item_id
        ORDER BY tc.cluster_id, s.source_type, s.source_item_id
        """
    ).fetchall()
    columns = [str(column[0]) for column in con.description]
    grouped: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = dict(zip(columns, raw))
        cluster_id = str(row.get("cluster_id") or "")
        if not cluster_id:
            continue
        target = grouped.setdefault(
            cluster_id,
            {
                "cluster_id": cluster_id,
                "canonical_title": str(row.get("canonical_title") or ""),
                "trend_score": _bounded(row.get("trend_score")),
                "opportunity_score": _bounded(row.get("opportunity_score")),
                "item_count": int(row.get("item_count") or 0),
                "source_type_count": int(row.get("source_type_count") or 0),
                "publisher_count": int(row.get("publisher_count") or 0),
                "first_seen_at": row.get("first_seen_at"),
                "last_seen_at": row.get("last_seen_at"),
                "calculated_at": row.get("calculated_at"),
                "items": [],
            },
        )
        if row.get("source_item_id") is not None:
            target["items"].append(row)
    return list(grouped.values())


def _recent_supply_discoveries(
    con: duckdb.DuckDBPyConnection,
    *,
    cutoff: datetime,
) -> dict[str, dict[str, int]]:
    rows = con.execute(
        """
        SELECT tci.cluster_id, cqd.source_type,
               COUNT(*) AS discovery_count,
               SUM(CASE WHEN cqd.is_new THEN 1 ELSE 0 END) AS new_count
        FROM trend_cluster_items tci
        JOIN collection_query_discoveries cqd
          ON cqd.source_item_id = tci.source_item_id
        WHERE cqd.discovered_at >= ?
          AND cqd.source_type IN ('naver_news', 'naver_blog', 'daum_web', 'daum_cafe')
        GROUP BY tci.cluster_id, cqd.source_type
        """,
        [cutoff],
    ).fetchall()
    result: dict[str, dict[str, int]] = {}
    for cluster_id, source_type, discovery_count, new_count in rows:
        target = result.setdefault(str(cluster_id), {})
        target[f"{source_type}_discoveries"] = int(discovery_count or 0)
        target[f"{source_type}_new"] = int(new_count or 0)
    return result


def _source_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        source_type = str(item.get("source_type") or "")
        if source_type:
            counts[source_type] = counts.get(source_type, 0) + 1
    return counts


def _source_spread(items: list[dict[str, Any]]) -> tuple[dict[str, str], ...]:
    first_by_type: dict[str, datetime] = {}
    for item in items:
        source_type = str(item.get("source_type") or "")
        first_seen = _source_first_seen(item)
        if not source_type or first_seen is None:
            continue
        current = first_by_type.get(source_type)
        if current is None or first_seen < current:
            first_by_type[source_type] = first_seen
    ordered = sorted(first_by_type.items(), key=lambda pair: (pair[1], pair[0]))
    return tuple(
        {
            "source_type": source_type,
            "first_seen_at": observed.isoformat(timespec="seconds"),
        }
        for source_type, observed in ordered
    )


def _demand_score(
    cluster: dict[str, Any],
    *,
    source_counts: dict[str, int],
) -> float:
    base = (
        _bounded(cluster.get("trend_score")) * 0.42
        + _bounded(cluster.get("opportunity_score")) * 0.28
    )
    presence = min(
        18.0,
        source_counts.get("google_trends", 0) * 7.0
        + source_counts.get("wikipedia_pageviews", 0) * 6.0
        + source_counts.get("youtube", 0) * 4.0,
    )
    diversity = min(
        10.0,
        max(0, int(cluster.get("source_type_count") or 0) - 1) * 2.5,
    )
    signal_strength = 0.0
    for item in cluster.get("items") or ():
        if str(item.get("source_type") or "") not in DEMAND_SOURCE_TYPES:
            continue
        signal_value = _bounded(item.get("signal_value"), 0.0, 1_000_000_000.0)
        if signal_value > 0:
            signal_strength += min(4.0, math.log1p(signal_value) * 0.85)
    return round(_bounded(base + presence + diversity + min(8.0, signal_strength)), 1)


def _supply_score(
    cluster: dict[str, Any],
    *,
    source_counts: dict[str, int],
    discovery_counts: dict[str, int],
) -> float:
    portal_items = sum(source_counts.get(source_type, 0) for source_type in SUPPLY_SOURCE_TYPES)
    recent_discoveries = sum(
        discovery_counts.get(f"{source_type}_discoveries", 0)
        for source_type in SUPPLY_SOURCE_TYPES
    )
    recent_new = sum(
        discovery_counts.get(f"{source_type}_new", 0)
        for source_type in SUPPLY_SOURCE_TYPES
    )
    publisher_component = min(15.0, int(cluster.get("publisher_count") or 0) * 2.0)
    score = (
        12.0 * math.sqrt(max(0, portal_items))
        + 5.0 * math.sqrt(max(0, recent_discoveries))
        + 3.0 * math.sqrt(max(0, recent_new))
        + publisher_component
    )
    return round(_bounded(score), 1)


def _previous_snapshot(
    con: duckdb.DuckDBPyConnection,
    *,
    cluster_id: str,
    observed_at: datetime,
) -> dict[str, Any] | None:
    row = con.execute(
        """
        SELECT observed_at, demand_score, supply_score, trend_score, velocity
        FROM trend_opportunity_snapshots
        WHERE cluster_id = ? AND observed_at < ?
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        [cluster_id, observed_at],
    ).fetchone()
    if row is None:
        return None
    return {
        "observed_at": row[0],
        "demand_score": float(row[1] or 0),
        "supply_score": float(row[2] or 0),
        "trend_score": float(row[3] or 0),
        "velocity": float(row[4] or 0),
    }


def _motion(
    previous: dict[str, Any] | None,
    *,
    observed_at: datetime,
    demand_score: float,
) -> tuple[float, float]:
    if previous is None or not isinstance(previous.get("observed_at"), datetime):
        return 0.0, 0.0
    hours = max(
        0.25,
        (observed_at - previous["observed_at"]).total_seconds() / 3600.0,
    )
    velocity = _bounded(
        (demand_score - float(previous.get("demand_score") or 0)) / hours,
        -50.0,
        50.0,
    )
    acceleration = _bounded(
        (velocity - float(previous.get("velocity") or 0)) / hours,
        -50.0,
        50.0,
    )
    return round(velocity, 2), round(acceleration, 2)


def _saturation_score(
    *,
    demand_score: float,
    supply_score: float,
    velocity: float,
) -> float:
    gap_component = _bounded(50.0 + supply_score - demand_score)
    score = supply_score * 0.55 + gap_component * 0.45
    if velocity < 0:
        score += min(15.0, abs(velocity) * 2.0)
    return round(_bounded(score), 1)


def _expected_lifetime(
    title: str,
    *,
    radar_status: str,
    saturation_score: float,
) -> str:
    normalized = " ".join(str(title or "").split()).casefold()
    if radar_status == STATUS_SATURATED:
        return "ending"
    if any(term in normalized for term in _BREAKING_TERMS):
        return "hours"
    if any(term in normalized for term in _DURABLE_INFORMATIONAL_TERMS):
        return "weeks"
    if radar_status == STATUS_HOT:
        return "1_2_days"
    if any(term in normalized for term in _TIME_SENSITIVE_TERMS):
        return "3_7_days"
    if radar_status == STATUS_OPPORTUNITY and saturation_score < 40:
        return "weeks"
    return "3_7_days"


def _status_and_action(
    *,
    demand_score: float,
    supply_score: float,
    saturation_score: float,
    velocity: float,
    base_opportunity_score: float,
    last_seen_at: datetime | None,
    observed_at: datetime,
) -> tuple[str, str]:
    age_hours = (
        max(0.0, (observed_at - last_seen_at).total_seconds() / 3600.0)
        if isinstance(last_seen_at, datetime)
        else 0.0
    )
    if (
        saturation_score >= 72
        or (velocity <= -2.5 and supply_score >= 55)
        or (age_hours >= 36 and demand_score < 35)
    ):
        return STATUS_SATURATED, ACTION_CLOSE
    if velocity >= 3.0 and demand_score >= 55 and saturation_score < 65:
        return STATUS_HOT, ACTION_WRITE_NOW
    if (
        demand_score >= 48
        and base_opportunity_score >= 42
        and saturation_score <= 55
        and supply_score <= 65
    ):
        return STATUS_OPPORTUNITY, ACTION_WRITE_NOW
    if age_hours >= 48 and demand_score < 40:
        return STATUS_SATURATED, ACTION_CLOSE
    return STATUS_EARLY, ACTION_WATCH


def _snapshot_id(cluster_id: str, observed_at: datetime) -> str:
    payload = f"{cluster_id}|{observed_at.isoformat(timespec='microseconds')}"
    return "oprad_" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def build_opportunity_snapshot(
    con: duckdb.DuckDBPyConnection,
    cluster: dict[str, Any],
    *,
    observed_at: datetime,
    discovery_counts: dict[str, int] | None = None,
) -> OpportunityRadarSnapshot:
    ensure_opportunity_radar_schema(con)
    cluster_id = str(cluster.get("cluster_id") or "").strip()
    if not cluster_id:
        raise ValueError("cluster_id is required")

    counts = _source_counts(list(cluster.get("items") or ()))
    spread = _source_spread(list(cluster.get("items") or ()))
    demand = _demand_score(cluster, source_counts=counts)
    supply = _supply_score(
        cluster,
        source_counts=counts,
        discovery_counts=dict(discovery_counts or {}),
    )
    previous = _previous_snapshot(
        con,
        cluster_id=cluster_id,
        observed_at=observed_at,
    )
    velocity, acceleration = _motion(
        previous,
        observed_at=observed_at,
        demand_score=demand,
    )
    saturation = _saturation_score(
        demand_score=demand,
        supply_score=supply,
        velocity=velocity,
    )
    base_opportunity = _bounded(cluster.get("opportunity_score"))
    status, action = _status_and_action(
        demand_score=demand,
        supply_score=supply,
        saturation_score=saturation,
        velocity=velocity,
        base_opportunity_score=base_opportunity,
        last_seen_at=cluster.get("last_seen_at"),
        observed_at=observed_at,
    )
    lifetime = _expected_lifetime(
        str(cluster.get("canonical_title") or ""),
        radar_status=status,
        saturation_score=saturation,
    )
    reasons = (
        f"수요 {demand:.1f}/100 · 공급 {supply:.1f}/100 · 포화 {saturation:.1f}/100",
        f"관심 속도 {velocity:+.2f}점/시간 · 가속도 {acceleration:+.2f}",
        (
            "출처 확산 " + " → ".join(item["source_type"] for item in spread)
            if spread
            else "출처 최초 포착 순서 미확인"
        ),
        f"기존 글감기회 {base_opportunity:.1f}/100 · 예상 수명 {lifetime}",
    )
    return OpportunityRadarSnapshot(
        snapshot_id=_snapshot_id(cluster_id, observed_at),
        cluster_id=cluster_id,
        canonical_title=str(cluster.get("canonical_title") or ""),
        observed_at=observed_at,
        first_seen_at=cluster.get("first_seen_at"),
        last_seen_at=cluster.get("last_seen_at"),
        trend_score=_bounded(cluster.get("trend_score")),
        base_opportunity_score=base_opportunity,
        demand_score=demand,
        supply_score=supply,
        saturation_score=saturation,
        velocity=velocity,
        acceleration=acceleration,
        expected_lifetime=lifetime,
        radar_status=status,
        recommended_action=action,
        item_count=int(cluster.get("item_count") or 0),
        source_type_count=int(cluster.get("source_type_count") or 0),
        publisher_count=int(cluster.get("publisher_count") or 0),
        source_spread=spread,
        source_counts=counts,
        reasons=reasons,
    )


def _store_snapshot(con: duckdb.DuckDBPyConnection, snapshot: OpportunityRadarSnapshot) -> None:
    con.execute(
        """
        INSERT INTO trend_opportunity_snapshots(
            snapshot_id, cluster_id, canonical_title, observed_at,
            first_seen_at, last_seen_at, trend_score, base_opportunity_score,
            demand_score, supply_score, saturation_score, velocity, acceleration,
            expected_lifetime, radar_status, recommended_action,
            item_count, source_type_count, publisher_count,
            source_spread_json, source_counts_json, reasons_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cluster_id, observed_at) DO UPDATE SET
            snapshot_id = EXCLUDED.snapshot_id,
            canonical_title = EXCLUDED.canonical_title,
            first_seen_at = EXCLUDED.first_seen_at,
            last_seen_at = EXCLUDED.last_seen_at,
            trend_score = EXCLUDED.trend_score,
            base_opportunity_score = EXCLUDED.base_opportunity_score,
            demand_score = EXCLUDED.demand_score,
            supply_score = EXCLUDED.supply_score,
            saturation_score = EXCLUDED.saturation_score,
            velocity = EXCLUDED.velocity,
            acceleration = EXCLUDED.acceleration,
            expected_lifetime = EXCLUDED.expected_lifetime,
            radar_status = EXCLUDED.radar_status,
            recommended_action = EXCLUDED.recommended_action,
            item_count = EXCLUDED.item_count,
            source_type_count = EXCLUDED.source_type_count,
            publisher_count = EXCLUDED.publisher_count,
            source_spread_json = EXCLUDED.source_spread_json,
            source_counts_json = EXCLUDED.source_counts_json,
            reasons_json = EXCLUDED.reasons_json
        """,
        [
            snapshot.snapshot_id, snapshot.cluster_id, snapshot.canonical_title,
            snapshot.observed_at, snapshot.first_seen_at, snapshot.last_seen_at,
            snapshot.trend_score, snapshot.base_opportunity_score, snapshot.demand_score,
            snapshot.supply_score, snapshot.saturation_score, snapshot.velocity,
            snapshot.acceleration, snapshot.expected_lifetime, snapshot.radar_status,
            snapshot.recommended_action, snapshot.item_count, snapshot.source_type_count,
            snapshot.publisher_count,
            json.dumps(snapshot.source_spread, ensure_ascii=False),
            json.dumps(snapshot.source_counts, ensure_ascii=False),
            json.dumps(snapshot.reasons, ensure_ascii=False),
        ],
    )


def _upsert_watchlist(con: duckdb.DuckDBPyConnection, snapshot: OpportunityRadarSnapshot) -> None:
    existing = con.execute(
        """
        SELECT first_tracked_at, radar_status, status_changed_at
        FROM trend_opportunity_watchlist
        WHERE cluster_id = ?
        """,
        [snapshot.cluster_id],
    ).fetchone()
    first_tracked_at = (
        existing[0] if existing and isinstance(existing[0], datetime) else snapshot.observed_at
    )
    previous_status = str(existing[1] or "") if existing else ""
    status_changed_at = (
        existing[2] if existing and previous_status == snapshot.radar_status else snapshot.observed_at
    )
    con.execute(
        """
        INSERT INTO trend_opportunity_watchlist(
            cluster_id, canonical_title, first_seen_at, last_seen_at,
            first_tracked_at, last_evaluated_at, radar_status, recommended_action,
            demand_score, supply_score, saturation_score, velocity, acceleration,
            expected_lifetime, source_spread_json, source_counts_json,
            status_changed_at, latest_snapshot_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cluster_id) DO UPDATE SET
            canonical_title = EXCLUDED.canonical_title,
            first_seen_at = EXCLUDED.first_seen_at,
            last_seen_at = EXCLUDED.last_seen_at,
            last_evaluated_at = EXCLUDED.last_evaluated_at,
            radar_status = EXCLUDED.radar_status,
            recommended_action = EXCLUDED.recommended_action,
            demand_score = EXCLUDED.demand_score,
            supply_score = EXCLUDED.supply_score,
            saturation_score = EXCLUDED.saturation_score,
            velocity = EXCLUDED.velocity,
            acceleration = EXCLUDED.acceleration,
            expected_lifetime = EXCLUDED.expected_lifetime,
            source_spread_json = EXCLUDED.source_spread_json,
            source_counts_json = EXCLUDED.source_counts_json,
            status_changed_at = EXCLUDED.status_changed_at,
            latest_snapshot_id = EXCLUDED.latest_snapshot_id
        """,
        [
            snapshot.cluster_id, snapshot.canonical_title, snapshot.first_seen_at,
            snapshot.last_seen_at, first_tracked_at, snapshot.observed_at,
            snapshot.radar_status, snapshot.recommended_action, snapshot.demand_score,
            snapshot.supply_score, snapshot.saturation_score, snapshot.velocity,
            snapshot.acceleration, snapshot.expected_lifetime,
            json.dumps(snapshot.source_spread, ensure_ascii=False),
            json.dumps(snapshot.source_counts, ensure_ascii=False), status_changed_at,
            snapshot.snapshot_id,
        ],
    )


def refresh_opportunity_radar(
    con: duckdb.DuckDBPyConnection,
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """현재 trend_clusters를 한 번의 레이더 관측으로 저장하고 Watchlist를 갱신합니다."""
    ensure_opportunity_radar_schema(con)
    clusters = _load_current_clusters(con)
    if not clusters:
        return {"status": "empty", "observed": 0, "counts": {}}

    resolved_at = observed_at or max(
        (
            cluster.get("calculated_at")
            for cluster in clusters
            if isinstance(cluster.get("calculated_at"), datetime)
        ),
        default=datetime.now(),
    )
    supply_discoveries = _recent_supply_discoveries(
        con,
        cutoff=resolved_at - timedelta(hours=24),
    )
    snapshots: list[OpportunityRadarSnapshot] = []
    for cluster in clusters:
        snapshot = build_opportunity_snapshot(
            con,
            cluster,
            observed_at=resolved_at,
            discovery_counts=supply_discoveries.get(str(cluster["cluster_id"]), {}),
        )
        _store_snapshot(con, snapshot)
        _upsert_watchlist(con, snapshot)
        snapshots.append(snapshot)

    counts: dict[str, int] = {status: 0 for status in STATUS_LABELS}
    for snapshot in snapshots:
        counts[snapshot.radar_status] = counts.get(snapshot.radar_status, 0) + 1
    return {
        "status": "recorded",
        "observed": len(snapshots),
        "observed_at": resolved_at.isoformat(timespec="seconds"),
        "counts": counts,
    }


def list_opportunity_watchlist(
    con: duckdb.DuckDBPyConnection,
    *,
    statuses: Iterable[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    ensure_opportunity_radar_schema(con)
    allowed = set(STATUS_LABELS)
    selected = [
        str(value or "").strip()
        for value in (statuses or ())
        if str(value or "").strip() in allowed
    ]
    where_sql = ""
    params: list[Any] = []
    if statuses is not None:
        if not selected:
            return []
        placeholders = ", ".join("?" for _ in selected)
        where_sql = f"WHERE radar_status IN ({placeholders})"
        params.extend(selected)
    params.append(max(1, min(int(limit), 500)))
    rows = con.execute(
        f"""
        SELECT cluster_id, canonical_title, first_seen_at, last_seen_at,
               first_tracked_at, last_evaluated_at, radar_status,
               recommended_action, demand_score, supply_score,
               saturation_score, velocity, acceleration, expected_lifetime,
               source_spread_json, source_counts_json, status_changed_at,
               latest_snapshot_id
        FROM trend_opportunity_watchlist
        {where_sql}
        ORDER BY
            CASE radar_status
                WHEN 'hot' THEN 0
                WHEN 'opportunity' THEN 1
                WHEN 'early' THEN 2
                ELSE 3
            END,
            CASE recommended_action WHEN 'write_now' THEN 0 WHEN 'watch' THEN 1 ELSE 2 END,
            velocity DESC, demand_score DESC, last_evaluated_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    columns = [str(column[0]) for column in con.description]
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(zip(columns, row))
        for key, fallback in (("source_spread_json", []), ("source_counts_json", {})):
            try:
                item[key.removesuffix("_json")] = json.loads(
                    item.pop(key) or json.dumps(fallback)
                )
            except (TypeError, json.JSONDecodeError):
                item[key.removesuffix("_json")] = fallback
        status = str(item.get("radar_status") or STATUS_EARLY)
        action = str(item.get("recommended_action") or ACTION_WATCH)
        item["status_label"] = STATUS_LABELS.get(status, status)
        item["action_label"] = ACTION_LABELS.get(action, action)
        result.append(item)
    return result


def get_opportunity_summary(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    ensure_opportunity_radar_schema(con)
    rows = con.execute(
        """
        SELECT radar_status, COUNT(*)
        FROM trend_opportunity_watchlist
        GROUP BY radar_status
        """
    ).fetchall()
    counts = {status: 0 for status in STATUS_LABELS}
    for status, count in rows:
        key = str(status or "")
        if key in counts:
            counts[key] = int(count or 0)
    return counts
