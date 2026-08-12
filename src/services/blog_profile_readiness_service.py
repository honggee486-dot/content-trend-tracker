from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping
from urllib.parse import urlparse

from src.blog_platform_presentation import get_blog_platform_presentation
from src.services.blog_editor_navigation_service import (
    BLOGGER_LOGIN_URL,
    NAVER_LOGIN_URL,
    TISTORY_LOGIN_URL,
    extract_tistory_blog_home_url,
    normalize_platform_editor_url,
)


READY_STATUS = "ready"
MISSING_STATUS = "missing"
INVALID_STATUS = "invalid"

_GENERIC_WRITE_URLS = {
    "blogger": {
        "https://blogger.com",
        "https://blogger.com/",
        "https://www.blogger.com",
        "https://www.blogger.com/",
    },
    "naver_blog": {
        "https://blog.naver.com",
        "https://blog.naver.com/",
    },
    "tistory": {
        "https://tistory.com",
        "https://tistory.com/",
        "https://www.tistory.com",
        "https://www.tistory.com/",
        TISTORY_LOGIN_URL,
    },
}


@dataclass(frozen=True)
class BlogProfileReadiness:
    blog_profile_id: str
    profile_name: str
    platform: str
    platform_label: str
    status: str
    is_ready: bool
    connection_value: str
    message: str
    recommended_action: str


@dataclass(frozen=True)
class BlogProfileReadinessSummary:
    items: tuple[BlogProfileReadiness, ...]
    ready_count: int
    missing_count: int
    invalid_count: int

    @property
    def total_count(self) -> int:
        return len(self.items)

    @property
    def is_fully_ready(self) -> bool:
        return self.total_count > 0 and self.ready_count == self.total_count

    def platform_counts(self, platform: str) -> tuple[int, int]:
        matches = [item for item in self.items if item.platform == platform]
        return sum(int(item.is_ready) for item in matches), len(matches)


def _normalized_url(value: object) -> str:
    return str(value or "").strip().rstrip("/").casefold()


def _has_non_generic_value(platform: str, value: object) -> bool:
    normalized = _normalized_url(value)
    if not normalized:
        return False
    generic = {
        _normalized_url(item)
        for item in _GENERIC_WRITE_URLS.get(platform, set())
    }
    return normalized not in generic


def _custom_url_is_valid(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlparse(text)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def evaluate_blog_profile_readiness(
    profile: Mapping[str, object],
) -> BlogProfileReadiness:
    platform = str(profile.get("platform") or "custom").strip()
    presentation = get_blog_platform_presentation(platform)
    profile_id = str(profile.get("blog_profile_id") or platform).strip()
    profile_name = str(profile.get("profile_name") or profile_id).strip()
    write_url = str(profile.get("write_url") or "").strip()
    login_url = str(profile.get("login_url") or "").strip()

    if platform == "tistory":
        home_url = (
            extract_tistory_blog_home_url(write_url)
            or extract_tistory_blog_home_url(login_url)
        )
        if home_url:
            return BlogProfileReadiness(
                blog_profile_id=profile_id,
                profile_name=profile_name,
                platform=platform,
                platform_label=presentation.label,
                status=READY_STATUS,
                is_ready=True,
                connection_value=home_url,
                message="개인 티스토리 블로그 주소가 연결되어 있습니다.",
                recommended_action="발행 보조에서 새 글 편집기 이동을 확인하세요.",
            )
        has_specific_value = any(
            _has_non_generic_value(platform, value)
            for value in (write_url, login_url)
            if _normalized_url(value) != _normalized_url(TISTORY_LOGIN_URL)
        )
        status = INVALID_STATUS if has_specific_value else MISSING_STATUS
        message = (
            "저장된 주소에서 개인 티스토리 블로그를 확인하지 못했습니다."
            if status == INVALID_STATUS
            else "개인 티스토리 블로그 주소가 아직 저장되지 않았습니다."
        )
        return BlogProfileReadiness(
            blog_profile_id=profile_id,
            profile_name=profile_name,
            platform=platform,
            platform_label=presentation.label,
            status=status,
            is_ready=False,
            connection_value="",
            message=message,
            recommended_action=(
                "설정 → 발행 채널 → 🟠 티스토리 1에서 "
                "https://내블로그.tistory.com 형태로 저장하세요."
            ),
        )

    if platform in {"blogger", "naver_blog"}:
        if not _has_non_generic_value(platform, write_url):
            label = "Blogger" if platform == "blogger" else "네이버"
            return BlogProfileReadiness(
                blog_profile_id=profile_id,
                profile_name=profile_name,
                platform=platform,
                platform_label=presentation.label,
                status=MISSING_STATUS,
                is_ready=False,
                connection_value="",
                message=f"{label} 새 글 편집기 주소가 아직 저장되지 않았습니다.",
                recommended_action=(
                    f"설정 → 발행 채널 → {profile_name}에서 실제 새 글 편집기 주소를 저장하세요."
                ),
            )
        try:
            editor_url = normalize_platform_editor_url(platform, write_url)
        except ValueError as exc:
            return BlogProfileReadiness(
                blog_profile_id=profile_id,
                profile_name=profile_name,
                platform=platform,
                platform_label=presentation.label,
                status=INVALID_STATUS,
                is_ready=False,
                connection_value="",
                message=str(exc),
                recommended_action=(
                    f"설정 → 발행 채널 → {profile_name}에서 홈 주소가 아니라 "
                    "실제 새 글 편집기 주소를 다시 저장하세요."
                ),
            )
        return BlogProfileReadiness(
            blog_profile_id=profile_id,
            profile_name=profile_name,
            platform=platform,
            platform_label=presentation.label,
            status=READY_STATUS,
            is_ready=True,
            connection_value=editor_url,
            message="새 글 편집기 주소가 연결되어 있습니다.",
            recommended_action="발행 보조에서 새 글 편집기 이동을 확인하세요.",
        )

    if _custom_url_is_valid(write_url):
        return BlogProfileReadiness(
            blog_profile_id=profile_id,
            profile_name=profile_name,
            platform=platform,
            platform_label=presentation.label,
            status=READY_STATUS,
            is_ready=True,
            connection_value=write_url,
            message="글쓰기 주소가 연결되어 있습니다.",
            recommended_action="발행 보조에서 주소 이동을 확인하세요.",
        )
    status = INVALID_STATUS if write_url else MISSING_STATUS
    return BlogProfileReadiness(
        blog_profile_id=profile_id,
        profile_name=profile_name,
        platform=platform,
        platform_label=presentation.label,
        status=status,
        is_ready=False,
        connection_value="",
        message=(
            "저장된 글쓰기 주소가 올바른 URL 형식이 아닙니다."
            if status == INVALID_STATUS
            else "글쓰기 주소가 아직 저장되지 않았습니다."
        ),
        recommended_action="설정에서 실제 글쓰기 주소를 저장하세요.",
    )


def summarize_blog_profile_readiness(
    profiles: Iterable[Mapping[str, object]],
) -> BlogProfileReadinessSummary:
    items = tuple(evaluate_blog_profile_readiness(profile) for profile in profiles)
    return BlogProfileReadinessSummary(
        items=items,
        ready_count=sum(int(item.status == READY_STATUS) for item in items),
        missing_count=sum(int(item.status == MISSING_STATUS) for item in items),
        invalid_count=sum(int(item.status == INVALID_STATUS) for item in items),
    )
