from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
from uuid import uuid4

from src.services.publish_preparation_service import PublishCopyPackage


SCHEMA_VERSION = "1.0"
SOURCE_NAME = "content-trend-tracker"
DEFAULT_TTL_SECONDS = 600
MAX_TTL_SECONDS = 1800

_KNOWN_HOST_PATTERNS: dict[str, tuple[str, ...]] = {
    "naver_blog": ("blog.naver.com",),
    "tistory": ("www.tistory.com", "*.tistory.com"),
    "blogger": ("www.blogger.com", "blogger.com"),
}


@dataclass(frozen=True)
class ChromeExtensionHandoff:
    payload: dict[str, Any]
    serialized: str
    checksum: str
    issued_at: str
    expires_at: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime:
    current = value or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _checksum_for_payload(payload_without_checksum: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        _canonical_json(payload_without_checksum).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _normalize_host(value: str) -> str:
    return str(value or "").strip().lower().rstrip(".")


def _host_from_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return _normalize_host(parsed.hostname)


def build_allowed_host_patterns(profile: Mapping[str, Any]) -> list[str]:
    platform = str(profile.get("platform") or "custom").strip()
    patterns: list[str] = list(_KNOWN_HOST_PATTERNS.get(platform, ()))
    write_host = _host_from_url(str(profile.get("write_url") or ""))
    if write_host:
        patterns.insert(0, write_host)

    result: list[str] = []
    seen: set[str] = set()
    for value in patterns:
        clean = str(value or "").strip().lower()
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def host_matches_patterns(hostname: str, patterns: Sequence[str]) -> bool:
    host = _normalize_host(hostname)
    if not host:
        return False
    for pattern in patterns:
        clean = _normalize_host(str(pattern or ""))
        if not clean:
            continue
        if clean.startswith("*."):
            suffix = clean[1:]
            if host.endswith(suffix) and host != suffix.lstrip("."):
                return True
        elif host == clean:
            return True
    return False


def _normalize_image_slots(
    slots: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, slot in enumerate(list(slots)[:3], start=1):
        result.append(
            {
                "slot_number": index,
                "role": str(slot.get("role") or f"이미지 {index}").strip(),
                "position": str(slot.get("position") or "본문 중간").strip(),
                "alt_text": str(slot.get("alt_text") or "").strip(),
                "note": str(slot.get("note") or "").strip(),
            }
        )
    return result


def build_chrome_extension_handoff(
    *,
    draft: Mapping[str, Any],
    profile: Mapping[str, Any],
    package: PublishCopyPackage,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    nonce: str | None = None,
) -> ChromeExtensionHandoff:
    draft_id = str(draft.get("draft_id") or "").strip()
    profile_id = str(profile.get("blog_profile_id") or "").strip()
    if not draft_id:
        raise ValueError("Chrome 확장 전달 데이터에 사용할 초안 ID가 없습니다.")
    if not profile_id:
        raise ValueError("Chrome 확장 전달 데이터에 사용할 블로그 프로필 ID가 없습니다.")

    write_url = str(profile.get("write_url") or "").strip()
    write_host = _host_from_url(write_url)
    if not write_host:
        raise ValueError("Chrome 확장 전달에 사용할 유효한 글쓰기 페이지 주소가 없습니다.")

    ttl = int(ttl_seconds)
    if ttl < 60 or ttl > MAX_TTL_SECONDS:
        raise ValueError(
            f"Chrome 확장 전달 유효시간은 60~{MAX_TTL_SECONDS}초여야 합니다."
        )

    issued = _as_utc(now)
    expires = issued + timedelta(seconds=ttl)
    allowed_hosts = build_allowed_host_patterns(profile)
    if not allowed_hosts:
        raise ValueError("Chrome 확장이 사용할 허용 호스트를 만들 수 없습니다.")

    clean_nonce = re.sub(r"[^A-Za-z0-9_-]", "", str(nonce or uuid4().hex))[:64]
    if len(clean_nonce) < 8:
        raise ValueError("Chrome 확장 전달 nonce는 8자 이상이어야 합니다.")

    payload_without_checksum: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE_NAME,
        "issued_at": _iso_z(issued),
        "expires_at": _iso_z(expires),
        "nonce": clean_nonce,
        "target": {
            "platform": str(profile.get("platform") or "custom").strip(),
            "profile_id": profile_id,
            "profile_name": str(profile.get("profile_name") or "").strip(),
            "write_url": write_url,
            "allowed_host_patterns": allowed_hosts,
        },
        "content": {
            "title": package.seo_title,
            "body": package.output_body,
            "tags": list(package.output_tags),
            "meta_description": package.meta_description,
            "focus_keywords": list(package.focus_keywords),
            "image_slots": _normalize_image_slots(package.image_slots),
        },
        "safety": {
            "requires_user_action": True,
            "may_submit": False,
            "contains_credentials": False,
            "stores_browser_session": False,
        },
    }
    checksum = _checksum_for_payload(payload_without_checksum)
    payload = {**payload_without_checksum, "checksum": checksum}
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    return ChromeExtensionHandoff(
        payload=payload,
        serialized=serialized,
        checksum=checksum,
        issued_at=payload["issued_at"],
        expires_at=payload["expires_at"],
    )


def validate_chrome_extension_handoff(
    value: str | Mapping[str, Any],
    *,
    now: datetime | None = None,
    current_hostname: str = "",
) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("Chrome 확장 전달 JSON을 읽을 수 없습니다.") from exc
    else:
        payload = dict(value)

    if not isinstance(payload, dict):
        raise ValueError("Chrome 확장 전달 데이터는 JSON 객체여야 합니다.")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("지원하지 않는 Chrome 확장 전달 스키마입니다.")
    if payload.get("source") != SOURCE_NAME:
        raise ValueError("콘텐츠 트렌드 트래커가 만든 전달 데이터가 아닙니다.")

    checksum = str(payload.get("checksum") or "")
    unsigned = {key: item for key, item in payload.items() if key != "checksum"}
    if checksum != _checksum_for_payload(unsigned):
        raise ValueError("Chrome 확장 전달 데이터의 체크섬이 일치하지 않습니다.")

    try:
        expires_at = datetime.fromisoformat(
            str(payload.get("expires_at") or "").replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("Chrome 확장 전달 만료 시각이 올바르지 않습니다.") from exc
    if _as_utc(now) >= _as_utc(expires_at):
        raise ValueError("Chrome 확장 전달 데이터의 유효시간이 지났습니다.")

    safety = payload.get("safety")
    if not isinstance(safety, dict):
        raise ValueError("Chrome 확장 전달 안전 정보가 없습니다.")
    if (
        safety.get("requires_user_action") is not True
        or safety.get("may_submit") is not False
        or safety.get("contains_credentials") is not False
        or safety.get("stores_browser_session") is not False
    ):
        raise ValueError("Chrome 확장 전달 안전 계약이 올바르지 않습니다.")

    target = payload.get("target")
    content = payload.get("content")
    if not isinstance(target, dict) or not isinstance(content, dict):
        raise ValueError("Chrome 확장 전달 대상 또는 본문 정보가 없습니다.")
    patterns = target.get("allowed_host_patterns")
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("Chrome 확장 허용 호스트가 없습니다.")
    if current_hostname and not host_matches_patterns(current_hostname, patterns):
        raise ValueError("현재 페이지는 이 전달 데이터가 허용한 블로그 편집기가 아닙니다.")

    if not str(content.get("title") or "").strip():
        raise ValueError("Chrome 확장으로 입력할 제목이 없습니다.")
    if not str(content.get("body") or "").strip():
        raise ValueError("Chrome 확장으로 입력할 본문이 없습니다.")
    tags = content.get("tags")
    image_slots = content.get("image_slots")
    if not isinstance(tags, list):
        raise ValueError("Chrome 확장 태그 형식이 올바르지 않습니다.")
    if not isinstance(image_slots, list) or len(image_slots) != 3:
        raise ValueError("Chrome 확장 이미지 슬롯은 정확히 3개여야 합니다.")

    return payload
