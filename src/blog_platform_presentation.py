from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BlogPlatformPresentation:
    label: str
    short_label: str
    tab: str
    emoji: str
    accent: str
    soft_background: str


BLOG_PLATFORM_PRESENTATION = {
    "blogger": BlogPlatformPresentation(
        label="Google Blogger",
        short_label="Blogger",
        tab="🔵 Blogger 3",
        emoji="🔵",
        accent="#4285F4",
        soft_background="rgba(66, 133, 244, 0.10)",
    ),
    "naver_blog": BlogPlatformPresentation(
        label="네이버 블로그",
        short_label="네이버",
        tab="🟢 네이버 1",
        emoji="🟢",
        accent="#03C75A",
        soft_background="rgba(3, 199, 90, 0.10)",
    ),
    "tistory": BlogPlatformPresentation(
        label="티스토리",
        short_label="티스토리",
        tab="🟠 티스토리 1",
        emoji="🟠",
        accent="#F97316",
        soft_background="rgba(249, 115, 22, 0.10)",
    ),
}
BLOG_PLATFORM_ORDER = ("blogger", "naver_blog", "tistory")


def get_blog_platform_presentation(platform: object) -> BlogPlatformPresentation:
    key = str(platform or "").strip()
    return BLOG_PLATFORM_PRESENTATION.get(
        key,
        BlogPlatformPresentation(
            label="블로그",
            short_label="블로그",
            tab="블로그",
            emoji="⚪",
            accent="#64748B",
            soft_background="rgba(100, 116, 139, 0.10)",
        ),
    )
