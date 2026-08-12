from __future__ import annotations

import pytest

from src.services.blog_editor_navigation_service import (
    BLOGGER_LOGIN_URL,
    NAVER_LOGIN_URL,
    TISTORY_LOGIN_URL,
    build_tistory_write_url,
    extract_tistory_blog_home_url,
    normalize_platform_editor_url,
    resolve_blog_editor_navigation,
)


def test_tistory_home_builds_direct_new_post_url() -> None:
    assert build_tistory_write_url("myblog.tistory.com") == (
        "https://myblog.tistory.com/manage/newpost"
    )
    assert build_tistory_write_url(
        "https://myblog.tistory.com/manage/newpost/123?type=post"
    ) == "https://myblog.tistory.com/manage/newpost"


def test_generic_tistory_portal_is_not_treated_as_personal_blog() -> None:
    assert extract_tistory_blog_home_url("https://www.tistory.com/") == ""
    assert extract_tistory_blog_home_url(TISTORY_LOGIN_URL) == ""
    with pytest.raises(ValueError, match="내 티스토리 블로그 주소"):
        build_tistory_write_url("https://www.tistory.com/")


def test_exact_blogger_editor_url_is_required() -> None:
    valid = "https://www.blogger.com/blog/post/edit/1234567890"
    assert normalize_platform_editor_url("blogger", valid) == valid
    with pytest.raises(ValueError, match="새 글"):
        normalize_platform_editor_url("blogger", BLOGGER_LOGIN_URL)


def test_exact_naver_editor_url_is_required() -> None:
    valid = "https://blog.naver.com/PostWriteForm.naver?blogId=sample"
    assert normalize_platform_editor_url("naver_blog", valid) == valid
    with pytest.raises(ValueError, match="글쓰기"):
        normalize_platform_editor_url("naver_blog", "https://blog.naver.com/sample")


@pytest.mark.parametrize(
    ("platform", "write_url", "expected_label", "expected_login"),
    [
        (
            "blogger",
            "https://www.blogger.com/blog/post/edit/1234567890",
            "Blogger 글쓰기 바로 열기",
            BLOGGER_LOGIN_URL,
        ),
        (
            "naver_blog",
            "https://blog.naver.com/PostWriteForm.naver?blogId=sample",
            "네이버 글쓰기 바로 열기",
            NAVER_LOGIN_URL,
        ),
    ],
)
def test_platform_navigation_opens_saved_exact_editor(
    platform: str,
    write_url: str,
    expected_label: str,
    expected_login: str,
) -> None:
    target = resolve_blog_editor_navigation(
        {
            "platform": platform,
            "platform_label": platform,
            "write_url": write_url,
        }
    )
    assert target.configuration_required is False
    assert target.write_url == write_url
    assert target.login_url == expected_login
    assert target.action_label == expected_label


def test_generic_blogger_and_naver_urls_require_configuration() -> None:
    blogger = resolve_blog_editor_navigation(
        {"platform": "blogger", "write_url": "https://www.blogger.com/"}
    )
    naver = resolve_blog_editor_navigation(
        {"platform": "naver_blog", "write_url": "https://blog.naver.com/"}
    )
    assert blogger.configuration_required is True
    assert naver.configuration_required is True


def test_tistory_navigation_opens_direct_editor_when_specific_blog_is_saved() -> None:
    target = resolve_blog_editor_navigation(
        {
            "platform": "tistory",
            "platform_label": "티스토리",
            "login_url": TISTORY_LOGIN_URL,
            "write_url": "https://sample.tistory.com/",
        }
    )
    assert target.configuration_required is False
    assert target.write_url == "https://sample.tistory.com/manage/newpost"
    assert target.action_label == "티스토리 글쓰기 바로 열기"
