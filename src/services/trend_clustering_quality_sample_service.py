"""최신 2단계 군집 작업의 결과 품질을 읽기 전용으로 재구성합니다."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

import duckdb


_REQUIRED_TABLE_COLUMNS = {
    "trend_clustering_jobs": {
        "job_id",
        "model_name",
        "processed_units",
        "existing_links",
        "new_clusters",
        "uncertain_units",
        "needs_review_items",
        "started_at",
        "finished_at",
    },
    "trend_cluster_processing": {
        "source_item_id",
        "model_name",
        "first_stage_key",
        "cluster_id",
        "status",
        "attempt_count",
        "last_error",
        "processed_at",
        "updated_at",
    },
    "trend_clusters": {
        "cluster_id",
        "canonical_title",
        "calculated_at",
    },
    "trend_cluster_items": {
        "cluster_id",
        "source_item_id",
    },
    "source_items": {
        "source_item_id",
        "source_type",
        "raw_title",
        "source_name",
        "published_at",
    },
}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat(sep=" ", timespec="seconds"))
    return str(value)


def _table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    return table_name in {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}


def _table_columns(con: duckdb.DuckDBPyConnection, table_name: str) -> set[str]:
    return {
        str(row[1])
        for row in con.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    }


def _unavailable(
    reason: str,
    *,
    job_id: str = "",
    missing: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "reason": str(reason),
        "missing": list(missing or []),
        "job_id": str(job_id or ""),
        "snapshot_matches_job": False,
        "reconstruction_reliable": False,
        "processed_candidate_count": 0,
        "singleton_candidate_count": 0,
        "singleton_candidate_percent": 0.0,
        "multi_candidate_cluster_count": 0,
        "multi_candidate_candidate_count": 0,
        "existing_link_cluster_count": 0,
        "existing_link_candidate_count": 0,
        "uncertain_candidate_count": 0,
        "needs_review_source_item_count": 0,
        "retry_source_item_count": 0,
        "consistency": {},
        "samples": {
            "multi_candidate_clusters": [],
            "existing_link_clusters": [],
            "singleton_candidates": [],
            "unresolved_candidates": [],
        },
    }


def _item_payload(row: dict[str, Any], *, in_job: bool) -> dict[str, Any]:
    return {
        "source_item_id": str(row.get("source_item_id") or ""),
        "source_type": str(row.get("source_type") or ""),
        "title": str(row.get("raw_title") or ""),
        "source_name": str(row.get("source_name") or ""),
        "published_at": _iso(row.get("published_at")),
        "in_job": bool(in_job),
    }


def build_trend_clustering_quality_sample(
    con: duckdb.DuckDBPyConnection,
    *,
    job_id: str,
    sample_limit: int = 8,
) -> dict[str, Any]:
    """저장된 최신 군집 결과만으로 단독·다중·기존 연결·불확실 표본을 만듭니다.

    군집을 다시 실행하거나 API를 호출하지 않습니다. 현재 ``trend_clusters`` 스냅샷이
    지정 작업 시각과 일치할 때만 신규/기존 연결 재구성을 신뢰 가능한 것으로 표시합니다.
    """
    bounded_sample_limit = max(1, min(int(sample_limit), 20))
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        return _unavailable("job_not_found")

    missing_tables = [
        table_name
        for table_name in _REQUIRED_TABLE_COLUMNS
        if not _table_exists(con, table_name)
    ]
    if missing_tables:
        return _unavailable(
            "missing_tables",
            job_id=normalized_job_id,
            missing=missing_tables,
        )

    missing_columns: list[str] = []
    for table_name, required_columns in _REQUIRED_TABLE_COLUMNS.items():
        existing_columns = _table_columns(con, table_name)
        missing_columns.extend(
            f"{table_name}.{column_name}"
            for column_name in sorted(required_columns - existing_columns)
        )
    if missing_columns:
        return _unavailable(
            "missing_columns",
            job_id=normalized_job_id,
            missing=missing_columns,
        )

    cursor = con.execute(
        """
        SELECT job_id, model_name, processed_units, existing_links, new_clusters,
               uncertain_units, needs_review_items, started_at, finished_at
        FROM trend_clustering_jobs
        WHERE job_id = ?
        LIMIT 1
        """,
        [normalized_job_id],
    )
    job_row = cursor.fetchone()
    if job_row is None:
        return _unavailable("job_not_found", job_id=normalized_job_id)
    job = dict(zip([str(item[0]) for item in cursor.description], job_row))
    started_at = job.get("started_at")
    finished_at = job.get("finished_at")
    if started_at is None or finished_at is None:
        return _unavailable("job_timing_incomplete", job_id=normalized_job_id)

    window_end = finished_at + timedelta(seconds=5)
    cluster_times = con.execute(
        "SELECT MIN(calculated_at), MAX(calculated_at) FROM trend_clusters"
    ).fetchone()
    cluster_min_at = cluster_times[0] if cluster_times else None
    cluster_max_at = cluster_times[1] if cluster_times else None
    snapshot_matches_job = bool(
        cluster_min_at is not None
        and cluster_max_at is not None
        and cluster_min_at >= started_at
        and cluster_max_at <= window_end
    )

    processing_cursor = con.execute(
        """
        SELECT p.source_item_id, p.first_stage_key, p.cluster_id, p.status,
               p.attempt_count, p.last_error, p.processed_at, p.updated_at,
               s.source_type, s.raw_title, s.source_name, s.published_at
        FROM trend_cluster_processing p
        JOIN source_items s ON s.source_item_id = p.source_item_id
        WHERE p.model_name = ?
          AND COALESCE(p.updated_at, p.processed_at) >= ?
          AND COALESCE(p.updated_at, p.processed_at) <= ?
        ORDER BY COALESCE(p.updated_at, p.processed_at), p.source_item_id
        """,
        [str(job.get("model_name") or ""), started_at, window_end],
    )
    processing_columns = [str(item[0]) for item in processing_cursor.description]
    processing_rows = [
        dict(zip(processing_columns, values))
        for values in processing_cursor.fetchall()
    ]
    if not processing_rows:
        result = _unavailable(
            "processing_rows_not_found",
            job_id=normalized_job_id,
        )
        result.update(
            {
                "snapshot_matches_job": snapshot_matches_job,
                "cluster_snapshot_min_at": _iso(cluster_min_at),
                "cluster_snapshot_max_at": _iso(cluster_max_at),
                "job_started_at": _iso(started_at),
                "job_finished_at": _iso(finished_at),
            }
        )
        return result

    member_cursor = con.execute(
        """
        WITH job_clusters AS (
            SELECT DISTINCT cluster_id
            FROM trend_cluster_processing
            WHERE model_name = ?
              AND COALESCE(updated_at, processed_at) >= ?
              AND COALESCE(updated_at, processed_at) <= ?
              AND COALESCE(cluster_id, '') <> ''
        )
        SELECT tci.cluster_id, tc.canonical_title, s.source_item_id,
               s.source_type, s.raw_title, s.source_name, s.published_at
        FROM trend_cluster_items tci
        JOIN job_clusters jc ON jc.cluster_id = tci.cluster_id
        JOIN trend_clusters tc ON tc.cluster_id = tci.cluster_id
        JOIN source_items s ON s.source_item_id = tci.source_item_id
        ORDER BY tci.cluster_id, s.source_item_id
        """,
        [str(job.get("model_name") or ""), started_at, window_end],
    )
    member_columns = [str(item[0]) for item in member_cursor.description]
    members_by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for values in member_cursor.fetchall():
        row = dict(zip(member_columns, values))
        members_by_cluster[str(row.get("cluster_id") or "")].append(row)

    all_job_source_ids = {
        str(row.get("source_item_id") or "")
        for row in processing_rows
        if str(row.get("source_item_id") or "")
    }
    candidates: dict[str, dict[str, Any]] = {}
    missing_first_stage_key_count = 0
    for row in processing_rows:
        source_id = str(row.get("source_item_id") or "")
        first_stage_key = str(row.get("first_stage_key") or "").strip()
        if not first_stage_key:
            missing_first_stage_key_count += 1
            first_stage_key = f"source:{source_id}"
        candidate = candidates.setdefault(
            first_stage_key,
            {
                "first_stage_key": first_stage_key,
                "rows": [],
                "statuses": set(),
                "cluster_ids": set(),
            },
        )
        candidate["rows"].append(row)
        candidate["statuses"].add(str(row.get("status") or ""))
        cluster_id = str(row.get("cluster_id") or "")
        if cluster_id:
            candidate["cluster_ids"].add(cluster_id)

    processed_candidates: dict[str, dict[str, Any]] = {}
    unresolved_candidates: dict[str, dict[str, Any]] = {}
    ambiguous_candidate_cluster_count = 0
    for key, candidate in candidates.items():
        statuses = set(candidate["statuses"])
        cluster_ids = set(candidate["cluster_ids"])
        if statuses == {"processed"} and len(cluster_ids) == 1:
            processed_candidates[key] = candidate
        else:
            if statuses == {"processed"} and len(cluster_ids) != 1:
                ambiguous_candidate_cluster_count += 1
            unresolved_candidates[key] = candidate

    candidate_keys_by_cluster: dict[str, set[str]] = defaultdict(set)
    job_source_ids_by_cluster: dict[str, set[str]] = defaultdict(set)
    for key, candidate in processed_candidates.items():
        cluster_id = next(iter(candidate["cluster_ids"]))
        candidate_keys_by_cluster[cluster_id].add(key)
        job_source_ids_by_cluster[cluster_id].update(
            str(row.get("source_item_id") or "")
            for row in candidate["rows"]
            if str(row.get("source_item_id") or "")
        )

    cluster_groups = {
        "multi_candidate_clusters": [],
        "existing_link_clusters": [],
        "singleton_candidates": [],
    }
    existing_link_candidate_count = 0
    multi_candidate_candidate_count = 0
    for cluster_id, candidate_keys in candidate_keys_by_cluster.items():
        current_members = members_by_cluster.get(cluster_id, [])
        current_member_ids = {
            str(row.get("source_item_id") or "")
            for row in current_members
            if str(row.get("source_item_id") or "")
        }
        outside_job_ids = current_member_ids - all_job_source_ids
        if outside_job_ids:
            category = "existing_link_clusters"
            existing_link_candidate_count += len(candidate_keys)
        elif len(candidate_keys) >= 2:
            category = "multi_candidate_clusters"
            multi_candidate_candidate_count += len(candidate_keys)
        else:
            category = "singleton_candidates"

        canonical_title = ""
        if current_members:
            canonical_title = str(current_members[0].get("canonical_title") or "")
        if not canonical_title:
            first_candidate = processed_candidates[next(iter(candidate_keys))]
            canonical_title = str(first_candidate["rows"][0].get("raw_title") or "")

        job_member_ids = job_source_ids_by_cluster.get(cluster_id, set())
        ordered_members = sorted(
            current_members,
            key=lambda row: (
                str(row.get("source_item_id") or "") not in job_member_ids,
                str(row.get("raw_title") or ""),
                str(row.get("source_item_id") or ""),
            ),
        )
        cluster_groups[category].append(
            {
                "cluster_id": cluster_id,
                "canonical_title": canonical_title,
                "job_candidate_count": len(candidate_keys),
                "job_source_item_count": len(job_member_ids),
                "current_cluster_item_count": len(current_member_ids),
                "preexisting_item_count": len(outside_job_ids),
                "items": [
                    _item_payload(
                        row,
                        in_job=str(row.get("source_item_id") or "") in all_job_source_ids,
                    )
                    for row in ordered_members[:4]
                ],
            }
        )

    for category in cluster_groups:
        cluster_groups[category].sort(
            key=lambda row: (
                -int(row.get("job_candidate_count") or 0),
                str(row.get("canonical_title") or ""),
                str(row.get("cluster_id") or ""),
            )
        )

    unresolved_samples: list[dict[str, Any]] = []
    for candidate in unresolved_candidates.values():
        rows = list(candidate["rows"])
        statuses = sorted({str(row.get("status") or "") for row in rows})
        rows.sort(
            key=lambda row: (
                str(row.get("status") or "") != "needs_review",
                -int(row.get("attempt_count") or 0),
                str(row.get("raw_title") or ""),
            )
        )
        unresolved_samples.append(
            {
                "statuses": statuses,
                "source_item_count": len(rows),
                "maximum_attempt_count": max(
                    (int(row.get("attempt_count") or 0) for row in rows),
                    default=0,
                ),
                "last_error": next(
                    (
                        str(row.get("last_error") or "")
                        for row in rows
                        if str(row.get("last_error") or "")
                    ),
                    "",
                ),
                "items": [
                    _item_payload(row, in_job=True)
                    for row in rows[:4]
                ],
            }
        )
    unresolved_samples.sort(
        key=lambda row: (
            "needs_review" not in row["statuses"],
            -int(row["maximum_attempt_count"]),
            str(row["items"][0]["title"] if row["items"] else ""),
        )
    )

    processed_candidate_count = len(processed_candidates)
    singleton_candidate_count = len(cluster_groups["singleton_candidates"])
    multi_candidate_cluster_count = len(cluster_groups["multi_candidate_clusters"])
    reconstructed_new_clusters = (
        singleton_candidate_count + multi_candidate_cluster_count
    )
    uncertain_candidate_count = len(unresolved_candidates)
    needs_review_source_item_count = sum(
        str(row.get("status") or "") == "needs_review" for row in processing_rows
    )
    retry_source_item_count = sum(
        str(row.get("status") or "") == "retry" for row in processing_rows
    )

    consistency = {
        "processed_units_match": (
            processed_candidate_count == int(job.get("processed_units") or 0)
        ),
        "existing_links_match": (
            existing_link_candidate_count == int(job.get("existing_links") or 0)
        ),
        "new_clusters_match": (
            reconstructed_new_clusters == int(job.get("new_clusters") or 0)
        ),
        "uncertain_units_match": (
            uncertain_candidate_count == int(job.get("uncertain_units") or 0)
        ),
        "needs_review_items_match": (
            needs_review_source_item_count == int(job.get("needs_review_items") or 0)
        ),
    }
    consistency["all_match"] = all(consistency.values())
    reconstruction_reliable = bool(
        snapshot_matches_job
        and consistency["all_match"]
        and missing_first_stage_key_count == 0
        and ambiguous_candidate_cluster_count == 0
    )

    return {
        "available": True,
        "reason": "" if snapshot_matches_job else "cluster_snapshot_changed_since_job",
        "missing": [],
        "job_id": normalized_job_id,
        "job_started_at": _iso(started_at),
        "job_finished_at": _iso(finished_at),
        "cluster_snapshot_min_at": _iso(cluster_min_at),
        "cluster_snapshot_max_at": _iso(cluster_max_at),
        "snapshot_matches_job": snapshot_matches_job,
        "reconstruction_reliable": reconstruction_reliable,
        "processing_source_item_count": len(processing_rows),
        "processed_candidate_count": processed_candidate_count,
        "singleton_candidate_count": singleton_candidate_count,
        "singleton_candidate_percent": (
            round(singleton_candidate_count / processed_candidate_count * 100, 1)
            if processed_candidate_count
            else 0.0
        ),
        "multi_candidate_cluster_count": multi_candidate_cluster_count,
        "multi_candidate_candidate_count": multi_candidate_candidate_count,
        "existing_link_cluster_count": len(cluster_groups["existing_link_clusters"]),
        "existing_link_candidate_count": existing_link_candidate_count,
        "uncertain_candidate_count": uncertain_candidate_count,
        "needs_review_source_item_count": needs_review_source_item_count,
        "retry_source_item_count": retry_source_item_count,
        "missing_first_stage_key_count": missing_first_stage_key_count,
        "ambiguous_candidate_cluster_count": ambiguous_candidate_cluster_count,
        "consistency": consistency,
        "samples": {
            "multi_candidate_clusters": cluster_groups["multi_candidate_clusters"][:bounded_sample_limit],
            "existing_link_clusters": cluster_groups["existing_link_clusters"][:bounded_sample_limit],
            "singleton_candidates": cluster_groups["singleton_candidates"][:bounded_sample_limit],
            "unresolved_candidates": unresolved_samples[:bounded_sample_limit],
        },
        "sample_limit": bounded_sample_limit,
        "interpretation_note": (
            "단독 후보 비율은 품질 결함 판정이 아니라 실제 제목·사건 비교를 위한 표본 신호입니다. "
            "reconstruction_reliable=true일 때만 최신 작업의 신규/기존 연결 재구성을 신뢰합니다."
        ),
    }
