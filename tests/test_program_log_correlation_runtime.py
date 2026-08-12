from __future__ import annotations

from src.services.program_log_context import current_program_log_correlation_id
from src.services.program_log_correlation_runtime import (
    install_program_log_correlation_contract,
)


def test_refresh_runs_inside_collection_correlation(monkeypatch) -> None:
    from src.services import trend_clustering_job_service as jobs
    from src.services import trend_discovery_service as discovery

    seen = []

    def fake_refresh(*args, **kwargs):
        seen.append(current_program_log_correlation_id())
        return {"status": "ok"}

    def fake_run(job_id, *args, **kwargs):
        return 0

    monkeypatch.setattr(discovery, "refresh_trend_sources_short_connections", fake_refresh)
    monkeypatch.setattr(jobs, "run_clustering_job", fake_run)
    install_program_log_correlation_contract()

    result = discovery.refresh_trend_sources_short_connections(
        "test.duckdb",
        collection_run_id="collection_123",
    )

    assert result == {"status": "ok"}
    assert seen == ["collection_123"]
    assert current_program_log_correlation_id() == ""


def test_clustering_job_runs_inside_job_correlation(monkeypatch) -> None:
    from src.services import trend_clustering_job_service as jobs
    from src.services import trend_discovery_service as discovery

    seen = []

    def fake_refresh(*args, **kwargs):
        return {"status": "ok"}

    def fake_run(job_id, *args, **kwargs):
        seen.append((job_id, current_program_log_correlation_id()))
        return 0

    monkeypatch.setattr(discovery, "refresh_trend_sources_short_connections", fake_refresh)
    monkeypatch.setattr(jobs, "run_clustering_job", fake_run)
    install_program_log_correlation_contract()

    result = jobs.run_clustering_job("cluster_job_456")

    assert result == 0
    assert seen == [("cluster_job_456", "cluster_job_456")]
    assert current_program_log_correlation_id() == ""
