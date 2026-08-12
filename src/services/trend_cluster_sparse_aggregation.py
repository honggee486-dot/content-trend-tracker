from __future__ import annotations

import hashlib
from collections import defaultdict
from itertools import combinations
from typing import Any, Iterable, Sequence

from src.services.trend_cluster_safety_service import (
    build_existing_option_payload,
    must_split_profiles,
)
from src.services.trend_cluster_sparse_protocol import (
    CLUSTERING_REQUIRED_BASE_VIEWS,
    candidate_profile,
    clean_text,
)

CLUSTERING_AUTO_MERGE_VIEW_COUNT = 2


def deterministic_group_id(candidate_ids: Iterable[str]) -> str:
    key = "|".join(
        sorted({clean_text(value) for value in candidate_ids if clean_text(value)})
    )
    return "sparse_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:18]


def _compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return not bool(must_split_profiles(candidate_profile(left), candidate_profile(right)))


def aggregate_sparse_assignments(
    selected: Sequence[dict[str, Any]],
    *,
    views_by_candidate: dict[str, set[str]],
    failed_views_by_candidate: dict[str, set[str]],
    existing_votes: dict[str, dict[str, list[tuple[str, int, int]]]],
    group_proposals: Sequence[dict[str, Any]],
    uncertain_views: dict[str, set[str]],
    conflict_edges: set[frozenset[str]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    candidate_map = {
        clean_text(candidate.get("candidate_id")): candidate for candidate in selected
    }
    expected_views: dict[str, set[str]] = {}
    for candidate_id, candidate in candidate_map.items():
        expected = set(CLUSTERING_REQUIRED_BASE_VIEWS)
        if build_existing_option_payload(candidate):
            expected.add("existing")
        expected_views[candidate_id] = expected

    incomplete = {
        candidate_id
        for candidate_id, expected in expected_views.items()
        if not expected.issubset(views_by_candidate.get(candidate_id, set()))
        or bool(failed_views_by_candidate.get(candidate_id))
    }
    explicit_uncertain = {
        candidate_id for candidate_id, views in uncertain_views.items() if views
    }

    accepted_existing: dict[str, tuple[str, int, int]] = {}
    conflicting_existing: set[str] = set()
    for candidate_id, cluster_votes in existing_votes.items():
        ranked = sorted(
            (
                (len({view for view, _, _ in votes}), cluster_id, votes)
                for cluster_id, votes in cluster_votes.items()
            ),
            reverse=True,
        )
        if not ranked:
            continue
        best_count, best_cluster_id, best_votes = ranked[0]
        if len(ranked) > 1 and ranked[1][0] == best_count:
            conflicting_existing.add(candidate_id)
        elif best_count >= CLUSTERING_AUTO_MERGE_VIEW_COUNT:
            accepted_existing[candidate_id] = (
                best_cluster_id,
                max(confidence for _, confidence, _ in best_votes),
                best_votes[0][2],
            )

    pair_views: defaultdict[frozenset[str], set[str]] = defaultdict(set)
    pair_confidence: defaultdict[frozenset[str], list[int]] = defaultdict(list)
    proposal_titles: list[tuple[set[str], str, int]] = []
    for proposal in group_proposals:
        members = {
            clean_text(value)
            for value in proposal.get("candidate_ids") or ()
            if clean_text(value) in candidate_map
        }
        if len(members) < 2:
            continue
        view = clean_text(proposal.get("view"))
        confidence = int(proposal.get("confidence") or 0)
        title = clean_text(proposal.get("representative_title"))
        proposal_titles.append((members, title, confidence))
        for left, right in combinations(sorted(members), 2):
            edge = frozenset((left, right))
            pair_views[edge].add(view)
            pair_confidence[edge].append(confidence)

    blocked = incomplete | explicit_uncertain | conflicting_existing
    parent = {candidate_id: candidate_id for candidate_id in candidate_map}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    accepted_edges = 0
    safety_blocked_edges = 0
    for edge, views in pair_views.items():
        if len(edge) != 2 or len(views) < CLUSTERING_AUTO_MERGE_VIEW_COUNT:
            continue
        if edge in conflict_edges:
            continue
        left, right = tuple(edge)
        if (
            left in blocked
            or right in blocked
            or left in accepted_existing
            or right in accepted_existing
        ):
            continue
        if not _compatible(candidate_map[left], candidate_map[right]):
            safety_blocked_edges += 1
            continue
        union(left, right)
        accepted_edges += 1

    components: defaultdict[str, list[str]] = defaultdict(list)
    for candidate_id in candidate_map:
        if candidate_id not in blocked and candidate_id not in accepted_existing:
            components[find(candidate_id)].append(candidate_id)
    component_by_candidate = {
        candidate_id: members
        for members in components.values()
        if len(members) >= 2
        for candidate_id in members
    }

    assignments: list[dict[str, Any]] = []
    for candidate_id, candidate in candidate_map.items():
        if candidate_id in blocked:
            assignments.append(
                {
                    "candidate_id": candidate_id,
                    "decision": "uncertain",
                    "existing_option_id": 0,
                    "existing_cluster_id": "",
                    "new_group_id": "",
                    "representative_title": "",
                    "confidence": 0,
                }
            )
            continue
        if candidate_id in accepted_existing:
            cluster_id, confidence, option_id = accepted_existing[candidate_id]
            assignments.append(
                {
                    "candidate_id": candidate_id,
                    "decision": "existing",
                    "existing_option_id": option_id,
                    "existing_cluster_id": cluster_id,
                    "new_group_id": "",
                    "representative_title": clean_text(candidate.get("title")),
                    "confidence": confidence,
                }
            )
            continue
        members = component_by_candidate.get(candidate_id)
        if members:
            member_set = set(members)
            title_candidates = [
                (confidence, title)
                for proposal_members, title, confidence in proposal_titles
                if title and len(proposal_members & member_set) >= 2
            ]
            confidence_values = [
                confidence
                for edge, values in pair_confidence.items()
                if candidate_id in edge and edge <= member_set
                for confidence in values
            ]
            assignments.append(
                {
                    "candidate_id": candidate_id,
                    "decision": "new",
                    "existing_option_id": 0,
                    "existing_cluster_id": "",
                    "new_group_id": deterministic_group_id(members),
                    "representative_title": (
                        max(title_candidates)[1]
                        if title_candidates
                        else clean_text(candidate_map[sorted(members)[0]].get("title"))
                    ),
                    "confidence": max(confidence_values or [90]),
                }
            )
            continue
        assignments.append(
            {
                "candidate_id": candidate_id,
                "decision": "new",
                "existing_option_id": 0,
                "existing_cluster_id": "",
                "new_group_id": deterministic_group_id([candidate_id]),
                "representative_title": clean_text(candidate.get("title")),
                "confidence": 90,
            }
        )

    group_counts: defaultdict[str, int] = defaultdict(int)
    for row in assignments:
        if row["decision"] == "new":
            group_counts[str(row["new_group_id"])] += 1
    diagnostics = {
        "accepted_existing": len(accepted_existing),
        "conflicting_existing": len(conflicting_existing),
        "accepted_edges": accepted_edges,
        "safety_blocked_edges": safety_blocked_edges,
        "incomplete_candidates": len(incomplete),
        "explicit_uncertain_candidates": len(explicit_uncertain),
        "singleton_candidates": sum(
            row["decision"] == "new"
            and group_counts[str(row["new_group_id"])] == 1
            for row in assignments
        ),
    }
    return assignments, diagnostics
