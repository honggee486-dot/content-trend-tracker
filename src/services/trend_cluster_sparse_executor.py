from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from src.config import GeminiConfig
from src.services.gemini_service import (
    GeminiHttpError,
    call_gemini_structured_output,
    normalize_gemini_api_result,
)
from src.services.trend_cluster_safety_service import build_existing_option_payload
from src.services.trend_cluster_sparse_protocol import (
    CLUSTERING_ACTIVE_VIEWS,
    CLUSTERING_FEATURE_ID,
    CLUSTERING_FEATURE_VERSION,
    SPARSE_RESPONSE_SCHEMA,
    build_sparse_request_text,
    candidate_payload,
    candidate_topic_sort_key,
    clean_text,
    parse_sparse_response,
)
from src.services.trend_cluster_token_runtime import (
    AdaptiveInputTokenEstimator,
    CLUSTERING_HARD_INPUT_TOKENS,
    CLUSTERING_TARGET_INPUT_TOKENS,
    CLUSTERING_TPM_LIMIT,
    GLOBAL_TOKEN_ESTIMATOR,
    GLOBAL_TPM_LIMITER,
    SlidingWindowTpmLimiter,
    register_call_metrics,
)


@dataclass(frozen=True)
class RequestChunk:
    view: str
    batch_id: str
    candidates: tuple[tuple[int, dict[str, Any]], ...]
    request_text: str
    estimated_tokens: int


@dataclass(frozen=True)
class SparseViewExecution:
    views_by_candidate: dict[str, set[str]]
    failed_views_by_candidate: dict[str, set[str]]
    existing_votes: dict[str, dict[str, list[tuple[str, int, int]]]]
    group_proposals: tuple[dict[str, Any], ...]
    uncertain_views: dict[str, set[str]]
    conflict_edges: set[frozenset[str]]
    calls: tuple[dict[str, Any], ...]
    diagnostics: dict[str, int]


def partition_for_view(
    numbered_candidates: Sequence[tuple[int, dict[str, Any]]],
    *,
    view: str,
    batch_id: str,
    estimator: AdaptiveInputTokenEstimator,
    target_tokens: int = CLUSTERING_TARGET_INPUT_TOKENS,
) -> tuple[list[RequestChunk], set[int]]:
    ordered = sorted(
        numbered_candidates,
        key=lambda row: candidate_topic_sort_key(row[1], view=view),
    )
    chunks: list[RequestChunk] = []
    oversized: set[int] = set()
    current: list[tuple[int, dict[str, Any]]] = []
    current_payload_characters = 0

    def request_characters(payload_characters: int, count: int) -> int:
        request_id = f"{batch_id}:{view}:{len(chunks) + 1:04d}"
        empty_length = len(build_sparse_request_text(request_id, view, ()))
        return empty_length + payload_characters + max(0, count - 1)

    def finalize(rows: list[tuple[int, dict[str, Any]]]) -> None:
        if not rows:
            return
        request_id = f"{batch_id}:{view}:{len(chunks) + 1:04d}"
        request_text = build_sparse_request_text(request_id, view, rows)
        chunks.append(
            RequestChunk(
                view,
                request_id,
                tuple(rows),
                request_text,
                estimator.estimate_text(request_text),
            )
        )

    for candidate_no, candidate in ordered:
        payload_text = json.dumps(
            candidate_payload(candidate_no, candidate, view=view),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        payload_characters = len(payload_text)
        trial_count = len(current) + 1
        estimate = estimator.estimate_characters(
            request_characters(
                current_payload_characters + payload_characters,
                trial_count,
            )
        )
        if current and estimate > target_tokens:
            finalize(current)
            current = []
            current_payload_characters = 0
            estimate = estimator.estimate_characters(
                request_characters(payload_characters, 1)
            )
        if estimate > CLUSTERING_HARD_INPUT_TOKENS:
            oversized.add(candidate_no)
            continue
        current.append((candidate_no, candidate))
        current_payload_characters += payload_characters
    finalize(current)
    return chunks, oversized


def _success_call_row(
    *,
    request_hash: str,
    chunk: RequestChunk,
    reservation: Any,
    estimator: AdaptiveInputTokenEstimator,
    output_text: str,
    input_tokens: int | None,
    output_tokens: int | None,
    thought_tokens: int | None,
    total_tokens: int | None,
    finish_reason: str,
    finish_message: str,
    duration_ms: int,
    status: str,
    error_type: str,
    error_message: str,
) -> dict[str, Any]:
    return {
        "feature_id": CLUSTERING_FEATURE_ID,
        "feature_version": CLUSTERING_FEATURE_VERSION,
        "request_hash": request_hash,
        "request_text": chunk.request_text,
        "response_text": output_text,
        "requested_item_count": len(chunk.candidates),
        "status": status,
        "http_status": 200,
        "error_type": error_type,
        "error_message": error_message,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "thought_tokens": thought_tokens,
        "total_tokens": total_tokens,
        "finish_reason": finish_reason,
        "finish_message": finish_message,
        "analysis_view": chunk.view,
        "estimated_input_tokens": chunk.estimated_tokens,
        "target_input_tokens": CLUSTERING_TARGET_INPUT_TOKENS,
        "hard_input_tokens": CLUSTERING_HARD_INPUT_TOKENS,
        "tpm_limit": CLUSTERING_TPM_LIMIT,
        "tpm_used_before": reservation.used_before,
        "tpm_wait_seconds": reservation.wait_seconds,
        "estimator_tokens_per_character": estimator.tokens_per_character,
        "duration_ms": duration_ms,
    }


def _failed_call_row(
    *,
    request_hash: str,
    chunk: RequestChunk,
    reservation: Any,
    estimator: AdaptiveInputTokenEstimator,
    info: Any,
    error_type: str,
    error_message: str,
    duration_ms: int,
) -> dict[str, Any]:
    return {
        "feature_id": CLUSTERING_FEATURE_ID,
        "feature_version": CLUSTERING_FEATURE_VERSION,
        "request_hash": request_hash,
        "request_text": chunk.request_text,
        "response_text": "",
        "requested_item_count": len(chunk.candidates),
        "status": "failed",
        "http_status": getattr(info, "http_status", None),
        "error_type": error_type,
        "error_message": error_message,
        "input_tokens": None,
        "output_tokens": None,
        "thought_tokens": None,
        "total_tokens": None,
        "finish_reason": clean_text(getattr(info, "finish_reason", "")),
        "finish_message": clean_text(getattr(info, "finish_message", "")),
        "analysis_view": chunk.view,
        "estimated_input_tokens": chunk.estimated_tokens,
        "target_input_tokens": CLUSTERING_TARGET_INPUT_TOKENS,
        "hard_input_tokens": CLUSTERING_HARD_INPUT_TOKENS,
        "tpm_limit": CLUSTERING_TPM_LIMIT,
        "tpm_used_before": reservation.used_before,
        "tpm_wait_seconds": reservation.wait_seconds,
        "estimator_tokens_per_character": estimator.tokens_per_character,
        "duration_ms": duration_ms,
    }


def execute_sparse_views(
    config: GeminiConfig,
    selected: Sequence[dict[str, Any]],
    *,
    batch_id: str,
    api_call: Callable[..., tuple[Any, ...]] = call_gemini_structured_output,
    estimator: AdaptiveInputTokenEstimator | None = None,
    limiter: SlidingWindowTpmLimiter | None = None,
) -> SparseViewExecution:
    active_estimator = estimator or GLOBAL_TOKEN_ESTIMATOR
    active_limiter = limiter or GLOBAL_TPM_LIMITER
    candidate_by_id = {
        clean_text(candidate.get("candidate_id")): candidate for candidate in selected
    }
    candidate_no_by_id = {
        candidate_id: index
        for index, candidate_id in enumerate(candidate_by_id, start=1)
    }
    id_by_no = {
        candidate_no: candidate_id
        for candidate_id, candidate_no in candidate_no_by_id.items()
    }
    numbered_all = [
        (candidate_no_by_id[candidate_id], candidate)
        for candidate_id, candidate in candidate_by_id.items()
    ]

    views_by_candidate: defaultdict[str, set[str]] = defaultdict(set)
    failed_views_by_candidate: defaultdict[str, set[str]] = defaultdict(set)
    existing_votes: defaultdict[
        str, defaultdict[str, list[tuple[str, int, int]]]
    ] = defaultdict(lambda: defaultdict(list))
    group_proposals: list[dict[str, Any]] = []
    uncertain_views: defaultdict[str, set[str]] = defaultdict(set)
    conflict_edges: set[frozenset[str]] = set()
    calls: list[dict[str, Any]] = []
    diagnostics: defaultdict[str, int] = defaultdict(int)

    for view in CLUSTERING_ACTIVE_VIEWS:
        numbered = (
            [
                row
                for row in numbered_all
                if build_existing_option_payload(row[1])
            ]
            if view == "existing"
            else list(numbered_all)
        )
        if not numbered:
            continue
        chunks, oversized = partition_for_view(
            numbered,
            view=view,
            batch_id=batch_id,
            estimator=active_estimator,
        )
        for candidate_no in oversized:
            candidate_id = id_by_no[candidate_no]
            failed_views_by_candidate[candidate_id].add(view)
            uncertain_views[candidate_id].add("oversized")

        for chunk in chunks:
            request_hash = hashlib.sha256(
                (
                    f"{CLUSTERING_FEATURE_ID}|{CLUSTERING_FEATURE_VERSION}|"
                    f"{config.model}|minimal|{chunk.request_text}"
                ).encode("utf-8")
            ).hexdigest()
            reservation = active_limiter.reserve(
                request_hash,
                min(chunk.estimated_tokens, CLUSTERING_HARD_INPUT_TOKENS),
            )
            started = time.perf_counter()
            try:
                raw_result = api_call(
                    config,
                    chunk.request_text,
                    request_hash,
                    feature_id=CLUSTERING_FEATURE_ID,
                    response_schema=SPARSE_RESPONSE_SCHEMA,
                    use_google_search=False,
                    thinking_level="minimal",
                    timeout_seconds=min(max(30, int(config.timeout_seconds)), 240),
                )
                (
                    output_text,
                    input_tokens,
                    output_tokens,
                    thought_tokens,
                    total_tokens,
                    finish_reason,
                    finish_message,
                ) = normalize_gemini_api_result(raw_result)
                duration_ms = int((time.perf_counter() - started) * 1000)
                active_limiter.reconcile(request_hash, input_tokens)
                parsed = parse_sparse_response(
                    output_text,
                    candidate_by_no={
                        candidate_no: candidate
                        for candidate_no, candidate in chunk.candidates
                    },
                    finish_reason=finish_reason,
                )
                status = "success" if parsed.valid else "failed"
                error_type = "" if parsed.valid else "sparse_response_invalid"
                active_estimator.observe(
                    request_characters=len(chunk.request_text),
                    estimated_tokens=chunk.estimated_tokens,
                    actual_tokens=input_tokens,
                    status=status,
                    error_type=error_type,
                )
                chunk_ids = {
                    id_by_no[candidate_no] for candidate_no, _ in chunk.candidates
                }
                if parsed.valid:
                    for candidate_id in chunk_ids:
                        views_by_candidate[candidate_id].add(view)
                    for row in parsed.existing_links:
                        candidate_id = id_by_no[int(row["candidate_no"])]
                        existing_votes[candidate_id][str(row["cluster_id"])].append(
                            (
                                view,
                                int(row["confidence"]),
                                int(row["option_id"]),
                            )
                        )
                    for row in parsed.new_groups:
                        group_proposals.append(
                            {
                                "view": view,
                                "candidate_ids": tuple(
                                    id_by_no[int(candidate_no)]
                                    for candidate_no in row["candidate_nos"]
                                ),
                                "representative_title": row[
                                    "representative_title"
                                ],
                                "confidence": row["confidence"],
                            }
                        )
                    for candidate_no in parsed.uncertain_nos:
                        uncertain_views[id_by_no[int(candidate_no)]].add(view)
                    for candidate_no in parsed.invalid_nos:
                        failed_views_by_candidate[id_by_no[int(candidate_no)]].add(
                            view
                        )
                    for row in parsed.conflicts:
                        left_no, right_no = row["candidate_nos"]
                        conflict_edges.add(
                            frozenset((id_by_no[left_no], id_by_no[right_no]))
                        )
                    for key, value in parsed.diagnostics.items():
                        diagnostics[key] += int(value or 0)
                else:
                    for candidate_id in chunk_ids:
                        failed_views_by_candidate[candidate_id].add(view)

                call_row = _success_call_row(
                    request_hash=request_hash,
                    chunk=chunk,
                    reservation=reservation,
                    estimator=active_estimator,
                    output_text=output_text,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    thought_tokens=thought_tokens,
                    total_tokens=total_tokens,
                    finish_reason=finish_reason,
                    finish_message=finish_message,
                    duration_ms=duration_ms,
                    status=status,
                    error_type=error_type,
                    error_message=parsed.error_message,
                )
            except Exception as exc:
                duration_ms = int((time.perf_counter() - started) * 1000)
                info = exc.info if isinstance(exc, GeminiHttpError) else None
                error_type = clean_text(
                    getattr(info, "error_type", "") or type(exc).__name__
                )
                error_message = clean_text(getattr(info, "message", "") or exc)
                active_estimator.observe(
                    request_characters=len(chunk.request_text),
                    estimated_tokens=chunk.estimated_tokens,
                    actual_tokens=None,
                    status="failed",
                    error_type=error_type,
                )
                for candidate_no, _ in chunk.candidates:
                    failed_views_by_candidate[id_by_no[candidate_no]].add(view)
                call_row = _failed_call_row(
                    request_hash=request_hash,
                    chunk=chunk,
                    reservation=reservation,
                    estimator=active_estimator,
                    info=info,
                    error_type=error_type,
                    error_message=error_message,
                    duration_ms=duration_ms,
                )
            calls.append(call_row)
            register_call_metrics(request_hash, call_row)

    return SparseViewExecution(
        dict(views_by_candidate),
        {
            key: set(value)
            for key, value in failed_views_by_candidate.items()
        },
        {
            candidate_id: {
                cluster_id: list(votes)
                for cluster_id, votes in cluster_votes.items()
            }
            for candidate_id, cluster_votes in existing_votes.items()
        },
        tuple(group_proposals),
        {key: set(value) for key, value in uncertain_views.items()},
        set(conflict_edges),
        tuple(calls),
        dict(diagnostics),
    )
