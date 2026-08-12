from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from src.services.blogger_draft_service import (
    build_blogger_post_content,
    ensure_blogger_draft_schema,
    get_blogger_connection_status,
    get_blogger_profile_binding,
    list_blogger_blogs,
    list_blogger_draft_uploads,
    save_blogger_profile_binding,
    upload_blogger_draft,
)


class _FakeRequest:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class _FakeBlogs:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def listByUser(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeRequest(self.response)


class _FakePosts:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def insert(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeRequest(self.response)


class _FakeClient:
    def __init__(self, *, blogs=None, post=None):
        self.blogs_api = _FakeBlogs(blogs or {"items": []})
        self.posts_api = _FakePosts(post or {"id": "post-1", "status": "DRAFT"})

    def blogs(self):
        return self.blogs_api

    def posts(self):
        return self.posts_api


def _package():
    return SimpleNamespace(
        seo_title="SEO 제목",
        output_body="# 제목\n\n본문 **강조**",
        output_tags=("AI", "가이드", "AI"),
    )


def _draft():
    return {"draft_id": "draft-1"}


def _profile():
    return {"blog_profile_id": "profile-blogger", "platform": "blogger"}


def test_schema_and_profile_binding_are_additive() -> None:
    con = duckdb.connect(":memory:")
    try:
        ensure_blogger_draft_schema(con)
        save_blogger_profile_binding(
            con,
            blog_profile_id="profile-blogger",
            blogger_blog_id="blog-1",
            blogger_blog_name="테스트 블로그",
            blogger_blog_url="https://example.blogspot.com/",
        )
        binding = get_blogger_profile_binding(
            con,
            blog_profile_id="profile-blogger",
        )
        assert binding is not None
        assert binding["blogger_blog_id"] == "blog-1"
        assert binding["blogger_blog_name"] == "테스트 블로그"
    finally:
        con.close()


def test_list_blogger_blogs_uses_self_admin_view() -> None:
    client = _FakeClient(
        blogs={
            "items": [
                {"id": "2", "name": "B 블로그", "url": "https://b.example/"},
                {"id": "1", "name": "A 블로그", "url": "https://a.example/"},
            ]
        }
    )

    blogs = list_blogger_blogs(api_client=client)

    assert [item["id"] for item in blogs] == ["1", "2"]
    assert client.blogs_api.calls == [
        {"userId": "self", "fetchUserInfo": True, "view": "ADMIN"}
    ]


def test_upload_creates_draft_only_and_reuses_identical_content() -> None:
    con = duckdb.connect(":memory:")
    client = _FakeClient(post={"id": "post-123", "status": "DRAFT"})
    try:
        first = upload_blogger_draft(
            con,
            draft=_draft(),
            profile=_profile(),
            package=_package(),
            blogger_blog_id="blog-1",
            api_client=client,
        )
        second = upload_blogger_draft(
            con,
            draft=_draft(),
            profile=_profile(),
            package=_package(),
            blogger_blog_id="blog-1",
            api_client=client,
        )

        assert first.reused is False
        assert second.reused is True
        assert second.blogger_post_id == "post-123"
        assert len(client.posts_api.calls) == 1
        call = client.posts_api.calls[0]
        assert call["blogId"] == "blog-1"
        assert call["isDraft"] is True
        assert call["body"]["title"] == "SEO 제목"
        assert call["body"]["labels"] == ["AI", "가이드"]
        assert "<h1>제목</h1>" in call["body"]["content"]
        uploads = list_blogger_draft_uploads(
            con,
            draft_id="draft-1",
            blog_profile_id="profile-blogger",
        )
        assert len(uploads) == 1
        assert uploads[0]["status"] == "draft_created"
    finally:
        con.close()


def test_live_response_is_rejected_and_not_recorded() -> None:
    con = duckdb.connect(":memory:")
    client = _FakeClient(post={"id": "post-live", "status": "LIVE"})
    try:
        with pytest.raises(RuntimeError, match="초안이 아닌 공개 상태"):
            upload_blogger_draft(
                con,
                draft=_draft(),
                profile=_profile(),
                package=_package(),
                blogger_blog_id="blog-1",
                api_client=client,
            )
        assert list_blogger_draft_uploads(
            con,
            draft_id="draft-1",
            blog_profile_id="profile-blogger",
        ) == []
    finally:
        con.close()


def test_non_blogger_profile_is_rejected_before_api_call() -> None:
    con = duckdb.connect(":memory:")
    client = _FakeClient()
    try:
        with pytest.raises(ValueError, match="Blogger 프로필"):
            upload_blogger_draft(
                con,
                draft=_draft(),
                profile={"blog_profile_id": "profile-1", "platform": "tistory"},
                package=_package(),
                blogger_blog_id="blog-1",
                api_client=client,
            )
        assert client.posts_api.calls == []
    finally:
        con.close()


def test_connection_status_does_not_read_or_expose_secret_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client_path = tmp_path / "client.json"
    token_path = tmp_path / "token.json"
    client_path.write_text('{"secret":"do-not-read"}', encoding="utf-8")
    token_path.write_text('{"token":"do-not-read"}', encoding="utf-8")
    monkeypatch.setattr(
        "src.services.blogger_draft_service._load_google_dependencies",
        lambda: (object(), object(), object(), object()),
    )

    status = get_blogger_connection_status(
        client_secret_path=client_path,
        token_path=token_path,
    )

    assert status.dependency_ready is True
    assert status.client_secret_ready is True
    assert status.token_ready is True
    assert "do-not-read" not in status.message


def test_markdown_body_is_converted_to_html() -> None:
    html = build_blogger_post_content("## 소제목\n\n- 하나\n- 둘")
    assert "<h2>소제목</h2>" in html
    assert "<li>하나</li>" in html
