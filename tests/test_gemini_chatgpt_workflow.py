import json
from pathlib import Path

import pytest

from src.database import connect_database, init_database
from src.services.ai_result_parser import (
    parse_ai_result,
    validate_ai_result_against_references,
)
from src.services.content_pack_service import (
    get_topic_content_defaults,
    link_topic_to_trend_cluster,
    save_content_pack,
)
from src.services.draft_service import (
    get_draft,
    get_fact_check_summary,
    get_fact_checks,
    save_generation_and_draft,
)
from src.services.reference_service import add_topic_reference
from src.services.topic_service import add_manual_topic


def _seed_gemini_topic_analysis(con) -> tuple[str, str]:
    topic_id, _ = add_manual_topic(con, title="청년 지원금 신청 일정")
    content_plan = {
        "audience": "지원 대상 여부와 신청 일정을 확인하려는 청년",
        "purpose": "공식 공고를 기준으로 신청 조건과 일정을 빠르게 판단",
        "category": "정책",
        "target_length": 2200,
        "title_rules": [
            "지원 대상과 기준 시점을 제목에 드러낸다",
            "공식 확인 전 혜택을 확정하지 않는다",
        ],
        "outline": [
            "지원 제도 개요",
            "지원 대상",
            "신청 일정",
            "신청 방법",
            "확인할 주의사항",
        ],
        "forbidden_expressions": [
            "누구나 받을 수 있다",
            "무조건 지급된다",
            "100% 확정",
        ],
        "timeliness": {
            "type": "short_lived",
            "publish_priority": 5,
            "freshness_window_hours": 48,
            "recheck_before_writing": True,
            "reason": "신청 일정과 자격 조건이 공고에서 변경될 수 있음",
        },
        "evidence_plan": {
            "required_source_types": ["공식 시행 공고", "신청 안내 페이지"],
            "evidence_gaps": ["최종 신청 마감일", "소득 기준 적용 시점"],
            "official_search_queries": [
                "청년 지원금 공식 시행 공고",
                "청년 지원금 신청 안내 공식",
            ],
        },
        "primary_direction_reason": "신청 자격과 일정 중심 설명이 독자에게 가장 실용적임",
    }
    con.execute(
        """
        INSERT INTO trend_cluster_ai_profiles(
            cluster_id, canonical_title, display_title, summary,
            verification_points_json, content_plan_json,
            model_name, feature_version, created_at, updated_at
        ) VALUES (
            'cluster_support', '청년 지원금 신청 일정',
            '청년 지원금 신청 일정과 자격 확인',
            '공개 관심 신호가 확인됐지만 최신 공식 공고 확인이 필요한 글감',
            ?, ?, 'gemini-3.6-flash', '5', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """,
        [
            json.dumps(
                ["공식 시행일", "지원 대상", "신청 방법"],
                ensure_ascii=False,
            ),
            json.dumps(content_plan, ensure_ascii=False),
        ],
    )
    con.execute(
        """
        INSERT INTO trend_cluster_ai_angles(
            angle_id, cluster_id, canonical_title, angle_order,
            angle_label, angle_text, rationale, search_queries_json,
            model_name, feature_version, created_at, updated_at
        ) VALUES (
            'angle_support', 'cluster_support', '청년 지원금 신청 일정', 1,
            '신청 실무', '지원 자격과 신청 일정 중심으로 정리',
            '독자가 신청 가능 여부를 가장 먼저 판단해야 함',
            '["청년 지원금 공식 신청 일정"]',
            'gemini-3.6-flash', '5', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """
    )
    link_topic_to_trend_cluster(
        con,
        topic_id=topic_id,
        cluster_id="cluster_support",
    )
    reference_id, _ = add_topic_reference(
        con,
        topic_id=topic_id,
        reference_type="official_agency",
        title="청년 지원금 공식 시행 공고",
        publisher="지원정책 담당기관",
        url="https://official.example/support-notice",
        published_at="2026-07-30",
        memo="지원 대상, 신청 기간과 접수 방법을 확인하는 공식 공고",
    )
    return topic_id, reference_id


def _chatgpt_v2_payload() -> dict:
    return {
        "schema_version": "2.0",
        "title": "청년 지원금 신청 전 확인할 자격과 일정",
        "summary": "공식 시행 공고를 기준으로 지원 대상과 신청 절차를 정리합니다.",
        "category": "정책",
        "tags": ["청년지원", "지원금", "신청일정"],
        "blocks": [
            {
                "type": "heading",
                "level": 1,
                "text": "청년 지원금 신청 전 확인할 자격과 일정",
            },
            {
                "type": "paragraph",
                "text": (
                    "청년 지원금은 대상 조건과 신청 기간을 공식 공고에서 "
                    "확인한 뒤 준비해야 합니다. "
                )
                * 35,
            },
            {
                "type": "heading",
                "level": 2,
                "text": "신청 전에 확인할 항목",
            },
            {
                "type": "bullet_list",
                "items": [
                    "지원 대상과 소득 기준",
                    "신청 시작일과 최종 마감일",
                    "온라인 또는 방문 접수 방법",
                ],
            },
        ],
        "fact_checks": [
            {
                "claim": "최종 신청 마감일과 소득 기준 적용 시점",
                "status": "needs_verification",
                "reason": "공식 공고의 최신 적용 범위를 다시 확인해야 함",
                "source_ids": ["S1"],
            }
        ],
        "sources": [
            {
                "id": "S1",
                "title": "청년 지원금 공식 시행 공고",
                "publisher": "지원정책 담당기관",
                "url": "https://official.example/support-notice",
                "published_at": "2026-07-30",
            }
        ],
    }


def test_gemini_plan_to_chatgpt_draft_and_fact_check_end_to_end(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "ai-workflow.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        topic_id, reference_id = _seed_gemini_topic_analysis(con)
        defaults = get_topic_content_defaults(
            con,
            topic_id=topic_id,
            default_audience="일반 독자",
            default_purpose="정보 제공",
        )

        assert defaults["source"] == "ai"
        assert defaults["angle"].startswith("지원 자격과 신청 일정 중심으로 정리")
        assert "조사 초점: 청년 지원금 공식 신청 일정" in defaults["angle"]
        assert defaults["timeliness"]["publish_priority"] == 5
        assert defaults["evidence_plan"]["evidence_gaps"] == [
            "최종 신청 마감일",
            "소득 기준 적용 시점",
        ]
        assert defaults["primary_direction_reason"] == (
            "신청 자격과 일정 중심 설명이 독자에게 가장 실용적임"
        )
        assert defaults["fact_check_items"] == [
            "공식 시행일",
            "지원 대상",
            "신청 방법",
            "최종 신청 마감일",
            "소득 기준 적용 시점",
        ]

        pack = save_content_pack(
            con,
            topic_id=topic_id,
            audience=defaults["audience"],
            purpose=defaults["purpose"],
            angle=defaults["angle"],
            category=defaults["category"],
            target_length=defaults["target_length"],
            title_rules=defaults["title_rules"],
            outline=defaults["outline"],
            forbidden_expressions=defaults["forbidden_expressions"],
            fact_check_items=defaults["fact_check_items"],
            selected_source_item_ids=[],
            selected_reference_ids=[reference_id],
        )

        assert "Gemini와 ChatGPT" in pack["prompt_text"]
        assert "최종 신청 마감일" in pack["prompt_text"]
        assert "소득 기준 적용 시점" in pack["prompt_text"]
        assert pack["references"][0]["id"] == "S1"

        raw_response = json.dumps(_chatgpt_v2_payload(), ensure_ascii=False)
        parsed = parse_ai_result(raw_response)
        checked = validate_ai_result_against_references(
            parsed,
            pack["references"],
        )

        assert checked.is_valid
        generation_id, draft_id = save_generation_and_draft(
            con,
            content_pack_id=pack["content_pack_id"],
            ai_provider="ChatGPT",
            raw_response=raw_response,
            result=checked,
        )
        repeated_generation_id, repeated_draft_id = save_generation_and_draft(
            con,
            content_pack_id=pack["content_pack_id"],
            ai_provider="ChatGPT",
            raw_response=raw_response,
            result=checked,
        )

        assert repeated_generation_id == generation_id
        assert repeated_draft_id == draft_id
        assert con.execute("SELECT COUNT(*) FROM generation_sessions").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM draft_revisions").fetchone()[0] == 1

        draft = get_draft(con, draft_id)
        assert draft is not None
        assert draft["title"] == _chatgpt_v2_payload()["title"]
        assert len(draft["sources"]) == 1
        assert draft["sources"][0]["id"] == "S1"
        assert draft["sources"][0]["title"] == "청년 지원금 공식 시행 공고"
        assert draft["sources"][0]["publisher"] == "지원정책 담당기관"
        assert draft["sources"][0]["url"] == "https://official.example/support-notice"

        fact_checks = get_fact_checks(con, draft_id)
        assert len(fact_checks) == 1
        assert fact_checks[0]["check_status"] == "needs_verification"
        assert fact_checks[0]["source_ids"] == ["S1"]
        assert get_fact_check_summary(con, draft_id) == {
            "total": 1,
            "verified": 0,
            "needs_verification": 1,
            "needs_revision": 0,
            "unresolved": 1,
        }

        topic_status = con.execute(
            "SELECT status FROM topics WHERE topic_id = ?",
            [topic_id],
        ).fetchone()[0]
        generation_schema = con.execute(
            "SELECT schema_version FROM generation_sessions WHERE generation_id = ?",
            [generation_id],
        ).fetchone()[0]

    assert topic_status == "draft_complete"
    assert generation_schema == "2.0"


def test_invalid_chatgpt_source_never_creates_draft(tmp_path: Path) -> None:
    db_path = tmp_path / "invalid-ai-workflow.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        topic_id, reference_id = _seed_gemini_topic_analysis(con)
        defaults = get_topic_content_defaults(
            con,
            topic_id=topic_id,
            default_audience="일반 독자",
            default_purpose="정보 제공",
        )
        pack = save_content_pack(
            con,
            topic_id=topic_id,
            audience=defaults["audience"],
            purpose=defaults["purpose"],
            angle=defaults["angle"],
            category=defaults["category"],
            target_length=defaults["target_length"],
            title_rules=defaults["title_rules"],
            outline=defaults["outline"],
            forbidden_expressions=defaults["forbidden_expressions"],
            fact_check_items=defaults["fact_check_items"],
            selected_source_item_ids=[],
            selected_reference_ids=[reference_id],
        )

        payload = _chatgpt_v2_payload()
        payload["sources"][0]["url"] = "https://invented.example/fake"
        raw_response = json.dumps(payload, ensure_ascii=False)
        parsed = parse_ai_result(raw_response)
        checked = validate_ai_result_against_references(
            parsed,
            pack["references"],
        )

        assert not checked.is_valid
        assert any("URL과 다릅니다" in error for error in checked.errors)
        with pytest.raises(ValueError, match="형식 검사를 통과한 결과"):
            save_generation_and_draft(
                con,
                content_pack_id=pack["content_pack_id"],
                ai_provider="ChatGPT",
                raw_response=raw_response,
                result=checked,
            )

        assert con.execute("SELECT COUNT(*) FROM generation_sessions").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM fact_check_items").fetchone()[0] == 0
