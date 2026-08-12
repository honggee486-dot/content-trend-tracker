from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.database import connect_database, init_database, set_setting
from src.services import post_collection_cleanup_runtime as runtime


def _install_fake_flow(monkeypatch, db_path, events, *, cleanup_result, fail=False):
    from src.services import data_maintenance_service as maintenance
    from src.services import trend_discovery_service as discovery

    def fake_cleanup(con, **kwargs):
        events.append("cleanup")
        return cleanup_result

    def fake_prepare(con, *args, **kwargs):
        events.append("prepare")
        return {"status": "ready"}

    def fake_refresh(path, *args, **kwargs):
        events.append("collect")
        if fail:
            raise RuntimeError("collection failed")
        with connect_database(path) as con:
            discovery.prepare_trend_ranking_rebuild(con)
        return {"ranking": {"status": "success"}}

    monkeypatch.setattr(maintenance, "run_automatic_cleanup_if_due", fake_cleanup)
    monkeypatch.setattr(discovery, "prepare_trend_ranking_rebuild", fake_prepare)
    monkeypatch.setattr(discovery, "refresh_trend_sources_short_connections", fake_refresh)
    runtime.install_post_collection_cleanup_contract()
    return maintenance, discovery


def test_due_cleanup_runs_between_collection_and_ranking(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "cleanup-order.duckdb"
    init_database(db_path)
    events = []
    result = SimpleNamespace(
        source_items_deleted=3,
        cluster_links_deleted=2,
        empty_clusters_deleted=1,
        sync_runs_deleted=4,
        collection_runs_deleted=5,
        api_usage_rows_deleted=6,
        total_rows_deleted=21,
        checkpoint_completed=False,
        finished_at=datetime(2026, 8, 6, 13, 0, 0),
    )
    maintenance, discovery = _install_fake_flow(
        monkeypatch,
        db_path,
        events,
        cleanup_result=result,
    )

    with connect_database(db_path) as con:
        deferred = maintenance.run_automatic_cleanup_if_due(
            con,
            enabled=True,
            source_retention_days=30,
            sync_run_retention_days=90,
            api_usage_retention_months=13,
            now=datetime(2026, 8, 6, 13, 0, 0),
        )

    assert events == []
    assert deferred is not None
    assert deferred.executed is False

    discovery.refresh_trend_sources_short_connections(
        db_path,
        collection_run_id="collection_test",
    )

    assert events == ["collect", "cleanup", "prepare"]
    assert deferred.executed is True
    assert deferred.source_items_deleted == 3
    assert deferred.total_rows_deleted == 21


def test_not_due_cleanup_is_checked_only_after_collection(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "cleanup-skip.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        set_setting(con, "data_cleanup_last_date", "2026-08-06")

    events = []
    maintenance, discovery = _install_fake_flow(
        monkeypatch,
        db_path,
        events,
        cleanup_result=None,
    )

    with connect_database(db_path) as con:
        cleanup_result = maintenance.run_automatic_cleanup_if_due(
            con,
            enabled=True,
            source_retention_days=30,
            sync_run_retention_days=90,
            api_usage_retention_months=13,
            now=datetime(2026, 8, 6, 13, 0, 0),
        )

    assert cleanup_result is None
    assert events == []

    discovery.refresh_trend_sources_short_connections(
        db_path,
        collection_run_id="collection_test",
    )

    assert events == ["collect", "cleanup", "prepare"]


def test_normal_return_total_source_failure_discards_pending_cleanup(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "cleanup-total-source-failure.duckdb"
    init_database(db_path)
    events = []
    result = SimpleNamespace(source_items_deleted=1, total_rows_deleted=1)
    from src.services import data_maintenance_service as maintenance
    from src.services import trend_discovery_service as discovery

    def fake_cleanup(con, **kwargs):
        events.append("cleanup")
        return result

    def fake_refresh(path, *args, **kwargs):
        events.append("collect")
        return {
            "errors": {"google_trends": "public API failed"},
            "ranking": {"status": "skipped_source_failure"},
        }

    monkeypatch.setattr(maintenance, "run_automatic_cleanup_if_due", fake_cleanup)
    monkeypatch.setattr(discovery, "refresh_trend_sources_short_connections", fake_refresh)
    runtime.install_post_collection_cleanup_contract()

    with connect_database(db_path) as con:
        deferred = maintenance.run_automatic_cleanup_if_due(
            con,
            enabled=True,
            source_retention_days=30,
            sync_run_retention_days=90,
            api_usage_retention_months=13,
            now=datetime(2026, 8, 6, 13, 0, 0),
        )

    returned = discovery.refresh_trend_sources_short_connections(
        db_path,
        collection_run_id="collection_test",
    )

    assert returned["ranking"]["status"] == "skipped_source_failure"
    assert events == ["collect"]
    assert deferred is not None
    assert deferred.executed is False


def test_collection_failure_discards_pending_cleanup(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "cleanup-failure.duckdb"
    init_database(db_path)
    events = []
    result = SimpleNamespace(source_items_deleted=1, total_rows_deleted=1)
    maintenance, discovery = _install_fake_flow(
        monkeypatch,
        db_path,
        events,
        cleanup_result=result,
        fail=True,
    )

    with connect_database(db_path) as con:
        deferred = maintenance.run_automatic_cleanup_if_due(
            con,
            enabled=True,
            source_retention_days=30,
            sync_run_retention_days=90,
            api_usage_retention_months=13,
            now=datetime(2026, 8, 6, 13, 0, 0),
        )

    with pytest.raises(RuntimeError, match="collection failed"):
        discovery.refresh_trend_sources_short_connections(
            db_path,
            collection_run_id="collection_test",
        )

    assert events == ["collect"]
    assert deferred.executed is False
