from __future__ import annotations

from src.services.trend_cluster_safety_service import (
    build_existing_option_payload,
    refine_first_stage_candidates,
    resolve_existing_option_id,
)


def _item(source_id: str, title: str, *tokens: str) -> dict:
    return {
        "source_item_id": source_id,
        "canonical_title": title,
        "source_type": "naver_news",
        "source_name": "테스트 뉴스",
        "editorial_identity_tokens": set(tokens),
        "identity_tokens": set(tokens),
        "calendar_identity_tokens": set(),
    }


def test_same_url_candidate_is_split_when_direction_conflicts() -> None:
    candidate = {
        "candidate_id": "same-url",
        "title": "삼성전자 주가 변동",
        "examples": ["삼성전자 주가 급등", "삼성전자 주가 급락"],
        "items": [
            _item("up", "삼성전자 주가 급등", "삼성전자", "주가", "급등"),
            _item("down", "삼성전자 주가 급락", "삼성전자", "주가", "급락"),
        ],
        "item_count": 2,
        "first_stage_kind": "same_url",
        "existing_cluster_candidates": [],
    }

    refined = refine_first_stage_candidates([candidate])

    assert len(refined) == 2
    assert {row["items"][0]["source_item_id"] for row in refined} == {"up", "down"}
    assert all(row["first_stage_kind"] == "rule_split" for row in refined)
    assert all("must_split:direction" in row["first_stage_rule_ids"] for row in refined)


def test_same_numbered_event_is_merged_only_with_shared_subject() -> None:
    candidates = [
        {
            "candidate_id": "lotto-a",
            "title": "로또 1234회 당첨번호 발표",
            "examples": [],
            "items": [
                _item(
                    "lotto-a-item",
                    "로또 1234회 당첨번호 발표",
                    "로또",
                    "1234회",
                    "당첨번호",
                    "발표",
                )
            ],
            "item_count": 1,
            "first_stage_kind": "single",
            "existing_cluster_candidates": [],
        },
        {
            "candidate_id": "lotto-b",
            "title": "로또복권 1234회 당첨 결과",
            "examples": [],
            "items": [
                _item(
                    "lotto-b-item",
                    "로또복권 1234회 당첨 결과",
                    "로또복권",
                    "1234회",
                    "당첨",
                    "결과",
                )
            ],
            "item_count": 1,
            "first_stage_kind": "single",
            "existing_cluster_candidates": [],
        },
    ]

    refined = refine_first_stage_candidates(candidates)

    assert len(refined) == 1
    assert refined[0]["item_count"] == 2
    assert refined[0]["first_stage_kind"] == "rule_merge"
    assert "must_merge:numbered_event" in refined[0]["first_stage_rule_ids"]


def test_same_company_different_action_remains_separate() -> None:
    candidates = [
        {
            "candidate_id": "release",
            "title": "삼성전자 갤럭시 S26 출시",
            "items": [
                _item(
                    "release-item",
                    "삼성전자 갤럭시 S26 출시",
                    "삼성전자",
                    "갤럭시",
                    "s26",
                    "출시",
                )
            ],
            "item_count": 1,
            "first_stage_kind": "single",
            "existing_cluster_candidates": [],
        },
        {
            "candidate_id": "factory",
            "title": "삼성전자 평택 공장 증설 투자",
            "items": [
                _item(
                    "factory-item",
                    "삼성전자 평택 공장 증설 투자",
                    "삼성전자",
                    "평택",
                    "공장",
                    "증설",
                    "투자",
                )
            ],
            "item_count": 1,
            "first_stage_kind": "single",
            "existing_cluster_candidates": [],
        },
    ]

    refined = refine_first_stage_candidates(candidates)

    assert len(refined) == 2
    assert {row["candidate_id"] for row in refined} == {"release", "factory"}


def test_conflicting_existing_option_is_removed_before_gemini_request() -> None:
    candidate = {
        "candidate_id": "release",
        "title": "삼성전자 갤럭시 S26 출시",
        "items": [
            _item(
                "release-item",
                "삼성전자 갤럭시 S26 출시",
                "삼성전자",
                "갤럭시",
                "s26",
                "출시",
            )
        ],
        "item_count": 1,
        "first_stage_kind": "single",
        "existing_cluster_candidates": [
            {
                "cluster_id": "release-old",
                "title": "삼성전자 갤럭시 S26 공개",
                "item_count": 3,
                "examples": ["갤럭시 S26 신제품 공개"],
            },
            {
                "cluster_id": "factory-old",
                "title": "삼성전자 평택 공장 증설 투자",
                "item_count": 4,
                "examples": ["평택 생산시설 투자"],
            },
        ],
    }

    refined = refine_first_stage_candidates([candidate])
    options = build_existing_option_payload(refined[0])

    assert [row["option_id"] for row in options] == [1]
    assert options[0]["title"] == "삼성전자 갤럭시 S26 공개"
    assert "cluster_id" not in options[0]
    assert resolve_existing_option_id(refined[0], 1) == "release-old"
    assert resolve_existing_option_id(refined[0], 2) == ""
