from __future__ import annotations

from typing import Any, Callable, Sequence

MAX_CANDIDATES_PER_GEMINI_CLUSTER_REQUEST = 300


def build_candidate_capped_partition(
    original: Callable[..., tuple[list[Any], set[int]]],
    *,
    max_candidates: int = MAX_CANDIDATES_PER_GEMINI_CLUSTER_REQUEST,
) -> Callable[..., tuple[list[Any], set[int]]]:
    """기존 토큰 분할을 유지한 채 너무 큰 후보 묶음만 더 잘게 나눕니다."""
    bounded_max = max(1, int(max_candidates))

    def wrapped(
        numbered_candidates: Sequence[tuple[int, dict[str, Any]]],
        *,
        view: str,
        batch_id: str,
        estimator: Any,
        target_tokens: int | None = None,
    ) -> tuple[list[Any], set[int]]:
        call_kwargs: dict[str, Any] = {
            "view": view,
            "batch_id": batch_id,
            "estimator": estimator,
        }
        if target_tokens is not None:
            call_kwargs["target_tokens"] = int(target_tokens)
        chunks, oversized = original(numbered_candidates, **call_kwargs)
        if not chunks or all(len(chunk.candidates) <= bounded_max for chunk in chunks):
            return chunks, oversized

        from src.services.trend_cluster_sparse_executor import RequestChunk
        from src.services.trend_cluster_sparse_protocol import build_sparse_request_text

        result: list[Any] = []
        for chunk in chunks:
            rows = list(chunk.candidates)
            for offset in range(0, len(rows), bounded_max):
                selected = rows[offset : offset + bounded_max]
                request_id = f"{batch_id}:{view}:{len(result) + 1:04d}"
                request_text = build_sparse_request_text(request_id, view, selected)
                result.append(
                    RequestChunk(
                        view=view,
                        batch_id=request_id,
                        candidates=tuple(selected),
                        request_text=request_text,
                        estimated_tokens=estimator.estimate_text(request_text),
                    )
                )
        return result, oversized

    setattr(wrapped, "_gemini_cluster_candidate_cap", True)
    setattr(wrapped, "_gemini_cluster_candidate_cap_original", original)
    return wrapped


def install_trend_cluster_request_cap_contract() -> None:
    """CLI·예약·Streamlit의 2차 군집에 같은 300개 후보 상한을 적용합니다."""
    from src.services import trend_cluster_sparse_executor as sparse_module

    current = sparse_module.partition_for_view
    if getattr(current, "_gemini_cluster_candidate_cap", False):
        return
    sparse_module.partition_for_view = build_candidate_capped_partition(current)
