from __future__ import annotations

from pathlib import Path

from src.database import connect_database, init_database
import src.services.trend_clustering_job_service as clustering_jobs
from scripts.run_p2_clustering_sample import _create_trial_job


def test_p2_clustering_sample_limits_created_job_to_one_batch(tmp_path: Path) -> None:
    db_path = tmp_path / "trial.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        created = _create_trial_job(con, clustering_jobs)
        row = con.execute(
            """
            SELECT status, launcher, batch_size, max_batches
            FROM trend_clustering_jobs
            WHERE job_id = ?
            """,
            [str(created["job_id"])],
        ).fetchone()

    assert created["created"] is True
    assert int(created["max_batches"]) == 1
    assert row is not None
    assert row[0] == "queued"
    assert row[1] == "p2_diagnostic_trial"
    assert int(row[2]) == 300
    assert int(row[3]) == 1


def test_p2_clustering_sample_does_not_reconfigure_existing_active_job(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "active.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        first = _create_trial_job(con, clustering_jobs)
        second = _create_trial_job(con, clustering_jobs)
        row = con.execute(
            """
            SELECT max_batches
            FROM trend_clustering_jobs
            WHERE job_id = ?
            """,
            [str(first["job_id"])],
        ).fetchone()

    assert first["created"] is True
    assert second["created"] is False
    assert second["job_id"] == first["job_id"]
    assert row is not None
    assert int(row[0]) == 1
