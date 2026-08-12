from __future__ import annotations

import json

from src.config import GeminiConfig
from src.services.trend_cluster_ai_review_service import classify_cluster_batch


def _config() -> GeminiConfig:
    return GeminiConfig(
        api_key="test-key",
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


def _item(source_item_id: str, title: str, direction: str) -> dict:
    return {
        "source_item_id": source_item_id,
        "canonical_title": title,
        "source_type": "naver_news",
        "source_name": "테스트 뉴스",
        "identity_tokens": {"삼성전자", "주가", direction},
        "editorial_identity_tokens": {"삼성전자", "주가", direction},
        "calendar_identity_tokens": set(),
    }


def test_first_stage_split_and_second_stage_local_options_are_one_contract() -> None:
    candidate = {
        "candidate_id": "same-url",
        "title": "삼성전자 주가 변동",
        "examples": ["삼성전자 주가 급등", "삼성전자 주가 급락"],
        "items": [
            _item("up-item", "삼성전자 주가 급등", "급등"),
            _item("down-item", "삼성전자 주가 급락", "급락"),
        ],
        "item_count": 2,
        "first_stage_kind": "same_url",
        "identity_tokens": {"삼성전자", "주가", "급등", "급락"},
        "editorial_tokens": {"삼성전자", "주가", "급등", "급락"},
        "calendar_tokens": set(),
        "existing_cluster_candidates": [
            {
                "cluster_id": "up-old",
                "title": "삼성전자 주가 급등",
                "item_count": 3,
                "examples": ["삼성전자 주가 상승"],
            },
            {
                "cluster_id": "down-old",
                "title": "삼성전자 주가 급락",
                "item_count": 4,
                "examples": ["삼성전자 주가 하락"],
            },
        ],
    }

    def api_call(config, request_text, request_hash, **kwargs):
        payload = json.loads(request_text.split("\n\n", 1)[1])
        requested = payload["candidates"]
        assert len(requested) == 2
        assert {row["title"] for row in requested} == {
            "삼성전자 주가 급등",
            "삼성전자 주가 급락",
        }
        assert all(row["first_stage_rule_ids"] == ["must_split:direction"] for row in requested)
        assert all([option["option_id"] for option in row["existing_options"]] == [1] for row in requested)
        assert "up-old" not in request_text
        assert "down-old" not in request_text
        return (
            json.dumps(
                {
                    "assignments": [
                        {
                            "candidate_id": row["candidate_id"],
                            "decision": "existing",
                            "existing_option_id": 1,
                            "new_group_id": "",
                            "representative_title": row["title"],
                            "confidence": 97,
                        }
                        for row in requested
                    ]
                },
                ensure_ascii=False,
            ),
            100,
            40,
            0,
            140,
            "STOP",
            "",
        )

    result = classify_cluster_batch(
        _config(),
        [candidate],
        max_candidates=300,
        api_call=api_call,
    )

    assert result.status == "success"
    assert result.requested_candidates == 2
    assert result.completed_candidates == 2
    assert result.uncertain_candidates == 0
    assert {row["existing_cluster_id"] for row in result.assignments} == {
        "up-old",
        "down-old",
    }
