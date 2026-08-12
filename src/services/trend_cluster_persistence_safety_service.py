from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable


def normalize_trend_ranking_calculation(calculation: Any) -> Any:
    """군집 저장 직전 중복 기본키와 중복 연결을 멱등하게 정리합니다.

    같은 cluster_id가 여러 계산 행으로 만들어진 경우 하나의 군집 정체성으로
    취급하고 원문 연결은 합집합으로 보존합니다. 다음 배치에서 전체 점수가 다시
    계산되므로 이번 저장에서는 데이터 유실과 트랜잭션 실패 방지를 우선합니다.
    """
    cluster_rows = [dict(row) for row in calculation.cluster_rows]
    cluster_item_rows = [dict(row) for row in calculation.cluster_item_rows]

    unique_clusters: dict[str, dict[str, Any]] = {}
    duplicate_cluster_rows = 0
    for row in cluster_rows:
        cluster_id = str(row.get("cluster_id") or "").strip()
        if not cluster_id:
            continue
        current = unique_clusters.get(cluster_id)
        if current is None:
            unique_clusters[cluster_id] = row
            continue
        duplicate_cluster_rows += 1
        current_count = int(current.get("item_count") or 0)
        candidate_count = int(row.get("item_count") or 0)
        if candidate_count > current_count:
            unique_clusters[cluster_id] = row

    valid_cluster_ids = set(unique_clusters)
    unique_items: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_item_rows = 0
    for row in cluster_item_rows:
        cluster_id = str(row.get("cluster_id") or "").strip()
        source_item_id = str(row.get("source_item_id") or "").strip()
        if not cluster_id or not source_item_id or cluster_id not in valid_cluster_ids:
            continue
        key = (cluster_id, source_item_id)
        if key in unique_items:
            duplicate_item_rows += 1
            continue
        unique_items[key] = row

    item_counts: dict[str, int] = {cluster_id: 0 for cluster_id in valid_cluster_ids}
    for cluster_id, _source_item_id in unique_items:
        item_counts[cluster_id] += 1
    for cluster_id, row in unique_clusters.items():
        row["item_count"] = item_counts.get(cluster_id, 0)

    if duplicate_cluster_rows <= 0 and duplicate_item_rows <= 0:
        return calculation

    batch_log = dict(calculation.batch_log or {})
    batch_log["duplicate_cluster_rows_collapsed"] = duplicate_cluster_rows
    batch_log["duplicate_cluster_item_rows_collapsed"] = duplicate_item_rows
    batch_log["persistence_warning"] = (
        f"중복 군집 행 {duplicate_cluster_rows}개와 "
        f"중복 원문 연결 {duplicate_item_rows}개를 저장 전에 정리했습니다."
    )

    ai_clustering = dict(calculation.ai_clustering or {})
    ai_clustering["persistence_deduplicated"] = True
    ai_clustering["duplicate_cluster_rows_collapsed"] = duplicate_cluster_rows
    ai_clustering["duplicate_cluster_item_rows_collapsed"] = duplicate_item_rows

    return replace(
        calculation,
        cluster_rows=tuple(unique_clusters.values()),
        cluster_item_rows=tuple(unique_items.values()),
        ai_clustering=ai_clustering,
        batch_log=batch_log,
    )


def finalize_prepared_trend_rankings_safely(
    con: Any,
    calculation: Any,
    *,
    finalizer: Callable[[Any, Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """기존 최종 저장기를 호출하되 저장 입력의 기본키 중복을 먼저 제거합니다."""
    if finalizer is None:
        from src.services.trend_discovery_service import (
            finalize_prepared_trend_rankings as finalizer,
        )

    return finalizer(con, normalize_trend_ranking_calculation(calculation))
