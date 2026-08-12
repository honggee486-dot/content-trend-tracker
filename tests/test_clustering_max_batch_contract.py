from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.database import connect_database, init_database
from src.services.trend_clustering_diagnostic_service import (
    build_trend_clustering_trial_diagnostic,
)


def test_diagnostic_rejects_configured_max_batches_above_twenty(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "too-many-batches.duckdb"
    init_database(db_path)
    now = datetime(2026, 8, 6, 0, 0, 0)
    with connect_database(db_path) as con:
        con.execute(
            """
            INSERT INTO trend_clustering_jobs(
                job_id, status, launcher, model_name, scan_limit, batch_size,
                max_batches, completed_batches, processed_units,
                processed_source_items, remaining_items, existing_links,
                new_clusters, uncertain_units, conflict_units, needs_review_items,
                input_tokens, output_tokens, thought_tokens, total_tokens,
                error_message, created_at, started_at, heartbeat_at, finished_at
            ) VALUES (
                'over-limit', 'partial', 'test', 'gemini-3.5-flash-lite',
                4000, 300, 21, 1, 300, 300, 100, 100, 200, 0, 0, 0,
                1000, 500, 0, 1500, '', ?, ?, ?, ?
            )
            """,
            [now - timedelta(minutes=3), now - timedelta(minutes=2), now, now],
        )
        con.execute(
            """
            INSERT INTO trend_clustering_job_batches(
                job_id, batch_number, status, first_stage_units,
                processed_units, processed_source_items, existing_links,
                new_clusters, uncertain_units, conflict_units,
                needs_review_items, input_tokens, output_tokens,
                thought_tokens, total_tokens, duration_ms, error_message,
                started_at, finished_at
            ) VALUES (
                'over-limit', 1, 'success', 300, 300, 300, 100, 200,
                0, 0, 0, 1000, 500, 0, 1500, 60000, '', ?, ?
            )
            """,
            [now - timedelta(minutes=2), now - timedelta(minutes=1)],
        )
        diagnostic = build_trend_clustering_trial_diagnostic(con)

    assert diagnostic["configured_max_batches"] == 21
    assert diagnostic["completed_within_configured_limit"] is False
    assert diagnostic["trial_contract_ok"] is False
    assert diagnostic["status"] == "시험 계약 점검"
