from __future__ import annotations

from src.services.trend_cluster_ai_review_service import (
    MAX_CANDIDATES_PER_REQUEST,
    MAX_REQUEST_CHARACTERS,
    select_cluster_batch_candidates,
)


def _large_candidates(count: int = 350) -> list[dict]:
    return [
        {
            "candidate_id": f"candidate-{index:03d}",
            "title": f"군집 후보 {index:03d} " + ("가" * 500),
            "examples": ["나" * 500],
            "item_count": 1,
            "source_types": ["naver"],
            "publishers": ["테스트 매체"],
            "first_seen_at": "2026-08-05 00:00:00",
            "last_seen_at": "2026-08-05 01:00:00",
            "existing_cluster_candidates": [],
        }
        for index in range(count)
    ]


def test_default_request_limits_are_five_hundred_thousand_and_three_hundred() -> None:
    assert MAX_REQUEST_CHARACTERS == 500_000
    assert MAX_CANDIDATES_PER_REQUEST == 300


def test_raised_limit_keeps_more_candidates_without_exceeding_candidate_cap() -> None:
    candidates = _large_candidates()

    previous_selection = select_cluster_batch_candidates(
        candidates,
        max_candidates=400,
        max_request_characters=300_000,
    )
    current_selection = select_cluster_batch_candidates(
        candidates,
        max_candidates=400,
    )

    assert len(previous_selection) < len(current_selection)
    assert len(current_selection) == 300
    assert current_selection[-1]["candidate_id"] == "candidate-299"
