from datetime import datetime, timedelta
from pathlib import Path

from src.database import connect_database, init_database
from src.services.data_maintenance_service import (
    cleanup_old_data,
    get_database_stats,
    run_automatic_cleanup_if_due,
)
from src.services.topic_service import add_manual_topic, upsert_source_signal


def _signal(external_id: str, title: str, observed_at: datetime) -> dict:
    return {
        "source_type": "naver_news",
        "external_id": external_id,
        "title": title,
        "source_name": "테스트뉴스",
        "source_url": f"https://example.com/{external_id}",
        "published_at": observed_at,
        "observed_at": observed_at,
        "metadata": {},
    }


def test_cleanup_deletes_only_old_unlinked_source_items(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    old_time = now - timedelta(days=40)
    recent_time = now - timedelta(days=2)

    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="보관할 주제")
        upsert_source_signal(con, _signal("old-linked", "보관할 주제", old_time))
        upsert_source_signal(
            con,
            _signal("old-unlinked", "삭제할 오래된 자료", old_time),
            create_topic=False,
        )
        upsert_source_signal(
            con,
            _signal("recent-unlinked", "최근 자료", recent_time),
            create_topic=False,
        )
        linked_id = con.execute(
            "SELECT source_item_id FROM source_items WHERE external_id = 'old-linked'"
        ).fetchone()[0]
        con.execute(
            """
            INSERT OR IGNORE INTO topic_source_links(
                topic_id, source_item_id, match_type, match_score, linked_at
            ) VALUES (?, ?, 'manual', 1.0, ?)
            """,
            [topic_id, linked_id, now],
        )

        result = cleanup_old_data(
            con,
            source_retention_days=30,
            sync_run_retention_days=90,
            api_usage_retention_months=13,
            now=now,
            checkpoint=False,
        )
        remaining = {
            row[0]
            for row in con.execute("SELECT external_id FROM source_items").fetchall()
        }

    assert result.source_items_deleted == 1
    assert remaining == {"old-linked", "recent-unlinked"}


def test_cleanup_trims_old_runs_and_usage_but_keeps_recent_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    with connect_database(db_path) as con:
        con.execute(
            """
            INSERT INTO sync_runs VALUES
            ('old', 'naver', ?, ?, 'success', 0, 0, 0, NULL),
            ('recent', 'naver', ?, ?, 'success', 0, 0, 0, NULL)
            """,
            [now - timedelta(days=100), now - timedelta(days=100), now, now],
        )
        con.execute(
            """
            INSERT INTO api_usage_counters VALUES
            ('naver', 'search_api', 'day', '2025-01-01', 1, ?),
            ('naver', 'search_api', 'month', '2025-01', 1, ?),
            ('naver', 'search_api', 'day', '2026-07-15', 1, ?),
            ('naver', 'search_api', 'month', '2026-07', 1, ?)
            """,
            [now, now, now, now],
        )

        result = cleanup_old_data(
            con,
            source_retention_days=30,
            sync_run_retention_days=90,
            api_usage_retention_months=13,
            now=now,
            checkpoint=False,
        )
        run_ids = {row[0] for row in con.execute("SELECT sync_run_id FROM sync_runs").fetchall()}
        usage_keys = {
            (row[0], row[1])
            for row in con.execute(
                "SELECT period_type, period_key FROM api_usage_counters"
            ).fetchall()
        }

    assert result.sync_runs_deleted == 1
    assert result.api_usage_rows_deleted == 2
    assert run_ids == {"recent"}
    assert usage_keys == {("day", "2026-07-15"), ("month", "2026-07")}


def test_automatic_cleanup_runs_only_once_per_day(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    with connect_database(db_path) as con:
        first = run_automatic_cleanup_if_due(
            con,
            enabled=True,
            source_retention_days=30,
            sync_run_retention_days=90,
            api_usage_retention_months=13,
            now=now,
        )
        second = run_automatic_cleanup_if_due(
            con,
            enabled=True,
            source_retention_days=30,
            sync_run_retention_days=90,
            api_usage_retention_months=13,
            now=now + timedelta(hours=2),
        )

    assert first is not None
    assert second is None


def test_database_stats_report_old_unlinked_count(tmp_path: Path) -> None:
    db_path = tmp_path / "main.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    with connect_database(db_path) as con:
        upsert_source_signal(
            con,
            _signal("old", "오래된 자료", now - timedelta(days=40)),
            create_topic=False,
        )
        upsert_source_signal(
            con,
            _signal("recent", "최근 자료", now - timedelta(hours=1)),
            create_topic=False,
        )
        stats = get_database_stats(
            con,
            db_path=db_path,
            retention_days=30,
            lookback_hours=72,
            now=now,
        )

    assert stats.source_items_total == 2
    assert stats.source_items_recent == 1
    assert stats.source_items_old_unlinked == 1
    assert stats.database_size_bytes > 0
