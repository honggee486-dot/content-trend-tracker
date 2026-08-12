from __future__ import annotations

import json

from src.config import GeminiConfig
from src.services.trend_cluster_sparse_orchestrator import (
    classify_sparse_multi_view_batch,
    summarize_sparse_response_duplicates,
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


def _candidate(candidate_id: str, title: str, subject: str) -> dict:
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
            "subjects": (subject,),
            "title_fingerprints": (candidate_id,),
        },
        "existing_cluster_candidates": [],
    }


def _fake_sparse_api(
    _config: GeminiConfig,
    request_text: str,
    _request_hash: str,
    **_kwargs,
) -> tuple[str, int, int, int, int, str, str]:
    payload = json.loads(request_text.split("\n\n", 1)[1])
    same_event_rows = [
        row for row in payload["candidates"] if "같은 사건" in row["title"]
    ]
    same_event_nos = [int(row["candidate_no"]) for row in same_event_rows]
    representative_no = next(
        (
            int(row["candidate_no"])
            for row in same_event_rows
            if "두 번째 표현" in row["title"]
        ),
        same_event_nos[0] if same_event_nos else 0,
    )
    groups = []
    if payload["view"] in {"title", "event"} and len(same_event_nos) >= 2:
        groups.append(
            {
                "candidate_nos": same_event_nos,
                "representative_candidate_no": representative_no,
                "confidence": 95,
            }
        )
    response = {
        "existing_links": [],
        "new_groups": groups,
        "uncertain_nos": [],
        "conflicts": [],
    }
    return (
        json.dumps(response, ensure_ascii=False),
        1_000,
        100,
        0,
        1_100,
        "STOP",
        "",
    )


def test_orchestrator_merges_two_view_agreement_and_finalizes_singleton() -> None:
    candidates = [
        _candidate("a", "같은 사건 첫 번째 표현", "공통주제"),
        _candidate("b", "같은 사건 두 번째 표현", "공통주제"),
        _candidate("c", "완전히 독립된 사건", "독립주제"),
    ]
    estimator = AdaptiveInputTokenEstimator(tokens_per_character=1.0)
    limiter = SlidingWindowTpmLimiter(limit=250_000)

    result = classify_sparse_multi_view_batch(
        _config(),
        candidates,
        api_call=_fake_sparse_api,
        estimator=estimator,
        limiter=limiter,
    )

    assignments = {row["candidate_id"]: row for row in result.assignments}
    assert result.status == "success"
    assert result.requested_candidates == 3
    assert result.completed_candidates == 3
    assert result.uncertain_candidates == 0
    assert assignments["a"]["new_group_id"] == assignments["b"]["new_group_id"]
    assert assignments["a"]["representative_title"] == "같은 사건 두 번째 표현"
    assert assignments["c"]["new_group_id"] != assignments["a"]["new_group_id"]
    assert {row["analysis_view"] for row in result.calls} == {
        "title",
        "event",
        "identity",
    }
    assert all(row["estimated_input_tokens"] > 0 for row in result.calls)
    assert all(row["finish_reason"] == "STOP" for row in result.calls)
    assert "분석 관점 3개" in result.error_message
    assert f"실제 Gemini 요청 {len(result.calls)}회" in result.error_message


def test_orchestrator_keeps_candidate_uncertain_when_one_view_is_invalid() -> None:
    candidate = _candidate("a", "검토가 필요한 사건", "검토주제")

    def invalid_identity_api(
        _config: GeminiConfig,
        request_text: str,
        _request_hash: str,
        **_kwargs,
    ) -> tuple[str, int, int, int, int, str, str]:
        payload = json.loads(request_text.split("\n\n", 1)[1])
        response = {
            "existing_links": [],
            "new_groups": [],
            "uncertain_nos": [],
            "conflicts": [],
        }
        finish_reason = "MAX_TOKENS" if payload["view"] == "identity" else "STOP"
        return (json.dumps(response), 1_000, 100, 0, 1_100, finish_reason, "")

    result = classify_sparse_multi_view_batch(
        _config(),
        [candidate],
        api_call=invalid_identity_api,
        estimator=AdaptiveInputTokenEstimator(tokens_per_character=1.0),
        limiter=SlidingWindowTpmLimiter(limit=250_000),
    )

    assert result.status == "uncertain"
    assert result.assignments[0]["decision"] == "uncertain"
    assert result.uncertain_candidates == 1
    assert any(row["error_type"] == "sparse_response_invalid" for row in result.calls)


def test_sparse_response_duplicate_diagnostics_separate_violation_types() -> None:
    response = json.dumps(
        {
            "existing_links": [
                {"candidate_no": 1, "option_id": 1, "confidence": 95},
                {"candidate_no": 1, "option_id": 1, "confidence": 90},
            ],
            "new_groups": [
                {
                    "candidate_nos": [2, 2, 3],
                    "representative_candidate_no": 2,
                    "confidence": 90,
                },
                {
                    "candidate_nos": [3, 4],
                    "representative_candidate_no": 3,
                    "confidence": 90,
                },
            ],
            "uncertain_nos": [4, 4, 5, 2],
            "conflicts": [],
        }
    )

    diagnostics = summarize_sparse_response_duplicates(response)

    assert diagnostics == {
        "duplicate_existing_candidate_no": 1,
        "duplicate_within_new_group_candidate_no": 1,
        "duplicate_across_new_groups_candidate_no": 1,
        "duplicate_uncertain_candidate_no": 1,
        "cross_category_duplicate_candidate_no": 2,
    }


def test_orchestrator_surfaces_cross_category_duplicate_diagnostic() -> None:
    candidates = [
        _candidate("a", "중복 응답 후보 A", "공통주제"),
        _candidate("b", "중복 응답 후보 B", "공통주제"),
    ]

    def duplicate_title_api(
        _config: GeminiConfig,
        request_text: str,
        _request_hash: str,
        **_kwargs,
    ) -> tuple[str, int, int, int, int, str, str]:
        payload = json.loads(request_text.split("\n\n", 1)[1])
        if payload["view"] == "title":
            response = {
                "existing_links": [],
                "new_groups": [
                    {
                        "candidate_nos": [1, 2],
                        "representative_candidate_no": 1,
                        "confidence": 95,
                    }
                ],
                "uncertain_nos": [1],
                "conflicts": [],
            }
        else:
            response = {
                "existing_links": [],
                "new_groups": [],
                "uncertain_nos": [],
                "conflicts": [],
            }
        return (json.dumps(response), 1_000, 100, 0, 1_100, "STOP", "")

    result = classify_sparse_multi_view_batch(
        _config(),
        candidates,
        api_call=duplicate_title_api,
        estimator=AdaptiveInputTokenEstimator(tokens_per_character=1.0),
        limiter=SlidingWindowTpmLimiter(limit=250_000),
    )

    assert "duplicate_candidate_no=1" in result.error_message
    assert "cross_category_duplicate_candidate_no=1" in result.error_message
    assert "duplicate_within_new_group_candidate_no" not in result.error_message
