from __future__ import annotations

import base64
import binascii
import json
import os
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROVIDER_ID = "cloudflare_workers_ai"
DEFAULT_MODEL = "@cf/black-forest-labs/flux-1-schnell"
DEFAULT_STEPS = 4
MAX_STEPS = 8
DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_MAX_ATTEMPTS = 2
FINAL_WIDTH = 1280
FINAL_HEIGHT = 720
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_OVERLAY_TEMPLATE = "hero_v1"

STYLE_PRESETS: dict[str, str] = {
    "general": "clean editorial illustration, modern, trustworthy, professional, balanced neutral color palette",
    "technology": "clean modern technology editorial illustration, precise geometric forms, sophisticated and credible",
    "economy_policy": "professional editorial illustration for economy or public policy, restrained, credible, documentary-inspired",
    "lifestyle": "clean practical lifestyle editorial illustration, approachable, bright, uncluttered, useful rather than decorative",
    "health": "calm health information editorial illustration, clean, reassuring, non-diagnostic, professional",
}


class RepresentativeImageError(RuntimeError):
    """Base error for representative-image generation."""


class RepresentativeImageConfigurationError(RepresentativeImageError):
    """Raised before any network call when required credentials are unavailable."""


class RepresentativeImageResponseError(RepresentativeImageError):
    """Raised when a provider response cannot be validated as an image."""


@dataclass(frozen=True)
class CloudflareRepresentativeImageConfig:
    account_id: str
    api_token: str
    model: str = DEFAULT_MODEL
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

    @property
    def is_configured(self) -> bool:
        return bool(self.account_id.strip() and self.api_token.strip())


@dataclass(frozen=True)
class RepresentativeImagePlan:
    prompt: str
    title: str
    category: str
    summary: str
    style_preset: str
    seed: int
    steps: int = DEFAULT_STEPS
    final_width: int = FINAL_WIDTH
    final_height: int = FINAL_HEIGHT
    aspect_ratio: str = DEFAULT_ASPECT_RATIO
    overlay_template: str = DEFAULT_OVERLAY_TEMPLATE


@dataclass(frozen=True)
class RepresentativeImageResult:
    status: str
    provider: str
    model: str
    prompt: str
    seed: int
    steps: int
    attempts: int
    image_bytes: bytes = b""
    image_format: str = ""
    created_at: str = ""
    final_width: int = FINAL_WIDTH
    final_height: int = FINAL_HEIGHT
    aspect_ratio: str = DEFAULT_ASPECT_RATIO
    overlay_template: str = DEFAULT_OVERLAY_TEMPLATE
    style_preset: str = "general"
    error_type: str = ""
    error_message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "generated" and bool(self.image_bytes)


Transport = Callable[[str, Mapping[str, str], Mapping[str, Any], int], Any]
SleepFunc = Callable[[float], None]


def get_cloudflare_representative_image_config() -> CloudflareRepresentativeImageConfig:
    timeout = _env_int(
        "CLOUDFLARE_IMAGE_TIMEOUT_SECONDS",
        DEFAULT_TIMEOUT_SECONDS,
        minimum=5,
        maximum=120,
    )
    attempts = _env_int(
        "CLOUDFLARE_IMAGE_MAX_ATTEMPTS",
        DEFAULT_MAX_ATTEMPTS,
        minimum=1,
        maximum=3,
    )
    return CloudflareRepresentativeImageConfig(
        account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip(),
        api_token=os.getenv("CLOUDFLARE_API_TOKEN", "").strip(),
        timeout_seconds=timeout,
        max_attempts=attempts,
    )


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return min(max(value, minimum), maximum)


def _clean(value: object, *, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum].strip()


def normalize_style_preset(value: object) -> str:
    key = _clean(value, maximum=40).casefold().replace("-", "_").replace(" ", "_")
    return key if key in STYLE_PRESETS else "general"


def build_representative_image_plan(
    *,
    title: str,
    category: str = "",
    summary: str = "",
    style_preset: str = "general",
    seed: int | None = None,
    steps: int = DEFAULT_STEPS,
) -> RepresentativeImagePlan:
    clean_title = _clean(title, maximum=200)
    if not clean_title:
        raise ValueError("대표 이미지 프롬프트를 만들려면 글 제목이 필요합니다.")
    clean_category = _clean(category, maximum=80)
    clean_summary = _clean(summary, maximum=500)
    preset = normalize_style_preset(style_preset)
    step_count = min(max(int(steps), 1), MAX_STEPS)
    if seed is None:
        seed = random.SystemRandom().randint(1, 9_999_999_999)
    seed = min(max(int(seed), 1), 9_999_999_999)

    subject_parts = [f"Article topic: {clean_title}."]
    if clean_category:
        subject_parts.append(f"Category: {clean_category}.")
    if clean_summary:
        subject_parts.append(f"Context: {clean_summary}.")
    subject = " ".join(subject_parts)
    prompt = (
        f"{subject} "
        f"Create a {STYLE_PRESETS[preset]} background illustration for a Korean blog hero image. "
        "Compose it as a wide cinematic scene suitable for a 16:9 final crop, with one clear visual subject "
        "and generous negative space for a title overlay. "
        "Do not render any readable text, letters, numbers, captions, logos, trademarks, signatures, "
        "watermarks, UI elements, or dense clutter. "
        "Avoid sensational, misleading, photorealistic-news-event framing unless the subject explicitly requires it."
    )
    prompt = prompt[:2048].rstrip()
    return RepresentativeImagePlan(
        prompt=prompt,
        title=clean_title,
        category=clean_category,
        summary=clean_summary,
        style_preset=preset,
        seed=seed,
        steps=step_count,
    )


def build_overlay_spec(plan: RepresentativeImagePlan) -> dict[str, Any]:
    """Return the stable layout contract; raster compositing is intentionally separate."""
    return {
        "template": plan.overlay_template,
        "canvas": {
            "width": plan.final_width,
            "height": plan.final_height,
            "aspect_ratio": plan.aspect_ratio,
        },
        "title": plan.title,
        "category": plan.category,
        "safe_area": {
            "left": 80,
            "top": 80,
            "right": 720,
            "bottom": 640,
        },
        "rules": {
            "max_title_lines": 3,
            "text_generated_by_model": False,
            "background_crop": "center_cover",
        },
    }


def _endpoint(config: CloudflareRepresentativeImageConfig) -> str:
    account_id = config.account_id.strip()
    model = config.model.strip()
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"


def _default_transport(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout_seconds: int,
) -> bytes:
    request = Request(
        url,
        data=json.dumps(dict(payload), ensure_ascii=False).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed HTTPS provider endpoint
        return response.read()


def _response_payload(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        try:
            return json.loads(bytes(value).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return bytes(value)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _extract_base64_image(payload: Any) -> str:
    payload = _response_payload(payload)
    if isinstance(payload, Mapping):
        if payload.get("success") is False:
            errors = payload.get("errors") or []
            raise RepresentativeImageResponseError(
                f"Cloudflare Workers AI 응답이 실패 상태입니다: {str(errors)[:500]}"
            )
        result = payload.get("result", payload)
        if isinstance(result, Mapping):
            image = result.get("image")
            if isinstance(image, str) and image.strip():
                return image.strip()
        if isinstance(result, str) and result.strip():
            return result.strip()
        image = payload.get("image")
        if isinstance(image, str) and image.strip():
            return image.strip()
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    raise RepresentativeImageResponseError("Cloudflare Workers AI 응답에서 이미지 데이터를 찾지 못했습니다.")


def _decode_image(encoded: str) -> tuple[bytes, str]:
    candidate = encoded.strip()
    if candidate.startswith("data:") and "," in candidate:
        candidate = candidate.split(",", 1)[1]
    try:
        image = base64.b64decode(candidate, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RepresentativeImageResponseError("대표 이미지 Base64 응답을 해석하지 못했습니다.") from exc
    if image.startswith(b"\xff\xd8\xff"):
        return image, "jpeg"
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return image, "png"
    raise RepresentativeImageResponseError("지원하는 JPEG/PNG 대표 이미지가 아닙니다.")


def _cloudflare_http_error(exc: HTTPError) -> tuple[str, str, bool]:
    internal_code = 0
    detail = ""
    try:
        raw = exc.read()
    except Exception:
        raw = b""
    if raw:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {}
        errors = parsed.get("errors") if isinstance(parsed, Mapping) else None
        if isinstance(errors, list) and errors and isinstance(errors[0], Mapping):
            try:
                internal_code = int(errors[0].get("code") or 0)
            except (TypeError, ValueError):
                internal_code = 0
            detail = _clean(errors[0].get("message"), maximum=300)

    if int(exc.code) == 429 and internal_code == 3036:
        return (
            "free_allocation_exhausted",
            detail or "Cloudflare Workers AI 일일 무료 할당량을 모두 사용했습니다.",
            False,
        )
    if int(exc.code) == 403 and internal_code == 5035:
        return (
            "paid_plan_required",
            detail or "현재 모델 호출에 Workers Paid 플랜이 필요합니다.",
            False,
        )
    retryable = int(exc.code) in {408, 429, 500, 502, 503, 504}
    return (
        "http_error",
        detail or f"Cloudflare Workers AI HTTP {exc.code}",
        retryable,
    )


def generate_representative_image(
    plan: RepresentativeImagePlan,
    *,
    config: CloudflareRepresentativeImageConfig | None = None,
    transport: Transport | None = None,
    sleep_func: SleepFunc = time.sleep,
) -> RepresentativeImageResult:
    config = config or get_cloudflare_representative_image_config()
    if not config.is_configured:
        raise RepresentativeImageConfigurationError(
            "Cloudflare Workers AI 대표 이미지 생성에는 CLOUDFLARE_ACCOUNT_ID와 CLOUDFLARE_API_TOKEN이 필요합니다."
        )
    if config.model != DEFAULT_MODEL:
        raise RepresentativeImageConfigurationError(
            "1차 대표 이미지 생성 계약에서는 검토된 Cloudflare FLUX.1-schnell 모델만 사용합니다."
        )

    runner = transport or _default_transport
    payload = {"prompt": plan.prompt, "seed": plan.seed, "steps": plan.steps}
    headers = {
        "Authorization": f"Bearer {config.api_token}",
        "Content-Type": "application/json",
    }
    attempts = 0
    last_type = ""
    last_message = ""

    for attempt in range(1, max(1, int(config.max_attempts)) + 1):
        attempts = attempt
        try:
            raw = runner(_endpoint(config), headers, payload, int(config.timeout_seconds))
            encoded = _extract_base64_image(raw)
            image_bytes, image_format = _decode_image(encoded)
            return RepresentativeImageResult(
                status="generated",
                provider=PROVIDER_ID,
                model=config.model,
                prompt=plan.prompt,
                seed=plan.seed,
                steps=plan.steps,
                attempts=attempts,
                image_bytes=image_bytes,
                image_format=image_format,
                created_at=datetime.now(timezone.utc).isoformat(),
                final_width=plan.final_width,
                final_height=plan.final_height,
                aspect_ratio=plan.aspect_ratio,
                overlay_template=plan.overlay_template,
                style_preset=plan.style_preset,
            )
        except HTTPError as exc:
            last_type, last_message, retryable = _cloudflare_http_error(exc)
            if not retryable or attempt >= config.max_attempts:
                break
        except (URLError, TimeoutError) as exc:
            last_type = "network_error"
            last_message = str(getattr(exc, "reason", exc) or "network error")[:500]
            if attempt >= config.max_attempts:
                break
        except RepresentativeImageResponseError as exc:
            last_type = "response_error"
            last_message = str(exc)
            break
        if attempt < config.max_attempts:
            sleep_func(min(2.0 ** (attempt - 1), 4.0))

    return RepresentativeImageResult(
        status="failed",
        provider=PROVIDER_ID,
        model=config.model,
        prompt=plan.prompt,
        seed=plan.seed,
        steps=plan.steps,
        attempts=attempts,
        created_at=datetime.now(timezone.utc).isoformat(),
        final_width=plan.final_width,
        final_height=plan.final_height,
        aspect_ratio=plan.aspect_ratio,
        overlay_template=plan.overlay_template,
        style_preset=plan.style_preset,
        error_type=last_type or "generation_failed",
        error_message=last_message or "대표 이미지 생성에 실패했습니다.",
    )


def build_generation_metadata(
    result: RepresentativeImageResult,
    *,
    draft_id: str = "",
    image_stage: str = "background",
) -> dict[str, Any]:
    metadata = asdict(result)
    metadata.pop("image_bytes", None)
    metadata["draft_id"] = _clean(draft_id, maximum=120)
    metadata["image_stage"] = _clean(image_stage, maximum=40) or "background"
    return metadata
