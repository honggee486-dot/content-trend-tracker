from __future__ import annotations

from copy import deepcopy
from typing import Any


_OFFICIAL_CAPTURE_FREE_IMAGE_FALLBACK = {
    "status": "not_found",
    "search_query": "공식 화면 캡처가 불가능할 때 사용할 설명 이미지 검색어",
    "page_url": "",
    "provider": "",
    "creator": "",
    "license_name": "",
    "license_url": "",
    "attribution": "",
    "checked_at": "YYYY-MM-DD",
    "commercial_use_allowed": False,
    "payment_required": False,
    "premium_or_subscription_required": False,
    "editorial_only": False,
    "verification_note": "이 위치는 공식 근거 화면 직접 캡처를 우선하므로 무료 이미지 자산을 확정하지 않음",
}


def install_content_pack_capture_schema_consistency_contract() -> None:
    """Keep the schema 2.1 example unambiguous when official capture is selected."""
    import src.services.content_pack_service as content_pack_module

    schema = content_pack_module.OUTPUT_SCHEMA_EXAMPLE
    blocks = schema.get("blocks") if isinstance(schema, dict) else None
    if not isinstance(blocks, list):
        return
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        if block.get("image_strategy") == "official_capture":
            block["free_image"] = deepcopy(_OFFICIAL_CAPTURE_FREE_IMAGE_FALLBACK)
        break
