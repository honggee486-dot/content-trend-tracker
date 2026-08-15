from __future__ import annotations

from types import SimpleNamespace

from src.services import trend_blog_ai_routing_service as routing
from src.services import trend_candidate_ai_evaluation_service as evaluation
from src.services import trend_cluster_sparse_executor as clustering
from src.services.adaptive_gemini_next_request_runtime import (
    build_adaptive_blog_routing_executor,
    build_adaptive_candidate_evaluation_executor,
    build_adaptive_sparse_view_executor,
)
from src.services.trend_cluster_token_runtime import AdaptiveInputTokenEstimator


def _evaluation_candidate(index: int) -> dict[str, object]:
    return {
        "cluster_id": f"eval-{index}",
        "topic": f"평가 주제 {index}",
    }


def _route_candidate(index: int) -> dict[str, object]:
    return {
        "cluster_id": f"route-{index}",
        "title": f"분류 주제 {index}",
    }


def test_candidate_evaluation_partial_result_shrinks_the_immediate_next_partition(
    monkeypatch,
) -> None:
    ratios: list[float] = []

    def partition(candidates, *, estimator, **kwargs):
        ratios.append(estimator.tokens_per_character)
        candidate = candidates[0]
        text = "x" * 1_000
        return [
            evaluation.CandidateEvaluationChunk(
                1,
                (candidate,),
                text,
                estimator.estimate_text(text),
            )
        ], []

    def original(preparation, *args, **kwargs):
        estimator = kwargs["estimator"]
        estimator.observe(
            request_characters=1_000,
            estimated_tokens=estimator.estimate_text("x" * 1_000),
            actual_tokens=None,
            status="partial",
            error_type="response_validation_error",
        )
        return evaluation.CandidateEvaluationExecution(
            preparation,
            (),
            ({"status": "partial", "error_type": "response_validation_error"},),
        )

    monkeypatch.setattr(evaluation, "partition_candidate_evaluations", partition)
    executor = build_adaptive_candidate_evaluation_executor(original, evaluation)
    candidates = tuple(_evaluation_candidate(index) for index in range(2))
    preparation = evaluation.CandidateEvaluationPreparation(
        "ready", "run-1", candidates, (), 2, 0, 0, ()
    )
    estimator = AdaptiveInputTokenEstimator(tokens_per_character=2.0)

    result = executor(
        preparation,
        config=SimpleNamespace(),
        estimator=estimator,
    )

    assert len(ratios) == 2
    assert ratios[1] > ratios[0]
    assert len(result.preparation.chunks) == 2


def test_blog_routing_service_failure_shrinks_the_immediate_next_partition(
    monkeypatch,
) -> None:
    ratios: list[float] = []

    def partition(candidates, *, estimator, **kwargs):
        ratios.append(estimator.tokens_per_character)
        candidate = candidates[0]
        text = "x" * 1_000
        return [
            routing.BlogRouteChunk(
                1,
                (candidate,),
                text,
                estimator.estimate_text(text),
            )
        ], []

    def original(preparation, *args, **kwargs):
        estimator = kwargs["estimator"]
        estimator.observe(
            request_characters=1_000,
            estimated_tokens=estimator.estimate_text("x" * 1_000),
            actual_tokens=None,
            status="failed",
            error_type="service_unavailable",
        )
        return routing.BlogRoutingExecution(
            preparation,
            (),
            ({"status": "failed", "error_type": "service_unavailable"},),
        )

    monkeypatch.setattr(routing, "partition_blog_route_candidates", partition)
    executor = build_adaptive_blog_routing_executor(original, routing)
    candidates = tuple(_route_candidate(index) for index in range(2))
    preparation = routing.BlogRoutingPreparation(
        "ready", candidates, (), 0, 0, ()
    )
    estimator = AdaptiveInputTokenEstimator(tokens_per_character=2.0)

    result = executor(
        preparation,
        config=SimpleNamespace(),
        estimator=estimator,
    )

    assert len(ratios) == 2
    assert ratios[1] > ratios[0]
    assert len(result.preparation.chunks) == 2


def test_second_stage_clustering_three_successes_expand_the_immediate_next_partition(
    monkeypatch,
) -> None:
    ratios: list[float] = []

    def partition(numbered_candidates, *, view, batch_id, estimator, **kwargs):
        ratios.append(estimator.tokens_per_character)
        row = numbered_candidates[0]
        text = "x" * 1_000
        return [
            clustering.RequestChunk(
                view,
                f"{batch_id}:{view}:0001",
                (row,),
                text,
                estimator.estimate_text(text),
            )
        ], set()

    def original(config, selected, **kwargs):
        estimator = kwargs["estimator"]
        estimator.observe(
            request_characters=1_000,
            estimated_tokens=estimator.estimate_text("x" * 1_000),
            actual_tokens=1_000,
            status="success",
            error_type="",
        )
        return clustering.SparseViewExecution(
            {}, {}, {}, (), {}, set(), ({"status": "success"},), {}
        )

    monkeypatch.setattr(clustering, "partition_for_view", partition)
    monkeypatch.setattr(clustering, "CLUSTERING_ACTIVE_VIEWS", ("title",))
    executor = build_adaptive_sparse_view_executor(original, clustering)
    candidates = [
        {"candidate_id": f"candidate-{index}", "title": f"주제 {index}"}
        for index in range(4)
    ]
    estimator = AdaptiveInputTokenEstimator(tokens_per_character=2.0)

    result = executor(
        SimpleNamespace(),
        candidates,
        batch_id="batch-1",
        estimator=estimator,
    )

    assert len(ratios) == 4
    assert ratios[1] == ratios[0]
    assert ratios[2] == ratios[0]
    assert ratios[3] < ratios[0]
    assert len(result.calls) == 4
