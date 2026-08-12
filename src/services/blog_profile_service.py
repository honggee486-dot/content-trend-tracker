from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlparse
from uuid import uuid4

import duckdb


PLATFORM_DEFINITIONS: dict[str, dict[str, str]] = {
    "naver_blog": {
        "label": "네이버 블로그",
        "default_login_url": "https://nid.naver.com/nidlogin.login",
        "default_write_url": "https://blog.naver.com/",
        "default_output_format": "plain_text",
    },
    "tistory": {
        "label": "티스토리",
        "default_login_url": "https://www.tistory.com/auth/login",
        "default_write_url": "https://www.tistory.com/",
        "default_output_format": "markdown",
    },
    "wordpress_com": {
        "label": "WordPress.com",
        "default_login_url": "https://wordpress.com/log-in",
        "default_write_url": "https://wordpress.com/post",
        "default_output_format": "markdown",
    },
    "wordpress_self_hosted": {
        "label": "WordPress 자체 설치",
        "default_login_url": "",
        "default_write_url": "",
        "default_output_format": "markdown",
    },
    "blogger": {
        "label": "Google Blogger",
        "default_login_url": "https://www.blogger.com/",
        "default_write_url": "https://www.blogger.com/",
        "default_output_format": "plain_text",
    },
    "velog": {
        "label": "Velog",
        "default_login_url": "https://velog.io/",
        "default_write_url": "https://velog.io/write",
        "default_output_format": "markdown",
    },
    "brunchstory": {
        "label": "브런치스토리",
        "default_login_url": "https://brunch.co.kr/",
        "default_write_url": "https://brunch.co.kr/",
        "default_output_format": "plain_text",
    },
    "custom": {
        "label": "사용자 지정",
        "default_login_url": "",
        "default_write_url": "",
        "default_output_format": "plain_text",
    },
}

OUTPUT_FORMAT_LABELS = {
    "plain_text": "일반 텍스트",
    "markdown": "Markdown",
}


def get_platform_options() -> list[str]:
    return list(PLATFORM_DEFINITIONS)


def get_platform_definition(platform: str) -> dict[str, str]:
    return PLATFORM_DEFINITIONS.get(platform, PLATFORM_DEFINITIONS["custom"])


def _validate_url(value: str, *, field_label: str, required: bool) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        if required:
            raise ValueError(f"{field_label}을 입력하세요.")
        return ""
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_label}은 http 또는 https 주소여야 합니다.")
    return normalized


def _normalize_tags(tags: list[str] | str | None) -> list[str]:
    if tags is None:
        return []
    values = tags.split(",") if isinstance(tags, str) else tags
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = str(value or "").strip().lstrip("#")
        if not tag or tag.casefold() in seen:
            continue
        seen.add(tag.casefold())
        normalized.append(tag)
    return normalized[:30]


def list_blog_profiles(
    con: duckdb.DuckDBPyConnection,
    *,
    active_only: bool = True,
) -> list[dict[str, object]]:
    where_clause = "WHERE is_active = TRUE" if active_only else ""
    rows = con.execute(
        f"""
        SELECT blog_profile_id, profile_name, platform, login_url, write_url,
               output_format, default_category, default_tags_json,
               is_default, is_active, created_at, updated_at
        FROM blog_profiles
        {where_clause}
        ORDER BY is_default DESC, updated_at DESC, profile_name
        """
    ).fetchall()
    columns = [item[0] for item in con.description]
    profiles: list[dict[str, object]] = []
    for row in rows:
        profile = dict(zip(columns, row, strict=True))
        platform = str(profile.get("platform") or "custom")
        profile["platform_label"] = get_platform_definition(platform)["label"]
        profile["output_format_label"] = OUTPUT_FORMAT_LABELS.get(
            str(profile.get("output_format") or "plain_text"),
            "일반 텍스트",
        )
        try:
            tags = json.loads(str(profile.get("default_tags_json") or "[]"))
        except json.JSONDecodeError:
            tags = []
        profile["default_tags"] = [str(tag) for tag in tags if str(tag).strip()]
        profiles.append(profile)
    return profiles


def get_blog_profile(
    con: duckdb.DuckDBPyConnection,
    blog_profile_id: str,
) -> dict[str, object] | None:
    profiles = [
        item
        for item in list_blog_profiles(con, active_only=False)
        if str(item["blog_profile_id"]) == str(blog_profile_id)
    ]
    return profiles[0] if profiles else None


def save_blog_profile(
    con: duckdb.DuckDBPyConnection,
    *,
    profile_name: str,
    platform: str,
    login_url: str,
    write_url: str,
    output_format: str,
    default_category: str = "",
    default_tags: list[str] | str | None = None,
    is_default: bool = False,
    blog_profile_id: str | None = None,
) -> str:
    normalized_name = str(profile_name or "").strip()
    if not normalized_name:
        raise ValueError("블로그 프로필 이름을 입력하세요.")
    if platform not in PLATFORM_DEFINITIONS:
        raise ValueError("지원하지 않는 블로그 플랫폼입니다.")
    if output_format not in OUTPUT_FORMAT_LABELS:
        raise ValueError("지원하지 않는 기본 출력 형식입니다.")
    normalized_login_url = _validate_url(
        login_url,
        field_label="로그인 페이지 주소",
        required=False,
    )
    normalized_write_url = _validate_url(
        write_url,
        field_label="글쓰기 페이지 주소",
        required=True,
    )
    current_id = str(blog_profile_id or f"blog_{uuid4().hex}")
    duplicate = con.execute(
        """
        SELECT blog_profile_id
        FROM blog_profiles
        WHERE lower(profile_name) = lower(?)
          AND blog_profile_id <> ?
          AND is_active = TRUE
        LIMIT 1
        """,
        [normalized_name, current_id],
    ).fetchone()
    if duplicate is not None:
        raise ValueError("같은 이름의 활성 블로그 프로필이 이미 있습니다.")

    now = datetime.now()
    if is_default:
        con.execute("UPDATE blog_profiles SET is_default = FALSE WHERE is_default = TRUE")
    con.execute(
        """
        INSERT INTO blog_profiles(
            blog_profile_id, profile_name, platform, login_url, write_url,
            output_format, default_category, default_tags_json,
            is_default, is_active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?)
        ON CONFLICT(blog_profile_id) DO UPDATE SET
            profile_name = EXCLUDED.profile_name,
            platform = EXCLUDED.platform,
            login_url = EXCLUDED.login_url,
            write_url = EXCLUDED.write_url,
            output_format = EXCLUDED.output_format,
            default_category = EXCLUDED.default_category,
            default_tags_json = EXCLUDED.default_tags_json,
            is_default = EXCLUDED.is_default,
            is_active = TRUE,
            updated_at = EXCLUDED.updated_at
        """,
        [
            current_id,
            normalized_name,
            platform,
            normalized_login_url,
            normalized_write_url,
            output_format,
            str(default_category or "").strip(),
            json.dumps(_normalize_tags(default_tags), ensure_ascii=False),
            bool(is_default),
            now,
            now,
        ],
    )
    return current_id


def archive_blog_profile(
    con: duckdb.DuckDBPyConnection,
    blog_profile_id: str,
) -> None:
    row = con.execute(
        "SELECT is_default FROM blog_profiles WHERE blog_profile_id = ?",
        [blog_profile_id],
    ).fetchone()
    if row is None:
        raise ValueError("블로그 프로필을 찾을 수 없습니다.")
    con.execute(
        """
        UPDATE blog_profiles
        SET is_active = FALSE, is_default = FALSE, updated_at = ?
        WHERE blog_profile_id = ?
        """,
        [datetime.now(), blog_profile_id],
    )
    if bool(row[0]):
        replacement = con.execute(
            """
            SELECT blog_profile_id
            FROM blog_profiles
            WHERE is_active = TRUE
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        if replacement is not None:
            con.execute(
                "UPDATE blog_profiles SET is_default = TRUE WHERE blog_profile_id = ?",
                [replacement[0]],
            )


def restore_blog_profile(
    con: duckdb.DuckDBPyConnection,
    blog_profile_id: str,
) -> None:
    row = con.execute(
        """
        SELECT profile_name, is_active
        FROM blog_profiles
        WHERE blog_profile_id = ?
        """,
        [blog_profile_id],
    ).fetchone()
    if row is None:
        raise ValueError("블로그 프로필을 찾을 수 없습니다.")
    if bool(row[1]):
        return
    duplicate = con.execute(
        """
        SELECT 1
        FROM blog_profiles
        WHERE lower(profile_name) = lower(?)
          AND is_active = TRUE
        LIMIT 1
        """,
        [row[0]],
    ).fetchone()
    if duplicate is not None:
        raise ValueError("같은 이름의 활성 블로그 프로필이 있어 복원할 수 없습니다.")
    has_default = bool(
        con.execute(
            "SELECT COUNT(*) FROM blog_profiles WHERE is_active = TRUE AND is_default = TRUE"
        ).fetchone()[0]
    )
    con.execute(
        """
        UPDATE blog_profiles
        SET is_active = TRUE, is_default = ?, updated_at = ?
        WHERE blog_profile_id = ?
        """,
        [not has_default, datetime.now(), blog_profile_id],
    )
