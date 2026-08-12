"""NAVER·Daum 최근 분석 시간 범위 전체 적용 여부를 읽기 전용으로 진단합니다."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import duckdb

from src.database import get_setting
from src.services.portal_full_window_analysis_runtime import (
    PORTAL_FULL_WINDOW_SETTING_VALUE,
)
from src.services.trend_discovery_service import (
    DEFAULT_ANALYSIS_SOURCE_LIMITS,
    prepare_trend_ranking_rebuild,
)


_PORTAL_GROUPS = {
    "naver": ("NAVER", ("naver_news", "naver_blog")),
    "daum": ("Daum", ("daum_web", "daum_cafe")),
}
_REQUIRED_TABLES = (
    "app_settings",
    "source_items",
    "trend_clusters",
    "trend_cluster_items",
    "trend_cluster_processing",
)
_DEFAULT_LOOKBACK_HOURS = 72


def _table_names(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}


def _setting_int(
    con: duckdb.DuckDBPyConnection,
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    raw = get_setting(con, key, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        value = int(default)
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value


def _effective_portal_limits() -> dict[str, int]:
    return {
        group_name: PORTAL_FULL_WINDOW_SETTING_VALUE
        for group_name in _PORTAL_GROUPS
    }


def _stored_portal_limits(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {
        group_name: _setting_int(
            con,
            f"trend_analysis_{group_name}_limit",
            PORTAL_FULL_WINDOW_SETTING_VALUE,
            minimum=0,
            maximum=20_000,
        )
        for group_name in _PORTAL_GROUPS
    }


def _unavailable(
    *,
    lookback_hours: int,
    stored_limits: dict[str, int] | None = None,
    missing_tables: list[str] | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "analysis_mode": "portal_full_window",
        "lookback_hours": int(lookback_hours),
        "limits": _effective_portal_limits(),
        "stored_legacy_limits": dict(stored_limits or {}),
        "groups": {},
        "outside_limit_unclustered_items": 0,
        "selected_pending_items": 0,
        "missing_tables": list(missing_tables or []),
        "error_type": type(error).__name__ if error is not None else "",
        "error_message": str(error)[:500] if error is not None else "",
    }


def build_source_analysis_limit_diagnostic(
    con: duckdb.DuckDBPyConnection,
) -> dict[str, Any]:
    """실제 순위 준비 로직으로 NAVER·Daum 전체 기간 입력 계약을 검증합니다.

    외부 API를 호출하거나 설정·원문·군집 상태를 변경하지 않습니다. 과거 DB에 남은
    500개 이상의 포털 상한 값은 호환 정보로만 표시하며 실제 선택에는 적용하지 않습니다.
    ``outside_limit_*`` 필드는 기존 JSON 소비자 호환을 위해 유지하고 정상 계약에서는 0입니다.
    """
    tables = _table_names(con)
    missing_tables = [name for name in _REQUIRED_TABLES if name not in tables]
    if missing_tables:
        return _unavailable(
            lookback_hours=_DEFAULT_LOOKBACK_HOURS,
            missing_tables=missing_tables,
        )

    lookback_hours = _setting_int(
        con,
        "trend_lookback_hours",
        _DEFAULT_LOOKBACK_HOURS,
        minimum=6,
    )
    stored_limits = _stored_portal_limits(con)

    source_limits = dict(DEFAULT_ANALYSIS_SOURCE_LIMITS)
    for group_name in source_limits:
        if group_name in _PORTAL_GROUPS:
            # 기존 500~20,000 설정도 런타임 호환 계약에서 전체 기간으로 해석됩니다.
            source_limits[group_name] = int(stored_limits[group_name])
            continue
        source_limits[group_name] = _setting_int(
            con,
            f"trend_analysis_{group_name}_limit",
            int(DEFAULT_ANALYSIS_SOURCE_LIMITS[group_name]),
            minimum=10,
            maximum=20_000,
        )

    try:
        preparation = prepare_trend_ranking_rebuild(
            con,
            lookback_hours=lookback_hours,
            source_limits=source_limits,
        )
    except Exception as exc:
        return _unavailable(
            lookback_hours=lookback_hours,
            stored_limits=stored_limits,
            error=exc,
        )

    selected_by_group: dict[str, set[str]] = {
        group_name: set() for group_name in _PORTAL_GROUPS
    }
    source_type_to_group = {
        source_type: group_name
        for group_name, (_label, source_types) in _PORTAL_GROUPS.items()
        for source_type in source_types
    }
    for item in preparation.items:
        group_name = source_type_to_group.get(str(item.get("source_type") or ""))
        source_id = str(item.get("source_item_id") or "")
        if group_name and source_id:
            selected_by_group[group_name].add(source_id)

    pending_ids = {
        str(source_id or "")
        for source_id, _attempt_count in preparation.processing_attempts
        if str(source_id or "")
    }

    cutoff = datetime.now() - timedelta(hours=lookback_hours)
    cursor = con.execute(
        """
        SELECT s.source_item_id, s.source_type,
               EXISTS (
                   SELECT 1
                   FROM trend_cluster_items tci
                   WHERE tci.source_item_id = s.source_item_id
               ) AS is_clustered
        FROM source_items s
        WHERE s.source_type IN ('naver_news', 'naver_blog', 'daum_web', 'daum_cafe')
          AND COALESCE(s.published_at, s.observed_at, s.imported_at) >= ?
        """,
        [cutoff],
    )
    columns = [str(item[0]) for item in cursor.description]
    rows = [dict(zip(columns, values)) for values in cursor.fetchall()]

    groups: dict[str, dict[str, Any]] = {}
    total_outside_unclustered = 0
    total_selected_pending = 0
    for group_name, (label, source_types) in _PORTAL_GROUPS.items():
        group_rows = [
            row for row in rows if str(row.get("source_type") or "") in source_types
        ]
        recent_ids = {
            str(row.get("source_item_id") or "")
            for row in group_rows
            if str(row.get("source_item_id") or "")
        }
        unclustered_ids = {
            str(row.get("source_item_id") or "")
            for row in group_rows
            if str(row.get("source_item_id") or "") and not bool(row.get("is_clustered"))
        }
        selected_ids = selected_by_group[group_name] & recent_ids
        outside_ids = recent_ids - selected_ids
        selected_unclustered = unclustered_ids & selected_ids
        outside_unclustered = unclustered_ids & outside_ids
        selected_pending = selected_ids & pending_ids
        recent_unclustered = len(unclustered_ids)
        outside_unclustered_count = len(outside_unclustered)
        outside_rate = (
            round(outside_unclustered_count / recent_unclustered * 100, 1)
            if recent_unclustered
            else 0.0
        )
        groups[group_name] = {
            "label": label,
            "analysis_mode": "full_window",
            "configured_limit": PORTAL_FULL_WINDOW_SETTING_VALUE,
            "stored_legacy_limit": int(stored_limits[group_name]),
            "effective_limit": len(recent_ids),
            "recent_items": len(recent_ids),
            "selected_items": len(selected_ids),
            "outside_limit_items": len(outside_ids),
            "recent_unclustered_items": recent_unclustered,
            "selected_unclustered_items": len(selected_unclustered),
            "outside_limit_unclustered_items": outside_unclustered_count,
            "outside_limit_unclustered_percent": outside_rate,
            "selected_pending_items": len(selected_pending),
            "limit_reached": bool(outside_ids),
        }
        total_outside_unclustered += outside_unclustered_count
        total_selected_pending += len(selected_pending)

    return {
        "available": True,
        "analysis_mode": "portal_full_window",
        "lookback_hours": lookback_hours,
        "limits": _effective_portal_limits(),
        "stored_legacy_limits": stored_limits,
        "groups": groups,
        "outside_limit_unclustered_items": total_outside_unclustered,
        "selected_pending_items": total_selected_pending,
        "missing_tables": [],
        "error_type": "",
        "error_message": "",
    }
