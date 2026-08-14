from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

import duckdb

from src.database import get_setting, set_setting
from src.services.blog_channel_strategy_service import (
    MANAGED_BLOG_CHANNELS,
    BlogChannelRecommendation,
    recommend_blog_channel,
)


PLATFORM_PREFIXES: dict[str, str] = {
    "blogger": "B",
    "tistory": "T",
    "naver_blog": "N",
}
DISPLAY_NAME_SETTING_PREFIX = "trend_blog_recommendation_display_name."
TISTORY_CHANNEL_KEY = "tistory"


def display_name_setting_key(channel_key: str) -> str:
    normalized = str(channel_key or "").strip()
    if not normalized:
        raise ValueError("추천 표시 이름을 저장할 채널 키가 없습니다.")
    return f"{DISPLAY_NAME_SETTING_PREFIX}{normalized}"


def _profile_name_for_channel(
    con: duckdb.DuckDBPyConnection,
    channel_key: str,
) -> str:
    normalized = str(channel_key or "").strip()
    if not normalized:
        return ""
    try:
        if normalized == TISTORY_CHANNEL_KEY:
            row = con.execute(
                """
                SELECT profile_name
                FROM blog_profiles
                WHERE platform = 'tistory'
                  AND is_active = TRUE
                ORDER BY is_default DESC, updated_at DESC, profile_name
                LIMIT 1
                """
            ).fetchone()
        else:
            row = con.execute(
                """
                SELECT p.profile_name
                FROM blog_profile_strategies s
                JOIN blog_profiles p ON p.blog_profile_id = s.blog_profile_id
                WHERE s.strategy_code = ?
                  AND p.is_active = TRUE
                LIMIT 1
                """,
                [normalized],
            ).fetchone()
    except Exception:
        return ""
    return str(row[0] or "").strip() if row is not None else ""


def get_recommendation_display_name(
    con: duckdb.DuckDBPyConnection,
    channel_key: str,
) -> str:
    override = str(
        get_setting(con, display_name_setting_key(channel_key), "") or ""
    ).strip()
    if override:
        return override
    return _profile_name_for_channel(con, channel_key)


def set_recommendation_display_name(
    con: duckdb.DuckDBPyConnection,
    channel_key: str,
    display_name: object,
) -> None:
    set_setting(
        con,
        display_name_setting_key(channel_key),
        str(display_name or "").strip(),
    )


def format_recommended_blog_label(platform: object, display_name: object = "") -> str:
    prefix = PLATFORM_PREFIXES.get(str(platform or "").strip(), "")
    name = str(display_name or "").strip()
    if not prefix:
        return name
    return f"{prefix}:{name}"


def managed_strategy_definitions_for_trend_list() -> list[dict[str, Any]]:
    """Return read-only managed routing definitions for the trend candidate list.

    The trend list must not create or migrate blog profiles just to show a hint.
    Current managed routing remains Blogger 3 + Naver 1. Tistory stays a manual
    publish profile until a concrete routing role is defined.
    """
    strategies: list[dict[str, Any]] = []
    for raw in MANAGED_BLOG_CHANNELS:
        item = dict(raw)
        # A trend title with no clear specialist term is safer on the current-issues
        # Blogger than on the local-experience Naver profile. Positive term scores
        # still win before this tie-breaker is considered.
        item["is_default"] = str(item.get("strategy_code") or "") == "blogger_current"
        strategies.append(item)
    return strategies


def _load_ai_profile_contexts(
    con: duckdb.DuckDBPyConnection,
    cluster_ids: Sequence[str],
) -> dict[str, dict[str, str]]:
    normalized_ids = list(dict.fromkeys(str(value or "").strip() for value in cluster_ids))
    normalized_ids = [value for value in normalized_ids if value]
    if not normalized_ids:
        return {}

    try:
        placeholders = ", ".join("?" for _ in normalized_ids)
        rows = con.execute(
            f"""
            SELECT cluster_id, display_title, summary, content_plan_json
            FROM trend_cluster_ai_profiles
            WHERE cluster_id IN ({placeholders})
            """,
            normalized_ids,
        ).fetchall()
    except Exception:
        return {}

    result: dict[str, dict[str, str]] = {}
    for cluster_id, display_title, summary, content_plan_json in rows:
        try:
            content_plan = json.loads(str(content_plan_json or "{}"))
        except (TypeError, json.JSONDecodeError):
            content_plan = {}
        category = (
            str(content_plan.get("category") or "").strip()
            if isinstance(content_plan, dict)
            else ""
        )
        result[str(cluster_id)] = {
            "display_title": str(display_title or "").strip(),
            "summary": str(summary or "").strip(),
            "category": category,
        }
    return result


def _load_saved_ai_route_codes(
    con: duckdb.DuckDBPyConnection,
    cluster_ids: Sequence[str],
) -> dict[str, str]:
    normalized_ids = list(dict.fromkeys(str(value or "").strip() for value in cluster_ids))
    normalized_ids = [value for value in normalized_ids if value]
    if not normalized_ids:
        return {}
    try:
        placeholders = ", ".join("?" for _ in normalized_ids)
        rows = con.execute(
            f"""
            SELECT cluster_id, strategy_code
            FROM trend_blog_ai_routes
            WHERE cluster_id IN ({placeholders})
            """,
            normalized_ids,
        ).fetchall()
    except Exception:
        # 기존 DB나 API 분류 전 화면에서는 예전 로컬 추천을 그대로 유지합니다.
        return {}
    return {
        str(cluster_id): str(strategy_code or "").strip()
        for cluster_id, strategy_code in rows
        if str(cluster_id or "").strip()
    }


def _recommendation_input(
    row: Mapping[str, Any],
    ai_context: Mapping[str, str] | None,
) -> dict[str, Any]:
    context = ai_context or {}
    return {
        "title": str(context.get("display_title") or row.get("주제") or "").strip(),
        "category": str(context.get("category") or "").strip(),
        "summary": str(context.get("summary") or "").strip(),
        "tags": [],
        "body_markdown": "",
    }


def build_trend_blog_recommendation_labels(
    con: duckdb.DuckDBPyConnection,
    rows: Sequence[Mapping[str, Any]],
    *,
    strategies: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, str]:
    """Build a display-only blog hint for visible trend candidates.

    A saved Gemini semantic route is preferred when available. The display path never
    calls an external API; before the first API routing run or after a failed batch it
    falls back to the existing local keyword/category scorer. A saved recommendation
    name still overrides the current blog profile name.
    """
    visible_rows = [dict(row) for row in rows]
    if not visible_rows:
        return {}
    effective_strategies = list(
        strategies if strategies is not None else managed_strategy_definitions_for_trend_list()
    )
    if not effective_strategies:
        return {}

    cluster_ids = [str(row.get("cluster_id") or "") for row in visible_rows]
    ai_contexts = _load_ai_profile_contexts(con, cluster_ids)
    saved_ai_routes = _load_saved_ai_route_codes(con, cluster_ids)
    strategy_by_code = {
        str(item.get("strategy_code") or ""): item for item in effective_strategies
    }
    display_names = {
        code: get_recommendation_display_name(con, code)
        for code in strategy_by_code
        if code
    }

    result: dict[str, str] = {}
    for row in visible_rows:
        cluster_id = str(row.get("cluster_id") or "").strip()
        if not cluster_id:
            continue

        saved_strategy_code = saved_ai_routes.get(cluster_id, "")
        if saved_strategy_code in strategy_by_code:
            strategy = strategy_by_code[saved_strategy_code]
            platform = str(strategy.get("platform") or "")
            result[cluster_id] = format_recommended_blog_label(
                platform,
                display_names.get(saved_strategy_code, ""),
            )
            continue

        recommendation: BlogChannelRecommendation | None = recommend_blog_channel(
            _recommendation_input(row, ai_contexts.get(cluster_id)),
            effective_strategies,
        )
        if recommendation is None:
            continue
        strategy = strategy_by_code.get(recommendation.strategy_code, {})
        platform = str(strategy.get("platform") or "")
        result[cluster_id] = format_recommended_blog_label(
            platform,
            display_names.get(recommendation.strategy_code, ""),
        )
    return result
