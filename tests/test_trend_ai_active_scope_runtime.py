from __future__ import annotations

from datetime import datetime, timedelta

import duckdb

from src.services.trend_ai_active_scope_runtime import (
    _build_blog_loader,
    _build_evaluation_loader,
    active_trend_cluster_ids,
)


def _database():
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE app_settings (
            setting_key VARCHAR PRIMARY KEY,
            setting_value VARCHAR,
            updated_at TIMESTAMP
        )
        """
    )
    con.execute(
        "INSERT INTO app_settings VALUES ('trend_lookback_hours', '72', NOW())"
    )
    con.execute(
        """
        CREATE TABLE trend_clusters (
            cluster_id VARCHAR PRIMARY KEY,
            first_seen_at TIMESTAMP,
            last_seen_at TIMESTAMP
        )
        """
    )
    now = datetime.now()
    con.executemany(
        "INSERT INTO trend_clusters VALUES (?, ?, ?)",
        [
            ["recent-1", now - timedelta(hours=5), now - timedelta(hours=1)],
            ["recent-2", now - timedelta(hours=80), now - timedelta(hours=2)],
            ["old-1", now - timedelta(days=8), now - timedelta(days=5)],
        ],
    )
    return con


def test_active_scope_uses_configured_72_hour_last_seen_window() -> None:
    con = _database()
    try:
        assert active_trend_cluster_ids(con) == {"recent-1", "recent-2"}
    finally:
        con.close()


def test_candidate_evaluation_and_blog_routing_both_exclude_old_clusters() -> None:
    con = _database()
    candidates = [
        {"cluster_id": "recent-1", "topic": "최근 1"},
        {"cluster_id": "recent-2", "topic": "최근 2"},
        {"cluster_id": "old-1", "topic": "과거"},
    ]
    try:
        evaluation_loader = _build_evaluation_loader(
            lambda _con: (list(candidates), 0)
        )
        evaluation_rows, skipped = evaluation_loader(con)
        blog_loader = _build_blog_loader(lambda _con: list(candidates))
        blog_rows = blog_loader(con)

        assert [row["cluster_id"] for row in evaluation_rows] == [
            "recent-1",
            "recent-2",
        ]
        assert skipped == 0
        assert [row["cluster_id"] for row in blog_rows] == [
            "recent-1",
            "recent-2",
        ]
    finally:
        con.close()
