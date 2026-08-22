from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


IMAGE_STRATEGIES = {"official_capture", "verified_free", "generated"}
PUBLIC_CAPTURE_BLOCKED_PATH_MARKERS = (
    "/login",
    "/signin",
    "/sign-in",
    "/account",
    "/dashboard",
    "/admin",
    "/oauth",
)


@dataclass(frozen=True)
class ImageAcquisitionPlan:
    index: int
    strategy: str
    status: str
    action: str
    reason: str
    position: str = ""
    purpose: str = ""
    caption: str = ""
    alt_text: str = ""
    source_id: str = ""
    source_url: str = ""
    capture_target: str = ""
    capture_anchor: str = ""
    capture_note: str = ""
    prompt: str = ""
    asset_page_url: str = ""
    provider: str = ""
    zero_cost_only: bool = True
    use_isolated_unauthenticated_browser: bool = False
    allow_login: bool = False
    allow_cookie_import: bool = False
    allow_captcha_bypass: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: object, *, maximum: int = 1000) -> str:
    return " ".join(str(value or "").split())[:maximum].strip()


def _normalized_url(value: object) -> str:
    text = _clean(value, maximum=2000)
    if not text:
        return ""
    parsed = urlparse(text)
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/") or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{scheme}://{host}{port}{path}{query}"


def _is_public_http_url(value: object) -> tuple[bool, str]:
    text = _clean(value, maximum=2000)
    if not text:
        return False, "공식 페이지 URL이 없습니다."
    parsed = urlparse(text)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return False, "http 또는 https 공개 페이지 URL이 아닙니다."
    if parsed.username or parsed.password:
        return False, "인증정보가 포함된 URL은 자동 캡처하지 않습니다."

    host = parsed.hostname.casefold()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return False, "로컬 주소는 자동 캡처 대상이 아닙니다."
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return False, "사설·로컬·예약 IP 주소는 자동 캡처하지 않습니다."

    path = parsed.path.casefold()
    if any(marker in path for marker in PUBLIC_CAPTURE_BLOCKED_PATH_MARKERS):
        return False, "로그인·계정·관리자 성격의 URL은 자동 캡처하지 않습니다."
    return True, ""


def _source_map(data: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    sources = data.get("sources")
    if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes, bytearray)):
        return result
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        source_id = _clean(source.get("id"), maximum=120)
        source_url = _clean(source.get("url"), maximum=2000)
        if source_id:
            result[source_id] = source_url
    return result


def _strategy(block: Mapping[str, Any]) -> str:
    value = _clean(block.get("image_strategy"), maximum=40)
    if value in IMAGE_STRATEGIES:
        return value
    capture = block.get("source_capture")
    if isinstance(capture, Mapping) and capture.get("needed") is True:
        return "official_capture"
    free_image = block.get("free_image")
    if isinstance(free_image, Mapping) and free_image.get("status") == "verified_free":
        return "verified_free"
    return "generated"


def _official_capture_plan(
    block: Mapping[str, Any],
    *,
    index: int,
    source_urls: Mapping[str, str],
) -> ImageAcquisitionPlan:
    capture = block.get("source_capture")
    if not isinstance(capture, Mapping):
        capture = {}
    source_id = _clean(capture.get("source_id"), maximum=120)
    source_url = _clean(capture.get("source_url"), maximum=2000)
    capture_target = _clean(capture.get("capture_target"), maximum=1000)
    capture_anchor = _clean(capture.get("capture_anchor"), maximum=300)
    capture_note = _clean(capture.get("capture_note"), maximum=1200)

    reasons: list[str] = []
    if capture.get("needed") is not True:
        reasons.append("source_capture.needed가 true가 아닙니다.")
    expected_url = _clean(source_urls.get(source_id), maximum=2000) if source_id else ""
    if not source_id or not expected_url:
        reasons.append("검증된 sources의 source_id를 확인할 수 없습니다.")
    elif _normalized_url(expected_url) != _normalized_url(source_url):
        reasons.append("source_url이 검증된 sources URL과 일치하지 않습니다.")
    public, public_reason = _is_public_http_url(source_url)
    if not public:
        reasons.append(public_reason)
    if not capture_target:
        reasons.append("자동 캡처 대상 설명이 없습니다.")
    if not capture_anchor:
        reasons.append("페이지에서 찾을 정확한 capture_anchor가 없습니다.")

    status = "needs_review" if reasons else "ready"
    reason = " ".join(reason for reason in reasons if reason) or "공개 공식 페이지의 근거 영역을 자동 캡처할 준비가 됐습니다."
    return ImageAcquisitionPlan(
        index=index,
        strategy="official_capture",
        status=status,
        action="capture_public_source" if status == "ready" else "manual_review",
        reason=reason,
        position=_clean(block.get("position"), maximum=300),
        purpose=_clean(block.get("purpose"), maximum=500),
        caption=_clean(block.get("caption"), maximum=500),
        alt_text=_clean(block.get("alt_text"), maximum=500),
        source_id=source_id,
        source_url=source_url,
        capture_target=capture_target,
        capture_anchor=capture_anchor,
        capture_note=capture_note,
        zero_cost_only=True,
        use_isolated_unauthenticated_browser=status == "ready",
        allow_login=False,
        allow_cookie_import=False,
        allow_captcha_bypass=False,
    )


def _verified_free_plan(block: Mapping[str, Any], *, index: int) -> ImageAcquisitionPlan:
    free_image = block.get("free_image")
    if not isinstance(free_image, Mapping):
        free_image = {}
    verified = free_image.get("status") == "verified_free"
    commercial = free_image.get("commercial_use_allowed") is True
    paid = free_image.get("payment_required") is True or free_image.get("premium_or_subscription_required") is True
    page_url = _clean(free_image.get("page_url"), maximum=2000)
    license_url = _clean(free_image.get("license_url"), maximum=2000)
    ready = bool(verified and commercial and not paid and page_url and license_url)
    return ImageAcquisitionPlan(
        index=index,
        strategy="verified_free",
        status="ready" if ready else "needs_review",
        action="download_verified_free" if ready else "manual_review",
        reason=(
            "검증된 무료 자산과 별도 라이선스 근거가 있어 자동 준비할 수 있습니다."
            if ready
            else "무료 자산의 상업 이용·비용·라이선스 근거를 자동 확정할 수 없습니다."
        ),
        position=_clean(block.get("position"), maximum=300),
        purpose=_clean(block.get("purpose"), maximum=500),
        caption=_clean(block.get("caption"), maximum=500),
        alt_text=_clean(block.get("alt_text"), maximum=500),
        asset_page_url=page_url,
        provider=_clean(free_image.get("provider"), maximum=120),
        zero_cost_only=True,
    )


def _generated_plan(block: Mapping[str, Any], *, index: int) -> ImageAcquisitionPlan:
    prompt = _clean(block.get("prompt"), maximum=2048)
    ready = bool(prompt)
    return ImageAcquisitionPlan(
        index=index,
        strategy="generated",
        status="ready" if ready else "needs_review",
        action="generate_zero_cost_image" if ready else "manual_review",
        reason=(
            "생성 프롬프트가 있어 0원 이미지 생성 경로로 자동 준비할 수 있습니다."
            if ready
            else "생성 이미지 프롬프트가 없어 자동 생성할 수 없습니다."
        ),
        position=_clean(block.get("position"), maximum=300),
        purpose=_clean(block.get("purpose"), maximum=500),
        caption=_clean(block.get("caption"), maximum=500),
        alt_text=_clean(block.get("alt_text"), maximum=500),
        prompt=prompt,
        provider="cloudflare_workers_ai",
        zero_cost_only=True,
    )


def build_image_acquisition_plan(
    block: Mapping[str, Any],
    *,
    index: int,
    source_urls: Mapping[str, str] | None = None,
) -> ImageAcquisitionPlan:
    strategy = _strategy(block)
    if strategy == "official_capture":
        return _official_capture_plan(block, index=index, source_urls=source_urls or {})
    if strategy == "verified_free":
        return _verified_free_plan(block, index=index)
    return _generated_plan(block, index=index)


def build_image_acquisition_plans(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    source_urls = _source_map(data)
    blocks = data.get("blocks")
    images: list[Mapping[str, Any]] = []
    if isinstance(blocks, Sequence) and not isinstance(blocks, (str, bytes, bytearray)):
        images = [item for item in blocks if isinstance(item, Mapping) and item.get("type") == "image"]
    if not images:
        prompts = data.get("image_prompts")
        if isinstance(prompts, Sequence) and not isinstance(prompts, (str, bytes, bytearray)):
            images = [item for item in prompts if isinstance(item, Mapping)]
    return [
        build_image_acquisition_plan(image, index=index, source_urls=source_urls).to_dict()
        for index, image in enumerate(images, start=1)
    ]
