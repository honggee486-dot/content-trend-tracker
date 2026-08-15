from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Callable, Iterable

from src.config import GeminiConfig
from src.services.gemini_service import call_gemini_structured_output
from src.services.trend_cluster_sparse_aggregation import (
    aggregate_sparse_assignments,
)
from src.services.trend_cluster_sparse_executor import execute_sparse_views
from src.services.trend_cluster_sparse_protocol import (
    CLUSTERING_SCAN_CANDIDATE_LIMIT,
    clean_text,
    select_all_topic_candidates,
)
from src.services.trend_cluster_token_runtime import (
    AdaptiveInputTokenEstimator,
    CLUSTERING_TARGET_INPUT_TOKENS,
    CLUSTERING_TPM_LIMIT,
    GLOBAL_TOKEN_ESTIMATOR,
    SlidingWindowTpmLimiter,
)


def _response_candidate_no(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 1 <= number <= 10**9 else None


def _repeated_candidate_count(values: Iterable[int]) -> int:
    return sum(count > 1 for count in Counter(values).values())


def summarize_sparse_response_duplicates(response_text: str) -> dict[str, int]:
    """희소 응답 중복을 결과 범주별로 분해해 원인 진단에 사용합니다."""
    try:
        parsed = json.loads(str(response_text or ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}

    raw_existing = parsed.get("existing_links")
    raw_groups = parsed.get("new_groups")
    raw_uncertain = parsed.get("uncertain_nos")
    if not (
        isinstance(raw_existing, list)
        and isinstance(raw_groups, list)
        and isinstance(raw_uncertain, list)
    ):
        return {}

    existing_nos: list[int] = []
    for row in raw_existing:
        if not isinstance(row, dict):
            continue
        number = _response_candidate_no(row.get("candidate_no"))
        if number is not None:
            existing_nos.append(number)

    group_nos: list[int] = []
    group_memberships: defaultdict[int, int] = defaultdict(int)
    within_group_duplicates: set[int] = set()
    for row in raw_groups:
        if not isinstance(row, dict):
            continue
        numbers = [
            number
            for number in (
                _response_candidate_no(value)
                for value in row.get("candidate_nos") or ()
            )
            if number is not None
        ]
        group_nos.extend(numbers)
        counts = Counter(numbers)
        within_group_duplicates.update(
            number for number, count in counts.items() if count > 1
        )
        for number in counts:
            group_memberships[number] += 1

    uncertain_nos = [
        number
        for number in (_response_candidate_no(value) for value in raw_uncertain)
        if number is not None
    ]

    category_memberships: defaultdict[int, int] = defaultdict(int)
    for category in (set(existing_nos), set(group_nos), set(uncertain_nos)):
        for number in category:
            category_memberships[number] += 1

    return {
        "duplicate_existing_candidate_no": _repeated_candidate_count(existing_nos),
        "duplicate_within_new_group_candidate_no": len(within_group_duplicates),
        "duplicate_across_new_groups_candidate_no": sum(
            count > 1 for count in group_memberships.values()
        ),
        "duplicate_uncertain_candidate_no": _repeated_candidate_count(uncertain_nos),
        "cross_category_duplicate_candidate_no": sum(
            count > 1 for count in category_memberships.values()
        ),
    }


def classify_sparse_multi_view_batch(
    config: GeminiConfig,
    candidates: Iterable[dict[str, Any]],
    *,
    batch_id: str = "cluster_batch_0001",
    max_candidates: int = CLUSTERING_SCAN_CANDIDATE_LIMIT,
    api_call: Callable[..., tuple[Any, ...]] = call_gemini_structured_output,
    estimator: AdaptiveInputTokenEstimator | None = None,
    limiter: SlidingWindowTpmLimiter | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> Any:
    """관점별 희소 응답을 모두 모은 뒤 병합·단독·보류를 최종 결정합니다."""
    from src.services.trend_cluster_ai_review_service import ClusterGroupingExecution

    selected = select_all_topic_candidates(
        candidates,
        batch_id=batch_id,
        max_candidates=max_candidates,
    )
    requested_candidates = len(selected)
    if not selected:
        return ClusterGroupingExecution("nothing_to_group", (), (), 0, 0, 0)
    if not config.api_key:
        return ClusterGroupingExecution(
            "missing_api_key",
            (),
            (),
            requested_candidates,
            0,
            0,
            "Gemini 인증 설정이 없어 2차 군집을 실행하지 않았습니다.",
        )

    execution = execute_sparse_views(
        config,
        selected,
        batch_id=batch_id,
        api_call=api_call,
        estimator=estimator,
        limiter=limiter,
        progress_callback=progress_callback,
    )
    assignments, aggregate_diagnostics = aggregate_sparse_assignments(
        selected,
        views_by_candidate=execution.views_by_candidate,
        failed_views_by_candidate=execution.failed_views_by_candidate,
        existing_votes=execution.existing_votes,
        group_proposals=execution.group_proposals,
        uncertain_views=execution.uncertain_views,
        conflict_edges=execution.conflict_edges,
    )
    diagnostics = dict(execution.diagnostics)
    for call in execution.calls:
        if clean_text(call.get("status")).casefold() != "success":
            continue
        for key, value in summarize_sparse_response_duplicates(
            str(call.get("response_text") or "")
        ).items():
            diagnostics[key] = diagnostics.get(key, 0) + int(value or 0)
    for key, value in aggregate_diagnostics.items():
        diagnostics[key] = diagnostics.get(key, 0) + int(value or 0)

    uncertain_count = sum(
        clean_text(row.get("decision")) == "uncertain" for row in assignments
    )
    completed_count = requested_candidates - uncertain_count
    failed_call_count = sum(
        clean_text(row.get("status")) != "success" for row in execution.calls
    )
    wait_seconds = sum(
        float(row.get("tpm_wait_seconds") or 0.0) for row in execution.calls
    )
    analysis_views = {
        clean_text(row.get("analysis_view"))
        for row in execution.calls
        if clean_text(row.get("analysis_view"))
    }
    error_parts = [
        f"분석 관점 {len(analysis_views)}개",
        f"실제 Gemini 요청 {len(execution.calls)}회",
        f"불확실 {uncertain_count}개",
        f"실패 요청 {failed_call_count}회",
        f"입력 대기 {wait_seconds:.1f}초",
    ]
    notable = {
        key: value for key, value in diagnostics.items() if int(value or 0) > 0
    }
    if notable:
        error_parts.append(
            "검증 "
            + ", ".join(
                f"{key}={value}" for key, value in sorted(notable.items())
            )
        )

    if completed_count == requested_candidates and failed_call_count == 0:
        status = "success"
    elif completed_count > 0:
        status = "partial"
    else:
        status = "uncertain" if execution.calls else "failed"
    return ClusterGroupingExecution(
        status=status,
        assignments=tuple(assignments),
        calls=execution.calls,
        requested_candidates=requested_candidates,
        completed_candidates=completed_count,
        uncertain_candidates=uncertain_count,
        error_message=" | ".join(error_parts)[:1500],
    )


def aggregate_call_metrics(calls: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = [dict(row) for row in calls]
    return {
        "request_count": len(rows),
        "analysis_views": tuple(
            sorted(
                {
                    clean_text(row.get("analysis_view"))
                    for row in rows
                    if clean_text(row.get("analysis_view"))
                }
            )
        ),
        "estimated_input_tokens": sum(
            int(row.get("estimated_input_tokens") or 0) for row in rows
        ),
        "input_tokens": sum(int(row.get("input_tokens") or 0) for row in rows),
        "output_tokens": sum(int(row.get("output_tokens") or 0) for row in rows),
        "thought_tokens": sum(int(row.get("thought_tokens") or 0) for row in rows),
        "total_tokens": sum(int(row.get("total_tokens") or 0) for row in rows),
        "tpm_wait_seconds": round(
            sum(float(row.get("tpm_wait_seconds") or 0.0) for row in rows),
            3,
        ),
        "api_duration_ms": sum(int(row.get("duration_ms") or 0) for row in rows),
        "max_input_tokens": max(
            (int(row.get("input_tokens") or 0) for row in rows),
            default=0,
        ),
        "target_input_tokens": CLUSTERING_TARGET_INPUT_TOKENS,
        "tpm_limit": CLUSTERING_TPM_LIMIT,
        "estimator_tokens_per_character": (
            GLOBAL_TOKEN_ESTIMATOR.tokens_per_character
        ),
    }
