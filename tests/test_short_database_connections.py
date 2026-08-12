from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import src.services.trend_discovery_service as trend_service


class _PublicAdapter:
    def __init__(self, active_connections: list[int]) -> None:
        self.active_connections = active_connections
        self.request_count = 0

    def load_signals(self, limit: int = 100):
        assert self.active_connections[0] == 0
        self.request_count += 1
        return [
            {
                "source_type": "google_trends",
                "external_id": "google-1",
                "title": "테스트 트렌드",
            }
        ][:limit]


def _install_short_connection_fakes(monkeypatch):
    active_connections = [0]
    events: list[str] = []

    class _Result:
        def __init__(self, value: int) -> None:
            self.value = value

        def fetchone(self):
            return (self.value,)

    class _Connection:
        def execute(self, query, *args, **kwargs):
            events.append("db-query")
            if "COUNT(*) FROM trend_clusters" in query:
                return _Result(7)
            if "COUNT(*) FROM source_items" in query:
                return _Result(42)
            raise AssertionError(f"unexpected SQL during short-connection test: {query}")

    @contextmanager
    def fake_connect(_db_path):
        active_connections[0] += 1
        events.append("db-open")
        try:
            yield _Connection()
        finally:
            active_connections[0] -= 1
            events.append("db-close")

    monkeypatch.setattr(trend_service, "connect_database", fake_connect)
    monkeypatch.setattr(
        trend_service,
        "build_portal_search_queries",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        trend_service,
        "import_preloaded_source_signals",
        lambda con, signals, **kwargs: {
            "status": "success",
            "items_read": len(signals),
            "items_added": len(signals),
            "items_updated": 0,
            "items_skipped": 0,
        },
    )
    monkeypatch.setattr(
        trend_service,
        "record_local_api_calls",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        trend_service,
        "record_source_import_failure",
        lambda *args, **kwargs: None,
    )
    preparation = SimpleNamespace(status="ready")
    calculation = SimpleNamespace(preparation=preparation)

    def fake_prepare(con, **kwargs):
        assert active_connections[0] == 1
        events.append("ranking-read")
        return preparation

    def fake_calculate(prepared, **kwargs):
        assert prepared is preparation
        assert active_connections[0] == 0
        assert callable(kwargs.get("progress_callback"))
        events.append("ranking-calculate-no-db")
        return calculation

    def fake_finalize(con, calculated):
        assert calculated is calculation
        assert active_connections[0] == 1
        events.append("ranking-save")
        return {
            "items": 1,
            "clusters": 1,
            "reused": False,
            "timings": {"analysis": 0.0, "database": 0.0, "total": 0.0},
        }

    monkeypatch.setattr(trend_service, "prepare_trend_ranking_rebuild", fake_prepare)
    monkeypatch.setattr(
        trend_service,
        "calculate_prepared_trend_rankings",
        fake_calculate,
    )
    monkeypatch.setattr(
        trend_service,
        "finalize_prepared_trend_rankings",
        fake_finalize,
    )
    return active_connections, events


def test_public_network_request_runs_without_duckdb_connection(monkeypatch):
    active_connections, events = _install_short_connection_fakes(monkeypatch)
    adapter = _PublicAdapter(active_connections)

    result = trend_service.refresh_trend_sources_short_connections(
        "test.duckdb",
        google_trends_adapter=adapter,
        google_trends_limit=10,
    )

    assert active_connections[0] == 0
    assert result["google_trends"]["items_read"] == 1
    assert result["ranking"]["clusters"] == 1
    assert "ranking-calculate-no-db" in events



class _FailingPublicAdapter:
    def __init__(self, active_connections: list[int]) -> None:
        self.active_connections = active_connections
        self.request_count = 0

    def load_signals(self, limit: int = 100):
        assert self.active_connections[0] == 0
        self.request_count += 1
        raise RuntimeError("public API failed")


def test_public_failure_is_recorded_after_network_connection_is_closed(monkeypatch):
    active_connections, events = _install_short_connection_fakes(monkeypatch)
    recorded: list[tuple[str, str]] = []

    def fake_record_failure(con, *, sync_source_type, error):
        assert active_connections[0] == 1
        recorded.append((sync_source_type, str(error)))

    monkeypatch.setattr(
        trend_service,
        "record_source_import_failure",
        fake_record_failure,
    )

    result = trend_service.refresh_trend_sources_short_connections(
        "test.duckdb",
        google_trends_adapter=_FailingPublicAdapter(active_connections),
    )

    assert active_connections[0] == 0
    assert "public API failed" in result["errors"]["google_trends"]
    assert recorded == [("google_trends_rss", "public API failed")]
    assert result["ranking"]["status"] == "skipped_source_failure"
    assert result["ranking"]["clusters"] == 7
    assert result["ranking"]["items"] == 42
    assert result["ranking"]["ai_clustering"]["defer_topic_angles"] is True
    assert "ranking-calculate-no-db" not in events


def test_partial_source_success_still_rebuilds_ranking(monkeypatch):
    active_connections, events = _install_short_connection_fakes(monkeypatch)

    result = trend_service.refresh_trend_sources_short_connections(
        "test.duckdb",
        google_trends_adapter=_FailingPublicAdapter(active_connections),
        wikipedia_adapter=_PublicAdapter(active_connections),
    )

    assert active_connections[0] == 0
    assert "google_trends" in result["errors"]
    assert result["wikipedia"]["items_read"] == 1
    assert result["ranking"]["clusters"] == 1
    assert "ranking-calculate-no-db" in events


def test_portal_network_request_runs_between_short_db_connections(monkeypatch):
    active_connections, events = _install_short_connection_fakes(monkeypatch)
    adapter = object()
    plan = {
        "provider": "NAVER",
        "api_name": "search",
        "sync_source_type": "naver_search",
        "tasks": [{"kwargs": {"query": "테스트"}}],
        "max_workers": 1,
        "retry_budget": 0,
        "max_retries": 0,
    }

    def fake_prepare(con, **kwargs):
        assert active_connections[0] == 1
        events.append("portal-plan")
        return plan

    def fake_fetch(current_adapter, tasks, **kwargs):
        assert current_adapter is adapter
        assert active_connections[0] == 0
        events.append("portal-network-no-db")
        return {
            "signals": [],
            "attempt_count": 1,
            "successful_requests": 1,
            "failed_requests": 0,
            "skipped_requests": 0,
            "retry_count": 0,
            "request_errors": [],
            "network_seconds": 0.1,
        }

    def fake_finalize(con, current_plan, fetch_result, **kwargs):
        assert active_connections[0] == 1
        assert current_plan is plan
        events.append("portal-save")
        return {
            "status": "success",
            "items_read": 0,
            "items_added": 0,
            "items_updated": 0,
            "items_skipped": 0,
            "request_count": 1,
            "retry_count": 0,
        }

    monkeypatch.setattr(trend_service, "_prepare_naver_collection", fake_prepare)
    monkeypatch.setattr(trend_service, "_fetch_portal_tasks", fake_fetch)
    monkeypatch.setattr(trend_service, "_finalize_portal_collection", fake_finalize)

    result = trend_service.refresh_trend_sources_short_connections(
        "test.duckdb",
        naver_adapter=adapter,
        configured_seed_queries=["테스트"],
    )

    assert active_connections[0] == 0
    assert result["naver"]["status"] == "success"
    assert events.index("portal-plan") < events.index("portal-network-no-db")
    assert events.index("portal-network-no-db") < events.index("portal-save")


def test_scheduled_refresh_preserves_last_ranking_when_background_clustering_holds_lock(
    monkeypatch,
):
    events: list[str] = []

    class _Result:
        def __init__(self, value: int) -> None:
            self.value = value

        def fetchone(self):
            return (self.value,)

    class _Connection:
        def execute(self, query, *args, **kwargs):
            events.append("db-query")
            if "COUNT(*) FROM trend_clusters" in query:
                return _Result(27)
            if "COUNT(*) FROM source_items" in query:
                return _Result(910)
            raise AssertionError(f"unexpected SQL during overlap test: {query}")

    @contextmanager
    def fake_connect(_db_path):
        events.append("db-open")
        try:
            yield _Connection()
        finally:
            events.append("db-close")

    monkeypatch.setattr(trend_service, "connect_database", fake_connect)
    monkeypatch.setattr(
        trend_service,
        "build_portal_search_queries",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        trend_service,
        "acquire_trend_clustering_lock",
        lambda *args, **kwargs: SimpleNamespace(
            acquired=False,
            lock=None,
            message="백그라운드 2차 군집 작업 실행 중",
        ),
    )
    monkeypatch.setattr(
        trend_service,
        "calculate_prepared_trend_rankings",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("잠금 충돌 시 순위 계산을 실행하면 안 됩니다.")
        ),
    )

    result = trend_service.refresh_trend_sources_short_connections("test.duckdb")

    assert result["ranking"]["status"] == "skipped_overlap"
    assert result["ranking"]["clusters"] == 27
    assert result["ranking"]["items"] == 910
    assert result["ranking"]["ai_clustering"]["defer_topic_angles"] is True
    assert "백그라운드 2차 군집" in result["ranking"]["ai_clustering"]["error_message"]
