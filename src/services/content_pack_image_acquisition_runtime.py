from __future__ import annotations

import json
from copy import deepcopy
from functools import wraps
from typing import Any

from src.services.content_pack_image_acquisition_service import build_image_acquisition_plan


_AUTO_CAPTURE_PROMPT_SECTION = r"""
[이미지 자동 획득 우선 규칙]
- 위 image_strategy 규칙은 유지하되 정상 흐름에서는 운영자가 이미지를 직접 준비하지 않습니다. 프로그램이 각 image 블록을 자동 획득 작업으로 처리하고 `user_action`은 자동 처리 실패·권리 조건 불명확·페이지 구조 변경 때만 사용하는 예외 안내입니다.
- `official_capture`는 공개된 공식 페이지를 격리된 비로그인 브라우저로 자동 캡처하기 위한 계획입니다. 기존 Chrome 프로필·쿠키·세션을 가져오거나 자동 로그인·CAPTCHA 우회를 요구하지 않습니다.
- 새 `official_capture`에는 기존 source_id, source_url, capture_target, capture_note, checked_at과 함께 `source_capture.capture_anchor`를 채웁니다.
- `capture_anchor`는 자동 브라우저가 근거 영역을 찾는 데 사용할 실제 페이지의 짧고 구체적인 표시 문구입니다. 정확한 문구를 확신할 수 없으면 빈 문자열로 두며, 프로그램은 이를 추측해서 클릭하지 않고 사용자 확인 필요 상태로 둡니다.
- 공개 페이지가 아니거나 로그인·계정·관리자 화면, 사설/로컬 주소, 출처 URL 불일치, anchor 부재이면 자동 캡처하지 않습니다.
- 한 글에서 생성 이미지와 공식 화면 캡처를 함께 사용할 수 있습니다. 이미지 개수를 채우기 위해 불필요한 image 블록을 만들지 않습니다.
""".strip()


_PROMPT_REPLACEMENTS = (
    (
        "`official_capture`는 AI가 캡처 이미지를 만들어 냈다고 주장하는 방식이 아닙니다. 운영자가 실제 공식 페이지를 열어 직접 캡처하도록 정확한 링크와 작업 지시를 작성합니다.",
        "`official_capture`는 AI가 캡처 이미지를 만들어 냈다고 주장하는 방식이 아닙니다. 공개 공식 페이지에서 프로그램이 자동 캡처할 수 있도록 정확한 링크와 범위를 작성합니다.",
    ),
    (
        "모든 image 블록의 `user_action`에는 운영자가 실제로 해야 할 일을 `링크 열기 → 캡처/다운로드/생성 → 확인 → 지정 위치에 삽입`처럼 바로 실행할 수 있게 씁니다.",
        "모든 image 블록의 `user_action`에는 정상 자동 흐름의 지시가 아니라 자동 획득이 실패했을 때 필요한 최소 수동 확인 방법만 씁니다.",
    ),
)


def _patch_schema(content_pack_module: Any) -> None:
    schema = content_pack_module.OUTPUT_SCHEMA_EXAMPLE
    blocks = schema.get("blocks") if isinstance(schema, dict) else None
    if not isinstance(blocks, list):
        return
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        capture = block.get("source_capture")
        if isinstance(capture, dict):
            capture.setdefault("capture_anchor", "Input")
        break


def _wrap_build_ai_prompt(content_pack_module: Any) -> None:
    current = content_pack_module.build_ai_prompt
    if getattr(current, "_image_acquisition_automation_wrapper", False):
        return

    @wraps(current)
    def wrapped(*args, **kwargs):
        prompt = current(*args, **kwargs)
        for old, new in _PROMPT_REPLACEMENTS:
            prompt = prompt.replace(old, new)
        if "[이미지 자동 획득 우선 규칙]" in prompt:
            return prompt
        marker = "\n[자료팩]\n"
        if marker in prompt:
            return prompt.replace(marker, f"\n{_AUTO_CAPTURE_PROMPT_SECTION}\n\n[자료팩]\n", 1)
        return f"{prompt.rstrip()}\n\n{_AUTO_CAPTURE_PROMPT_SECTION}\n"

    wrapped._image_acquisition_automation_wrapper = True  # type: ignore[attr-defined]
    content_pack_module.build_ai_prompt = wrapped


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


def _raw_image_blocks(loaded: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = loaded.get("blocks")
    if not isinstance(blocks, list):
        return []
    return [block for block in blocks if isinstance(block, dict) and block.get("type") == "image"]


def _rebuild_body_markdown(parser_module: Any, data: dict[str, Any]) -> None:
    blocks = data.get("blocks")
    if not isinstance(blocks, list):
        return
    body = parser_module.blocks_to_markdown(title=str(data.get("title") or ""), blocks=blocks)
    image_number = 0
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        image_number += 1
        plan = block.get("image_acquisition")
        if isinstance(plan, dict) and plan.get("status") == "ready":
            continue
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
    if getattr(current, "_image_acquisition_automation_wrapper", False):
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

        data = result.data
        blocks = data.get("blocks")
        if not isinstance(blocks, list):
            return result
        normalized_images = [block for block in blocks if isinstance(block, dict) and block.get("type") == "image"]
        raw_images = _raw_image_blocks(loaded)
        prompts = [item for item in data.get("image_prompts") or [] if isinstance(item, dict)]
        source_urls = _source_url_map(data)
        warnings = list(result.warnings)

        for index, block in enumerate(normalized_images, start=1):
            raw_block = raw_images[index - 1] if index - 1 < len(raw_images) else {}
            raw_capture = raw_block.get("source_capture") if isinstance(raw_block, dict) else None
            capture = block.get("source_capture")
            if not isinstance(capture, dict):
                capture = {}
                block["source_capture"] = capture
            anchor = ""
            if isinstance(raw_capture, dict):
                raw_anchor = raw_capture.get("capture_anchor", "")
                anchor = raw_anchor.strip() if isinstance(raw_anchor, str) else ""
            capture["capture_anchor"] = anchor

            plan = build_image_acquisition_plan(block, index=index, source_urls=source_urls).to_dict()
            block["image_acquisition"] = deepcopy(plan)
            if block.get("image_strategy") == "official_capture" and plan["status"] != "ready":
                warnings.append(f"blocks[image:{index}] 공식 화면 자동 캡처 준비 미완료: {plan['reason']}")
            if index - 1 < len(prompts):
                prompts[index - 1]["source_capture"] = deepcopy(capture)
                prompts[index - 1]["image_acquisition"] = deepcopy(plan)

        data["image_acquisition_plans"] = [
            deepcopy(block["image_acquisition"])
            for block in normalized_images
            if isinstance(block.get("image_acquisition"), dict)
        ]
        _rebuild_body_markdown(parser_module, data)
        return parser_module.ParseResult(
            data,
            result.json_text,
            list(result.errors),
            parser_module._deduplicate_messages(warnings),
        )

    wrapped._image_acquisition_automation_wrapper = True  # type: ignore[attr-defined]
    parser_module.parse_ai_result = wrapped


def install_content_pack_image_acquisition_automation_contract() -> None:
    """Make schema 2.1 image tasks automation-first without breaking older capture results."""
    import src.services.ai_result_parser as parser_module
    import src.services.content_pack_service as content_pack_module

    _patch_schema(content_pack_module)
    _wrap_build_ai_prompt(content_pack_module)
    _wrap_parse_ai_result(parser_module)
