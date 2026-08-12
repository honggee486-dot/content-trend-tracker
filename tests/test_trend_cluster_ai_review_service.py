from __future__ import annotations

import json

from src.config import GeminiConfig
from src.services.trend_cluster_ai_review_service import (
    FEATURE_VERSION,
    MAX_CANDIDATES_PER_REQUEST,
    MAX_REQUEST_CHARACTERS,
    classify_cluster_batch,
    cluster_title_batches,
    select_cluster_batch_candidates,
)


def _config(api_key: str = "test-key") -> GeminiConfig:
    return GeminiConfig(
        api_key=api_key,
        model="gemini-3.5-flash-lite",
        app_id="content-trend-tracker",
        quota_scope_id="test",
        timeout_seconds=60,
        retry_wait_seconds=1.0,
        retry_max_wait_seconds=10.0,
        topic_angle_timeout_seconds=600,
        topic_angle_batch_limit=15,
        topic_angle_max_parallel_requests=1,
        topic_angle_request_stagger_seconds=0.0,
        topic_angle_min_opportunity_score=50.0,
        daily_request_reference_limit=500,
        draft_thinking_level="minimal",
        topic_angle_thinking_level="medium",
    )


def _candidates() -> list[dict]:
    return [
        {
            "candidate_id": "a",
            "title": "삼성전자 차세대 D램 출시",
            "examples": ["삼성전자 D램 신제품 공개"],
            "item_count": 2,
            "existing_cluster_candidates": [
                {
                    "cluster_id": "memory-old",
                    "title": "삼성전자 차세대 메모리 출시",
                    "item_count": 3,
                    "examples": ["차세대 메모리 공개"],
                }
            ],
        },
        {
            "candidate_id": "b",
            "title": "삼성 새 RAM 공개",
            "examples": [],
            "item_count": 1,
            "existing_cluster_candidates": [],
        },
        {
            "candidate_id": "c",
            "title": "삼성전자 신규 공장 투자",
            "examples": [],
            "item_count": 1,
            "existing_cluster_candidates": [],
        },
    ]


def test_assignment_response_uses_scoped_existing_new_and_uncertain_contract() -> None:
    calls = []

    def api_call(config, request_text, request_hash, **kwargs):
        calls.append((request_text, kwargs))
        return (
            json.dumps(
                {
                    "assignments": [
                        {
                            "candidate_id": "a",
                            "decision": "existing",
                            "existing_option_id": 1,
                            "new_group_id": "",
                            "representative_title": "삼성전자 차세대 D램 출시",
                            "confidence": 97,
                        },
                        {
                            "candidate_id": "b",
                            "decision": "new",
                            "existing_option_id": 0,
                            "new_group_id": "memory-new",
                            "representative_title": "삼성 RAM 신제품 공개",
                            "confidence": 94,
                        },
                        {
                            "candidate_id": "c",
                            "decision": "uncertain",
                            "existing_option_id": 0,
                            "new_group_id": "",
                            "representative_title": "",
                            "confidence": 50,
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            120,
            40,
            5,
            165,
            "STOP",
            "",
        )

    result = classify_cluster_batch(_config(), _candidates(), api_call=api_call)

    assert result.status == "partial"
    assert result.requested_candidates == 3
    assert result.completed_candidates == 2
    assert result.uncertain_candidates == 1
    assert FEATURE_VERSION == "4"
    assert result.calls[0]["feature_id"] == "trend_cluster_grouping_v3"
    assert result.calls[0]["feature_version"] == "4"
    assert result.calls[0]["total_tokens"] == 165
    assert result.assignments[0]["existing_cluster_id"] == "memory-old"
    assert result.assignments[0]["existing_option_id"] == 1
    assert calls[0][1]["use_google_search"] is False
    assert calls[0][1]["thinking_level"] == "minimal"
    assert "기존 군집끼리 서로 병합하지 말고" in calls[0][0]
    assert '"option_id":1' in calls[0][0]
    assert "memory-old" not in calls[0][0]
    assert '"cluster_id"' not in calls[0][0]


def test_out_of_scope_existing_option_and_missing_candidate_become_uncertain() -> None:
    def api_call(*args, **kwargs):
        return (
            json.dumps(
                {
                    "assignments": [
                        {
                            "candidate_id": "a",
                            "decision": "existing",
                            "existing_option_id": 5,
                            "new_group_id": "",
                            "representative_title": "메모리 출시",
                            "confidence": 99,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            10,
            10,
            0,
            20,
            "STOP",
            "",
        )

    result = classify_cluster_batch(_config(), _candidates(), api_call=api_call)

    assert result.status == "uncertain"
    assert result.completed_candidates == 0
    assert result.uncertain_candidates == 3
    assert "응답 누락 2개" in result.error_message
    assert "허용되지 않은 기존 군집 선택 5" in result.error_message


def test_non_existing_decision_rejects_nonzero_existing_option() -> None:
    def api_call(*args, **kwargs):
        return (
            json.dumps(
                {
                    "assignments": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "decision": "new",
                            "existing_option_id": 1,
                            "new_group_id": candidate["candidate_id"],
                            "representative_title": candidate["title"],
                            "confidence": 95,
                        }
                        for candidate in _candidates()
                    ]
                },
                ensure_ascii=False,
            ),
            10,
            10,
            0,
            20,
            "STOP",
            "",
        )

    result = classify_cluster_batch(_config(), _candidates(), api_call=api_call)

    assert result.status == "uncertain"
    assert result.completed_candidates == 0
    assert "existing_option_id는 0이어야 함" in result.error_message


def test_missing_api_key_does_not_call_api() -> None:
    called = False

    def api_call(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("호출되면 안 됨")

    result = classify_cluster_batch(
        _config(api_key=""),
        _candidates(),
        api_call=api_call,
    )

    assert result.status == "missing_api_key"
    assert result.assignments == ()
    assert result.calls == ()
    assert called is False


def test_invalid_json_records_failed_call() -> None:
    def api_call(*args, **kwargs):
        return ("not-json", None, None, None)

    result = classify_cluster_batch(_config(), _candidates(), api_call=api_call)

    assert result.status == "failed"
    assert result.assignments == ()
    assert len(result.calls) == 1
    assert result.calls[0]["status"] == "failed"


def test_batch_selection_limits_candidate_count_and_request_characters() -> None:
    many = [
        {
            "candidate_id": f"candidate-{index:03d}",
            "title": f"아주 긴 군집 제목 {index:03d} " + ("가" * 4000),
            "examples": ["나" * 4000],
            "item_count": 1,
            "existing_cluster_candidates": [],
        }
        for index in range(450)
    ]

    selected = select_cluster_batch_candidates(
        many,
        max_candidates=400,
        max_request_characters=MAX_REQUEST_CHARACTERS,
    )

    assert 1 <= len(selected) < 300
    assert [row["candidate_id"] for row in selected] == [
        f"candidate-{index:03d}" for index in range(len(selected))
    ]


def test_batch_selection_allows_up_to_three_hundred_short_candidates() -> None:
    many = [
        {
            "candidate_id": f"candidate-{index:03d}",
            "title": f"군집 후보 {index:03d}",
            "examples": [],
            "item_count": 1,
            "existing_cluster_candidates": [],
        }
        for index in range(450)
    ]

    selected = select_cluster_batch_candidates(
        many,
        max_candidates=450,
        max_request_characters=500_000,
    )

    assert MAX_CANDIDATES_PER_REQUEST == 300
    assert len(selected) == 300
    assert selected[-1]["candidate_id"] == "candidate-299"


def test_compatibility_wrapper_reports_start_and_finish_progress() -> None:
    progress = []

    def api_call(*args, **kwargs):
        return (
            json.dumps(
                {
                    "assignments": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "decision": "new",
                            "existing_option_id": 0,
                            "new_group_id": candidate["candidate_id"],
                            "representative_title": candidate["title"],
                            "confidence": 95,
                        }
                        for candidate in _candidates()
                    ]
                },
                ensure_ascii=False,
            ),
            10,
            10,
            0,
            20,
            "STOP",
            "",
        )

    result = cluster_title_batches(
        _config(),
        [{"batch_id": "batch", "candidates": _candidates()}],
        api_call=api_call,
        progress_callback=lambda value, message: progress.append((value, message)),
    )

    assert result.status == "success"
    assert progress[0][0] == 0.0
    assert progress[-1][0] == 1.0
