from __future__ import annotations

import json
from copy import deepcopy
from functools import wraps
from typing import Any


IMAGE_STRATEGIES = {"official_capture", "verified_free", "generated"}

_CAPTURE_SCHEMA_EXAMPLE = {
    "image_strategy": "official_capture",
    "source_capture": {
        "needed": True,
        "source_id": "R1",
        "source_url": "https://example.com/official-pricing",
        "capture_target": "요금표에서 모델명과 입력·출력 토큰 단가가 함께 보이는 표 영역",
        "capture_note": "페이지 제목과 요금 단위가 함께 보이도록 필요한 영역만 캡처하고 계정·개인정보는 제외",
        "checked_at": "YYYY-MM-DD",
    },
    "user_action": "공식 링크 열기 → 지정된 요금표 영역 캡처 → 이 이미지 위치에 삽입 → 캡션과 출처 링크 확인",
}

_CAPTURE_PROMPT_SECTION = r"""
[공식 화면 캡처·이미지 작업 필수 규칙]
- 각 image 블록은 `image_strategy`를 `official_capture`, `verified_free`, `generated` 중 하나로 정합니다.
- 가격·요금·정책·통계·공식 기능·사양·일정처럼 변동 가능하고 공식 화면 자체가 독자의 사실 확인에 도움이 되는 경우 `official_capture`를 우선 검토합니다.
- `official_capture`는 AI가 캡처 이미지를 만들어 냈다고 주장하는 방식이 아닙니다. 운영자가 실제 공식 페이지를 열어 직접 캡처하도록 정확한 링크와 작업 지시를 작성합니다.
- `official_capture`일 때 `source_capture.needed=true`로 하고 source_id, source_url, capture_target, capture_note, checked_at을 모두 채웁니다.
- source_id는 sources의 S/R ID 중 실제 사실 근거로 사용한 하나를 가리켜야 하고, source_url은 그 출처의 공개된 공식 페이지 URL과 일치해야 합니다.
- capture_target은 `요금표 전체`처럼 두루뭉술하게 쓰지 말고 어떤 표·행·열·문구가 한 화면에 보여야 하는지 구체적으로 씁니다.
- capture_note에는 페이지 제목·기준일·단위처럼 문맥상 함께 보여야 할 항목과, 로그인 화면·계정명·이메일·결제정보·쿠키 등 캡처에서 제외할 개인정보를 적습니다.
- 로그인·유료 계정·개인 대시보드에만 보이는 화면을 캡처하라고 지시하지 않습니다. 공개된 공식 페이지를 우선합니다.
- 공식 화면 캡처가 사실 근거로 쓰이면 해당 공식 링크도 sources에 그대로 남겨 독자가 원문을 다시 확인할 수 있게 합니다.
- 공식 화면을 굳이 캡처할 필요가 없으면 `source_capture.needed=false`로 두고 `verified_free` 또는 `generated`를 사용합니다.
- `verified_free`는 기존 무료 이미지 2중 확인 규칙을 그대로 따르고, `generated`는 무료 이미지가 부적합하거나 설명용 생성 이미지가 더 나을 때 사용합니다.
- 모든 image 블록의 `user_action`에는 운영자가 실제로 해야 할 일을 `링크 열기 → 캡처/다운로드/생성 → 확인 → 지정 위치에 삽입`처럼 바로 실행할 수 있게 씁니다.
- 공식 근거 캡처가 우선인 경우 free_image는 `not_found`로 둘 수 있으며, 생성용 prompt는 공식 페이지 접근 실패나 캡처 사용이 부적절할 때의 설명 이미지 fallback으로만 작성합니다.

[AI 활용 고지 정책]
- 개별 글의 title, summary, 본문, caption, alt_text에 `AI로 작성`, `AI를 활용`, `AI가 생성` 같은 일반적인 AI 사용 고지 문구를 자동으로 넣지 않습니다.
- AI 활용 안내는 운영자가 블로그 공통 소개·정책 영역에서 별도로 관리합니다.
- 단, 법령·플랫폼 정책·특정 생성물 조건상 개별 고지가 반드시 필요하다고 확인되는 예외가 있으면 임의로 본문에 삽입하지 말고 fact_checks 또는 user_action에 운영자 확인 작업으로 남깁니다.
""".strip()


def _patch_output_schema(content_pack_module: Any) -> None:
    schema = content_pack_module.OUTPUT_SCHEMA_EXAMPLE
    blocks = schema.get("blocks") if isinstance(schema, dict) else None
    if not isinstance(blocks, list):
        return
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        for key, value in _CAPTURE_SCHEMA_EXAMPLE.items():
            if key not in block:
                block[key] = deepcopy(value)
        break


def _wrap_build_ai_prompt(content_pack_module: Any) -> None:
    current = content_pack_module.build_ai_prompt
    if getattr(current, "_source_capture_task_wrapper", False):
        return

    @wraps(current)
    def wrapped(*args, **kwargs):
        prompt = current(*args, **kwargs)
        if "[공식 화면 캡처·이미지 작업 필수 규칙]" in prompt:
            return prompt
        marker = "\n[자료팩]\n"
        if marker in prompt:
            return prompt.replace(
                marker,
                f"\n{_CAPTURE_PROMPT_SECTION}\n\n[자료팩]\n",
                1,
            )
        return f"{prompt.rstrip()}\n\n{_CAPTURE_PROMPT_SECTION}\n"

    wrapped._source_capture_task_wrapper = True  # type: ignore[attr-defined]
    content_pack_module.build_ai_prompt = wrapped


def _valid_url(parser_module: Any, value: str) -> bool:
    return bool(value) and bool(parser_module._valid_url(value))


def _normalize_source_capture(
    parser_module: Any,
    raw: Any,
    *,
    path: str,
    errors: list[str],
) -> dict[str, Any]:
    default = {
        "needed": False,
        "source_id": "",
        "source_url": "",
        "capture_target": "",
        "capture_note": "",
        "checked_at": "",
    }
    if raw is None:
        return default
    if not isinstance(raw, dict):
        errors.append(f"{path}는 객체여야 합니다.")
        return default

    needed = raw.get("needed")
    if not isinstance(needed, bool):
        errors.append(f"{path}.needed는 true 또는 false여야 합니다.")
        needed = False
    normalized = {"needed": bool(needed)}
    for field in ("source_id", "source_url", "capture_target", "capture_note", "checked_at"):
        value = raw.get(field, "")
        if not isinstance(value, str):
            errors.append(f"{path}.{field}는 문자열이어야 합니다.")
            value = ""
        normalized[field] = value.strip()

    if normalized["needed"]:
        for field in ("source_id", "source_url", "capture_target", "capture_note", "checked_at"):
            if not normalized[field]:
                errors.append(f"{path}.{field}는 공식 화면 캡처 시 비워둘 수 없습니다.")
        if normalized["source_url"] and not _valid_url(parser_module, normalized["source_url"]):
            errors.append(f"{path}.source_url은 실제 http 또는 https 공식 페이지여야 합니다.")
    return normalized


def _source_url_map(data: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for source in data.get("sources") or []:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id") or "").strip()
        source_url = str(source.get("url") or "").strip()
        if source_id:
            result[source_id] = source_url
    return result


def _default_user_action(
    *,
    strategy: str,
    capture: dict[str, Any],
    block: dict[str, Any],
) -> str:
    if strategy == "official_capture" and capture.get("source_url"):
        target = str(capture.get("capture_target") or "지정된 공식 정보 영역").strip()
        return (
            f"공식 링크 열기 → {target} 캡처 → 개인정보가 없는지 확인 → "
            "이 이미지 위치에 삽입 → 캡션과 출처 링크 확인"
        )
    free_image = block.get("free_image") if isinstance(block.get("free_image"), dict) else {}
    if strategy == "verified_free" and str(free_image.get("page_url") or "").strip():
        return (
            "무료 이미지 자산 페이지 열기 → 라이선스 조건 다시 확인 → 이미지 준비 → "
            "이 위치에 삽입 → 캡션·alt·필요한 출처표시 확인"
        )
    return (
        "생성용 prompt로 설명 이미지를 생성 → 사실과 어긋나는 요소가 없는지 확인 → "
        "이 위치에 삽입 → 캡션·alt 확인"
    )


def _strategy_for(raw_block: dict[str, Any], normalized_block: dict[str, Any], capture: dict[str, Any], errors: list[str], path: str) -> str:
    raw_strategy = raw_block.get("image_strategy")
    if raw_strategy is None or raw_strategy == "":
        if capture.get("needed"):
            return "official_capture"
        free_image = normalized_block.get("free_image")
        if isinstance(free_image, dict) and free_image.get("status") == "verified_free":
            return "verified_free"
        return "generated"
    if not isinstance(raw_strategy, str):
        errors.append(f"{path}.image_strategy는 문자열이어야 합니다.")
        return "generated"
    strategy = raw_strategy.strip()
    if strategy not in IMAGE_STRATEGIES:
        errors.append(
            f"{path}.image_strategy는 official_capture, verified_free, generated 중 하나여야 합니다: {strategy or '비어 있음'}"
        )
        return "generated"
    return strategy


def _augment_body_markdown(parser_module: Any, data: dict[str, Any]) -> None:
    blocks = data.get("blocks")
    if not isinstance(blocks, list):
        return
    body = parser_module.blocks_to_markdown(
        title=str(data.get("title") or ""),
        blocks=blocks,
    )
    image_number = 0
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        image_number += 1
        marker = f"[이미지 {image_number} 삽입 위치]"
        additions = [marker]
        action = str(block.get("user_action") or "").strip()
        if action:
            additions.append(f"*내가 할 일: {action}*")
        capture = block.get("source_capture")
        if isinstance(capture, dict) and capture.get("needed"):
            url = str(capture.get("source_url") or "").strip()
            target = str(capture.get("capture_target") or "").strip()
            note = str(capture.get("capture_note") or "").strip()
            if url:
                additions.append(f"*공식 화면: [링크 열기]({url})*")
            if target:
                additions.append(f"*캡처 대상: {target}*")
            if note:
                additions.append(f"*캡처 참고: {note}*")
        body = body.replace(marker, "\n".join(additions), 1)
    data["body_markdown"] = body


def _wrap_parse_ai_result(parser_module: Any) -> None:
    current = parser_module.parse_ai_result
    if getattr(current, "_source_capture_task_wrapper", False):
        return

    @wraps(current)
    def wrapped(raw_response: str):
        result = current(raw_response)
        if result.data is None:
            return result
        json_text = parser_module.extract_json_text(raw_response)
        try:
            loaded = json.loads(json_text) if json_text else None
        except json.JSONDecodeError:
            return result
        if not isinstance(loaded, dict) or str(loaded.get("schema_version") or "").strip() != "2.1":
            return result

        try:
            from src.services.ai_result_parser_v21_runtime import _strip_chatgpt_content_references

            loaded = _strip_chatgpt_content_references(loaded)
        except Exception:
            pass

        raw_blocks = loaded.get("blocks")
        if not isinstance(raw_blocks, list):
            return result

        data = result.data
        normalized_blocks = data.get("blocks")
        if not isinstance(normalized_blocks, list):
            return result

        errors = list(result.errors)
        warnings = list(result.warnings)
        source_urls = _source_url_map(data)
        raw_images = [block for block in raw_blocks if isinstance(block, dict) and block.get("type") == "image"]
        normalized_images = [block for block in normalized_blocks if isinstance(block, dict) and block.get("type") == "image"]
        prompts = [item for item in data.get("image_prompts") or [] if isinstance(item, dict)]

        for index, normalized_block in enumerate(normalized_images, start=1):
            raw_block = raw_images[index - 1] if index - 1 < len(raw_images) else {}
            path = f"blocks[image:{index}]"
            capture = _normalize_source_capture(
                parser_module,
                raw_block.get("source_capture") if isinstance(raw_block, dict) else None,
                path=f"{path}.source_capture",
                errors=errors,
            )
            strategy = _strategy_for(raw_block, normalized_block, capture, errors, path)

            if strategy == "official_capture":
                if not capture.get("needed"):
                    errors.append(f"{path}.source_capture.needed는 official_capture일 때 true여야 합니다.")
                source_id = str(capture.get("source_id") or "").strip()
                source_url = str(capture.get("source_url") or "").strip()
                if source_id and source_id not in source_urls:
                    errors.append(f"{path}.source_capture.source_id가 sources에 없습니다: {source_id}")
                elif source_id and source_url:
                    expected = str(source_urls.get(source_id) or "").strip()
                    if expected and parser_module._normalized_url(expected) != parser_module._normalized_url(source_url):
                        errors.append(
                            f"{path}.source_capture.source_url은 source_id={source_id}의 sources URL과 일치해야 합니다."
                        )
            elif capture.get("needed"):
                warnings.append(
                    f"{path}는 image_strategy={strategy}인데 source_capture.needed=true입니다. 공식 캡처가 필요하면 official_capture를 사용하세요."
                )

            user_action = raw_block.get("user_action") if isinstance(raw_block, dict) else ""
            if user_action is not None and not isinstance(user_action, str):
                errors.append(f"{path}.user_action은 문자열이어야 합니다.")
                user_action = ""
            user_action = str(user_action or "").strip() or _default_user_action(
                strategy=strategy,
                capture=capture,
                block=normalized_block,
            )

            normalized_block["image_strategy"] = strategy
            normalized_block["source_capture"] = deepcopy(capture)
            normalized_block["user_action"] = user_action
            if index - 1 < len(prompts):
                prompts[index - 1]["image_strategy"] = strategy
                prompts[index - 1]["source_capture"] = deepcopy(capture)
                prompts[index - 1]["user_action"] = user_action

        _augment_body_markdown(parser_module, data)
        return parser_module.ParseResult(
            data,
            result.json_text,
            parser_module._deduplicate_messages(errors),
            parser_module._deduplicate_messages(warnings),
        )

    wrapped._source_capture_task_wrapper = True  # type: ignore[attr-defined]
    parser_module.parse_ai_result = wrapped


def install_content_pack_capture_task_contract() -> None:
    """Extend schema 2.1 requests with official screenshot and operator task metadata."""
    import src.services.ai_result_parser as parser_module
    import src.services.content_pack_service as content_pack_module

    _patch_output_schema(content_pack_module)
    _wrap_build_ai_prompt(content_pack_module)
    _wrap_parse_ai_result(parser_module)
