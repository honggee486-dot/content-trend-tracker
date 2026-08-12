"""군집 밖 원문과 단일 출처 군집의 유사 후보를 읽기 전용으로 진단합니다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from typing import Any

import duckdb

from src.services.source_diversity_service import LOOKBACK_OPTIONS, SOURCE_LABELS
from src.services.trend_clustering_fallback_service import (
    calculate_item_similarity,
)
from src.services.trend_discovery_service import (
    _calendar_identity_tokens,
    _canonical_title,
    _editorial_identity_tokens,
    _item_time,
    _query_is_supported_by_item,
)
from src.services.trend_normalization import (
    compact_title,
    identity_tokens,
    normalize_title,
    normalize_url,
    strip_collection_scope,
    tokenize,
)


CLUSTER_MERGE_THRESHOLD = 0.72
MAX_UNCLUSTERED_SCAN = 300
MAX_SINGLE_CLUSTER_SCAN = 200
MAX_CANDIDATE_CLUSTERS = 300
MAX_ITEMS_PER_CANDIDATE = 6
DEFAULT_DISPLAY_LIMIT = 40


@dataclass(frozen=True)
class SimilarClusterCandidate:
    cluster_id: str
    canonical_title: str
    source_types: tuple[str, ...]
    similarity: float
    shared_tokens: tuple[str, ...]
    time_gap_hours: float | None

    @property
    def source_labels(self) -> tuple[str, ...]:
        return tuple(SOURCE_LABELS.get(value, value) for value in self.source_types)


@dataclass(frozen=True)
class UnclusteredItemCase:
    source_item_id: str
    source_type: str
    raw_title: str
    normalized_title: str
    source_url: str
    event_at: datetime | None
    signal_value: float
    candidate: SimilarClusterCandidate | None
    reason_code: str
    reason_label: str

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source_type, self.source_type or "기타")


@dataclass(frozen=True)
class SingleSourceClusterCase:
    cluster_id: str
    canonical_title: str
    source_type: str
    item_count: int
    last_seen_at: datetime | None
    sample_titles: tuple[str, ...]
    candidate: SimilarClusterCandidate | None
    reason_code: str
    reason_label: str

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source_type, self.source_type or "기타")


@dataclass(frozen=True)
class ClusterCaseDiagnosticReport:
    lookback_hours: int
    generated_at: datetime
    unclustered_total: int
    single_source_cluster_total: int
    unclustered_cases: tuple[UnclusteredItemCase, ...]
    single_source_cases: tuple[SingleSourceClusterCase, ...]

    @property
    def lookback_label(self) -> str:
        return LOOKBACK_OPTIONS.get(self.lookback_hours, f"최근 {self.lookback_hours}시간")


def _load_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _load_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _effective_time(row: dict[str, Any]) -> datetime | None:
    for key in ("published_at", "observed_at", "last_imported_at", "imported_at"):
        value = row.get(key)
        if isinstance(value, datetime):
            return value
    return None


def _prepare_item(row: dict[str, Any]) -> dict[str, Any]:
    metadata = _load_json_dict(row.get("metadata_json"))
    raw_title = strip_collection_scope(str(row.get("raw_title") or ""))
    item_title = strip_collection_scope(
        str(metadata.get("item_title") or raw_title)
    )
    query = strip_collection_scope(
        str(metadata.get("discovery_query") or metadata.get("keyword") or "")
    )
    prepared = dict(row)
    prepared.update(
        {
            "metadata": metadata,
            "raw_title": raw_title,
            "item_title": item_title,
            "query": query,
            "query_identity_tokens": identity_tokens(query),
        }
    )
    comparison_title = _canonical_title(prepared) or item_title or raw_title
    ids = identity_tokens(comparison_title)
    prepared.update(
        {
            "canonical_title": comparison_title,
            "normalized_title": normalize_title(comparison_title),
            "compact_title": compact_title(comparison_title),
            "identity_tokens": ids,
            "editorial_identity_tokens": _editorial_identity_tokens(ids),
            "calendar_identity_tokens": _calendar_identity_tokens(ids),
            "tokens": {
                token for token in tokenize(comparison_title) if len(token) >= 2
            },
            "normalized_url": str(row.get("normalized_url") or "")
            or normalize_url(str(row.get("source_url") or "")),
        }
    )
    prepared["query_supported"] = _query_is_supported_by_item(prepared)
    return prepared


def _rows_to_dicts(cursor) -> list[dict[str, Any]]:
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _load_recent_unclustered_items(
    con: duckdb.DuckDBPyConnection,
    *,
    since: datetime,
) -> tuple[int, list[dict[str, Any]]]:
    where_sql = "COALESCE(si.published_at, si.observed_at, si.last_imported_at, si.imported_at) >= ?"
    total = int(
        con.execute(
            f"""
            SELECT COUNT(*)
            FROM source_items si
            LEFT JOIN trend_cluster_items ci ON ci.source_item_id = si.source_item_id
            WHERE {where_sql}
              AND ci.source_item_id IS NULL
            """,
            [since],
        ).fetchone()[0]
    )
    rows = _rows_to_dicts(
        con.execute(
            f"""
            SELECT si.source_item_id, si.source_type, si.raw_title,
                   si.source_url, si.normalized_url, si.source_name,
                   si.published_at, si.observed_at, si.signal_value,
                   si.metadata_json, si.last_imported_at, si.imported_at
            FROM source_items si
            LEFT JOIN trend_cluster_items ci ON ci.source_item_id = si.source_item_id
            WHERE {where_sql}
              AND ci.source_item_id IS NULL
            ORDER BY COALESCE(si.published_at, si.observed_at, si.last_imported_at, si.imported_at) DESC,
                     si.source_item_id
            LIMIT ?
            """,
            [since, MAX_UNCLUSTERED_SCAN],
        )
    )
    return total, [_prepare_item(row) for row in rows]


def _load_candidate_clusters(
    con: duckdb.DuckDBPyConnection,
    *,
    since: datetime,
) -> list[dict[str, Any]]:
    cluster_rows = _rows_to_dicts(
        con.execute(
            """
            SELECT cluster_id, canonical_title, source_types_json,
                   item_count, first_seen_at, last_seen_at
            FROM trend_clusters
            WHERE COALESCE(last_seen_at, calculated_at) >= ?
            ORDER BY COALESCE(last_seen_at, calculated_at) DESC, cluster_id
            LIMIT ?
            """,
            [since, MAX_CANDIDATE_CLUSTERS],
        )
    )
    if not cluster_rows:
        return []

    cluster_ids = [str(row["cluster_id"]) for row in cluster_rows]
    placeholders = ", ".join("?" for _ in cluster_ids)
    item_rows = _rows_to_dicts(
        con.execute(
            f"""
            SELECT * EXCLUDE item_rank
            FROM (
                SELECT ci.cluster_id, si.source_item_id, si.source_type,
                       si.raw_title, si.source_url, si.normalized_url,
                       si.source_name, si.published_at, si.observed_at,
                       si.signal_value, si.metadata_json,
                       si.last_imported_at, si.imported_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY ci.cluster_id
                           ORDER BY COALESCE(si.published_at, si.observed_at, si.last_imported_at, si.imported_at) DESC,
                                    si.source_item_id
                       ) AS item_rank
                FROM trend_cluster_items ci
                JOIN source_items si ON si.source_item_id = ci.source_item_id
                WHERE ci.cluster_id IN ({placeholders})
            ) ranked
            WHERE item_rank <= ?
            ORDER BY cluster_id, item_rank
            """,
            [*cluster_ids, MAX_ITEMS_PER_CANDIDATE],
        )
    )
    items_by_cluster: dict[str, list[dict[str, Any]]] = {
        cluster_id: [] for cluster_id in cluster_ids
    }
    for row in item_rows:
        items_by_cluster[str(row["cluster_id"])].append(_prepare_item(row))

    result: list[dict[str, Any]] = []
    for row in cluster_rows:
        cluster_id = str(row["cluster_id"])
        source_types = tuple(
            sorted(
                {
                    str(value)
                    for value in _load_json_list(row.get("source_types_json"))
                    if str(value)
                }
                or {
                    str(item.get("source_type") or "")
                    for item in items_by_cluster.get(cluster_id, [])
                    if str(item.get("source_type") or "")
                }
            )
        )
        result.append(
            {
                **row,
                "cluster_id": cluster_id,
                "source_types": source_types,
                "items": items_by_cluster.get(cluster_id, []),
            }
        )
    return result


def _best_candidate_for_items(
    items: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    *,
    excluded_cluster_id: str = "",
    required_other_source: str = "",
) -> SimilarClusterCandidate | None:
    best_cluster: dict[str, Any] | None = None
    best_left: dict[str, Any] | None = None
    best_right: dict[str, Any] | None = None
    best_score = 0.0

    for cluster in clusters:
        if str(cluster.get("cluster_id") or "") == excluded_cluster_id:
            continue
        source_types = set(cluster.get("source_types") or ())
        if required_other_source and not (source_types - {required_other_source}):
            continue
        for left in items:
            for right in cluster.get("items") or []:
                score = calculate_item_similarity(left, right)
                if score > best_score:
                    best_score = score
                    best_cluster = cluster
                    best_left = left
                    best_right = right

    if best_cluster is None or best_left is None or best_right is None:
        return None

    left_tokens = set(best_left.get("editorial_identity_tokens") or ())
    right_tokens = set(best_right.get("editorial_identity_tokens") or ())
    left_time = _item_time(best_left)
    right_time = _item_time(best_right)
    gap_hours: float | None = None
    if left_time != datetime.min and right_time != datetime.min:
        gap_hours = abs((left_time - right_time).total_seconds()) / 3600.0

    return SimilarClusterCandidate(
        cluster_id=str(best_cluster["cluster_id"]),
        canonical_title=str(best_cluster.get("canonical_title") or ""),
        source_types=tuple(best_cluster.get("source_types") or ()),
        similarity=float(best_score),
        shared_tokens=tuple(sorted(left_tokens & right_tokens)),
        time_gap_hours=gap_hours,
    )


def _reason_for(
    *,
    source_type: str,
    editorial_tokens: set[str],
    candidate: SimilarClusterCandidate | None,
) -> tuple[str, str]:
    if candidate is None or candidate.similarity < 0.35:
        return "no_candidate", "유사한 기존 군집 후보가 뚜렷하지 않음"
    if source_type == "google_trends" and len(editorial_tokens) <= 1:
        return "short_search_term", "검색어형 짧은 제목이라 기사·영상 제목과 직접 비교가 어려움"
    if not candidate.shared_tokens:
        return "weak_keyword_overlap", "공통 핵심어가 부족해 제목 유사도만으로 병합하기 어려움"
    if candidate.time_gap_hours is not None and candidate.time_gap_hours > 168:
        return "large_time_gap", "후보 군집과 게시·관측 시각 차이가 큼"
    if candidate.similarity < CLUSTER_MERGE_THRESHOLD:
        return (
            "below_threshold",
            f"최고 유사도 {candidate.similarity * 100:.1f}%로 현재 병합 기준 72% 미달",
        )
    return (
        "analysis_scope_or_timing",
        "유사도는 충분해 분석 입력 상한·품질 필터·후보 탐색 경로·재계산 시점 차이 확인 필요",
    )


def _single_source_cluster_rows(
    con: duckdb.DuckDBPyConnection,
    *,
    since: datetime,
) -> tuple[int, list[dict[str, Any]]]:
    total = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM trend_clusters
            WHERE source_type_count = 1
              AND COALESCE(last_seen_at, calculated_at) >= ?
            """,
            [since],
        ).fetchone()[0]
    )
    rows = _rows_to_dicts(
        con.execute(
            """
            SELECT cluster_id, canonical_title, item_count,
                   source_types_json, first_seen_at, last_seen_at
            FROM trend_clusters
            WHERE source_type_count = 1
              AND COALESCE(last_seen_at, calculated_at) >= ?
            ORDER BY COALESCE(last_seen_at, calculated_at) DESC, cluster_id
            LIMIT ?
            """,
            [since, MAX_SINGLE_CLUSTER_SCAN],
        )
    )
    return total, rows


def analyze_cluster_cases(
    con: duckdb.DuckDBPyConnection,
    *,
    lookback_hours: int = 72,
    now: datetime | None = None,
    display_limit: int = DEFAULT_DISPLAY_LIMIT,
) -> ClusterCaseDiagnosticReport:
    """Return read-only cases that help explain fragmented or unclustered signals."""
    lookback = max(1, min(int(lookback_hours), 24 * 30))
    generated_at = now or datetime.now()
    since = generated_at - timedelta(hours=lookback)
    limit = max(1, min(int(display_limit), 100))

    unclustered_total, unclustered_items = _load_recent_unclustered_items(
        con,
        since=since,
    )
    clusters = _load_candidate_clusters(con, since=since)

    unclustered_cases: list[UnclusteredItemCase] = []
    for item in unclustered_items[:limit]:
        candidate = _best_candidate_for_items(
            [item],
            clusters,
            required_other_source=str(item.get("source_type") or ""),
        )
        editorial_tokens = set(item.get("editorial_identity_tokens") or ())
        reason_code, reason_label = _reason_for(
            source_type=str(item.get("source_type") or ""),
            editorial_tokens=editorial_tokens,
            candidate=candidate,
        )
        unclustered_cases.append(
            UnclusteredItemCase(
                source_item_id=str(item.get("source_item_id") or ""),
                source_type=str(item.get("source_type") or ""),
                raw_title=str(item.get("raw_title") or ""),
                normalized_title=str(item.get("normalized_title") or ""),
                source_url=str(item.get("source_url") or ""),
                event_at=_effective_time(item),
                signal_value=float(item.get("signal_value") or 0.0),
                candidate=candidate,
                reason_code=reason_code,
                reason_label=reason_label,
            )
        )

    single_total, single_rows = _single_source_cluster_rows(con, since=since)
    cluster_map = {str(cluster["cluster_id"]): cluster for cluster in clusters}
    single_cases: list[SingleSourceClusterCase] = []
    for row in single_rows[:limit]:
        cluster_id = str(row["cluster_id"])
        current = cluster_map.get(cluster_id)
        if current is None:
            continue
        items = list(current.get("items") or [])
        source_types = tuple(current.get("source_types") or ())
        source_type = str(source_types[0]) if source_types else ""
        candidate = _best_candidate_for_items(
            items,
            clusters,
            excluded_cluster_id=cluster_id,
            required_other_source=source_type,
        )
        union_tokens: set[str] = set()
        for item in items:
            union_tokens.update(item.get("editorial_identity_tokens") or ())
        reason_code, reason_label = _reason_for(
            source_type=source_type,
            editorial_tokens=union_tokens,
            candidate=candidate,
        )
        single_cases.append(
            SingleSourceClusterCase(
                cluster_id=cluster_id,
                canonical_title=str(row.get("canonical_title") or ""),
                source_type=source_type,
                item_count=int(row.get("item_count") or 0),
                last_seen_at=row.get("last_seen_at")
                if isinstance(row.get("last_seen_at"), datetime)
                else None,
                sample_titles=tuple(
                    str(item.get("raw_title") or "") for item in items[:5]
                ),
                candidate=candidate,
                reason_code=reason_code,
                reason_label=reason_label,
            )
        )

    return ClusterCaseDiagnosticReport(
        lookback_hours=lookback,
        generated_at=generated_at,
        unclustered_total=unclustered_total,
        single_source_cluster_total=single_total,
        unclustered_cases=tuple(unclustered_cases),
        single_source_cases=tuple(single_cases),
    )
