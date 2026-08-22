from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from src.services.content_pack_public_capture_service import (
    CaptureExecutor,
    CaptureResult,
    process_content_pack_captures,
)


def apply_public_capture_execution(
    data: dict[str, Any],
    *,
    executor: CaptureExecutor | None = None,
    output_dir: Path | str | None = None,
    auto_capture: bool = True,
) -> list[CaptureResult]:
    """Execute ready official captures without touching login state or publishing."""
    if not auto_capture:
        return []
    blocks = data.get("blocks")
    if not isinstance(blocks, list):
        return []

    results = process_content_pack_captures(
        data,
        executor=executor,
        output_dir=output_dir,
    )
    result_index = 0
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        if block.get("image_strategy") != "official_capture":
            continue
        if result_index >= len(results):
            break

        result = results[result_index]
        result_index += 1
        acquisition = block.get("image_acquisition")
        if not isinstance(acquisition, dict):
            acquisition = {}
            block["image_acquisition"] = acquisition
        acquisition["execution_status"] = result.status
        acquisition["execution_reason"] = result.review_reason

        if result.status == "success":
            block["captured_image"] = {
                "image_path": result.image_path,
                "image_format": result.image_format,
                "captured_at": result.captured_at,
                "page_title": result.page_title,
                "final_url": result.final_url,
                "dimensions": deepcopy(result.dimensions),
                "clip_rect": deepcopy(result.clip_rect),
                "provenance": deepcopy(result.provenance),
            }
            block["user_action"] = ""
        else:
            acquisition["status"] = "needs_review"
            acquisition["action"] = "manual_review"
            block["user_action"] = (
                "공식 화면 자동 캡처를 완료하지 못했습니다. "
                f"{result.review_reason or '공개 페이지와 캡처 범위를 확인하세요.'}"
            )

    data["image_acquisition_plans"] = [
        deepcopy(block["image_acquisition"])
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "image"
        and isinstance(block.get("image_acquisition"), dict)
    ]
    return results
