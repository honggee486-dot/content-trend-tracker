from __future__ import annotations

from functools import wraps
from typing import Any

from src.services.program_log_context import program_log_correlation


def install_program_log_correlation_contract() -> None:
    """수집·군집 한 번의 세부 로그를 같은 실행 ID로 묶습니다."""
    from src.services import trend_discovery_service as discovery
    from src.services import trend_clustering_job_service as clustering_jobs

    refresh = getattr(discovery, "refresh_trend_sources_short_connections", None)
    if callable(refresh) and not getattr(refresh, "_program_log_correlation", False):
        original_refresh = refresh

        @wraps(original_refresh)
        def refresh_with_correlation(*args: Any, **kwargs: Any):
            correlation_id = str(kwargs.get("collection_run_id") or "")
            with program_log_correlation(correlation_id):
                return original_refresh(*args, **kwargs)

        refresh_with_correlation._program_log_correlation = True  # type: ignore[attr-defined]
        discovery.refresh_trend_sources_short_connections = refresh_with_correlation

    run_job = getattr(clustering_jobs, "run_clustering_job", None)
    if callable(run_job) and not getattr(run_job, "_program_log_correlation", False):
        original_run_job = run_job

        @wraps(original_run_job)
        def run_job_with_correlation(job_id: Any, *args: Any, **kwargs: Any):
            with program_log_correlation(str(job_id or "")):
                return original_run_job(job_id, *args, **kwargs)

        run_job_with_correlation._program_log_correlation = True  # type: ignore[attr-defined]
        clustering_jobs.run_clustering_job = run_job_with_correlation
