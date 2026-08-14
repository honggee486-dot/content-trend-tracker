from __future__ import annotations

import json

from src.services.ai_result_parser import parse_ai_result
from src.services.content_pack_service import build_content_pack


def _build_pack() -> dict:
    return build_content_pack(
        {"title": "여름 전기요금 줄이는 방법", "summary": "가정용 절약 팁", "category": "생활", "memo": ""},
        [],
        audience="일반 독자",
        purpose="실용 정보 제공",
        angle="과장 없이 실제로 도움이 되는 절약 방법 정리",
        category="생활",
        target_length=1800,
        title_rules="과장하지 않는다",
        outline="도입\n핵심\n주의\n정리",
        forbidden_expressions="무조건",
        fact_check_items="요금 기준 확인",
    )


def _seo() -> dict:
    return {
        "primary_keyword": "여름 전기요금 줄이는 방법",
        "secondary_keywords": ["전기요금 절약", "에어컨 전기요금"],
        "search_intent": "전기요금을 실제로 줄일 수 있는 방법을 찾는다",
        "meta_description": "여름철 전기요금을 줄일 때 먼저 확인할 항목과 실용적인 절약 방법을 정리합니다.",
    }


def _paragraph() -> dict:
    return {
        "type": "paragraph",
        "text": (
            "전기요금을 줄이려면 사용 환경과 요금 기준을 먼저 확인하는 것이 중요합니다. "
            "가전제품별 소비전력과 사용 시간을 함께 살펴보고, 실제 생활에서 바꾸기 쉬운 항목부터 적용합니다. "
            "에어컨은 설정 온도만 보지 말고 실내외 온도, 단열 상태, 필터 관리와 사용 패턴을 함께 확인해야 합니다. "
            "구체적인 요금 단가나 정책은 작성 시점의 공식 자료를 기준으로 확인하고, 확인되지 않은 수치를 단정하지 않습니다. "
            "독자가 바로 적용할 수 있는 방법과 상황에 따라 달라지는 조건을 구분하면 불필요한 오해도 줄일 수 있습니다."
        ),
    }


def _free_image(status: str = "verified_free") -> dict:
    if status == "not_found":
        return {
            "status": "not_found",
            "search_query": "air conditioner home electricity saving",
            "page_url": "",
            "provider": "",
            "creator": "",
            "license_name": "",
            "license_url": "",
            "attribution": "",
            "checked_at": "2026-08-14",
            "commercial_use_allowed": False,
            "payment_required": False,
            "premium_or_subscription_required": False,
            "editorial_only": False,
            "verification_note": "2중 확인을 통과한 무료 이미지를 찾지 못해 생성 이미지로 대체",
        }
    return {
        "status": "verified_free",
        "search_query": "air conditioner home electricity saving",
        "page_url": "https://images.example.com/assets/air-conditioner-123",
        "provider": "Example Images",
        "creator": "Example Creator",
        "license_name": "Example Free License",
        "license_url": "https://images.example.com/license",
        "attribution": "",
        "checked_at": "2026-08-14",
        "commercial_use_allowed": True,
        "payment_required": False,
        "premium_or_subscription_required": False,
        "editorial_only": False,
        "verification_note": "개별 자산 페이지의 무료 표시와 공식 라이선스의 상업 이용 조건을 각각 확인",
    }


def _image(free_image: dict) -> dict:
    return {
        "type": "image",
        "position": "핵심 절약 방법 설명 뒤",
        "purpose": "에어컨 사용 환경을 시각적으로 보여준다",
        "free_image": free_image,
        "prompt": "한국 가정의 거실에서 에어컨을 효율적으로 사용하는 자연스러운 정보성 사진",
        "aspect_ratio": "16:9",
        "caption": "에어컨 사용 환경 예시",
        "alt_text": "거실에서 에어컨을 사용하는 가정의 모습",
    }


def _result(*, image: dict | None = None) -> dict:
    blocks = [_paragraph()]
    if image is not None:
        blocks.append(image)
    return {
        "schema_version": "2.1",
        "title": "여름 전기요금 줄이는 방법과 확인할 점",
        "summary": "전기요금 절약 방법을 실제 적용 순서대로 정리합니다.",
        "category": "생활",
        "tags": ["전기요금", "절약"],
        "seo": _seo(),
        "blocks": blocks,
        "fact_checks": [],
        "sources": [],
    }


def test_prompt_requires_people_first_seo_and_double_checked_free_images() -> None:
    pack = _build_pack()
    prompt = pack["prompt_text"]

    assert '"schema_version": "2.1"' in prompt
    assert "[SEO 필수 규칙]" in prompt
    assert "Google·NAVER" in prompt
    assert "키워드 반복" in prompt
    assert "저품질 글의 특징" in prompt
    assert "사람이 직접 작성·편집한 글처럼" in prompt
    assert "블로그로 인식" not in prompt
    assert "free_image" in prompt
    assert "개별 자산 페이지" in prompt
    assert "별도 공식 라이선스" in prompt
    assert "Premium·Pro·구독·크레딧" in prompt
    assert "생성용 prompt" in prompt


def test_schema_21_accepts_seo_without_forcing_unneeded_images() -> None:
    result = parse_ai_result(json.dumps(_result(), ensure_ascii=False))

    assert result.is_valid
    assert result.data is not None
    assert result.data["schema_version"] == "2.1"
    assert result.data["seo"]["primary_keyword"] == "여름 전기요금 줄이는 방법"
    assert result.data["image_prompts"] == []


def test_verified_free_image_requires_and_preserves_double_check_metadata() -> None:
    result = parse_ai_result(
        json.dumps(_result(image=_image(_free_image())), ensure_ascii=False)
    )

    assert result.is_valid
    assert result.data is not None
    assert result.data["blocks"][1]["free_image"]["status"] == "verified_free"
    assert result.data["image_prompts"][0]["free_image"]["license_name"] == "Example Free License"
    assert result.data["image_prompts"][0]["prompt"].startswith("한국 가정")


def test_paid_or_subscription_image_cannot_be_verified_free() -> None:
    free_image = _free_image()
    free_image["payment_required"] = True
    free_image["premium_or_subscription_required"] = True

    result = parse_ai_result(
        json.dumps(_result(image=_image(free_image)), ensure_ascii=False)
    )

    assert not result.is_valid
    joined = "\n".join(result.errors)
    assert "결제가 필요한 이미지" in joined
    assert "Premium·Pro·구독·크레딧" in joined


def test_same_asset_and_license_page_fails_double_check() -> None:
    free_image = _free_image()
    free_image["license_url"] = free_image["page_url"]

    result = parse_ai_result(
        json.dumps(_result(image=_image(free_image)), ensure_ascii=False)
    )

    assert not result.is_valid
    assert any("서로 다른 페이지" in error for error in result.errors)


def test_unverified_free_image_falls_back_to_generation_prompt() -> None:
    result = parse_ai_result(
        json.dumps(
            _result(image=_image(_free_image(status="not_found"))),
            ensure_ascii=False,
        )
    )

    assert result.is_valid
    assert result.data is not None
    image_prompt = result.data["image_prompts"][0]
    assert image_prompt["free_image"]["status"] == "not_found"
    assert image_prompt["free_image"]["page_url"] == ""
    assert image_prompt["prompt"]


def test_schema_20_remains_backward_compatible() -> None:
    legacy = {
        "schema_version": "2.0",
        "title": "기존 초안",
        "summary": "기존 구조",
        "category": "생활",
        "tags": [],
        "blocks": [_paragraph()],
        "fact_checks": [],
        "sources": [],
    }

    result = parse_ai_result(json.dumps(legacy, ensure_ascii=False))

    assert result.is_valid
    assert result.data is not None
    assert result.data["schema_version"] == "2.0"


def test_schema_21_strips_chatgpt_content_reference_markers_but_keeps_raw_json() -> None:
    payload = _result()
    marker = ":contentReference[oaicite:12]{index=12}"
    payload["summary"] = f"요약 문장.{marker}"
    payload["seo"]["meta_description"] = f"검색 설명.{marker}"
    payload["blocks"][0]["text"] = f"{payload['blocks'][0]['text']} {marker}"
    payload["blocks"].append(
        {
            "type": "bullet_list",
            "items": [f"정책 기준 확인{marker}", "일반 항목"],
        }
    )
    payload["fact_checks"] = [
        {
            "claim": f"정책 기준 주장{marker}",
            "status": "needs_verification",
            "reason": f"공식 자료 확인 필요{marker}",
            "source_ids": [],
        }
    ]
    raw = json.dumps(payload, ensure_ascii=False)

    result = parse_ai_result(raw)

    assert result.is_valid
    assert result.data is not None
    assert "contentReference" in result.json_text
    assert result.json_text == raw
    assert "contentReference" not in json.dumps(result.data, ensure_ascii=False)
    assert "contentReference" not in result.data["body_markdown"]
    assert result.data["summary"] == "요약 문장."
    assert result.data["seo"]["meta_description"] == "검색 설명."
    assert result.data["fact_checks"][0]["reason"] == "공식 자료 확인 필요"
