"""최신 군집 작업을 저비용 deterministic baseline과 읽기 전용으로 비교합니다."""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
import math
import re
from typing import Any

import duckdb
from rapidfuzz.fuzz import token_set_ratio

from src.services.trend_cluster_safety_service import (
    build_candidate_safety_profile,
    must_split_profiles,
)


BASELINE_VERSION = "1"
BASELINE_SIMILARITY_THRESHOLD = 92.0
BASELINE_PAIR_LIMIT = 20_000
BASELINE_BLOCK_SIZE_LIMIT = 200
BASELINE_SAMPLE_LIMIT = 8

_REQUIRED_TABLE_COLUMNS = {
    "trend_clustering_jobs": {"job_id", "model_name", "started_at", "finished_at"},
    "trend_cluster_processing": {
        "source_item_id",
        "model_name",
        "first_stage_key",
        "cluster_id",
        "status",
        "processed_at",
        "updated_at",
    },
    "source_items": {"source_item_id", "raw_title"},
}
_WORD_PATTERN = re.compile(r"[0-9A-Za-z가-힣][0-9A-Za-z가-힣._+-]*")
_STOPWORDS = {
    "관련",
    "소식",
    "정보",
    "정리",
    "최신",
    "뉴스",
    "브리핑",
    "특징",
    "선택",
    "포인트",
    "총정리",
}


def unavailable_deterministic_baseline(
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
        "baseline_version": BASELINE_VERSION,
        "comparison_complete": False,
        "evaluable_candidate_count": 0,
        "unresolved_candidate_count": 0,
        "blocked_candidate_pair_count": 0,
        "evaluated_candidate_pair_count": 0,
        "baseline_merge_pair_count": 0,
        "same_cluster_agreement_pair_count": 0,
        "different_cluster_disagreement_pair_count": 0,
        "stored_same_cluster_pair_count": 0,
        "precision_vs_current_percent": None,
        "recall_vs_current_percent": None,
        "samples": {"agreements": [], "disagreements": [], "safety_blocks": []},
    }


def _table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    return table_name in {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}


def _table_columns(con: duckdb.DuckDBPyConnection, table_name: str) -> set[str]:
    return {
        str(row[1])
        for row in con.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    }


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalized_title(value: Any) -> str:
    return "".join(match.group(0).casefold() for match in _WORD_PATTERN.finditer(_clean(value)))


def _title_tokens(values: list[str]) -> set[str]:
    return {
        match.group(0).casefold()
        for value in values
        for match in _WORD_PATTERN.finditer(value)
        if len(match.group(0)) >= 2 and match.group(0).casefold() not in _STOPWORDS
    }


def _profile_set(profile: dict[str, Any], field: str) -> set[str]:
    return {_clean(value).casefold() for value in profile.get(field) or () if _clean(value)}


def _candidate_payload(key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    titles = sorted(
        {_clean(row.get("raw_title")) for row in rows if _clean(row.get("raw_title"))},
        key=lambda value: (-len(value), value),
    )
    title = titles[0] if titles else ""
    candidate = {
        "items": [{"raw_title": value} for value in titles],
        "title": title,
        "examples": titles[1:4],
    }
    profile = build_candidate_safety_profile(candidate)
    tokens = _title_tokens(titles)
    comparison_text = " ".join(sorted(tokens)) or title.casefold()
    return {
        "candidate_key": key,
        "title": title,
        "titles": titles,
        "profile": profile,
        "comparison_text": comparison_text,
        "normalized_title": _normalized_title(title),
        "cluster_id": next(iter({str(row.get("cluster_id") or "") for row in rows})),
    }


def _blocking_terms(candidate: dict[str, Any]) -> tuple[str, ...]:
    profile = candidate["profile"]
    products = sorted(_profile_set(profile, "products"))
    subjects = sorted(_profile_set(profile, "subjects"), key=lambda value: (-len(value), value))
    return tuple(dict.fromkeys([*products, *subjects[:8]]))


def _strong_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_profile = left["profile"]
    right_profile = right["profile"]
    shared_products = _profile_set(left_profile, "products") & _profile_set(
        right_profile, "products"
    )
    shared_subjects = _profile_set(left_profile, "subjects") & _profile_set(
        right_profile, "subjects"
    )
    return bool(shared_products and shared_subjects) or len(shared_subjects) >= 2


def _pair_sample(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    similarity: float,
    rule: str,
) -> dict[str, Any]:
    return {
        "left_title": left["title"],
        "right_title": right["title"],
        "similarity": round(float(similarity), 1),
        "rule": str(rule),
        "current_same_cluster": left["cluster_id"] == right["cluster_id"],
    }


def build_deterministic_baseline_comparison(
    con: duckdb.DuckDBPyConnection,
    *,
    job_id: str,
    pair_limit: int = BASELINE_PAIR_LIMIT,
    block_size_limit: int = BASELINE_BLOCK_SIZE_LIMIT,
    sample_limit: int = BASELINE_SAMPLE_LIMIT,
) -> dict[str, Any]:
    """같은 작업 후보를 현재 저장 결과와 비교하되 군집 결과는 변경하지 않습니다."""
    normalized_job_id = _clean(job_id)
    if not normalized_job_id:
        return unavailable_deterministic_baseline("job_not_found")

    missing_tables = [name for name in _REQUIRED_TABLE_COLUMNS if not _table_exists(con, name)]
    if missing_tables:
        return unavailable_deterministic_baseline(
            "missing_tables", job_id=normalized_job_id, missing=missing_tables
        )
    missing_columns: list[str] = []
    for table_name, required in _REQUIRED_TABLE_COLUMNS.items():
        existing = _table_columns(con, table_name)
        missing_columns.extend(
            f"{table_name}.{column}" for column in sorted(required - existing)
        )
    if missing_columns:
        return unavailable_deterministic_baseline(
            "missing_columns", job_id=normalized_job_id, missing=missing_columns
        )

    job_cursor = con.execute(
        """
        SELECT job_id, model_name, started_at, finished_at
        FROM trend_clustering_jobs
        WHERE job_id = ?
        LIMIT 1
        """,
        [normalized_job_id],
    )
    job_row = job_cursor.fetchone()
    if job_row is None:
        return unavailable_deterministic_baseline("job_not_found", job_id=normalized_job_id)
    job = dict(zip([str(item[0]) for item in job_cursor.description], job_row))
    if job.get("started_at") is None or job.get("finished_at") is None:
        return unavailable_deterministic_baseline(
            "job_timing_incomplete", job_id=normalized_job_id
        )

    cursor = con.execute(
        """
        SELECT p.source_item_id, p.first_stage_key, p.cluster_id, p.status,
               s.raw_title
        FROM trend_cluster_processing p
        JOIN source_items s ON s.source_item_id = p.source_item_id
        WHERE p.model_name = ?
          AND COALESCE(p.updated_at, p.processed_at) >= ?
          AND COALESCE(p.updated_at, p.processed_at) <= ? + INTERVAL 5 SECOND
        ORDER BY p.first_stage_key, p.source_item_id
        """,
        [str(job.get("model_name") or ""), job["started_at"], job["finished_at"]],
    )
    columns = [str(item[0]) for item in cursor.description]
    rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
    if not rows:
        return unavailable_deterministic_baseline(
            "processing_rows_not_found", job_id=normalized_job_id
        )

    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    missing_first_stage_key_count = 0
    for row in rows:
        key = _clean(row.get("first_stage_key"))
        if not key:
            missing_first_stage_key_count += 1
            continue
        grouped[key].append(row)

    candidates: dict[str, dict[str, Any]] = {}
    unresolved_candidate_count = 0
    for key, candidate_rows in grouped.items():
        statuses = {_clean(row.get("status")) for row in candidate_rows}
        cluster_ids = {
            _clean(row.get("cluster_id"))
            for row in candidate_rows
            if _clean(row.get("cluster_id"))
        }
        if statuses != {"processed"} or len(cluster_ids) != 1:
            unresolved_candidate_count += 1
            continue
        candidates[key] = _candidate_payload(key, candidate_rows)

    bounded_pair_limit = max(1, min(int(pair_limit), 100_000))
    bounded_block_limit = max(2, min(int(block_size_limit), 500))
    bounded_sample_limit = max(1, min(int(sample_limit), 20))

    blocks: defaultdict[str, list[str]] = defaultdict(list)
    for key in sorted(candidates):
        for term in _blocking_terms(candidates[key]):
            blocks[term].append(key)

    candidate_pairs: set[tuple[str, str]] = set()
    skipped_common_block_count = 0
    pair_limit_reached = False
    for term in sorted(blocks):
        members = sorted(set(blocks[term]))
        if len(members) > bounded_block_limit:
            skipped_common_block_count += 1
            continue
        for left_key, right_key in combinations(members, 2):
            pair = (left_key, right_key)
            if pair in candidate_pairs:
                continue
            if len(candidate_pairs) >= bounded_pair_limit:
                pair_limit_reached = True
                break
            candidate_pairs.add(pair)
        if pair_limit_reached:
            break

    baseline_pairs: set[tuple[str, str]] = set()
    agreement_samples: list[dict[str, Any]] = []
    disagreement_samples: list[dict[str, Any]] = []
    safety_samples: list[dict[str, Any]] = []
    safety_blocked_pair_count = 0
    for left_key, right_key in sorted(candidate_pairs):
        left = candidates[left_key]
        right = candidates[right_key]
        similarity = float(token_set_ratio(left["comparison_text"], right["comparison_text"]))
        exact_title = bool(
            left["normalized_title"]
            and left["normalized_title"] == right["normalized_title"]
        )
        strong_identity = _strong_identity(left, right)
        proposed = exact_title or (
            strong_identity and similarity >= BASELINE_SIMILARITY_THRESHOLD
        )
        if not proposed:
            continue
        split_reason = must_split_profiles(left["profile"], right["profile"])
        if split_reason:
            safety_blocked_pair_count += 1
            if len(safety_samples) < bounded_sample_limit:
                safety_samples.append(
                    _pair_sample(
                        left,
                        right,
                        similarity=similarity,
                        rule=f"blocked:{split_reason}",
                    )
                )
            continue
        pair = (left_key, right_key)
        baseline_pairs.add(pair)
        sample = _pair_sample(
            left,
            right,
            similarity=similarity,
            rule="same_normalized_title" if exact_title else "high_title_similarity",
        )
        if left["cluster_id"] == right["cluster_id"]:
            if len(agreement_samples) < bounded_sample_limit:
                agreement_samples.append(sample)
        elif len(disagreement_samples) < bounded_sample_limit:
            disagreement_samples.append(sample)

    same_cluster_agreements = sum(
        candidates[left]["cluster_id"] == candidates[right]["cluster_id"]
        for left, right in baseline_pairs
    )
    different_cluster_disagreements = len(baseline_pairs) - same_cluster_agreements
    final_cluster_counts: defaultdict[str, int] = defaultdict(int)
    for candidate in candidates.values():
        final_cluster_counts[str(candidate["cluster_id"])] += 1
    stored_same_cluster_pairs = sum(
        math.comb(count, 2) for count in final_cluster_counts.values() if count >= 2
    )

    comparison_incomplete_reasons: list[str] = []
    if missing_first_stage_key_count:
        comparison_incomplete_reasons.append("missing_first_stage_key")
    if unresolved_candidate_count:
        comparison_incomplete_reasons.append("unresolved_candidates")
    if skipped_common_block_count:
        comparison_incomplete_reasons.append("oversized_blocks_skipped")
    if pair_limit_reached:
        comparison_incomplete_reasons.append("pair_limit_reached")
    comparison_complete = not comparison_incomplete_reasons

    if comparison_complete:
        interpretation_note = (
            "현재 저장 군집과의 일치율은 비교 기준일 뿐 정답률이 아닙니다. 이 baseline은 읽기 전용 "
            "진단이며 실제 군집·Gemini 호출·설정을 변경하지 않습니다."
        )
    else:
        interpretation_note = (
            "비교 범위가 불완전해 precision/recall을 채택 판단 지표로 제공하지 않습니다. "
            "comparison_incomplete_reasons를 먼저 확인해야 하며, 이 baseline은 읽기 전용 진단입니다."
        )

    return {
        "available": True,
        "reason": "",
        "missing": [],
        "job_id": normalized_job_id,
        "baseline_version": BASELINE_VERSION,
        "comparison_complete": comparison_complete,
        "comparison_incomplete_reasons": comparison_incomplete_reasons,
        "pair_limit_reached": pair_limit_reached,
        "pair_limit": bounded_pair_limit,
        "block_size_limit": bounded_block_limit,
        "similarity_threshold": BASELINE_SIMILARITY_THRESHOLD,
        "evaluable_candidate_count": len(candidates),
        "unresolved_candidate_count": unresolved_candidate_count,
        "missing_first_stage_key_count": missing_first_stage_key_count,
        "skipped_common_block_count": skipped_common_block_count,
        "blocked_candidate_pair_count": safety_blocked_pair_count,
        "evaluated_candidate_pair_count": len(candidate_pairs),
        "baseline_merge_pair_count": len(baseline_pairs),
        "same_cluster_agreement_pair_count": same_cluster_agreements,
        "different_cluster_disagreement_pair_count": different_cluster_disagreements,
        "stored_same_cluster_pair_count": stored_same_cluster_pairs,
        "precision_vs_current_percent": (
            round(same_cluster_agreements / len(baseline_pairs) * 100, 1)
            if comparison_complete and baseline_pairs
            else None
        ),
        "recall_vs_current_percent": (
            round(same_cluster_agreements / stored_same_cluster_pairs * 100, 1)
            if comparison_complete and stored_same_cluster_pairs
            else None
        ),
        "samples": {
            "agreements": agreement_samples,
            "disagreements": disagreement_samples,
            "safety_blocks": safety_samples,
        },
        "interpretation_note": interpretation_note,
    }
