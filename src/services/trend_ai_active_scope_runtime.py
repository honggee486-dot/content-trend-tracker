from __future__ import annotations

from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable, Sequence

from src.database import get_setting

DEFAULT_ACTIVE_TREND_HOURS = 72


def active_trend_lookback_hours(con: Any) -> int:
    try:
        raw = get_setting(con, "trend_lookback_hours", str(DEFAULT_ACTIVE_TREND_HOURS))
        return max(6, min(int(raw or DEFAULT_ACTIVE_TREND_HOURS), 24 * 30))
    except Exception:
        return DEFAULT_ACTIVE_TREND_HOURS


def _trend_cluster_columns(con: Any) -> set[str]:
    try:
        return {
            str(row[1])
            for row in con.execute("PRAGMA table_info('trend_clusters')").fetchall()
        }
    except Exception:
        return set()


def active_trend_cluster_ids(con: Any) -> set[str]:
    """현재 분석 시간 범위의 군집 ID를 반환하고 구형 스키마는 기존 동작을 보존합니다."""
    columns = _trend_cluster_columns(con)
    if "cluster_id" not in columns:
        return set()

    threshold = datetime.now() - timedelta(hours=active_trend_lookback_hours(con))
    if "last_seen_at" in columns and "first_seen_at" in columns:
        query = "SELECT cluster_id FROM trend_clusters WHERE COALESCE(last_seen_at, first_seen_at) >= ?"
        params = [threshold]
    elif "last_seen_at" in columns:
        query = "SELECT cluster_id FROM trend_clusters WHERE last_seen_at >= ?"
        params = [threshold]
    elif "first_seen_at" in columns:
        query = "SELECT cluster_id FROM trend_clusters WHERE first_seen_at >= ?"
        params = [threshold]
    else:
        # 테스트/구형 DB처럼 시간 컬럼이 아예 없으면 필터 근거가 없으므로 기존 범위를 유지합니다.
        query = "SELECT cluster_id FROM trend_clusters"
        params = []

    try:
        rows = con.execute(query, params).fetchall()
    except Exception:
        return set()
    return {str(row[0] or "").strip() for row in rows if str(row[0] or "").strip()}


def filter_active_candidate_rows(
    con: Any,
    candidates: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    active_ids = active_trend_cluster_ids(con)
    return [
        candidate
        for candidate in candidates
        if str(candidate.get("cluster_id") or "").strip() in active_ids
    ]


def _build_evaluation_loader(original: Callable[..., tuple[list[dict[str, Any]], int]]):
    @wraps(original)
    def wrapped(con: Any, *args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        candidates, _skipped_sensitive = original(con, *args, **kwargs)
        active_ids = active_trend_cluster_ids(con)
        filtered = [
            candidate
            for candidate in candidates
            if str(candidate.get("cluster_id") or "").strip() in active_ids
        ]
        return filtered, max(0, len(active_ids) - len(filtered))

    setattr(wrapped, "_active_trend_scope", True)
    return wrapped


def _build_blog_loader(original: Callable[..., list[dict[str, Any]]]):
    @wraps(original)
    def wrapped(con: Any, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return filter_active_candidate_rows(con, original(con, *args, **kwargs))

    setattr(wrapped, "_active_trend_scope", True)
    return wrapped


def _build_evaluation_summary(original: Callable[..., dict[str, Any]]):
    @wraps(original)
    def wrapped(con: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = dict(original(con, *args, **kwargs))
        active_ids = active_trend_cluster_ids(con)
        if not active_ids:
            result["current_clusters"] = 0
            result["evaluated_clusters"] = 0
            return result
        placeholders = ", ".join("?" for _ in active_ids)
        try:
            evaluated = int(
                con.execute(
                    f"SELECT COUNT(*) FROM trend_cluster_ai_evaluations WHERE cluster_id IN ({placeholders})",
                    sorted(active_ids),
                ).fetchone()[0]
                or 0
            )
        except Exception:
            evaluated = 0
        result["current_clusters"] = len(active_ids)
        result["evaluated_clusters"] = evaluated
        return result

    setattr(wrapped, "_active_trend_scope", True)
    return wrapped


def install_trend_ai_active_scope_contract() -> None:
    """Flash-Lite 후처리가 오래된 trend_clusters 전체를 다시 API 전송하지 않게 합니다."""
    from src.services import trend_blog_ai_routing_service as blog_module
    from src.services import trend_candidate_ai_evaluation_service as evaluation_module

    current_eval_loader = evaluation_module._load_candidates
    if not getattr(current_eval_loader, "_active_trend_scope", False):
        evaluation_module._load_candidates = _build_evaluation_loader(current_eval_loader)

    current_blog_loader = blog_module._load_candidates
    if not getattr(current_blog_loader, "_active_trend_scope", False):
        blog_module._load_candidates = _build_blog_loader(current_blog_loader)

    current_summary = evaluation_module.get_candidate_ai_evaluation_summary
    if not getattr(current_summary, "_active_trend_scope", False):
        evaluation_module.get_candidate_ai_evaluation_summary = _build_evaluation_summary(current_summary)
