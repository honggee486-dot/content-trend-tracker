from __future__ import annotations

import json
from threading import Lock

from src.config import GeminiConfig
from src.services.trend_cluster_sparse_orchestrator import (
    classify_sparse_multi_view_batch,
)
from src.services.trend_cluster_token_runtime import (
    AdaptiveInputTokenEstimator,
    SlidingWindowTpmLimiter,
)


def _config() -> GeminiConfig:
    return GeminiConfig(
        api_key="test-key",
        model="gemini-test",
        app_id="test-app",
        quota_scope_id="test-scope",
        timeout_seconds=60,
        retry_wait_seconds=1.0,
        retry_max_wait_seconds=2.0,
    )


def _candidate(candidate_id: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "title": f"순차 실행 검증 {candidate_id}",
        "examples": [],
        "items": [],
        "safety_refined": True,
        "safety_profile": {
            "dates": (),
            "numbered_events": (),
            "products": (),
            "actions": (),
            "directions": (),
            "subjects": ("순차검증",),
            "title_fingerprints": (candidate_id,),
        },
        "existing_cluster_candidates": [
            {
                "cluster_id": "existing-1",
                "title": "기존 순차 검증 군집",
                "examples": ["기존 순차 검증 군집"],
            }
        ],
    }


def test_views_and_token_chunks_are_called_one_at_a_time_in_fixed_order() -> None:
    lock = Lock()
    active_calls = 0
    max_active_calls = 0
    call_order: list[str] = []

    def fake_api(_config, request_text, _request_hash, **_kwargs):
        nonlocal active_calls, max_active_calls
        payload = json.loads(request_text.split("\n\n", 1)[1])
        with lock:
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            call_order.append(str(payload["view"]))
        response = {
            "existing_links": [],
            "new_groups": [],
            "uncertain_nos": [],
            "conflicts": [],
        }
        with lock:
            active_calls -= 1
        return json.dumps(response), 1_000, 10, 0, 1_010, "STOP", ""

    result = classify_sparse_multi_view_batch(
        _config(),
        [_candidate("a"), _candidate("b")],
        api_call=fake_api,
        estimator=AdaptiveInputTokenEstimator(tokens_per_character=1.0),
        limiter=SlidingWindowTpmLimiter(limit=250_000),
    )

    assert result.status == "success"
    assert max_active_calls == 1
    assert call_order == ["title", "event", "identity", "existing"]
    assert [row["analysis_view"] for row in result.calls] == call_order
