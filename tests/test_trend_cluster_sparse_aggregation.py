from __future__ import annotations

from src.services.trend_cluster_sparse_aggregation import (
    aggregate_sparse_assignments,
)


def _candidate(candidate_id: str, title: str, *, option: bool = False) -> dict:
    row = {
        "candidate_id": candidate_id,
        "title": title,
        "safety_profile": {
            "dates": (),
            "numbered_events": (),
            "products": (),
            "actions": (),
            "directions": (),
            "subjects": (title,),
            "title_fingerprints": (candidate_id,),
        },
        "existing_cluster_candidates": [],
    }
    if option:
        row["existing_cluster_candidates"] = [
            {
                "cluster_id": "existing-cluster",
                "title": "기존 군집",
                "examples": ["기존 군집 예시"],
            }
        ]
    return row


def _complete_views(*candidate_ids: str) -> dict[str, set[str]]:
    return {
        candidate_id: {"title", "event", "identity"}
        for candidate_id in candidate_ids
    }


def test_two_independent_views_are_required_for_new_merge() -> None:
    selected = [_candidate("a", "같은 사건 A"), _candidate("b", "같은 사건 B")]
    proposals = [
        {
            "view": "title",
            "candidate_ids": ("a", "b"),
            "representative_title": "같은 사건",
            "confidence": 95,
        },
        {
            "view": "event",
            "candidate_ids": ("a", "b"),
            "representative_title": "같은 사건",
            "confidence": 94,
        },
    ]

    assignments, diagnostics = aggregate_sparse_assignments(
        selected,
        views_by_candidate=_complete_views("a", "b"),
        failed_views_by_candidate={},
        existing_votes={},
        group_proposals=proposals,
        uncertain_views={},
        conflict_edges=set(),
    )

    assert {row["decision"] for row in assignments} == {"new"}
    assert len({row["new_group_id"] for row in assignments}) == 1
    assert diagnostics["accepted_edges"] == 1
    assert diagnostics["singleton_candidates"] == 0


def test_one_view_proposal_does_not_force_merge() -> None:
    selected = [_candidate("a", "후보 A"), _candidate("b", "후보 B")]

    assignments, diagnostics = aggregate_sparse_assignments(
        selected,
        views_by_candidate=_complete_views("a", "b"),
        failed_views_by_candidate={},
        existing_votes={},
        group_proposals=[
            {
                "view": "title",
                "candidate_ids": ("a", "b"),
                "representative_title": "비슷한 제목",
                "confidence": 90,
            }
        ],
        uncertain_views={},
        conflict_edges=set(),
    )

    assert len({row["new_group_id"] for row in assignments}) == 2
    assert diagnostics["accepted_edges"] == 0
    assert diagnostics["singleton_candidates"] == 2


def test_omitted_candidate_becomes_singleton_only_after_all_views_complete() -> None:
    selected = [_candidate("a", "독립 후보")]

    completed, _ = aggregate_sparse_assignments(
        selected,
        views_by_candidate=_complete_views("a"),
        failed_views_by_candidate={},
        existing_votes={},
        group_proposals=(),
        uncertain_views={},
        conflict_edges=set(),
    )
    incomplete, diagnostics = aggregate_sparse_assignments(
        selected,
        views_by_candidate={"a": {"title", "event"}},
        failed_views_by_candidate={},
        existing_votes={},
        group_proposals=(),
        uncertain_views={},
        conflict_edges=set(),
    )

    assert completed[0]["decision"] == "new"
    assert completed[0]["new_group_id"]
    assert incomplete[0]["decision"] == "uncertain"
    assert diagnostics["incomplete_candidates"] == 1


def test_explicit_uncertainty_is_held_instead_of_becoming_singleton() -> None:
    selected = [_candidate("a", "애매한 후보")]

    assignments, diagnostics = aggregate_sparse_assignments(
        selected,
        views_by_candidate=_complete_views("a"),
        failed_views_by_candidate={},
        existing_votes={},
        group_proposals=(),
        uncertain_views={"a": {"event"}},
        conflict_edges=set(),
    )

    assert assignments[0]["decision"] == "uncertain"
    assert diagnostics["explicit_uncertain_candidates"] == 1


def test_conflict_edge_blocks_merge_supported_by_two_views() -> None:
    selected = [_candidate("a", "후보 A"), _candidate("b", "후보 B")]
    proposals = [
        {
            "view": view,
            "candidate_ids": ("a", "b"),
            "representative_title": "병합 후보",
            "confidence": 95,
        }
        for view in ("title", "event")
    ]

    assignments, diagnostics = aggregate_sparse_assignments(
        selected,
        views_by_candidate=_complete_views("a", "b"),
        failed_views_by_candidate={},
        existing_votes={},
        group_proposals=proposals,
        uncertain_views={},
        conflict_edges={frozenset(("a", "b"))},
    )

    assert len({row["new_group_id"] for row in assignments}) == 2
    assert diagnostics["accepted_edges"] == 0


def test_existing_cluster_link_requires_two_view_votes() -> None:
    selected = [_candidate("a", "기존 연결 후보", option=True)]
    views = {"a": {"title", "event", "identity", "existing"}}

    one_vote, _ = aggregate_sparse_assignments(
        selected,
        views_by_candidate=views,
        failed_views_by_candidate={},
        existing_votes={"a": {"existing-cluster": [("existing", 96, 1)]}},
        group_proposals=(),
        uncertain_views={},
        conflict_edges=set(),
    )
    two_votes, diagnostics = aggregate_sparse_assignments(
        selected,
        views_by_candidate=views,
        failed_views_by_candidate={},
        existing_votes={
            "a": {
                "existing-cluster": [
                    ("event", 94, 1),
                    ("existing", 96, 1),
                ]
            }
        },
        group_proposals=(),
        uncertain_views={},
        conflict_edges=set(),
    )

    assert one_vote[0]["decision"] == "new"
    assert two_votes[0]["decision"] == "existing"
    assert two_votes[0]["existing_cluster_id"] == "existing-cluster"
    assert diagnostics["accepted_existing"] == 1
