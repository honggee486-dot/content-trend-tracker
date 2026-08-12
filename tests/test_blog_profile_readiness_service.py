from __future__ import annotations

import pytest

from src.services.blog_profile_readiness_service import (
    INVALID_STATUS,
    MISSING_STATUS,
    READY_STATUS,
    evaluate_blog_profile_readiness,
    summarize_blog_profile_readiness,
)


@pytest.mark.parametrize(
    ("profile", "expected_value"),
    [
        (
            {
                "blog_profile_id": "blogger_life",
                "profile_name": "생활자료",
                "platform": "blogger",
                "write_url": "https://www.blogger.com/blog/post/edit/1234567890",
            },
            "https://www.blogger.com/blog/post/edit/1234567890",
        ),
        (
            {
                "blog_profile_id": "naver_local",
                "profile_name": "국내 장소",
                "platform": "naver_blog",
                "write_url": "https://blog.naver.com/PostWriteForm.naver?blogId=sample",
            },
            "https://blog.naver.com/PostWriteForm.naver?blogId=sample",
        ),
        (
            {
                "blog_profile_id": "blog_tistory_default",
                "profile_name": "티스토리",
                "platform": "tistory",
                "login_url": "https://www.tistory.com/auth/login",
                "write_url": "https://sample.tistory.com/manage/newpost",
            },
            "https://sample.tistory.com",
        ),
    ],
)
def test_supported_platform_profile_is_ready(
    profile: dict[str, object],
    expected_value: str,
) -> None:
    result = evaluate_blog_profile_readiness(profile)

    assert result.status == READY_STATUS
    assert result.is_ready is True
    assert result.connection_value == expected_value
    assert "연결" in result.message


@pytest.mark.parametrize(
    "profile",
    [
        {
            "blog_profile_id": "blogger_life",
            "profile_name": "생활자료",
            "platform": "blogger",
            "write_url": "https://www.blogger.com/",
        },
        {
            "blog_profile_id": "naver_local",
            "profile_name": "국내 장소",
            "platform": "naver_blog",
            "write_url": "https://blog.naver.com/",
        },
        {
            "blog_profile_id": "blog_tistory_default",
            "profile_name": "티스토리",
            "platform": "tistory",
            "login_url": "https://www.tistory.com/auth/login",
            "write_url": "https://www.tistory.com/",
        },
    ],
)
def test_generic_or_empty_platform_address_is_missing(
    profile: dict[str, object],
) -> None:
    result = evaluate_blog_profile_readiness(profile)

    assert result.status == MISSING_STATUS
    assert result.is_ready is False
    assert result.connection_value == ""
    assert "설정 → 발행 채널" in result.recommended_action


@pytest.mark.parametrize(
    "profile",
    [
        {
            "blog_profile_id": "blogger_life",
            "profile_name": "생활자료",
            "platform": "blogger",
            "write_url": "https://www.blogger.com/dashboard",
        },
        {
            "blog_profile_id": "naver_local",
            "profile_name": "국내 장소",
            "platform": "naver_blog",
            "write_url": "https://blog.naver.com/sample",
        },
        {
            "blog_profile_id": "blog_tistory_default",
            "profile_name": "티스토리",
            "platform": "tistory",
            "write_url": "ftp://sample.tistory.com",
        },
    ],
)
def test_wrong_specific_address_is_invalid(
    profile: dict[str, object],
) -> None:
    result = evaluate_blog_profile_readiness(profile)

    assert result.status == INVALID_STATUS
    assert result.is_ready is False
    assert result.connection_value == ""
    assert "다시 저장" in result.recommended_action or "형태로 저장" in result.recommended_action


def test_summary_counts_and_platform_totals() -> None:
    profiles = [
        {
            "blog_profile_id": "blogger_ready",
            "profile_name": "Blogger 준비",
            "platform": "blogger",
            "write_url": "https://www.blogger.com/blog/post/edit/1",
        },
        {
            "blog_profile_id": "blogger_missing",
            "profile_name": "Blogger 미설정",
            "platform": "blogger",
            "write_url": "https://www.blogger.com/",
        },
        {
            "blog_profile_id": "naver_invalid",
            "profile_name": "네이버 오류",
            "platform": "naver_blog",
            "write_url": "https://blog.naver.com/sample",
        },
        {
            "blog_profile_id": "tistory_ready",
            "profile_name": "티스토리 준비",
            "platform": "tistory",
            "write_url": "https://sample.tistory.com/manage/newpost",
        },
    ]

    summary = summarize_blog_profile_readiness(profiles)

    assert summary.total_count == 4
    assert summary.ready_count == 2
    assert summary.missing_count == 1
    assert summary.invalid_count == 1
    assert summary.is_fully_ready is False
    assert summary.platform_counts("blogger") == (1, 2)
    assert summary.platform_counts("naver_blog") == (0, 1)
    assert summary.platform_counts("tistory") == (1, 1)
