from __future__ import annotations

import json

from src.services.ai_result_parser import parse_ai_result
from src.services.content_pack_service import build_content_pack


def _pack() -> dict:
    return build_content_pack(
        {
            "title": "AI API 토큰 사용 요금 비교",
            "summary": "공식 가격표를 기준으로 입력·출력 토큰 요금을 정리",
            "category": "IT",
            "memo": "공식 가격 페이지를 확인",
        },
        [],
        factual_references=[
            {
                "reference_id": "ref_pricing",
                "reference_type": "official",
                "reference_type_label": "공식 자료",
                "title": "Official Pricing",
                "publisher": "Example AI",
                "url": "https://example.com/official/pricing",
                "published_at": "2026-08-15",
                "memo": "모델별 입력·출력 토큰 단가와 과금 단위를 확인",
            }
        ],
        audience="AI API 비용을 확인하려는 일반 사용자",
        purpose="공식 가격 근거를 쉽게 비교",
        angle="현재 공식 가격과 과금 단위를 정확히 설명",
        category="IT",
        target_length=2200,
        title_rules="과장하지 않는다",
        outline="핵심 요금\n비교\n주의사항",
        forbidden_expressions="무조건",
        fact_check_items="공식 가격과 기준일",
    )


def _free_not_found() -> dict:
    return {
        "status": "not_found",
        "search_query": "official AI API pricing screenshot fallback",
        "page_url": "",
        "provider": "",
        "creator": "",
        "license_name": "",
        "license_url": "",
        "attribution": "",
        "checked_at": "2026-08-15",
        "commercial_use_allowed": False,
        "payment_required": False,
        "premium_or_subscription_required": False,
        "editorial_only": False,
        "verification_note": "공식 가격 화면 직접 캡처를 우선하므로 무료 이미지 검색 결과는 사용하지 않음",
    }


def _seo() -> dict:
    return {
        "primary_keyword": "AI API 토큰 요금",
        "secondary_keywords": ["API 가격", "입력 토큰 비용", "출력 토큰 비용"],
        "search_intent": "공식 가격 기준으로 AI API 토큰 비용을 확인한다",
        "meta_description": "AI API의 입력·출력 토큰 요금을 공식 가격 페이지 기준으로 확인합니다.",
    }


def _official_capture_result() -> dict:
    source_url = "https://example.com/official/pricing"
    return {
        "schema_version": "2.1",
        "title": "AI API 토큰 요금 확인 방법",
        "summary": "공식 가격표에서 입력·출력 토큰 요금을 확인하는 방법을 정리합니다.",
        "category": "IT",
        "tags": ["AI API", "토큰 요금"],
        "seo": _seo(),
        "blocks": [
            {
                "type": "paragraph",
                "text": "AI API 가격은 변경될 수 있으므로 공식 가격 페이지의 현재 단가와 과금 단위를 함께 확인해야 합니다. 실제 글에는 기준일을 명시하고, 모델별 입력·출력 토큰 단가가 같은 표 안에서 비교되도록 정리합니다. 독자가 원문을 다시 확인할 수 있도록 공식 가격 페이지 링크도 함께 남깁니다.",
            },
            {
                "type": "image",
                "position": "토큰 요금 설명 바로 뒤",
                "purpose": "공식 가격표의 실제 요금 근거를 보여준다",
                "image_strategy": "official_capture",
                "source_capture": {
                    "needed": True,
                    "source_id": "R1",
                    "source_url": source_url,
                    "capture_target": "Pricing 표에서 모델명과 Input·Output 토큰 단가 열이 함께 보이는 영역",
                    "capture_note": "페이지 제목과 가격 단위를 포함하고 로그인 계정·결제정보는 제외",
                    "checked_at": "2026-08-15",
                },
                "user_action": "공식 링크 열기 → 지정된 가격표 영역 캡처 → 개인정보 확인 → 이 위치에 삽입",
                "free_image": _free_not_found(),
                "prompt": "공식 가격표 캡처를 사용할 수 없을 때 토큰 비용 구조만 설명하는 중립적 인포그래픽",
                "aspect_ratio": "16:9",
                "caption": "공식 가격 페이지의 토큰 요금표",
                "alt_text": "모델별 입력 및 출력 토큰 단가가 표시된 공식 가격표",
            },
        ],
        "fact_checks": [
            {
                "claim": "현재 토큰 단가는 공식 가격표 기준이다",
                "status": "verified",
                "reason": "공식 가격 페이지 확인",
                "source_ids": ["R1"],
            }
        ],
        "sources": [
            {
                "id": "R1",
                "title": "Official Pricing",
                "publisher": "Example AI",
                "url": source_url,
                "published_at": "2026-08-15",
            }
        ],
    }


def test_request_requires_official_capture_links_and_operator_tasks_without_per_post_ai_notice() -> None:
    prompt = _pack()["prompt_text"]

    assert "[공식 화면 캡처·이미지 작업 필수 규칙]" in prompt
    assert "official_capture" in prompt
    assert "source_capture.needed=true" in prompt
    assert "source_id, source_url, capture_target, capture_note, checked_at" in prompt
    assert "모든 image 블록의 `user_action`" in prompt
    assert "가격·요금·정책·통계" in prompt
    assert "로그인 화면·계정명·이메일·결제정보·쿠키" in prompt
    assert "개별 글의 title, summary, 본문, caption, alt_text" in prompt
    assert "블로그 공통 소개·정책 영역" in prompt
    assert '"image_strategy": "official_capture"' in prompt
    assert '"user_action"' in prompt


def test_official_capture_metadata_is_preserved_and_shown_at_image_position() -> None:
    result = parse_ai_result(json.dumps(_official_capture_result(), ensure_ascii=False))

    assert result.is_valid, result.errors
    assert result.data is not None
    image = result.data["blocks"][1]
    assert image["image_strategy"] == "official_capture"
    assert image["source_capture"]["needed"] is True
    assert image["source_capture"]["source_id"] == "R1"
    assert image["source_capture"]["source_url"] == "https://example.com/official/pricing"
    assert image["user_action"].startswith("공식 링크 열기")
    assert result.data["image_prompts"][0]["source_capture"]["capture_target"].startswith("Pricing 표")
    assert "*내가 할 일:" in result.data["body_markdown"]
    assert "[링크 열기](https://example.com/official/pricing)" in result.data["body_markdown"]
    assert "*캡처 대상: Pricing 표" in result.data["body_markdown"]


def test_official_capture_requires_known_source_and_matching_url() -> None:
    payload = _official_capture_result()
    payload["blocks"][1]["source_capture"]["source_id"] = "R9"
    payload["blocks"][1]["source_capture"]["source_url"] = "https://example.com/other"

    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))

    assert not result.is_valid
    joined = "\n".join(result.errors)
    assert "source_id가 sources에 없습니다" in joined


def test_legacy_schema_21_image_without_new_fields_remains_compatible_and_gets_operator_task() -> None:
    payload = _official_capture_result()
    image = payload["blocks"][1]
    image.pop("image_strategy")
    image.pop("source_capture")
    image.pop("user_action")
    image["free_image"] = {
        "status": "verified_free",
        "search_query": "AI developer working laptop",
        "page_url": "https://images.example.com/asset/1",
        "provider": "Example Images",
        "creator": "Creator",
        "license_name": "Free commercial license",
        "license_url": "https://images.example.com/license",
        "attribution": "",
        "checked_at": "2026-08-15",
        "commercial_use_allowed": True,
        "payment_required": False,
        "premium_or_subscription_required": False,
        "editorial_only": False,
        "verification_note": "개별 자산 페이지와 별도 라이선스 페이지를 확인",
    }

    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))

    assert result.is_valid, result.errors
    assert result.data is not None
    parsed_image = result.data["blocks"][1]
    assert parsed_image["image_strategy"] == "verified_free"
    assert parsed_image["source_capture"]["needed"] is False
    assert "무료 이미지 자산 페이지 열기" in parsed_image["user_action"]
    assert "*내가 할 일:" in result.data["body_markdown"]


def test_generated_image_without_capture_gets_generation_task() -> None:
    payload = _official_capture_result()
    image = payload["blocks"][1]
    image["image_strategy"] = "generated"
    image["source_capture"] = {
        "needed": False,
        "source_id": "",
        "source_url": "",
        "capture_target": "",
        "capture_note": "",
        "checked_at": "",
    }
    image["user_action"] = ""

    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))

    assert result.is_valid, result.errors
    assert result.data is not None
    parsed_image = result.data["blocks"][1]
    assert parsed_image["image_strategy"] == "generated"
    assert "생성용 prompt" in parsed_image["user_action"]
