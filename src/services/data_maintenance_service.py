"""로컬 DuckDB의 오래된 수집 데이터를 안전하게 정리하고 상태를 요약합니다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from src.database import get_setting, set_setting
from src.services.collection_history_service import cleanup_collection_history


@dataclass(frozen=True)
class DatabaseStats:
    database_size_bytes: int
    source_items_total: int
    source_items_recent: int
    source_items_linked: int
    source_items_old_unlinked: int
    sync_runs_total: int
    last_cleanup_at: str


@dataclass(frozen=True)
class CleanupResult:
    source_items_deleted: int
    cluster_links_deleted: int
    empty_clusters_deleted: int
    sync_runs_deleted: int
    collection_runs_deleted: int
    api_usage_rows_deleted: int
    checkpoint_completed: bool
    finished_at: datetime

    @property
    def total_rows_deleted(self) -> int:
        return (
            self.source_items_deleted
            + self.cluster_links_deleted
            + self.empty_clusters_deleted
            + self.sync_runs_deleted
            + self.collection_runs_deleted
            + self.api_usage_rows_deleted
        )


def _event_time_sql(alias: str = "s") -> str:
    return f"COALESCE({alias}.published_at, {alias}.observed_at, {alias}.imported_at)"


def _subtract_months_start(value: datetime, months: int) -> datetime:
    total = value.year * 12 + (value.month - 1) - max(0, int(months))
    year, month_index = divmod(total, 12)
    return datetime(year, month_index + 1, 1)


def get_database_stats(
    con: duckdb.DuckDBPyConnection,
    *,
    db_path: str | Path,
    retention_days: int = 30,
    lookback_hours: int = 72,
    now: datetime | None = None,
) -> DatabaseStats:
    current = now or datetime.now()
    old_cutoff = current - timedelta(days=max(1, int(retention_days)))
    recent_cutoff = current - timedelta(hours=max(1, int(lookback_hours)))

    source_items_total = int(con.execute("SELECT COUNT(*) FROM source_items").fetchone()[0] or 0)
    source_items_recent = int(
        con.execute(
            f"SELECT COUNT(*) FROM source_items s WHERE {_event_time_sql('s')} >= ?",
            [recent_cutoff],
        ).fetchone()[0]
        or 0
    )
    source_items_linked = int(
        con.execute("SELECT COUNT(DISTINCT source_item_id) FROM topic_source_links").fetchone()[0]
        or 0
    )
    source_items_old_unlinked = int(
        con.execute(
            f"""
            SELECT COUNT(*)
            FROM source_items s
            WHERE {_event_time_sql('s')} < ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM topic_source_links l
                  WHERE l.source_item_id = s.source_item_id
              )
            """,
            [old_cutoff],
        ).fetchone()[0]
        or 0
    )
    sync_runs_total = int(con.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0] or 0)
    last_cleanup_at = get_setting(con, "data_cleanup_last_at", "기록 없음") or "기록 없음"

    path = Path(db_path)
    database_size_bytes = path.stat().st_size if path.is_file() else 0
    return DatabaseStats(
        database_size_bytes=database_size_bytes,
        source_items_total=source_items_total,
        source_items_recent=source_items_recent,
        source_items_linked=source_items_linked,
        source_items_old_unlinked=source_items_old_unlinked,
        sync_runs_total=sync_runs_total,
        last_cleanup_at=last_cleanup_at,
    )


def cleanup_old_data(
    con: duckdb.DuckDBPyConnection,
    *,
    source_retention_days: int = 30,
    sync_run_retention_days: int = 90,
    api_usage_retention_months: int = 13,
    now: datetime | None = None,
    checkpoint: bool = True,
) -> CleanupResult:
    """사용자가 선택한 주제에 연결되지 않은 오래된 원본만 삭제합니다."""
    current = now or datetime.now()
    source_cutoff = current - timedelta(days=max(1, int(source_retention_days)))
    sync_cutoff = current - timedelta(days=max(1, int(sync_run_retention_days)))
    # 현재 달을 포함해 설정한 개월 수를 보관합니다.
    api_cutoff = _subtract_months_start(current, max(1, int(api_usage_retention_months)) - 1)
    api_day_key = api_cutoff.strftime("%Y-%m-%d")
    api_month_key = api_cutoff.strftime("%Y-%m")

    candidate_sql = f"""
        SELECT s.source_item_id
        FROM source_items s
        WHERE {_event_time_sql('s')} < ?
          AND NOT EXISTS (
              SELECT 1
              FROM topic_source_links l
              WHERE l.source_item_id = s.source_item_id
          )
    """
    source_items_deleted = int(
        con.execute(f"SELECT COUNT(*) FROM ({candidate_sql}) candidates", [source_cutoff]).fetchone()[0]
        or 0
    )
    cluster_links_deleted = int(
        con.execute(
            f"""
            SELECT COUNT(*)
            FROM trend_cluster_items tci
            WHERE tci.source_item_id IN ({candidate_sql})
            """,
            [source_cutoff],
        ).fetchone()[0]
        or 0
    )
    empty_clusters_before = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM trend_clusters tc
            WHERE NOT EXISTS (
                SELECT 1 FROM trend_cluster_items tci WHERE tci.cluster_id = tc.cluster_id
            )
            """
        ).fetchone()[0]
        or 0
    )
    sync_runs_deleted = int(
        con.execute("SELECT COUNT(*) FROM sync_runs WHERE started_at < ?", [sync_cutoff]).fetchone()[0]
        or 0
    )
    collection_runs_deleted = int(
        con.execute(
            "SELECT COUNT(*) FROM collection_runs WHERE started_at < ?",
            [sync_cutoff],
        ).fetchone()[0]
        or 0
    )
    api_usage_rows_deleted = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM api_usage_counters
            WHERE (period_type = 'day' AND period_key < ?)
               OR (period_type = 'month' AND period_key < ?)
            """,
            [api_day_key, api_month_key],
        ).fetchone()[0]
        or 0
    )

    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            f"""
            DELETE FROM trend_cluster_items
            WHERE source_item_id IN ({candidate_sql})
            """,
            [source_cutoff],
        )
        con.execute(
            f"DELETE FROM source_items WHERE source_item_id IN ({candidate_sql})",
            [source_cutoff],
        )
        con.execute(
            """
            DELETE FROM trend_clusters tc
            WHERE NOT EXISTS (
                SELECT 1 FROM trend_cluster_items tci WHERE tci.cluster_id = tc.cluster_id
            )
            """
        )
        con.execute("DELETE FROM sync_runs WHERE started_at < ?", [sync_cutoff])
        cleanup_collection_history(
            con,
            retention_days=sync_run_retention_days,
            now=current,
        )
        con.execute(
            """
            DELETE FROM api_usage_counters
            WHERE (period_type = 'day' AND period_key < ?)
               OR (period_type = 'month' AND period_key < ?)
            """,
            [api_day_key, api_month_key],
        )
        # 원본 수가 바뀌면 다음 순위 계산에서 캐시를 재사용하지 않습니다.
        if source_items_deleted:
            con.execute(
                "DELETE FROM app_settings WHERE setting_key = 'trend_ranking_signature'"
            )
        set_setting(con, "data_cleanup_last_date", current.strftime("%Y-%m-%d"))
        set_setting(con, "data_cleanup_last_at", current.strftime("%Y-%m-%d %H:%M:%S"))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    checkpoint_completed = False
    if checkpoint:
        try:
            con.execute("CHECKPOINT")
            checkpoint_completed = True
        except Exception:
            checkpoint_completed = False

    return CleanupResult(
        source_items_deleted=source_items_deleted,
        cluster_links_deleted=cluster_links_deleted,
        empty_clusters_deleted=empty_clusters_before,
        sync_runs_deleted=sync_runs_deleted,
        collection_runs_deleted=collection_runs_deleted,
        api_usage_rows_deleted=api_usage_rows_deleted,
        checkpoint_completed=checkpoint_completed,
        finished_at=current,
    )


def run_automatic_cleanup_if_due(
    con: duckdb.DuckDBPyConnection,
    *,
    enabled: bool,
    source_retention_days: int,
    sync_run_retention_days: int,
    api_usage_retention_months: int,
    now: datetime | None = None,
) -> CleanupResult | None:
    if not enabled:
        return None
    current = now or datetime.now()
    today = current.strftime("%Y-%m-%d")
    if get_setting(con, "data_cleanup_last_date", "") == today:
        return None
    return cleanup_old_data(
        con,
        source_retention_days=source_retention_days,
        sync_run_retention_days=sync_run_retention_days,
        api_usage_retention_months=api_usage_retention_months,
        now=current,
        checkpoint=False,
    )
