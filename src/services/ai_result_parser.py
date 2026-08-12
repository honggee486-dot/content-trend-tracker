from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse, urlsplit, urlunsplit

from src.services.blog_output_service import strip_duplicate_leading_title

JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
URL_PATTERN = re.compile(r"https?://[^\s<>{}\[\]\"']+", re.IGNORECASE)
RESEARCHED_SOURCE_ID_PATTERN = re.compile(r"^R[1-9]\d*$", re.IGNORECASE)
CONCRETE_FACT_PATTERN = re.compile(
    r"(?:\d[\d,.]*\s*(?:%|원|만원|억원|명|개|건|배|년|월|일|시간|분)|"
    r"법률|법령|정책|요금|가격|통계|조사|연구|지원금|세금|과태료|벌금|시장점유율)",
    re.IGNORECASE,
)
SCHEMA_V1_REQUIRED_FIELDS = {
    "schema_version",
    "title",
    "summary",
    "category",
    "tags",
    "body_markdown",
    "fact_checks",
    "sources",
    "image_prompts",
}
SCHEMA_V2_REQUIRED_FIELDS = {
    "schema_version",
    "title",
    "summary",
    "category",
    "tags",
    "blocks",
    "fact_checks",
    "sources",
}
REQUIRED_FIELDS = SCHEMA_V1_REQUIRED_FIELDS
SUPPORTED_SCHEMA_VERSIONS = {"1.0", "2.0"}
SUPPORTED_BLOCK_TYPES = {
    "paragraph",
    "heading",
    "bullet_list",
    "numbered_list",
    "quote",
    "image",
}


@dataclass
class ParseResult:
    data: dict[str, Any] | None
    json_text: str
    errors: list[str]
    warnings: list[str]

    @property
    def is_valid(self) -> bool:
        return self.data is not None and not self.errors


def build_ai_result_validation_fingerprint(
    *,
    content_pack_id: str,
    ai_provider: str,
    raw_response: str,
) -> str:
    """Return a stable token for the exact AI result that was validated."""
    payload = "\x1f".join(
        [
            str(content_pack_id or "").strip(),
            str(ai_provider or "").strip(),
            str(raw_response or ""),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract_json_text(raw_response: str) -> str:
    text = str(raw_response or "").strip()
    match = JSON_FENCE_PATTERN.search(text)
    if match:
        return match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1].strip()
    return text


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _normalized_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlsplit(text)
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def _deduplicate_messages(messages: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for message in messages:
        if message not in seen:
            seen.add(message)
            result.append(message)
    return result


def _normalized_title(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"\s+#+\s*$", "", text)
    text = text.strip("*_`~ \t")
    return re.sub(r"\s+", " ", text).casefold()


def _validate_string(
    data: dict[str, Any],
    key: str,
    errors: list[str],
    *,
    required_nonempty: bool = False,
) -> None:
    if key not in data:
        return
    value = data[key]
    if not isinstance(value, str):
        errors.append(f"{key} 항목은 문자열이어야 합니다.")
    elif required_nonempty and not value.strip():
        errors.append(f"{key} 항목은 비워둘 수 없습니다.")


def _validate_string_array(
    value: Any,
    path: str,
    errors: list[str],
    *,
    require_nonempty: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{path}는 배열이어야 합니다.")
        return []
    if require_nonempty and not value:
        errors.append(f"{path}는 하나 이상의 항목이 필요합니다.")
        return []
    normalized: list[str] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, str):
            errors.append(f"{path}[{index}]는 문자열이어야 합니다.")
            continue
        clean = item.strip()
        if not clean:
            errors.append(f"{path}[{index}]는 비워둘 수 없습니다.")
            continue
        normalized.append(clean)
    return normalized


def _validate_image_block(
    block: dict[str, Any],
    path: str,
    errors: list[str],
) -> dict[str, Any]:
    required_string_fields = [
        "position",
        "purpose",
        "prompt",
        "aspect_ratio",
        "caption",
        "alt_text",
    ]
    normalized: dict[str, Any] = {"type": "image"}
    for field in required_string_fields:
        field_path = f"{path}.{field}"
        if field not in block:
            errors.append(f"{field_path} 항목이 없습니다.")
            normalized[field] = ""
            continue
        value = block[field]
        if not isinstance(value, str):
            errors.append(f"{field_path}는 문자열이어야 합니다.")
            normalized[field] = ""
            continue
        clean = value.strip()
        if field != "caption" and not clean:
            errors.append(f"{field_path}는 비워둘 수 없습니다.")
        normalized[field] = clean
    return normalized


def _validate_blocks(
    blocks: Any,
    errors: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(blocks, list):
        errors.append("blocks 항목은 배열이어야 합니다.")
        return []
    if not blocks:
        errors.append("blocks 항목은 하나 이상의 본문 블록이 필요합니다.")
        return []

    normalized: list[dict[str, Any]] = []
    for index, block in enumerate(blocks, start=1):
        path = f"blocks[{index}]"
        if not isinstance(block, dict):
            errors.append(f"{path}는 객체여야 합니다.")
            continue

        block_type = block.get("type")
        if not isinstance(block_type, str) or not block_type.strip():
            errors.append(f"{path}.type은 비워둘 수 없는 문자열이어야 합니다.")
            continue
        block_type = block_type.strip()
        if block_type not in SUPPORTED_BLOCK_TYPES:
            errors.append(f"{path}.type은 지원하지 않는 값입니다: {block_type}")
            continue

        if block_type in {"paragraph", "quote"}:
            text = block.get("text")
            if not isinstance(text, str):
                errors.append(f"{path}.text는 문자열이어야 합니다.")
                normalized.append({"type": block_type, "text": ""})
                continue
            clean = text.strip()
            if not clean:
                errors.append(f"{path}.text는 비워둘 수 없습니다.")
            normalized.append({"type": block_type, "text": clean})
            continue

        if block_type == "heading":
            text = block.get("text")
            if not isinstance(text, str):
                errors.append(f"{path}.text는 문자열이어야 합니다.")
                clean_text = ""
            else:
                clean_text = text.strip()
                if not clean_text:
                    errors.append(f"{path}.text는 비워둘 수 없습니다.")
            level = block.get("level")
            if not isinstance(level, int) or isinstance(level, bool):
                errors.append(f"{path}.level은 1부터 6 사이의 정수여야 합니다.")
                clean_level = 2
            elif not 1 <= level <= 6:
                errors.append(f"{path}.level은 1부터 6 사이여야 합니다.")
                clean_level = min(max(level, 1), 6)
            else:
                clean_level = level
            normalized.append(
                {
                    "type": "heading",
                    "level": clean_level,
                    "text": clean_text,
                }
            )
            continue

        if block_type in {"bullet_list", "numbered_list"}:
            items = _validate_string_array(
                block.get("items"),
                f"{path}.items",
                errors,
                require_nonempty=True,
            )
            normalized.append({"type": block_type, "items": items})
            continue

        normalized.append(_validate_image_block(block, path, errors))

    return normalized


def blocks_to_markdown(
    *,
    title: str,
    blocks: Iterable[dict[str, Any]],
) -> str:
    """Convert validated schema 2.0 blocks into the legacy Markdown draft body."""
    sections: list[str] = []
    image_number = 0
    first_content_block = True
    normalized_title = _normalized_title(title)

    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").strip()

        if block_type == "paragraph":
            text = str(block.get("text") or "").strip()
            if text:
                sections.append(text)
                first_content_block = False
        elif block_type == "heading":
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            if first_content_block and normalized_title and _normalized_title(text) == normalized_title:
                first_content_block = False
                continue
            level = block.get("level")
            level = level if isinstance(level, int) and 1 <= level <= 6 else 2
            sections.append(f"{'#' * level} {text}")
            first_content_block = False
        elif block_type == "bullet_list":
            items = [str(item).strip() for item in block.get("items") or [] if str(item).strip()]
            if items:
                sections.append("\n".join(f"- {item}" for item in items))
                first_content_block = False
        elif block_type == "numbered_list":
            items = [str(item).strip() for item in block.get("items") or [] if str(item).strip()]
            if items:
                sections.append("\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1)))
                first_content_block = False
        elif block_type == "quote":
            text = str(block.get("text") or "").strip()
            if text:
                sections.append("\n".join(f"> {line}" for line in text.splitlines()))
                first_content_block = False
        elif block_type == "image":
            image_number += 1
            image_lines = [f"[이미지 {image_number} 삽입 위치]"]
            caption = str(block.get("caption") or "").strip()
            if caption:
                image_lines.append(f"*캡션: {caption}*")
            sections.append("\n".join(image_lines))
            first_content_block = False

    return "\n\n".join(section for section in sections if section).strip()


def image_prompts_from_blocks(
    blocks: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build the legacy image_prompts list from schema 2.0 image blocks."""
    prompts: list[dict[str, Any]] = []
    image_number = 0
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        image_number += 1
        prompts.append(
            {
                "image_number": image_number,
                "position": str(block.get("position") or "").strip(),
                "purpose": str(block.get("purpose") or "").strip(),
                "prompt": str(block.get("prompt") or "").strip(),
                "aspect_ratio": str(block.get("aspect_ratio") or "").strip(),
                "caption": str(block.get("caption") or "").strip(),
                "alt_text": str(block.get("alt_text") or "").strip(),
            }
        )
    return prompts


def _validate_sources_and_fact_checks(
    data: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    source_ids: set[str] = set()
    sources = data.get("sources")
    if isinstance(sources, list):
        for index, source in enumerate(sources, start=1):
            if not isinstance(source, dict):
                errors.append(f"sources[{index}]는 객체여야 합니다.")
                continue
            source_id = str(source.get("id") or "").strip()
            if not source_id:
                errors.append(f"sources[{index}]에 id가 없습니다.")
            elif source_id in source_ids:
                errors.append(f"중복된 출처 ID가 있습니다: {source_id}")
            else:
                source_ids.add(source_id)
            url = str(source.get("url") or "").strip()
            if url and not _valid_url(url):
                errors.append(f"sources[{index}] URL 형식이 올바르지 않습니다: {url}")

    fact_checks = data.get("fact_checks")
    if isinstance(fact_checks, list):
        for index, item in enumerate(fact_checks, start=1):
            if not isinstance(item, dict):
                errors.append(f"fact_checks[{index}]는 객체여야 합니다.")
                continue
            if not str(item.get("claim") or "").strip():
                errors.append(f"fact_checks[{index}]에 claim이 없습니다.")
            ids = item.get("source_ids", [])
            if not isinstance(ids, list):
                errors.append(f"fact_checks[{index}].source_ids는 배열이어야 합니다.")
                continue
            unknown = [str(value) for value in ids if str(value) not in source_ids]
            if unknown:
                warnings.append(
                    f"fact_checks[{index}]가 AI 출력 sources에 없는 ID를 참조합니다: {', '.join(unknown)}"
                )


def parse_ai_result(raw_response: str) -> ParseResult:
    errors: list[str] = []
    warnings: list[str] = []
    json_text = extract_json_text(raw_response)
    if not json_text:
        return ParseResult(None, "", ["붙여넣은 결과가 비어 있습니다."], [])

    try:
        loaded = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return ParseResult(
            None,
            json_text,
            [f"JSON 형식 오류: {exc.msg} (줄 {exc.lineno}, 열 {exc.colno})"],
            [],
        )

    if not isinstance(loaded, dict):
        return ParseResult(None, json_text, ["최상위 JSON은 객체여야 합니다."], [])

    data = dict(loaded)
    schema_version = data.get("schema_version")
    if not isinstance(schema_version, str):
        if "schema_version" in data:
            errors.append("schema_version 항목은 문자열이어야 합니다.")
        schema_version_text = ""
    else:
        schema_version_text = schema_version.strip()
        data["schema_version"] = schema_version_text

    if schema_version_text not in SUPPORTED_SCHEMA_VERSIONS:
        if schema_version_text:
            errors.append(
                f"지원하지 않는 schema_version입니다: {schema_version_text}. 지원 버전은 1.0, 2.0입니다."
            )
        else:
            errors.append("필수 항목 누락: schema_version")

    required_fields = (
        SCHEMA_V2_REQUIRED_FIELDS
        if schema_version_text == "2.0"
        else SCHEMA_V1_REQUIRED_FIELDS
    )
    missing = sorted(required_fields - set(data))
    if missing:
        errors.append("필수 항목 누락: " + ", ".join(missing))

    for key in ["title", "summary", "category"]:
        _validate_string(data, key, errors, required_nonempty=key == "title")

    for key in ["tags", "fact_checks", "sources"]:
        if key in data and not isinstance(data[key], list):
            errors.append(f"{key} 항목은 배열이어야 합니다.")

    if isinstance(data.get("tags"), list):
        if any(not isinstance(tag, str) for tag in data["tags"]):
            errors.append("tags의 모든 항목은 문자열이어야 합니다.")
        if len(data["tags"]) > 20:
            warnings.append("태그가 20개를 초과합니다. 발행 전에 줄이는 것을 권장합니다.")

    if schema_version_text == "1.0":
        _validate_string(data, "body_markdown", errors, required_nonempty=True)
        if isinstance(data.get("body_markdown"), str):
            data["body_markdown"] = strip_duplicate_leading_title(
                str(data.get("title") or ""),
                data["body_markdown"],
            )
        if "image_prompts" in data and not isinstance(data["image_prompts"], list):
            errors.append("image_prompts 항목은 배열이어야 합니다.")
    elif schema_version_text == "2.0":
        normalized_blocks = _validate_blocks(data.get("blocks"), errors)
        data["blocks"] = normalized_blocks
        data["body_markdown"] = blocks_to_markdown(
            title=str(data.get("title") or ""),
            blocks=normalized_blocks,
        )
        data["image_prompts"] = image_prompts_from_blocks(normalized_blocks)
        if not data["body_markdown"].strip():
            errors.append("blocks에서 변환된 본문이 비어 있습니다.")

    _validate_sources_and_fact_checks(data, errors, warnings)

    body = data.get("body_markdown")
    fact_checks = data.get("fact_checks")
    if isinstance(body, str):
        if len(body.strip()) < 300:
            warnings.append("본문이 300자보다 짧습니다. 의도한 분량인지 확인하세요.")
        if CONCRETE_FACT_PATTERN.search(body) and not fact_checks:
            warnings.append(
                "본문에 숫자·가격·정책 등 사실 확인이 필요한 표현이 있지만 fact_checks가 비어 있습니다."
            )

    return ParseResult(
        data,
        json_text,
        _deduplicate_messages(errors),
        _deduplicate_messages(warnings),
    )


def validate_ai_result_against_references(
    result: ParseResult,
    references: Iterable[dict[str, Any]],
) -> ParseResult:
    """Validate packaged sources and allow explicitly researched web sources.

    Packaged sources keep their S-number identity and URL. Sources discovered by
    ChatGPT or Gemini web search must use R1, R2, ... and include a valid URL.
    """
    if result.data is None:
        return result

    errors = list(result.errors)
    warnings = list(result.warnings)
    allowed_by_id: dict[str, dict[str, Any]] = {}
    allowed_urls: set[str] = set()
    for reference in references:
        if not isinstance(reference, dict):
            continue
        source_id = str(reference.get("id") or "").strip()
        if not source_id:
            continue
        allowed_by_id[source_id] = reference
        normalized = _normalized_url(str(reference.get("url") or ""))
        if normalized:
            allowed_urls.add(normalized)

    output_source_ids: set[str] = set()
    researched_source_ids: set[str] = set()
    researched_urls: set[str] = set()
    sources = result.data.get("sources")
    if isinstance(sources, list):
        for index, source in enumerate(sources, start=1):
            if not isinstance(source, dict):
                continue
            source_id = str(source.get("id") or "").strip()
            if not source_id:
                continue
            output_source_ids.add(source_id)
            allowed = allowed_by_id.get(source_id)
            if allowed is not None:
                actual_url = _normalized_url(str(source.get("url") or ""))
                expected_url = _normalized_url(str(allowed.get("url") or ""))
                if actual_url != expected_url:
                    if expected_url:
                        errors.append(
                            f"sources[{index}]의 URL이 자료팩의 {source_id} URL과 다릅니다."
                        )
                    elif actual_url:
                        errors.append(
                            f"sources[{index}]가 자료팩에 없던 URL을 {source_id}에 추가했습니다."
                        )
                continue

            if not RESEARCHED_SOURCE_ID_PATTERN.fullmatch(source_id):
                errors.append(
                    f"sources[{index}]의 새 조사 출처 ID는 R1, R2 형식이어야 합니다: {source_id}"
                )
                continue

            title = str(source.get("title") or "").strip()
            publisher = str(source.get("publisher") or "").strip()
            url = str(source.get("url") or "").strip()
            if not title:
                errors.append(f"sources[{index}]의 조사 출처 제목이 비어 있습니다.")
            if not publisher:
                errors.append(f"sources[{index}]의 조사 출처명이 비어 있습니다.")
            if not url or not _valid_url(url):
                errors.append(f"sources[{index}]의 조사 출처 URL이 올바르지 않습니다: {url}")
                continue
            researched_source_ids.add(source_id)
            researched_urls.add(_normalized_url(url))

    known_source_ids = set(allowed_by_id) | researched_source_ids
    fact_checks = result.data.get("fact_checks")
    if isinstance(fact_checks, list):
        for index, item in enumerate(fact_checks, start=1):
            if not isinstance(item, dict):
                continue
            ids = item.get("source_ids")
            if not isinstance(ids, list):
                continue
            unknown = [str(value) for value in ids if str(value) not in known_source_ids]
            if unknown:
                errors.append(
                    f"fact_checks[{index}]가 자료팩 또는 웹 조사 결과에 없는 출처 ID를 참조합니다: {', '.join(unknown)}"
                )
            omitted = [
                str(value)
                for value in ids
                if str(value) in known_source_ids and str(value) not in output_source_ids
            ]
            if omitted:
                warnings.append(
                    f"fact_checks[{index}]의 출처가 AI 출력 sources에서 빠졌습니다: {', '.join(omitted)}"
                )

    body = result.data.get("body_markdown")
    if isinstance(body, str):
        body_urls = {
            _normalized_url(match.rstrip(".,;:!?)"))
            for match in URL_PATTERN.findall(body)
            if _normalized_url(match.rstrip(".,;:!?)"))
        }
        unknown_urls = sorted(body_urls - allowed_urls - researched_urls)
        if unknown_urls:
            errors.append(
                "본문에 자료팩이나 웹 조사 sources에 없는 URL이 있습니다: "
                + ", ".join(unknown_urls)
            )

    return ParseResult(
        result.data,
        result.json_text,
        _deduplicate_messages(errors),
        _deduplicate_messages(warnings),
    )
