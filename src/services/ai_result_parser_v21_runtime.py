from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any


SEO_REQUIRED_STRING_FIELDS = (
    "primary_keyword",
    "search_intent",
    "meta_description",
)
FREE_IMAGE_REQUIRED_STRING_FIELDS = (
    "status",
    "search_query",
    "page_url",
    "provider",
    "creator",
    "license_name",
    "license_url",
    "attribution",
    "checked_at",
    "verification_note",
)
FREE_IMAGE_REQUIRED_BOOL_FIELDS = (
    "commercial_use_allowed",
    "payment_required",
    "premium_or_subscription_required",
    "editorial_only",
)
FREE_IMAGE_STATUSES = {"verified_free", "not_found"}
CHATGPT_CONTENT_REFERENCE_PATTERN = re.compile(
    r"[ \t]*:contentReference\[oaicite:\d+\]\{index=\d+\}",
    re.IGNORECASE,
)


def _strip_chatgpt_content_references(value: Any) -> Any:
    """Remove ChatGPT UI-only citation tokens from parsed display content."""
    if isinstance(value, str):
        return CHATGPT_CONTENT_REFERENCE_PATTERN.sub("", value)
    if isinstance(value, list):
        return [_strip_chatgpt_content_references(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_chatgpt_content_references(item)
            for key, item in value.items()
        }
    return value


def _validate_seo(parser_module, value: Any, errors: list[str], warnings: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append("seo 항목은 객체여야 합니다.")
        return {
            "primary_keyword": "",
            "secondary_keywords": [],
            "search_intent": "",
            "meta_description": "",
        }

    normalized: dict[str, Any] = {}
    for field in SEO_REQUIRED_STRING_FIELDS:
        raw = value.get(field)
        if not isinstance(raw, str):
            errors.append(f"seo.{field}는 문자열이어야 합니다.")
            normalized[field] = ""
            continue
        clean = raw.strip()
        if not clean:
            errors.append(f"seo.{field}는 비워둘 수 없습니다.")
        normalized[field] = clean

    normalized["secondary_keywords"] = parser_module._validate_string_array(
        value.get("secondary_keywords"),
        "seo.secondary_keywords",
        errors,
    )
    meta = str(normalized.get("meta_description") or "")
    if meta and len(meta) > 220:
        warnings.append("seo.meta_description이 220자를 초과합니다. 검색 결과용 설명으로는 너무 길 수 있습니다.")
    return normalized


def _validate_free_image(parser_module, value: Any, path: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{path}는 객체여야 합니다.")
        return {
            "status": "not_found",
            "search_query": "",
            "page_url": "",
            "provider": "",
            "creator": "",
            "license_name": "",
            "license_url": "",
            "attribution": "",
            "checked_at": "",
            "commercial_use_allowed": False,
            "payment_required": False,
            "premium_or_subscription_required": False,
            "editorial_only": False,
            "verification_note": "",
        }

    normalized: dict[str, Any] = {}
    for field in FREE_IMAGE_REQUIRED_STRING_FIELDS:
        field_path = f"{path}.{field}"
        if field not in value:
            errors.append(f"{field_path} 항목이 없습니다.")
            normalized[field] = ""
            continue
        raw = value[field]
        if not isinstance(raw, str):
            errors.append(f"{field_path}는 문자열이어야 합니다.")
            normalized[field] = ""
            continue
        normalized[field] = raw.strip()

    for field in FREE_IMAGE_REQUIRED_BOOL_FIELDS:
        field_path = f"{path}.{field}"
        if field not in value:
            errors.append(f"{field_path} 항목이 없습니다.")
            normalized[field] = False
            continue
        raw = value[field]
        if not isinstance(raw, bool):
            errors.append(f"{field_path}는 true 또는 false여야 합니다.")
            normalized[field] = False
            continue
        normalized[field] = raw

    status = str(normalized.get("status") or "")
    if status not in FREE_IMAGE_STATUSES:
        errors.append(
            f"{path}.status는 verified_free 또는 not_found여야 합니다: {status or '비어 있음'}"
        )

    if not str(normalized.get("search_query") or ""):
        errors.append(f"{path}.search_query는 비워둘 수 없습니다.")
    if not str(normalized.get("verification_note") or ""):
        errors.append(f"{path}.verification_note는 비워둘 수 없습니다.")

    if status == "verified_free":
        required_verified_strings = (
            "page_url",
            "provider",
            "creator",
            "license_name",
            "license_url",
            "checked_at",
            "verification_note",
        )
        for field in required_verified_strings:
            if not str(normalized.get(field) or ""):
                errors.append(f"{path}.{field}는 무료 이미지 확정 시 비워둘 수 없습니다.")

        page_url = str(normalized.get("page_url") or "")
        license_url = str(normalized.get("license_url") or "")
        if page_url and not parser_module._valid_url(page_url):
            errors.append(f"{path}.page_url은 실제 http 또는 https 자산 페이지여야 합니다.")
        if license_url and not parser_module._valid_url(license_url):
            errors.append(f"{path}.license_url은 실제 http 또는 https 공식 라이선스 페이지여야 합니다.")
        if (
            page_url
            and license_url
            and parser_module._normalized_url(page_url)
            == parser_module._normalized_url(license_url)
        ):
            errors.append(
                f"{path}의 page_url과 license_url은 2중 확인을 위해 서로 다른 페이지여야 합니다."
            )

        if normalized.get("commercial_use_allowed") is not True:
            errors.append(f"{path}는 상업적 블로그 사용 가능이 확인되지 않아 무료 이미지로 사용할 수 없습니다.")
        if normalized.get("payment_required") is not False:
            errors.append(f"{path}는 결제가 필요한 이미지이므로 무료 이미지로 사용할 수 없습니다.")
        if normalized.get("premium_or_subscription_required") is not False:
            errors.append(f"{path}는 Premium·Pro·구독·크레딧 조건이 있어 무료 이미지로 사용할 수 없습니다.")
        if normalized.get("editorial_only") is not False:
            errors.append(f"{path}는 editorial-only 이미지이므로 일반 상업 블로그용 무료 이미지로 사용할 수 없습니다.")

    return normalized


def _validate_v21_image_blocks(parser_module, blocks: Any, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(blocks, list):
        return []

    normalized_free_images: list[dict[str, Any]] = []
    for index, block in enumerate(blocks, start=1):
        if not isinstance(block, dict) or str(block.get("type") or "").strip() != "image":
            continue
        path = f"blocks[{index}].free_image"
        if "free_image" not in block:
            errors.append(f"{path} 항목이 없습니다.")
            normalized_free_images.append(_validate_free_image(parser_module, None, path, errors))
            continue
        normalized_free_images.append(
            _validate_free_image(parser_module, block.get("free_image"), path, errors)
        )
    return normalized_free_images


def _attach_v21_image_metadata(
    data: dict[str, Any],
    free_images: list[dict[str, Any]],
) -> None:
    image_index = 0
    blocks = data.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "image":
                continue
            if image_index < len(free_images):
                block["free_image"] = deepcopy(free_images[image_index])
            image_index += 1

    image_index = 0
    prompts = data.get("image_prompts")
    if isinstance(prompts, list):
        for item in prompts:
            if not isinstance(item, dict):
                continue
            if image_index < len(free_images):
                item["free_image"] = deepcopy(free_images[image_index])
            image_index += 1


def install_ai_result_parser_v21_contract() -> None:
    """Add strict schema 2.1 SEO/free-image validation while preserving 1.0/2.0."""
    import src.services.ai_result_parser as parser_module

    current = parser_module.parse_ai_result
    if getattr(current, "_seo_free_image_v21_wrapper", False):
        return

    original_parse = current

    def parse_ai_result(raw_response: str):
        json_text = parser_module.extract_json_text(raw_response)
        try:
            loaded = json.loads(json_text) if json_text else None
        except json.JSONDecodeError:
            return original_parse(raw_response)
        if not isinstance(loaded, dict) or str(loaded.get("schema_version") or "").strip() != "2.1":
            return original_parse(raw_response)

        loaded = _strip_chatgpt_content_references(loaded)
        errors: list[str] = []
        warnings: list[str] = []
        normalized_seo = _validate_seo(
            parser_module,
            loaded.get("seo"),
            errors,
            warnings,
        )
        free_images = _validate_v21_image_blocks(
            parser_module,
            loaded.get("blocks"),
            errors,
        )

        compatibility_data = deepcopy(loaded)
        compatibility_data["schema_version"] = "2.0"
        compatibility_raw = json.dumps(compatibility_data, ensure_ascii=False)
        base_result = original_parse(compatibility_raw)
        if base_result.data is None:
            return parser_module.ParseResult(
                None,
                json_text,
                parser_module._deduplicate_messages([*base_result.errors, *errors]),
                parser_module._deduplicate_messages([*base_result.warnings, *warnings]),
            )

        data = base_result.data
        data["schema_version"] = "2.1"
        data["seo"] = normalized_seo
        _attach_v21_image_metadata(data, free_images)
        return parser_module.ParseResult(
            data,
            json_text,
            parser_module._deduplicate_messages([*base_result.errors, *errors]),
            parser_module._deduplicate_messages([*base_result.warnings, *warnings]),
        )

    parse_ai_result._seo_free_image_v21_wrapper = True  # type: ignore[attr-defined]
    parser_module.parse_ai_result = parse_ai_result
