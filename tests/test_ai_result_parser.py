import json

from src.services.ai_result_parser import (
    parse_ai_result,
    validate_ai_result_against_references,
)


def valid_payload() -> dict:
    return {
        "schema_version": "1.0",
        "title": "테스트 제목",
        "summary": "요약",
        "category": "정보",
        "tags": ["테스트"],
        "body_markdown": "# 제목\n\n" + ("본문 내용입니다. " * 40),
        "fact_checks": [
            {
                "claim": "확인할 주장",
                "status": "needs_verification",
                "reason": "검증 필요",
                "source_ids": ["S1"],
            }
        ],
        "sources": [
            {
                "id": "S1",
                "title": "자료",
                "publisher": "출처",
                "url": "https://example.com/article",
                "published_at": "2026-07-01",
            }
        ],
        "image_prompts": [],
    }


def pack_references() -> list[dict]:
    return [
        {
            "id": "S1",
            "title": "자료팩 원본 자료",
            "publisher": "원본 출처",
            "url": "https://example.com/article",
            "published_at": "2026-07-01",
        }
    ]


def test_parse_json_fence() -> None:
    raw = "```json\n" + json.dumps(valid_payload(), ensure_ascii=False) + "\n```"
    result = parse_ai_result(raw)
    assert result.is_valid
    assert result.data["title"] == "테스트 제목"


def test_missing_field_is_error() -> None:
    payload = valid_payload()
    payload.pop("title")
    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))
    assert not result.is_valid
    assert any("title" in error for error in result.errors)


def test_pack_validation_accepts_matching_source() -> None:
    result = parse_ai_result(json.dumps(valid_payload(), ensure_ascii=False))
    checked = validate_ai_result_against_references(result, pack_references())
    assert checked.is_valid




def test_pack_validation_accepts_web_researched_source() -> None:
    payload = valid_payload()
    payload["sources"] = [
        {
            "id": "R1",
            "title": "공식 최신 자료",
            "publisher": "공식 기관",
            "url": "https://official.example/latest",
            "published_at": "2026-07-26",
        }
    ]
    payload["fact_checks"][0]["source_ids"] = ["R1"]
    payload["body_markdown"] += "\n\n출처: https://official.example/latest"
    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))
    checked = validate_ai_result_against_references(result, [])
    assert checked.is_valid


def test_pack_validation_rejects_researched_source_without_url() -> None:
    payload = valid_payload()
    payload["sources"] = [
        {
            "id": "R1",
            "title": "자료",
            "publisher": "기관",
            "url": "",
            "published_at": "2026-07-26",
        }
    ]
    payload["fact_checks"][0]["source_ids"] = ["R1"]
    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))
    checked = validate_ai_result_against_references(result, [])
    assert not checked.is_valid
    assert any("조사 출처 URL" in error for error in checked.errors)


def test_pack_validation_rejects_unknown_source_id() -> None:
    payload = valid_payload()
    payload["sources"][0]["id"] = "S9"
    payload["fact_checks"][0]["source_ids"] = ["S9"]
    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))
    checked = validate_ai_result_against_references(result, pack_references())
    assert not checked.is_valid
    assert any("R1, R2 형식" in error for error in checked.errors)


def test_pack_validation_rejects_changed_url() -> None:
    payload = valid_payload()
    payload["sources"][0]["url"] = "https://invented.example/new"
    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))
    checked = validate_ai_result_against_references(result, pack_references())
    assert not checked.is_valid
    assert any("URL과 다릅니다" in error for error in checked.errors)


def test_pack_validation_rejects_unknown_body_url() -> None:
    payload = valid_payload()
    payload["body_markdown"] += "\n\n추가 링크: https://invented.example/fake"
    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))
    checked = validate_ai_result_against_references(result, pack_references())
    assert not checked.is_valid
    assert any("자료팩이나 웹 조사 sources에 없는 URL" in error for error in checked.errors)


def test_concrete_claim_without_fact_checks_is_warning() -> None:
    payload = valid_payload()
    payload["body_markdown"] = "# 요금 안내\n\n가격은 10,000원이며 정책은 2026년에 변경됩니다. " * 20
    payload["fact_checks"] = []
    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))
    assert result.is_valid
    assert any("fact_checks가 비어" in warning for warning in result.warnings)


def test_validation_fingerprint_changes_with_pack_provider_or_response() -> None:
    from src.services.ai_result_parser import build_ai_result_validation_fingerprint

    base = build_ai_result_validation_fingerprint(
        content_pack_id="pack_1",
        ai_provider="ChatGPT",
        raw_response='{"title":"A"}',
    )
    assert base == build_ai_result_validation_fingerprint(
        content_pack_id="pack_1",
        ai_provider="ChatGPT",
        raw_response='{"title":"A"}',
    )
    assert base != build_ai_result_validation_fingerprint(
        content_pack_id="pack_2",
        ai_provider="ChatGPT",
        raw_response='{"title":"A"}',
    )
    assert base != build_ai_result_validation_fingerprint(
        content_pack_id="pack_1",
        ai_provider="Gemini",
        raw_response='{"title":"A"}',
    )
    assert base != build_ai_result_validation_fingerprint(
        content_pack_id="pack_1",
        ai_provider="ChatGPT",
        raw_response='{"title":"B"}',
    )

def valid_v2_payload() -> dict:
    return {
        "schema_version": "2.0",
        "title": "블록형 테스트 제목",
        "summary": "요약",
        "category": "정보",
        "tags": ["블록"],
        "blocks": [
            {
                "type": "heading",
                "level": 1,
                "text": "블록형 테스트 제목",
            },
            {
                "type": "paragraph",
                "text": "본문 내용입니다. " * 40,
            },
            {
                "type": "bullet_list",
                "items": ["첫 번째 항목", "두 번째 항목"],
            },
            {
                "type": "image",
                "position": "목록 뒤",
                "purpose": "본문 설명",
                "prompt": "차분한 정보성 설명 이미지",
                "aspect_ratio": "16:9",
                "caption": "설명 이미지",
                "alt_text": "본문 핵심 내용을 설명하는 이미지",
            },
        ],
        "fact_checks": [],
        "sources": [],
    }


def test_parse_schema_v2_blocks_into_legacy_markdown() -> None:
    result = parse_ai_result(json.dumps(valid_v2_payload(), ensure_ascii=False))
    assert result.is_valid
    assert result.data is not None
    assert result.data["schema_version"] == "2.0"
    assert result.data["body_markdown"].startswith("본문 내용입니다.")
    assert "# 블록형 테스트 제목" not in result.data["body_markdown"]
    assert "- 첫 번째 항목" in result.data["body_markdown"]
    assert "[이미지 1 삽입 위치]" in result.data["body_markdown"]


def test_parse_schema_v2_builds_legacy_image_prompts() -> None:
    result = parse_ai_result(json.dumps(valid_v2_payload(), ensure_ascii=False))
    assert result.is_valid
    assert result.data is not None
    assert result.data["image_prompts"] == [
        {
            "image_number": 1,
            "position": "목록 뒤",
            "purpose": "본문 설명",
            "prompt": "차분한 정보성 설명 이미지",
            "aspect_ratio": "16:9",
            "caption": "설명 이미지",
            "alt_text": "본문 핵심 내용을 설명하는 이미지",
        }
    ]


def test_schema_v2_reports_exact_invalid_block_path() -> None:
    payload = valid_v2_payload()
    payload["blocks"][1] = {"type": "video", "url": "https://example.com"}
    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))
    assert not result.is_valid
    assert any(
        "blocks[2].type은 지원하지 않는 값입니다: video" in error
        for error in result.errors
    )


def test_schema_v2_reports_invalid_list_item_path() -> None:
    payload = valid_v2_payload()
    payload["blocks"][2]["items"] = ["정상", 123]
    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))
    assert not result.is_valid
    assert any("blocks[3].items[2]는 문자열이어야 합니다." in error for error in result.errors)


def test_schema_v2_requires_image_fields() -> None:
    payload = valid_v2_payload()
    payload["blocks"][3].pop("alt_text")
    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))
    assert not result.is_valid
    assert any("blocks[4].alt_text 항목이 없습니다." in error for error in result.errors)


def test_unknown_schema_version_is_error() -> None:
    payload = valid_payload()
    payload["schema_version"] = "3.0"
    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))
    assert not result.is_valid
    assert any("지원하지 않는 schema_version" in error for error in result.errors)

def test_schema_v1_removes_duplicate_leading_title_heading() -> None:
    payload = valid_payload()
    payload["body_markdown"] = "# 테스트 제목\n\n" + ("본문 내용입니다. " * 40)
    result = parse_ai_result(json.dumps(payload, ensure_ascii=False))
    assert result.is_valid
    assert result.data is not None
    assert result.data["body_markdown"].startswith("본문 내용입니다.")
    assert "# 테스트 제목" not in result.data["body_markdown"]
