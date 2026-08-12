from __future__ import annotations

from src.services.trend_cluster_safety_service import refine_first_stage_candidates


def _candidate(candidate_id: str, title: str, *tokens: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "title": title,
        "examples": [],
        "items": [
            {
                "source_item_id": f"{candidate_id}-item",
                "canonical_title": title,
                "source_type": "naver_news",
                "source_name": "테스트 뉴스",
                "identity_tokens": set(tokens),
                "editorial_identity_tokens": set(tokens),
                "calendar_identity_tokens": set(),
            }
        ],
        "item_count": 1,
        "first_stage_kind": "single",
        "existing_cluster_candidates": [],
    }


def test_same_company_without_strong_event_identity_stays_undetermined() -> None:
    refined = refine_first_stage_candidates(
        [
            _candidate(
                "ai-strategy",
                "삼성전자 AI 사업 전략 발표",
                "삼성전자",
                "ai",
                "사업",
                "전략",
                "발표",
            ),
            _candidate(
                "chip-investment",
                "삼성전자 반도체 투자 계획 발표",
                "삼성전자",
                "반도체",
                "투자",
                "계획",
                "발표",
            ),
        ]
    )

    assert len(refined) == 2
    assert {row["candidate_id"] for row in refined} == {
        "ai-strategy",
        "chip-investment",
    }
    assert all(row["first_stage_rule_ids"] == ("undetermined",) for row in refined)
