from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import groupby
from typing import Any, Sequence

from src.services.trend_cluster_sparse_protocol import (
    build_sparse_request_text,
    candidate_payload,
    candidate_topic_sort_key,
)
from src.services.trend_cluster_token_runtime import (
    AdaptiveInputTokenEstimator,
    CLUSTERING_HARD_INPUT_TOKENS,
    CLUSTERING_TARGET_INPUT_TOKENS,
)

CLUSTERING_BOUNDARY_OVERLAP_TOKENS = 10_000


@dataclass(frozen=True)
class TopicRequestChunk:
    view: str
    batch_id: str
    candidates: tuple[tuple[int, dict[str, Any]], ...]
    request_text: str
    estimated_tokens: int


@dataclass(frozen=True)
class _PreparedRow:
    candidate_no: int
    candidate: dict[str, Any]
    payload_characters: int

    @property
    def row(self) -> tuple[int, dict[str, Any]]:
        return self.candidate_no, self.candidate


def _topic_bucket(candidate: dict[str, Any], view: str) -> tuple[str, ...]:
    key = candidate_topic_sort_key(candidate, view=view)
    return (key[0],) if key else ("",)


def partition_topic_chunks(
    numbered_candidates: Sequence[tuple[int, dict[str, Any]]],
    *,
    view: str,
    batch_id: str,
    estimator: AdaptiveInputTokenEstimator,
    target_tokens: int = CLUSTERING_TARGET_INPUT_TOKENS,
) -> tuple[list[TopicRequestChunk], set[int]]:
    """주제 경계를 우선 보존하고 큰 주제만 토큰 기준으로 겹쳐 나눕니다."""
    ordered = sorted(
        numbered_candidates,
        key=lambda row: candidate_topic_sort_key(row[1], view=view),
    )
    prepared = [
        _PreparedRow(
            candidate_no=candidate_no,
            candidate=candidate,
            payload_characters=len(
                json.dumps(
                    candidate_payload(candidate_no, candidate, view=view),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ),
        )
        for candidate_no, candidate in ordered
    ]
    topic_groups = [
        list(rows)
        for _key, rows in groupby(
            prepared,
            key=lambda row: _topic_bucket(row.candidate, view),
        )
    ]
    chunks: list[TopicRequestChunk] = []
    oversized: set[int] = set()
    current: list[_PreparedRow] = []
    current_payload_characters = 0
    empty_request_characters = len(
        build_sparse_request_text(f"{batch_id}:{view}:0001", view, ())
    )

    def estimate(payload_characters: int, count: int) -> int:
        return estimator.estimate_characters(
            empty_request_characters
            + max(0, int(payload_characters))
            + max(0, int(count) - 1)
        )

    def finalize(rows: Sequence[_PreparedRow]) -> None:
        if not rows:
            return
        request_id = f"{batch_id}:{view}:{len(chunks) + 1:04d}"
        text = build_sparse_request_text(
            request_id,
            view,
            [row.row for row in rows],
        )
        chunks.append(
            TopicRequestChunk(
                view=view,
                batch_id=request_id,
                candidates=tuple(row.row for row in rows),
                request_text=text,
                estimated_tokens=estimator.estimate_text(text),
            )
        )

    def overlap_tail(rows: Sequence[_PreparedRow]) -> list[_PreparedRow]:
        selected: list[_PreparedRow] = []
        payload_characters = 0
        for row in reversed(rows):
            next_characters = payload_characters + row.payload_characters
            if estimate(next_characters, len(selected) + 1) > CLUSTERING_BOUNDARY_OVERLAP_TOKENS:
                break
            selected.append(row)
            payload_characters = next_characters
        return list(reversed(selected))

    def split_large_topic(rows: Sequence[_PreparedRow]) -> None:
        segment: list[_PreparedRow] = []
        segment_characters = 0
        for row in rows:
            single_tokens = estimate(row.payload_characters, 1)
            if single_tokens > CLUSTERING_HARD_INPUT_TOKENS:
                oversized.add(row.candidate_no)
                continue
            trial_characters = segment_characters + row.payload_characters
            if segment and estimate(trial_characters, len(segment) + 1) > target_tokens:
                finalize(segment)
                segment = overlap_tail(segment)
                segment_characters = sum(
                    item.payload_characters for item in segment
                )
                while segment and estimate(
                    segment_characters + row.payload_characters,
                    len(segment) + 1,
                ) > target_tokens:
                    removed = segment.pop(0)
                    segment_characters -= removed.payload_characters
            segment.append(row)
            segment_characters += row.payload_characters
        finalize(segment)

    for group in topic_groups:
        group_characters = sum(row.payload_characters for row in group)
        if estimate(group_characters, len(group)) > target_tokens:
            finalize(current)
            current = []
            current_payload_characters = 0
            split_large_topic(group)
            continue
        combined_characters = current_payload_characters + group_characters
        if current and estimate(
            combined_characters,
            len(current) + len(group),
        ) > target_tokens:
            finalize(current)
            current = list(group)
            current_payload_characters = group_characters
        else:
            current.extend(group)
            current_payload_characters = combined_characters
    finalize(current)
    return chunks, oversized
