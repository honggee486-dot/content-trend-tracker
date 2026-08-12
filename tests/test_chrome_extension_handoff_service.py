from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.services.chrome_extension_handoff_service import (
    build_allowed_host_patterns,
    build_chrome_extension_handoff,
    host_matches_patterns,
    validate_chrome_extension_handoff,
)
from src.services.publish_preparation_service import (
    PublishCopyPackage,
    PublishOutputPolicy,
)


def _package() -> PublishCopyPackage:
    return PublishCopyPackage(
        seo_title="윈도우 앱 오류 해결",
        meta_description="윈도우 앱 설치 오류의 원인과 해결 순서를 정리합니다.",
        focus_keywords=("윈도우", "앱 오류"),
        image_slots=(
            {
                "slot_number": 1,
                "role": "대표 이미지",
                "position": "도입부 다음",
                "alt_text": "윈도우 앱 오류 해결",
                "note": "대표 화면",
            },
            {
                "slot_number": 2,
                "role": "핵심 설명 이미지",
                "position": "핵심 내용 중간",
                "alt_text": "오류 해결 단계",
                "note": "단계별 설명",
            },
            {
                "slot_number": 3,
                "role": "요약 이미지",
                "position": "마무리 전",
                "alt_text": "해결 방법 요약",
                "note": "체크리스트",
            },
        ),
        output_body="첫 문단입니다.\n\n두 번째 문단입니다.",
        output_tags=("윈도우", "앱오류"),
        image_guide_text="이미지 안내",
        full_output_text="전체 발행 패키지",
        policy=PublishOutputPolicy(
            "tistory_tech",
            "티스토리 IT·기술",
            45,
            150,
            8,
        ),
        warnings=(),
    )


def _draft() -> dict:
    return {
        "draft_id": "draft-1",
        "title": "원본 제목",
    }


def _profile(
    *,
    platform: str = "tistory",
    write_url: str = "https://my-blog.tistory.com/manage/newpost/",
) -> dict:
    return {
        "blog_profile_id": "profile-1",
        "profile_name": "티스토리 IT",
        "platform": platform,
        "write_url": write_url,
    }


def test_build_handoff_uses_expiring_checked_contract() -> None:
    now = datetime(2026, 8, 2, 14, 0, tzinfo=timezone.utc)
    handoff = build_chrome_extension_handoff(
        draft=_draft(),
        profile=_profile(),
        package=_package(),
        now=now,
        nonce="nonce-12345678",
    )

    payload = validate_chrome_extension_handoff(
        handoff.serialized,
        now=now + timedelta(minutes=5),
        current_hostname="my-blog.tistory.com",
    )

    assert payload["schema_version"] == "1.0"
    assert payload["source"] == "content-trend-tracker"
    assert payload["expires_at"] == "2026-08-02T14:10:00Z"
    assert payload["target"]["allowed_host_patterns"] == [
        "my-blog.tistory.com",
        "www.tistory.com",
        "*.tistory.com",
    ]
    assert payload["content"]["title"] == "윈도우 앱 오류 해결"
    assert payload["content"]["body"].startswith("첫 문단")
    assert payload["content"]["tags"] == ["윈도우", "앱오류"]
    assert len(payload["content"]["image_slots"]) == 3
    assert payload["safety"] == {
        "requires_user_action": True,
        "may_submit": False,
        "contains_credentials": False,
        "stores_browser_session": False,
    }
    assert payload["checksum"].startswith("sha256:")


def test_handoff_rejects_tamper_expiry_and_wrong_host() -> None:
    now = datetime(2026, 8, 2, 14, 0, tzinfo=timezone.utc)
    handoff = build_chrome_extension_handoff(
        draft=_draft(),
        profile=_profile(),
        package=_package(),
        now=now,
        nonce="nonce-12345678",
    )

    tampered = dict(handoff.payload)
    tampered["content"] = {**tampered["content"], "title": "변조 제목"}
    with pytest.raises(ValueError, match="체크섬"):
        validate_chrome_extension_handoff(tampered, now=now)

    with pytest.raises(ValueError, match="유효시간"):
        validate_chrome_extension_handoff(
            handoff.payload,
            now=now + timedelta(minutes=10),
        )

    with pytest.raises(ValueError, match="허용한 블로그 편집기"):
        validate_chrome_extension_handoff(
            handoff.payload,
            now=now + timedelta(minutes=1),
            current_hostname="example.com",
        )


def test_known_and_custom_hosts_are_minimally_scoped() -> None:
    assert build_allowed_host_patterns(
        _profile(platform="naver_blog", write_url="https://blog.naver.com/")
    ) == ["blog.naver.com"]
    assert build_allowed_host_patterns(
        _profile(platform="blogger", write_url="https://www.blogger.com/")
    ) == ["www.blogger.com", "blogger.com"]
    assert build_allowed_host_patterns(
        _profile(platform="custom", write_url="https://editor.example.com/write")
    ) == ["editor.example.com"]

    assert host_matches_patterns("abc.tistory.com", ["*.tistory.com"])
    assert not host_matches_patterns("tistory.com", ["*.tistory.com"])
    assert not host_matches_patterns("evil-tistory.com", ["*.tistory.com"])


def test_handoff_contains_no_credentials_or_browser_session_material() -> None:
    now = datetime(2026, 8, 2, 14, 0, tzinfo=timezone.utc)
    profile = {
        **_profile(),
        "login_url": "https://example.com/login",
        "password": "never-include",
        "cookie": "never-include",
    }
    handoff = build_chrome_extension_handoff(
        draft=_draft(),
        profile=profile,
        package=_package(),
        now=now,
        nonce="nonce-12345678",
    )

    assert "never-include" not in handoff.serialized
    assert '"password"' not in handoff.serialized
    assert '"cookie"' not in handoff.serialized
    assert '"login_url"' not in handoff.serialized


def test_handoff_requires_valid_ids_url_and_ttl() -> None:
    with pytest.raises(ValueError, match="초안 ID"):
        build_chrome_extension_handoff(
            draft={},
            profile=_profile(),
            package=_package(),
        )
    with pytest.raises(ValueError, match="글쓰기 페이지 주소"):
        build_chrome_extension_handoff(
            draft=_draft(),
            profile=_profile(write_url="not-a-url"),
            package=_package(),
        )
    with pytest.raises(ValueError, match="유효시간"):
        build_chrome_extension_handoff(
            draft=_draft(),
            profile=_profile(),
            package=_package(),
            ttl_seconds=30,
        )
