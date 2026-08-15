from __future__ import annotations

from src.services.trend_cluster_request_cap_runtime import (
    build_candidate_capped_partition,
)
from src.services.trend_cluster_sparse_executor import RequestChunk
from src.services.trend_cluster_sparse_protocol import build_sparse_request_text


class _Estimator:
    def estimate_text(self, text: str) -> int:
        return max(1, len(text) // 4)


def _rows(count: int):
    return [
        (
            index,
            {
                "candidate_id": f"candidate-{index}",
                "title": f"테스트 주제 {index}",
                "examples": [f"근거 {index}"],
            },
        )
        for index in range(1, count + 1)
    ]


def test_candidate_cap_splits_large_token_safe_chunk_into_300_item_requests() -> None:
    estimator = _Estimator()
    rows = _rows(1025)

    def original(numbered_candidates, *, view, batch_id, estimator, **kwargs):
        request_text = build_sparse_request_text(
            f"{batch_id}:{view}:0001",
            view,
            numbered_candidates,
        )
        return (
            [
                RequestChunk(
                    view=view,
                    batch_id=f"{batch_id}:{view}:0001",
                    candidates=tuple(numbered_candidates),
                    request_text=request_text,
                    estimated_tokens=estimator.estimate_text(request_text),
                )
            ],
            set(),
        )

    wrapped = build_candidate_capped_partition(original, max_candidates=300)
    chunks, oversized = wrapped(
        rows,
        view="title",
        batch_id="batch-1",
        estimator=estimator,
    )

    assert [len(chunk.candidates) for chunk in chunks] == [300, 300, 300, 125]
    assert oversized == set()
    assert [chunk.batch_id for chunk in chunks] == [
        "batch-1:title:0001",
        "batch-1:title:0002",
        "batch-1:title:0003",
        "batch-1:title:0004",
    ]
    flattened = [candidate_no for chunk in chunks for candidate_no, _ in chunk.candidates]
    assert flattened == list(range(1, 1026))
    assert all(chunk.estimated_tokens > 0 for chunk in chunks)


def test_candidate_cap_preserves_existing_small_token_chunk() -> None:
    estimator = _Estimator()
    rows = _rows(120)
    sentinel = RequestChunk(
        view="title",
        batch_id="batch-small:title:0001",
        candidates=tuple(rows),
        request_text="small",
        estimated_tokens=10,
    )

    def original(numbered_candidates, *, view, batch_id, estimator, **kwargs):
        return [sentinel], {999}

    wrapped = build_candidate_capped_partition(original, max_candidates=300)
    chunks, oversized = wrapped(
        rows,
        view="title",
        batch_id="batch-small",
        estimator=estimator,
    )

    assert chunks == [sentinel]
    assert oversized == {999}
