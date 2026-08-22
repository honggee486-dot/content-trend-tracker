from __future__ import annotations

import base64
import json
from urllib.error import HTTPError

import pytest

from src.services.content_pack_representative_image_service import (
    CloudflareRepresentativeImageConfig,
    RepresentativeImageConfigurationError,
    build_generation_metadata,
    build_overlay_spec,
    build_representative_image_plan,
    generate_representative_image,
)


def _config(**overrides):
    values = {
        "account_id": "account-id",
        "api_token": "secret-token",
        "timeout_seconds": 15,
        "max_attempts": 2,
    }
    values.update(overrides)
    return CloudflareRepresentativeImageConfig(**values)


def _image_response(raw_image: bytes) -> bytes:
    return json.dumps(
        {
            "result": {"image": base64.b64encode(raw_image).decode("ascii")},
            "success": True,
            "errors": [],
            "messages": [],
        }
    ).encode("utf-8")


def test_prompt_builds_background_only_16_by_9_overlay_contract() -> None:
    plan = build_representative_image_plan(
        title="에어컨 전기요금 줄이는 방법",
        category="생활",
        summary="여름철 전기요금 절약 원리를 설명합니다.",
        style_preset="lifestyle",
        seed=1234,
    )

    assert plan.seed == 1234
    assert plan.steps == 4
    assert plan.aspect_ratio == "16:9"
    assert plan.final_width == 1280
    assert plan.final_height == 720
    assert "Do not render any readable text" in plan.prompt
    assert "watermarks" in plan.prompt
    assert len(plan.prompt) <= 2048

    overlay = build_overlay_spec(plan)
    assert overlay["template"] == "hero_v1"
    assert overlay["canvas"] == {
        "width": 1280,
        "height": 720,
        "aspect_ratio": "16:9",
    }
    assert overlay["rules"]["text_generated_by_model"] is False
    assert overlay["rules"]["max_title_lines"] == 3


def test_success_uses_only_reviewed_cloudflare_flux_parameters() -> None:
    jpeg = b"\xff\xd8\xff" + b"representative-image"
    calls: list[dict] = []

    def transport(url, headers, payload, timeout):
        calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": dict(payload),
                "timeout": timeout,
            }
        )
        return _image_response(jpeg)

    plan = build_representative_image_plan(title="AI 신제품 정리", seed=77, steps=5)
    result = generate_representative_image(
        plan,
        config=_config(),
        transport=transport,
        sleep_func=lambda _: None,
    )

    assert result.succeeded is True
    assert result.image_bytes == jpeg
    assert result.image_format == "jpeg"
    assert result.model == "@cf/black-forest-labs/flux-1-schnell"
    assert calls[0]["url"].endswith(
        "/accounts/account-id/ai/run/@cf/black-forest-labs/flux-1-schnell"
    )
    assert calls[0]["headers"]["Authorization"] == "Bearer secret-token"
    assert calls[0]["payload"] == {
        "prompt": plan.prompt,
        "seed": 77,
        "steps": 5,
    }
    assert "width" not in calls[0]["payload"]
    assert "height" not in calls[0]["payload"]


def test_transient_429_retries_once_then_stops_on_success() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"image"
    call_count = 0

    def transport(url, headers, payload, timeout):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise HTTPError(url, 429, "rate limited", {}, None)
        return _image_response(png)

    result = generate_representative_image(
        build_representative_image_plan(title="생활 정보", seed=1),
        config=_config(max_attempts=2),
        transport=transport,
        sleep_func=lambda _: None,
    )

    assert result.succeeded is True
    assert result.attempts == 2
    assert result.image_format == "png"
    assert call_count == 2


def test_paid_plan_or_other_model_is_never_automatic_fallback() -> None:
    call_count = 0

    def transport(url, headers, payload, timeout):
        nonlocal call_count
        call_count += 1
        raise HTTPError(url, 403, "forbidden", {}, None)

    result = generate_representative_image(
        build_representative_image_plan(title="정책 정보", seed=2),
        config=_config(max_attempts=3),
        transport=transport,
        sleep_func=lambda _: None,
    )

    assert result.succeeded is False
    assert result.attempts == 1
    assert result.error_type == "http_error"
    assert call_count == 1

    with pytest.raises(RepresentativeImageConfigurationError):
        generate_representative_image(
            build_representative_image_plan(title="정책 정보", seed=2),
            config=_config(model="@cf/leonardo/lucid-origin"),
            transport=lambda *args: pytest.fail("다른 모델은 호출되면 안 됩니다."),
        )


def test_daily_free_allocation_exhaustion_does_not_retry_or_upgrade() -> None:
    call_count = 0

    def transport(url, headers, payload, timeout):
        nonlocal call_count
        call_count += 1
        body = json.dumps(
            {
                "success": False,
                "errors": [
                    {
                        "code": 3036,
                        "message": "daily free allocation exhausted",
                    }
                ],
            }
        ).encode("utf-8")
        raise HTTPError(url, 429, "rate limited", {}, __import__("io").BytesIO(body))

    result = generate_representative_image(
        build_representative_image_plan(title="생활 정보", seed=4),
        config=_config(max_attempts=3),
        transport=transport,
        sleep_func=lambda _: None,
    )

    assert result.succeeded is False
    assert result.error_type == "free_allocation_exhausted"
    assert result.attempts == 1
    assert call_count == 1


def test_missing_credentials_fails_before_network_call() -> None:
    called = False

    def transport(*args):
        nonlocal called
        called = True
        raise AssertionError("네트워크 호출이 실행되면 안 됩니다.")

    with pytest.raises(RepresentativeImageConfigurationError):
        generate_representative_image(
            build_representative_image_plan(title="대표 이미지", seed=3),
            config=_config(account_id="", api_token=""),
            transport=transport,
        )

    assert called is False


def test_metadata_keeps_reproducibility_fields_without_image_bytes() -> None:
    jpeg = b"\xff\xd8\xff" + b"x"
    plan = build_representative_image_plan(
        title="자동차 관리법",
        category="자동차",
        style_preset="general",
        seed=99,
    )
    result = generate_representative_image(
        plan,
        config=_config(),
        transport=lambda *args: _image_response(jpeg),
    )
    metadata = build_generation_metadata(result, draft_id="draft_123")

    assert metadata["provider"] == "cloudflare_workers_ai"
    assert metadata["model"] == "@cf/black-forest-labs/flux-1-schnell"
    assert metadata["prompt"] == plan.prompt
    assert metadata["seed"] == 99
    assert metadata["steps"] == 4
    assert metadata["final_width"] == 1280
    assert metadata["final_height"] == 720
    assert metadata["overlay_template"] == "hero_v1"
    assert metadata["draft_id"] == "draft_123"
    assert metadata["image_stage"] == "background"
    assert "image_bytes" not in metadata
