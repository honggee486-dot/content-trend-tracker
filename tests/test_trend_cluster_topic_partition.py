from __future__ import annotations

from collections import Counter

from src.services.trend_cluster_token_runtime import AdaptiveInputTokenEstimator
from src.services.trend_cluster_topic_partition import partition_topic_chunks


def _candidate(candidate_id: str, topic: str, size: int = 300) -> dict:
    title = f"{topic} {candidate_id} " + ("가" * size)
    return {
        "candidate_id": candidate_id,
        "title": title,
        "examples": [title],
        "items": [],
        "safety_refined": True,
        "safety_profile": {
            "dates": (),
            "numbered_events": (),
            "products": (),
            "actions": (),
            "directions": (),
            "subjects": (topic,),
            "title_fingerprints": (candidate_id,),
        },
        "existing_cluster_candidates": [],
    }


def test_topic_group_moves_whole_to_next_chunk_when_it_fits_target() -> None:
    estimator = AdaptiveInputTokenEstimator(tokens_per_character=1.0)
    rows = [
        (1, _candidate("a1", "가주제", 500)),
        (2, _candidate("a2", "가주제", 500)),
        (3, _candidate("b1", "나주제", 500)),
        (4, _candidate("b2", "나주제", 500)),
    ]

    chunks, oversized = partition_topic_chunks(
        rows,
        view="title",
        batch_id="batch",
        estimator=estimator,
        target_tokens=2_500,
    )

    assert not oversized
    chunk_numbers = [
        {candidate_no for candidate_no, _ in chunk.candidates}
        for chunk in chunks
    ]
    assert any({1, 2} <= numbers for numbers in chunk_numbers)
    assert any({3, 4} <= numbers for numbers in chunk_numbers)


def test_large_topic_is_split_with_boundary_overlap() -> None:
    estimator = AdaptiveInputTokenEstimator(tokens_per_character=1.0)
    rows = [
        (index, _candidate(str(index), "같은주제", 500))
        for index in range(1, 13)
    ]

    chunks, oversized = partition_topic_chunks(
        rows,
        view="title",
        batch_id="batch",
        estimator=estimator,
        target_tokens=3_000,
    )

    counts = Counter(
        candidate_no
        for chunk in chunks
        for candidate_no, _ in chunk.candidates
    )
    assert not oversized
    assert len(chunks) > 1
    assert set(counts) == set(range(1, 13))
    assert any(count > 1 for count in counts.values())
    assert all(chunk.estimated_tokens <= 3_000 for chunk in chunks)


def test_candidate_count_does_not_split_request_when_token_budget_allows() -> None:
    estimator = AdaptiveInputTokenEstimator(tokens_per_character=0.20)
    rows = [
        (index, _candidate(str(index), f"주제{index:04d}", 1))
        for index in range(1, 401)
    ]

    chunks, oversized = partition_topic_chunks(
        rows,
        view="title",
        batch_id="batch",
        estimator=estimator,
        target_tokens=225_000,
    )

    assert not oversized
    assert len(chunks) == 1
    assert len(chunks[0].candidates) == 400
    assert chunks[0].estimated_tokens <= 225_000
