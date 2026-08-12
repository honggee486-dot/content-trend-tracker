from __future__ import annotations

from pathlib import Path

from src import blogger_draft_ui


ROOT = Path(__file__).resolve().parents[1]


def test_blogger_ui_requires_user_confirmation_and_draft_only_copy() -> None:
    source = Path(blogger_draft_ui.__file__).read_text(encoding="utf-8")

    assert "Blogger 공식 API 비공개 초안" in source
    assert "공개 게시가 아니라 비공개 초안만 생성됨을 확인했습니다." in source
    assert "Blogger 비공개 초안 전송" in source
    assert "disabled=not confirmed" in source
    assert "게시 전환 API는 사용하지 않으며" in source
    assert "공개 게시·예약 게시·자동 로그인·쿠키 저장은 수행하지 않습니다" in source


def test_blogger_service_contains_insert_draft_but_no_publish_call() -> None:
    source = (ROOT / "src/services/blogger_draft_service.py").read_text(
        encoding="utf-8"
    )

    assert ".insert(" in source
    assert "isDraft=True" in source
    assert ".publish(" not in source
    assert "posts().publish" not in source
    assert 'status == "LIVE"' in source
    assert "동일" not in source or "UNIQUE(draft_id, blog_profile_id, content_hash)" in source


def test_secret_paths_are_ignored_and_safe_upload_excludes_oauth_json() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    safe_zip = (ROOT / "make_safe_upload_zip.bat").read_text(encoding="utf-8")

    assert "data/blogger_oauth_client.json" in gitignore
    assert "data/blogger_oauth_token.json" in gitignore
    assert '"oauth*.json"' in safe_zip
    assert '"client_secret*.json"' in safe_zip


def test_publish_preparation_connects_blogger_panel_only_for_blogger_profile() -> None:
    source = (ROOT / "src/publish_preparation_ui.py").read_text(encoding="utf-8")

    assert "render_blogger_draft_upload" in source
    assert 'if str(profile.get("platform") or "") == "blogger":' in source
    assert "package=package" in source
