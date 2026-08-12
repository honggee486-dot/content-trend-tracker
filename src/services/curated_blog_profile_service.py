from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import duckdb

from src.services.blog_channel_strategy_service import (
    install_default_blog_channels,
    list_managed_blog_channel_strategies,
)
from src.services.blog_profile_service import list_blog_profiles


CURATED_STRATEGY_ORDER = (
    "blogger_life",
    "blogger_tech",
    "blogger_current",
    "naver_local",
)
PRIMARY_TISTORY_PROFILE_ID = "blog_tistory_default"
_GENERIC_WRITE_URLS = {
    "naver_blog": {
        "https://blog.naver.com",
        "https://blog.naver.com/",
    },
    "tistory": {
        "https://www.tistory.com",
        "https://www.tistory.com/",
    },
}


@dataclass(frozen=True)
class CuratedBlogProfileSyncResult:
    profiles: tuple[dict[str, object], ...]
    archived_profile_ids: tuple[str, ...]
    restored_profile_ids: tuple[str, ...]
    primary_tistory_profile_id: str
    migrated_naver_from_profile_id: str

    @property
    def profile_ids(self) -> tuple[str, ...]:
        return tuple(str(item["blog_profile_id"]) for item in self.profiles)


def _normalized_url(value: object) -> str:
    return str(value or "").strip().rstrip("/").casefold()


def _has_specific_write_url(profile: dict[str, object]) -> bool:
    platform = str(profile.get("platform") or "")
    raw_url = str(profile.get("write_url") or "").strip()
    if not raw_url:
        return False
    generic = {
        _normalized_url(item)
        for item in _GENERIC_WRITE_URLS.get(platform, set())
    }
    return _normalized_url(raw_url) not in generic


def _pick_primary_tistory_profile(
    profiles: list[dict[str, object]],
) -> dict[str, object] | None:
    candidates = [
        profile
        for profile in profiles
        if str(profile.get("platform") or "") == "tistory"
    ]
    if not candidates:
        return None

    def rank(profile: dict[str, object]) -> tuple[int, int, int, int, str]:
        return (
            int(_has_specific_write_url(profile)),
            int(bool(profile.get("is_active"))),
            int(bool(profile.get("is_default"))),
            int(str(profile.get("blog_profile_id") or "") == PRIMARY_TISTORY_PROFILE_ID),
            str(profile.get("updated_at") or ""),
        )

    return max(candidates, key=rank)


def _create_primary_tistory_profile(
    con: duckdb.DuckDBPyConnection,
    *,
    now: datetime,
) -> str:
    con.execute(
        """
        INSERT INTO blog_profiles(
            blog_profile_id, profile_name, platform, login_url, write_url,
            output_format, default_category, default_tags_json,
            is_default, is_active, created_at, updated_at
        ) VALUES (?, ?, 'tistory', ?, ?, 'markdown', '', '[]',
                  FALSE, TRUE, ?, ?)
        """,
        [
            PRIMARY_TISTORY_PROFILE_ID,
            "티스토리",
            "https://www.tistory.com/auth/login",
            "https://www.tistory.com/",
            now,
            now,
        ],
    )
    return PRIMARY_TISTORY_PROFILE_ID


def _copy_specific_naver_connection(
    con: duckdb.DuckDBPyConnection,
    *,
    profiles: list[dict[str, object]],
    target_profile_id: str,
    now: datetime,
) -> str:
    target = next(
        (
            profile
            for profile in profiles
            if str(profile.get("blog_profile_id") or "") == target_profile_id
        ),
        None,
    )
    if target is None or _has_specific_write_url(target):
        return ""

    candidates = [
        profile
        for profile in profiles
        if str(profile.get("platform") or "") == "naver_blog"
        and str(profile.get("blog_profile_id") or "") != target_profile_id
        and _has_specific_write_url(profile)
    ]
    if not candidates:
        return ""
    source = max(
        candidates,
        key=lambda profile: (
            int(bool(profile.get("is_active"))),
            int(bool(profile.get("is_default"))),
            str(profile.get("updated_at") or ""),
        ),
    )
    con.execute(
        """
        UPDATE blog_profiles
        SET login_url = ?, write_url = ?, output_format = ?,
            default_category = ?, default_tags_json = ?, updated_at = ?
        WHERE blog_profile_id = ?
        """,
        [
            str(source.get("login_url") or ""),
            str(source.get("write_url") or ""),
            str(source.get("output_format") or "plain_text"),
            str(source.get("default_category") or ""),
            str(source.get("default_tags_json") or "[]"),
            now,
            target_profile_id,
        ],
    )
    return str(source.get("blog_profile_id") or "")


def synchronize_curated_blog_profiles(
    con: duckdb.DuckDBPyConnection,
) -> CuratedBlogProfileSyncResult:
    """Keep exactly Blogger 3, Naver 1 and Tistory 1 active.

    Extra rows are archived instead of deleted so historical assignments and
    publish records keep their original profile references.
    """
    now = datetime.now()
    archived_profile_ids: list[str] = []
    restored_profile_ids: list[str] = []
    migrated_naver_from_profile_id = ""

    con.execute("BEGIN TRANSACTION")
    try:
        install_default_blog_channels(con)
        profiles = list_blog_profiles(con, active_only=False)
        strategies = list_managed_blog_channel_strategies(
            con,
            active_only=False,
        )
        profile_id_by_strategy = {
            str(item.get("strategy_code") or ""): str(
                item.get("blog_profile_id") or ""
            )
            for item in strategies
        }
        missing_codes = [
            code
            for code in CURATED_STRATEGY_ORDER
            if not profile_id_by_strategy.get(code)
        ]
        if missing_codes:
            raise ValueError(
                "기본 발행 채널 프로필을 준비하지 못했습니다: "
                + ", ".join(missing_codes)
            )

        primary_tistory = _pick_primary_tistory_profile(profiles)
        if primary_tistory is None:
            primary_tistory_profile_id = _create_primary_tistory_profile(
                con,
                now=now,
            )
            profiles = list_blog_profiles(con, active_only=False)
        else:
            primary_tistory_profile_id = str(
                primary_tistory.get("blog_profile_id") or ""
            )

        naver_profile_id = profile_id_by_strategy["naver_local"]
        migrated_naver_from_profile_id = _copy_specific_naver_connection(
            con,
            profiles=profiles,
            target_profile_id=naver_profile_id,
            now=now,
        )

        ordered_profile_ids = [
            profile_id_by_strategy[code]
            for code in CURATED_STRATEGY_ORDER
        ]
        ordered_profile_ids.append(primary_tistory_profile_id)
        ordered_profile_ids = list(dict.fromkeys(ordered_profile_ids))
        if len(ordered_profile_ids) != 5:
            raise ValueError("고정 블로그 프로필 5개 구성을 만들 수 없습니다.")

        placeholders = ", ".join("?" for _ in ordered_profile_ids)
        restored_profile_ids = [
            str(row[0])
            for row in con.execute(
                f"""
                SELECT blog_profile_id
                FROM blog_profiles
                WHERE blog_profile_id IN ({placeholders})
                  AND is_active = FALSE
                ORDER BY blog_profile_id
                """,
                ordered_profile_ids,
            ).fetchall()
        ]
        if restored_profile_ids:
            restore_placeholders = ", ".join(
                "?" for _ in restored_profile_ids
            )
            con.execute(
                f"""
                UPDATE blog_profiles
                SET is_active = TRUE, updated_at = ?
                WHERE blog_profile_id IN ({restore_placeholders})
                """,
                [now, *restored_profile_ids],
            )

        archived_profile_ids = [
            str(row[0])
            for row in con.execute(
                f"""
                SELECT blog_profile_id
                FROM blog_profiles
                WHERE is_active = TRUE
                  AND blog_profile_id NOT IN ({placeholders})
                ORDER BY blog_profile_id
                """,
                ordered_profile_ids,
            ).fetchall()
        ]
        if archived_profile_ids:
            archive_placeholders = ", ".join(
                "?" for _ in archived_profile_ids
            )
            con.execute(
                f"""
                UPDATE blog_profiles
                SET is_active = FALSE, is_default = FALSE, updated_at = ?
                WHERE blog_profile_id IN ({archive_placeholders})
                """,
                [now, *archived_profile_ids],
            )

        default_rows = con.execute(
            f"""
            SELECT blog_profile_id
            FROM blog_profiles
            WHERE blog_profile_id IN ({placeholders})
              AND is_active = TRUE
              AND is_default = TRUE
            """,
            ordered_profile_ids,
        ).fetchall()
        default_ids = [str(row[0]) for row in default_rows]
        if len(default_ids) == 1:
            default_profile_id = default_ids[0]
        else:
            default_profile_id = naver_profile_id
            con.execute(
                f"""
                UPDATE blog_profiles
                SET is_default = CASE WHEN blog_profile_id = ? THEN TRUE ELSE FALSE END,
                    updated_at = ?
                WHERE blog_profile_id IN ({placeholders})
                """,
                [default_profile_id, now, *ordered_profile_ids],
            )

        active_count = int(
            con.execute(
                f"""
                SELECT COUNT(*)
                FROM blog_profiles
                WHERE is_active = TRUE
                  AND blog_profile_id IN ({placeholders})
                """,
                ordered_profile_ids,
            ).fetchone()[0]
        )
        if active_count != 5:
            raise ValueError("활성 블로그 프로필 5개 구성을 확인하지 못했습니다.")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    active_profiles = {
        str(profile["blog_profile_id"]): profile
        for profile in list_blog_profiles(con)
    }
    curated_profiles = tuple(
        active_profiles[profile_id]
        for profile_id in ordered_profile_ids
        if profile_id in active_profiles
    )
    if len(curated_profiles) != 5:
        raise ValueError("정리된 블로그 프로필 목록을 불러오지 못했습니다.")

    return CuratedBlogProfileSyncResult(
        profiles=curated_profiles,
        archived_profile_ids=tuple(archived_profile_ids),
        restored_profile_ids=tuple(restored_profile_ids),
        primary_tistory_profile_id=primary_tistory_profile_id,
        migrated_naver_from_profile_id=migrated_naver_from_profile_id,
    )
