from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.services.content_quality_review_service import (
    build_luna_review_prompt,
    build_text_review_payload,
    build_writer_revision_prompt,
    parse_luna_review_response,
    prepare_final_text_data,
    run_post_writing_quality_cycle,
)


def _review_article() -> dict:
    return {
        "schema_version": "2.1",
        "title": "지원 정책 신청 방법",
        "summary": "공식 안내 기준으로 신청 절차를 정리합니다.",
        "category": "정책",
        "tags": ["지원정책"],
        "seo": {
            "primary_keyword": "지원 정책 신청 방법",
            "secondary_keywords": ["지원 대상"],
            "search_intent": "지원 대상과 신청 절차 확인",
            "meta_description": "공식 안내 기준 신청 방법",
        },
        "blocks": [
            {
                "type": "paragraph",
                "text": "지원 대상과 신청 기간은 공식 안내를 기준으로 확인해야 합니다. 신청 전에 대상 조건을 확인하세요.",
            },
            {
                "type": "image",
                "position": "신청 절차 뒤",
                "purpose": "공식 화면 근거",
                "image_strategy": "official_capture",
                "prompt": "temporary image prompt",
            },
        ],
        "fact_checks": [
            {
                "claim": "지원 대상은 공식 안내 기준이다",
                "status": "verified",
                "reason": "공식 자료 확인",
                "source_ids": ["R1"],
            }
        ],
        "sources": [
            {
                "id": "R1",
                "title": "공식 안내",
                "publisher": "Example Government",
                "url": "https://www.example.go.kr/policy",
                "published_at": "2026-08-22",
            }
        ],
        "image_prompts": [{"prompt": "temporary image prompt"}],
        "image_acquisition_plans": [{"status": "ready"}],
    }


def test_luna_review_payload_excludes_all_image_work() -> None:
    payload = build_text_review_payload(
        _review_article(),
        context={"angle": "공식 근거 중심", "audience": "일반 독자"},
    )

    assert payload["image_review_excluded"] is True
    assert [block["type"] for block in payload["blocks"]] == ["paragraph"]
    assert "image_prompts" not in payload
    assert "image_acquisition_plans" not in payload
    assert payload["sources"][0]["id"] == "R1"
    prompt = build_luna_review_prompt(_review_article())
    assert "글을 직접 다시 쓰지 말고" in prompt
    assert "이미지는 최종 텍스트" in prompt


def test_luna_review_contract_requires_requests_only_when_revision_is_needed() -> None:
    passed = parse_luna_review_response(
        json.dumps(
            {
                "schema_version": "1.0",
                "review_status": "pass",
                "overall_reason": "현재 근거와 구조에서 실질적인 수정이 필요하지 않습니다.",
                "revision_requests": [],
                "keep_points": ["공식 근거 중심 설명"],
            },
            ensure_ascii=False,
        )
    )
    assert passed.review_status == "pass"

    with pytest.raises(ValueError):
        parse_luna_review_response(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "review_status": "revision_needed",
                    "overall_reason": "수정이 필요합니다.",
                    "revision_requests": [],
                    "keep_points": [],
                },
                ensure_ascii=False,
            )
        )


def test_writer_revision_prompt_preserves_good_parts_and_defers_images() -> None:
    review = parse_luna_review_response(
        json.dumps(
            {
                "schema_version": "1.0",
                "review_status": "revision_needed",
                "overall_reason": "한 문장이 근거보다 강합니다.",
                "revision_requests": [
                    {
                        "severity": "high",
                        "type": "fact_support",
                        "target": "지원 대상 설명",
                        "problem": "출처 범위보다 강한 단정",
                        "request": "출처가 직접 뒷받침하는 범위로 표현을 완화",
                    }
                ],
                "keep_points": ["첫 문단의 핵심 답변"],
            },
            ensure_ascii=False,
        )
    )
    prompt = build_writer_revision_prompt(_review_article(), review)

    assert "글 전체를 새로 쓰지 마세요" in prompt
    assert "첫 문단의 핵심 답변" in prompt
    assert "모든 image block을 제외" in prompt
    assert "temporary image prompt" not in prompt


def test_quality_cycle_calls_luna_once_and_skips_writer_when_review_passes() -> None:
    calls = {"luna": 0, "writer": 0}

    def luna_runner(prompt: str) -> str:
        calls["luna"] += 1
        assert "image_review_excluded" in prompt
        return json.dumps(
            {
                "schema_version": "1.0",
                "review_status": "pass",
                "overall_reason": "수정 없이 최종 사실 확인으로 진행할 수 있습니다.",
                "revision_requests": [],
                "keep_points": ["공식 출처 중심 구성"],
            },
            ensure_ascii=False,
        )

    def writer_runner(prompt: str) -> str:
        calls["writer"] += 1
        raise AssertionError("pass면 작성 모델을 다시 호출하면 안 됩니다.")

    result = run_post_writing_quality_cycle(
        _review_article(),
        luna_runner=luna_runner,
        writer_runner=writer_runner,
    )

    assert result.status == "review_pass"
    assert result.succeeded
    assert calls == {"luna": 1, "writer": 0}
    assert result.requires_final_fact_check is True
    assert result.requires_image_planning is True
    assert result.final_text_data is not None
    assert result.final_text_data["image_prompts"] == []
    assert all(block["type"] != "image" for block in result.final_text_data["blocks"])


def test_quality_cycle_rewrites_once_with_existing_parser_contract() -> None:
    calls = {"luna": 0, "writer": 0}

    def luna_runner(_: str) -> str:
        calls["luna"] += 1
        return json.dumps(
            {
                "schema_version": "1.0",
                "review_status": "revision_needed",
                "overall_reason": "중복 문장을 줄여야 합니다.",
                "revision_requests": [
                    {
                        "severity": "medium",
                        "type": "redundancy",
                        "target": "첫 문단",
                        "problem": "같은 신청 확인 안내가 반복됩니다.",
                        "request": "반복 표현을 하나로 합치되 사실 내용은 유지",
                    }
                ],
                "keep_points": ["공식 안내 링크"],
            },
            ensure_ascii=False,
        )

    revised = _review_article()
    revised["blocks"] = [
        {
            "type": "paragraph",
            "text": "지원 정책은 공식 안내를 기준으로 대상과 기간을 확인해야 합니다.",
        }
    ]
    revised["image_prompts"] = []

    def writer_runner(prompt: str) -> str:
        calls["writer"] += 1
        assert "Luna 수정 요청" in prompt
        assert "이미지는 최종 텍스트 검증 뒤" in prompt
        return json.dumps(revised, ensure_ascii=False)

    def parser(text: str):
        return SimpleNamespace(is_valid=True, data=json.loads(text), errors=[])

    result = run_post_writing_quality_cycle(
        _review_article(),
        luna_runner=luna_runner,
        writer_runner=writer_runner,
        parse_result=parser,
    )

    assert result.status == "revision_complete"
    assert result.succeeded
    assert calls == {"luna": 1, "writer": 1}
    assert result.final_text_data is not None
    assert result.final_text_data["image_prompts"] == []
    assert result.requires_final_fact_check is True
    assert result.requires_image_planning is True


def test_invalid_rewrite_stops_without_model_loop() -> None:
    calls = {"luna": 0, "writer": 0, "parser": 0}

    def luna_runner(_: str) -> str:
        calls["luna"] += 1
        return json.dumps(
            {
                "schema_version": "1.0",
                "review_status": "revision_needed",
                "overall_reason": "수정이 필요합니다.",
                "revision_requests": [
                    {
                        "severity": "low",
                        "type": "clarity",
                        "target": "첫 문단",
                        "problem": "표현이 길다",
                        "request": "의미를 유지해 간결하게 정리",
                    }
                ],
                "keep_points": [],
            },
            ensure_ascii=False,
        )

    def writer_runner(_: str) -> str:
        calls["writer"] += 1
        return "invalid"

    def parser(_: str):
        calls["parser"] += 1
        return SimpleNamespace(is_valid=False, data=None, errors=["invalid json"])

    result = run_post_writing_quality_cycle(
        _review_article(),
        luna_runner=luna_runner,
        writer_runner=writer_runner,
        parse_result=parser,
    )

    assert result.status == "rewrite_invalid"
    assert calls == {"luna": 1, "writer": 1, "parser": 1}
    assert result.errors == ("invalid json",)


def test_prepare_final_text_data_removes_stale_image_execution_plan() -> None:
    final_data = prepare_final_text_data(_review_article())

    assert all(block["type"] != "image" for block in final_data["blocks"])
    assert final_data["image_prompts"] == []
    assert "image_acquisition_plans" not in final_data
