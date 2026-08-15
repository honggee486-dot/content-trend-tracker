from __future__ import annotations

from functools import wraps
import sys
import threading
from typing import Any, Callable, Sequence

_CLUSTER_VIEW_LOCK = threading.RLock()


def _candidate_id(candidate: dict[str, Any]) -> str:
    return str(candidate.get("candidate_id") or candidate.get("cluster_id") or "").strip()


def build_adaptive_candidate_evaluation_executor(
    original: Callable[..., Any],
    evaluation_module: Any,
) -> Callable[..., Any]:
    """각 평가 요청 뒤 갱신된 estimator로 남은 후보를 즉시 다시 분할합니다."""
    if getattr(original, "_adaptive_next_request", False):
        return original

    @wraps(original)
    def wrapped(preparation: Any, *args: Any, **kwargs: Any) -> Any:
        if getattr(preparation, "status", "") != "ready":
            return original(preparation, *args, **kwargs)

        estimator = kwargs.get("estimator") or evaluation_module.GLOBAL_TOKEN_ESTIMATOR
        call_kwargs = dict(kwargs)
        call_kwargs["estimator"] = estimator
        call_kwargs["progress_callback"] = None
        progress_callback = kwargs.get("progress_callback")

        oversized_ids = set(getattr(preparation, "oversized_cluster_ids", ()) or ())
        remaining = [
            candidate
            for candidate in preparation.candidates
            if str(candidate.get("cluster_id") or "") not in oversized_ids
        ]
        total_candidates = max(1, len(remaining))
        processed = 0
        batch_number = 0
        actual_chunks: list[Any] = []
        evaluations: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []

        while remaining:
            chunks, dynamic_oversized = evaluation_module.partition_candidate_evaluations(
                remaining,
                estimator=estimator,
            )
            if dynamic_oversized:
                dynamic_set = {str(value) for value in dynamic_oversized if str(value)}
                oversized_ids.update(dynamic_set)
                remaining = [
                    candidate
                    for candidate in remaining
                    if str(candidate.get("cluster_id") or "") not in dynamic_set
                ]
                if not remaining:
                    break
                chunks, _ = evaluation_module.partition_candidate_evaluations(
                    remaining,
                    estimator=estimator,
                )
            if not chunks:
                break

            source_chunk = chunks[0]
            batch_number += 1
            chunk = evaluation_module.CandidateEvaluationChunk(
                batch_number=batch_number,
                candidates=source_chunk.candidates,
                request_text=source_chunk.request_text,
                estimated_tokens=source_chunk.estimated_tokens,
            )
            actual_chunks.append(chunk)
            if callable(progress_callback):
                progress_callback(
                    processed / total_candidates,
                    f"Flash-Lite 전체 글감 평가 {batch_number} 요청 중 ({len(chunk.candidates):,}개)",
                )

            single_preparation = evaluation_module.CandidateEvaluationPreparation(
                status="ready",
                run_id=preparation.run_id,
                candidates=chunk.candidates,
                chunks=(chunk,),
                current_cluster_count=preparation.current_cluster_count,
                reused_clusters=preparation.reused_clusters,
                skipped_sensitive_clusters=preparation.skipped_sensitive_clusters,
                oversized_cluster_ids=(),
            )
            execution = original(single_preparation, *args, **call_kwargs)
            evaluations.extend(execution.evaluations)
            calls.extend(execution.calls)

            sent_ids = {
                str(candidate.get("cluster_id") or "")
                for candidate in chunk.candidates
            }
            remaining = [
                candidate
                for candidate in remaining
                if str(candidate.get("cluster_id") or "") not in sent_ids
            ]
            processed += len(chunk.candidates)
            if callable(progress_callback):
                progress_callback(
                    min(1.0, processed / total_candidates),
                    f"Flash-Lite 전체 글감 평가 {batch_number} 요청 완료",
                )

            # 기존 전체평가 안전 정책을 유지합니다. HTTP/예외 실패면 후속 요청을
            # 추가 소모하지 않고, partial/검증 실패는 축소된 다음 묶음으로 계속합니다.
            if execution.calls and execution.calls[-1].get("status") == "failed":
                break

        effective_preparation = evaluation_module.CandidateEvaluationPreparation(
            status=preparation.status,
            run_id=preparation.run_id,
            candidates=preparation.candidates,
            chunks=tuple(actual_chunks),
            current_cluster_count=preparation.current_cluster_count,
            reused_clusters=preparation.reused_clusters,
            skipped_sensitive_clusters=preparation.skipped_sensitive_clusters,
            oversized_cluster_ids=tuple(sorted(oversized_ids)),
        )
        return evaluation_module.CandidateEvaluationExecution(
            effective_preparation,
            tuple(evaluations),
            tuple(calls),
        )

    wrapped._adaptive_next_request = True  # type: ignore[attr-defined]
    wrapped._adaptive_next_request_original = original  # type: ignore[attr-defined]
    return wrapped


def build_adaptive_blog_routing_executor(
    original: Callable[..., Any],
    routing_module: Any,
) -> Callable[..., Any]:
    """각 블로그 분류 요청 결과를 다음 요청의 입력 크기에 즉시 반영합니다."""
    if getattr(original, "_adaptive_next_request", False):
        return original

    @wraps(original)
    def wrapped(preparation: Any, *args: Any, **kwargs: Any) -> Any:
        if getattr(preparation, "status", "") != "ready":
            return original(preparation, *args, **kwargs)

        estimator = kwargs.get("estimator") or routing_module.GLOBAL_TOKEN_ESTIMATOR
        call_kwargs = dict(kwargs)
        call_kwargs["estimator"] = estimator
        call_kwargs["progress_callback"] = None
        progress_callback = kwargs.get("progress_callback")

        oversized_ids = set(getattr(preparation, "oversized_cluster_ids", ()) or ())
        remaining = [
            candidate
            for candidate in preparation.candidates
            if str(candidate.get("cluster_id") or "") not in oversized_ids
        ]
        total_candidates = max(1, len(remaining))
        processed = 0
        batch_number = 0
        actual_chunks: list[Any] = []
        routes: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        hard_stop_errors = {
            "daily_quota_exhausted",
            "rate_limited",
            "authentication_error",
            "permission_error",
            "model_not_found",
            "invalid_request",
        }

        while remaining:
            chunks, dynamic_oversized = routing_module.partition_blog_route_candidates(
                remaining,
                estimator=estimator,
            )
            if dynamic_oversized:
                dynamic_set = {str(value) for value in dynamic_oversized if str(value)}
                oversized_ids.update(dynamic_set)
                remaining = [
                    candidate
                    for candidate in remaining
                    if str(candidate.get("cluster_id") or "") not in dynamic_set
                ]
                if not remaining:
                    break
                chunks, _ = routing_module.partition_blog_route_candidates(
                    remaining,
                    estimator=estimator,
                )
            if not chunks:
                break

            source_chunk = chunks[0]
            batch_number += 1
            chunk = routing_module.BlogRouteChunk(
                batch_number=batch_number,
                candidates=source_chunk.candidates,
                request_text=source_chunk.request_text,
                estimated_tokens=source_chunk.estimated_tokens,
            )
            actual_chunks.append(chunk)
            if callable(progress_callback):
                progress_callback(
                    processed / total_candidates,
                    f"Flash-Lite 블로그 분류 {batch_number} 요청 중 ({len(chunk.candidates):,}개)",
                )

            single_preparation = routing_module.BlogRoutingPreparation(
                status="ready",
                candidates=chunk.candidates,
                chunks=(chunk,),
                reused_clusters=preparation.reused_clusters,
                skipped_sensitive_clusters=preparation.skipped_sensitive_clusters,
                oversized_cluster_ids=(),
            )
            execution = original(single_preparation, *args, **call_kwargs)
            routes.extend(execution.routes)
            calls.extend(execution.calls)

            sent_ids = {
                str(candidate.get("cluster_id") or "")
                for candidate in chunk.candidates
            }
            remaining = [
                candidate
                for candidate in remaining
                if str(candidate.get("cluster_id") or "") not in sent_ids
            ]
            processed += len(chunk.candidates)
            if callable(progress_callback):
                progress_callback(
                    min(1.0, processed / total_candidates),
                    f"Flash-Lite 블로그 분류 {batch_number} 요청 완료",
                )

            if execution.calls:
                last_call = execution.calls[-1]
                if (
                    last_call.get("status") == "failed"
                    and str(last_call.get("error_type") or "") in hard_stop_errors
                ):
                    break

        effective_preparation = routing_module.BlogRoutingPreparation(
            status=preparation.status,
            candidates=preparation.candidates,
            chunks=tuple(actual_chunks),
            reused_clusters=preparation.reused_clusters,
            skipped_sensitive_clusters=preparation.skipped_sensitive_clusters,
            oversized_cluster_ids=tuple(sorted(oversized_ids)),
        )
        return routing_module.BlogRoutingExecution(
            effective_preparation,
            tuple(routes),
            tuple(calls),
        )

    wrapped._adaptive_next_request = True  # type: ignore[attr-defined]
    wrapped._adaptive_next_request_original = original  # type: ignore[attr-defined]
    return wrapped


def _merge_sparse_execution(target: dict[str, Any], execution: Any) -> None:
    for candidate_id, views in execution.views_by_candidate.items():
        target["views"].setdefault(candidate_id, set()).update(views)
    for candidate_id, views in execution.failed_views_by_candidate.items():
        target["failed"].setdefault(candidate_id, set()).update(views)
    for candidate_id, cluster_votes in execution.existing_votes.items():
        candidate_bucket = target["votes"].setdefault(candidate_id, {})
        for cluster_id, votes in cluster_votes.items():
            candidate_bucket.setdefault(cluster_id, []).extend(votes)
    target["groups"].extend(execution.group_proposals)
    for candidate_id, views in execution.uncertain_views.items():
        target["uncertain"].setdefault(candidate_id, set()).update(views)
    target["conflicts"].update(execution.conflict_edges)
    target["calls"].extend(execution.calls)
    for key, value in execution.diagnostics.items():
        target["diagnostics"][key] = target["diagnostics"].get(key, 0) + int(value or 0)


def build_adaptive_sparse_view_executor(
    original: Callable[..., Any],
    sparse_module: Any,
) -> Callable[..., Any]:
    """2차 군집도 요청 직후 estimator 결과로 같은 실행의 다음 묶음을 재분할합니다."""
    if getattr(original, "_adaptive_next_request", False):
        return original

    @wraps(original)
    def wrapped(config: Any, selected: Sequence[dict[str, Any]], **kwargs: Any) -> Any:
        estimator = kwargs.get("estimator") or sparse_module.GLOBAL_TOKEN_ESTIMATOR
        call_kwargs = dict(kwargs)
        call_kwargs["estimator"] = estimator
        call_kwargs["progress_callback"] = None
        progress_callback = kwargs.get("progress_callback")
        batch_id = str(kwargs.get("batch_id") or "adaptive")

        candidate_by_id = {
            sparse_module.clean_text(candidate.get("candidate_id")): candidate
            for candidate in selected
            if sparse_module.clean_text(candidate.get("candidate_id"))
        }
        views = tuple(sparse_module.CLUSTERING_ACTIVE_VIEWS)
        total_pairs = 0
        for view in views:
            if view == "existing":
                total_pairs += sum(
                    1
                    for candidate in candidate_by_id.values()
                    if sparse_module.build_existing_option_payload(candidate)
                )
            else:
                total_pairs += len(candidate_by_id)
        total_pairs = max(1, total_pairs)
        processed_pairs = 0
        request_number = 0

        merged: dict[str, Any] = {
            "views": {},
            "failed": {},
            "votes": {},
            "groups": [],
            "uncertain": {},
            "conflicts": set(),
            "calls": [],
            "diagnostics": {},
        }

        for view in views:
            remaining = [
                candidate
                for candidate in candidate_by_id.values()
                if view != "existing"
                or sparse_module.build_existing_option_payload(candidate)
            ]
            while remaining:
                numbered = list(enumerate(remaining, start=1))
                chunks, oversized = sparse_module.partition_for_view(
                    numbered,
                    view=view,
                    batch_id=batch_id,
                    estimator=estimator,
                )
                if oversized:
                    oversized_ids = {
                        _candidate_id(candidate)
                        for number, candidate in numbered
                        if number in oversized
                    }
                    for candidate_id in oversized_ids:
                        merged["failed"].setdefault(candidate_id, set()).add(view)
                        merged["uncertain"].setdefault(candidate_id, set()).add("oversized")
                    remaining = [
                        candidate
                        for candidate in remaining
                        if _candidate_id(candidate) not in oversized_ids
                    ]
                    processed_pairs += len(oversized_ids)
                    if not remaining:
                        break
                    numbered = list(enumerate(remaining, start=1))
                    chunks, _ = sparse_module.partition_for_view(
                        numbered,
                        view=view,
                        batch_id=batch_id,
                        estimator=estimator,
                    )
                if not chunks:
                    break

                chunk = chunks[0]
                subset = [candidate for _, candidate in chunk.candidates]
                subset_ids = {_candidate_id(candidate) for candidate in subset}
                request_number += 1
                if callable(progress_callback):
                    progress_callback(
                        min(1.0, processed_pairs / total_pairs),
                        f"Flash-Lite 2차 군집 [{view} 요청 {request_number}] 요청 중 ({len(subset):,}개)",
                    )

                with _CLUSTER_VIEW_LOCK:
                    previous_views = sparse_module.CLUSTERING_ACTIVE_VIEWS
                    sparse_module.CLUSTERING_ACTIVE_VIEWS = (view,)
                    try:
                        execution = original(config, subset, **call_kwargs)
                    finally:
                        sparse_module.CLUSTERING_ACTIVE_VIEWS = previous_views
                _merge_sparse_execution(merged, execution)

                remaining = [
                    candidate
                    for candidate in remaining
                    if _candidate_id(candidate) not in subset_ids
                ]
                processed_pairs += len(subset)
                if callable(progress_callback):
                    progress_callback(
                        min(1.0, processed_pairs / total_pairs),
                        f"Flash-Lite 2차 군집 [{view} 요청 {request_number}] 완료",
                    )

        return sparse_module.SparseViewExecution(
            merged["views"],
            merged["failed"],
            merged["votes"],
            tuple(merged["groups"]),
            merged["uncertain"],
            merged["conflicts"],
            tuple(merged["calls"]),
            merged["diagnostics"],
        )

    wrapped._adaptive_next_request = True  # type: ignore[attr-defined]
    wrapped._adaptive_next_request_original = original  # type: ignore[attr-defined]
    return wrapped


def install_adaptive_next_request_contract() -> None:
    """주제방향을 제외한 세 자동 Gemini 배치가 매 요청 후 남은 후보를 재분할합니다."""
    from src.services import trend_blog_ai_routing_service as routing_module
    from src.services import trend_candidate_ai_evaluation_service as evaluation_module
    from src.services import trend_cluster_sparse_executor as sparse_module

    evaluation_module.execute_prepared_candidate_ai_evaluation = (
        build_adaptive_candidate_evaluation_executor(
            evaluation_module.execute_prepared_candidate_ai_evaluation,
            evaluation_module,
        )
    )
    routing_module.execute_prepared_blog_routing = build_adaptive_blog_routing_executor(
        routing_module.execute_prepared_blog_routing,
        routing_module,
    )
    sparse_module.execute_sparse_views = build_adaptive_sparse_view_executor(
        sparse_module.execute_sparse_views,
        sparse_module,
    )

    # orchestrator가 executor 함수를 이미 직접 import한 경우에도 같은 래퍼를 사용합니다.
    orchestrator = sys.modules.get("src.services.trend_cluster_sparse_orchestrator")
    if orchestrator is not None:
        setattr(orchestrator, "execute_sparse_views", sparse_module.execute_sparse_views)
