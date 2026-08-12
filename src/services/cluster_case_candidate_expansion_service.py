"""군집 사례 진단의 최근 후보 편향을 핵심어 기반 읽기 조회로 보완합니다."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

import duckdb

from src.services.cluster_case_diagnostic_service import (
    MAX_ITEMS_PER_CANDIDATE,
    ClusterCaseDiagnosticReport,
    _best_candidate_for_items,
    _load_candidate_clusters,
    _load_json_list,
    _prepare_item,
    _reason_for,
    _rows_to_dicts,
    analyze_cluster_cases,
)
from src.services.trend_normalization import tokenize


MAX_EXPANDED_CANDIDATE_CLUSTERS = 700
MAX_SEED_TERMS = 40
_GENERIC_TERMS = {
    "관련",
    "공개",
    "발표",
    "최신",
    "오늘",
    "내일",
    "정보",
    "정리",
    "분석",
    "이슈",
    "소식",
    "뉴스",
}


def _seed_terms(report: ClusterCaseDiagnosticReport) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    titles = [
        *(case.raw_title for case in report.unclustered_cases),
        *(case.canonical_title for case in report.single_source_cases),
    ]
    for title in titles:
        for token in tokenize(str(title or "")):
            clean = str(token or "").strip().casefold()
            if len(clean) < 2 or clean in _GENERIC_TERMS:
                continue
            counts[clean] = counts.get(clean, 0) + 1
    ordered = sorted(counts, key=lambda value: (-len(value), counts[value], value))
    return tuple(ordered[:MAX_SEED_TERMS])


def _load_matching_candidate_clusters(
    con: duckdb.DuckDBPyConnection,
    *,
    since,
    terms: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not terms:
        return []
    conditions = " OR ".join(
        "strpos(lower(canonical_title), ?) > 0" for _ in terms
    )
    rows = _rows_to_dicts(
        con.execute(
            f"""
            SELECT cluster_id, canonical_title, source_types_json,
                   item_count, first_seen_at, last_seen_at
            FROM trend_clusters
            WHERE COALESCE(last_seen_at, calculated_at) >= ?
              AND ({conditions})
            ORDER BY COALESCE(last_seen_at, calculated_at) DESC, cluster_id
            LIMIT ?
            """,
            [since, *terms, MAX_EXPANDED_CANDIDATE_CLUSTERS],
        )
    )
    if not rows:
        return []

    cluster_ids = [str(row["cluster_id"]) for row in rows]
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
                           ORDER BY COALESCE(
                               si.published_at,
                               si.observed_at,
                               si.last_imported_at,
                               si.imported_at
                           ) DESC,
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
    for row in rows:
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


def _load_source_item(con, source_item_id: str) -> dict[str, Any] | None:
    rows = _rows_to_dicts(
        con.execute(
            """
            SELECT source_item_id, source_type, raw_title, source_url,
                   normalized_url, source_name, published_at, observed_at,
                   signal_value, metadata_json, last_imported_at, imported_at
            FROM source_items
            WHERE source_item_id = ?
            """,
            [source_item_id],
        )
    )
    return _prepare_item(rows[0]) if rows else None


def _load_cluster_items(con, cluster_id: str) -> list[dict[str, Any]]:
    rows = _rows_to_dicts(
        con.execute(
            """
            SELECT si.source_item_id, si.source_type, si.raw_title,
                   si.source_url, si.normalized_url, si.source_name,
                   si.published_at, si.observed_at, si.signal_value,
                   si.metadata_json, si.last_imported_at, si.imported_at
            FROM trend_cluster_items ci
            JOIN source_items si ON si.source_item_id = ci.source_item_id
            WHERE ci.cluster_id = ?
            ORDER BY COALESCE(
                si.published_at,
                si.observed_at,
                si.last_imported_at,
                si.imported_at
            ) DESC,
            si.source_item_id
            LIMIT ?
            """,
            [cluster_id, MAX_ITEMS_PER_CANDIDATE],
        )
    )
    return [_prepare_item(row) for row in rows]


def _better_candidate(current, expanded):
    if expanded is None:
        return current
    if current is None or expanded.similarity > current.similarity:
        return expanded
    return current


def expand_cluster_case_candidates(
    con: duckdb.DuckDBPyConnection,
    report: ClusterCaseDiagnosticReport,
) -> ClusterCaseDiagnosticReport:
    """Expand candidate lookup without changing any cluster or source row."""
    terms = _seed_terms(report)
    if not terms:
        return report
    since = report.generated_at - timedelta(hours=report.lookback_hours)
    recent = _load_candidate_clusters(con, since=since)
    matched = _load_matching_candidate_clusters(con, since=since, terms=terms)
    candidate_map = {
        str(cluster["cluster_id"]): cluster for cluster in [*recent, *matched]
    }
    candidates = list(candidate_map.values())

    unclustered_cases = []
    for case in report.unclustered_cases:
        item = _load_source_item(con, case.source_item_id)
        expanded = (
            _best_candidate_for_items(
                [item],
                candidates,
                required_other_source=case.source_type,
            )
            if item is not None
            else None
        )
        candidate = _better_candidate(case.candidate, expanded)
        editorial_tokens = set(item.get("editorial_identity_tokens") or ()) if item else set()
        reason_code, reason_label = _reason_for(
            source_type=case.source_type,
            editorial_tokens=editorial_tokens,
            candidate=candidate,
        )
        unclustered_cases.append(
            replace(
                case,
                candidate=candidate,
                reason_code=reason_code,
                reason_label=reason_label,
            )
        )

    single_cases = []
    for case in report.single_source_cases:
        items = _load_cluster_items(con, case.cluster_id)
        expanded = _best_candidate_for_items(
            items,
            candidates,
            excluded_cluster_id=case.cluster_id,
            required_other_source=case.source_type,
        )
        candidate = _better_candidate(case.candidate, expanded)
        tokens: set[str] = set()
        for item in items:
            tokens.update(item.get("editorial_identity_tokens") or ())
        reason_code, reason_label = _reason_for(
            source_type=case.source_type,
            editorial_tokens=tokens,
            candidate=candidate,
        )
        single_cases.append(
            replace(
                case,
                candidate=candidate,
                reason_code=reason_code,
                reason_label=reason_label,
            )
        )

    return replace(
        report,
        unclustered_cases=tuple(unclustered_cases),
        single_source_cases=tuple(single_cases),
    )


def analyze_cluster_cases_with_expanded_candidates(
    con: duckdb.DuckDBPyConnection,
    **kwargs,
) -> ClusterCaseDiagnosticReport:
    report = analyze_cluster_cases(con, **kwargs)
    return expand_cluster_case_candidates(con, report)
