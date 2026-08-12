from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse


BLOGGER_LOGIN_URL = "https://www.blogger.com/"
NAVER_LOGIN_URL = "https://nid.naver.com/nidlogin.login"
TISTORY_LOGIN_URL = "https://www.tistory.com/auth/login"
_TISTORY_GENERIC_HOSTS = {
    "tistory.com",
    "www.tistory.com",
    "notice.tistory.com",
}


@dataclass(frozen=True)
class BlogEditorNavigationTarget:
    platform: str
    platform_label: str
    write_url: str
    login_url: str
    action_label: str
    action_help: str
    configuration_required: bool
    configuration_message: str


def _normalize_http_url(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlparse(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return text


def extract_tistory_blog_home_url(value: object) -> str:
    """Return only the personal Tistory blog origin, never the generic portal."""
    normalized = _normalize_http_url(value)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    host = str(parsed.hostname or "").casefold()
    if not host or host in _TISTORY_GENERIC_HOSTS:
        return ""
    if str(parsed.path or "").casefold().startswith("/auth/"):
        return ""
    scheme = parsed.scheme.lower()
    return f"{scheme}://{parsed.netloc}"


def build_tistory_write_url(value: object) -> str:
    home_url = extract_tistory_blog_home_url(value)
    if not home_url:
        raise ValueError(
            "내 티스토리 블로그 주소를 입력하세요. "
            "예: https://내블로그.tistory.com"
        )
    return f"{home_url}/manage/newpost"


def normalize_platform_editor_url(platform: object, value: object) -> str:
    """Validate an exact compose URL for platforms without a public URL contract."""
    platform_key = str(platform or "").strip()
    normalized = _normalize_http_url(value)
    if not normalized:
        label = "Blogger" if platform_key == "blogger" else "네이버"
        raise ValueError(f"{label} 새 글 편집기 주소를 입력하세요.")

    parsed = urlparse(normalized)
    host = str(parsed.hostname or "").casefold()
    route = f"{parsed.path}?{parsed.query}".casefold()

    if platform_key == "blogger":
        if host not in {"blogger.com", "www.blogger.com"} or "/blog/post/edit/" not in route:
            raise ValueError(
                "Blogger에서 해당 블로그의 ‘새 글’을 연 뒤 주소창의 편집기 주소를 "
                "그대로 복사해 입력하세요."
            )
        return normalized

    if platform_key == "naver_blog":
        if host != "blog.naver.com" or "write" not in route:
            raise ValueError(
                "네이버 블로그에서 ‘글쓰기’를 연 뒤 주소창의 편집기 주소를 "
                "그대로 복사해 입력하세요."
            )
        return normalized

    return normalized


def _configured_editor_url(platform: str, value: object) -> str:
    try:
        return normalize_platform_editor_url(platform, value)
    except ValueError:
        return ""


def resolve_blog_editor_navigation(
    profile: Mapping[str, object],
) -> BlogEditorNavigationTarget:
    platform = str(profile.get("platform") or "custom").strip()
    platform_label = str(
        profile.get("platform_label") or profile.get("profile_name") or "블로그"
    ).strip()
    raw_login_url = _normalize_http_url(profile.get("login_url"))
    raw_write_url = _normalize_http_url(profile.get("write_url"))

    if platform == "tistory":
        home_url = (
            extract_tistory_blog_home_url(raw_write_url)
            or extract_tistory_blog_home_url(raw_login_url)
        )
        if not home_url:
            return BlogEditorNavigationTarget(
                platform=platform,
                platform_label="티스토리",
                write_url="",
                login_url=TISTORY_LOGIN_URL,
                action_label="티스토리 글쓰기 바로 열기",
                action_help=(
                    "설정에 저장한 개인 티스토리 주소의 새 글 편집기를 엽니다. "
                    "로그인되어 있으면 바로 편집기로 이동합니다."
                ),
                configuration_required=True,
                configuration_message=(
                    "티스토리 개인 블로그 주소가 아직 연결되지 않았습니다. "
                    "설정 → 발행 채널 → 🟠 티스토리 1에서 "
                    "‘내 티스토리 블로그 주소’를 저장하세요."
                ),
            )
        return BlogEditorNavigationTarget(
            platform=platform,
            platform_label="티스토리",
            write_url=build_tistory_write_url(home_url),
            login_url=TISTORY_LOGIN_URL,
            action_label="티스토리 글쓰기 바로 열기",
            action_help=(
                "로그인되어 있으면 개인 티스토리의 새 글 편집기로 바로 이동합니다. "
                "로그인 화면이 나오면 로그인한 뒤 같은 버튼을 다시 누르세요."
            ),
            configuration_required=False,
            configuration_message="",
        )

    if platform == "blogger":
        editor_url = _configured_editor_url(platform, raw_write_url)
        login_url = BLOGGER_LOGIN_URL
        label = "Blogger"
    elif platform == "naver_blog":
        editor_url = _configured_editor_url(platform, raw_write_url)
        login_url = NAVER_LOGIN_URL
        label = "네이버"
    else:
        editor_url = raw_write_url
        login_url = raw_login_url
        label = platform_label

    configuration_required = not bool(editor_url)
    return BlogEditorNavigationTarget(
        platform=platform,
        platform_label=label,
        write_url=editor_url,
        login_url=login_url,
        action_label=f"{label} 글쓰기 바로 열기",
        action_help=(
            f"설정에 저장한 {label} 새 글 편집기 주소를 일반 Chrome에서 엽니다. "
            "로그인 화면이 나오면 로그인한 뒤 같은 버튼을 다시 누르세요."
        ),
        configuration_required=configuration_required,
        configuration_message=(
            f"{label} 새 글 편집기 주소가 없습니다. "
            "설정 → 발행 채널에서 실제 글쓰기 화면의 주소를 저장하세요."
            if configuration_required
            else ""
        ),
    )
