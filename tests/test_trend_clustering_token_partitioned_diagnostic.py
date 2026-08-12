from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import sys

import duckdb

from src.database import connect_database, init_database
from src.services.trend_cluster_sparse_protocol import CLUSTERING_SCAN_CANDIDATE_LIMIT
from src.services.trend_clustering_diagnostic_service import (
    build_trend_clustering_trial_diagnostic,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _insert_token_partitioned_trial(db_path: Path) -> None:
    now = datetime(2026, 8, 9, 16, 7, 35)
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
                'token-trial-job', 'partial', 'p2_diagnostic_trial',
                'gemini-3.5-flash-lite', ?, ?, 1, 1, 449, 455, 22, 1,
                427, 30, 0, 9, 392214, 6157, 0, 398371, '', ?, ?, ?, ?
            )
            """,
            [
                CLUSTERING_SCAN_CANDIDATE_LIMIT,
                CLUSTERING_SCAN_CANDIDATE_LIMIT,
                now - timedelta(minutes=4),
                now - timedelta(minutes=4),
                now,
                now,
            ],
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
                'token-trial-job', 1, 'partial', 479, 449, 455, 1, 427,
                30, 0, 9, 392214, 6157, 0, 398371, 221635, '', ?, ?
            )
            """,
            [now - timedelta(minutes=4), now],
        )


def test_p2_token_partitioned_sample_uses_snapshot_scan_contract(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "token-partitioned-trial.duckdb"
    init_database(db_path)
    _insert_token_partitioned_trial(db_path)

    before = db_path.stat()
    with duckdb.connect(str(db_path), read_only=True) as con:
        diagnostic = build_trend_clustering_trial_diagnostic(con)
    after = db_path.stat()

    assert (before.st_size, before.st_mtime_ns) == (
        after.st_size,
        after.st_mtime_ns,
    )
    assert diagnostic["contract_mode"] == "token_partitioned_snapshot"
    assert diagnostic["token_partitioned_contract"] is True
    assert diagnostic["scan_limit"] == CLUSTERING_SCAN_CANDIDATE_LIMIT
    assert diagnostic["configured_batch_size"] == CLUSTERING_SCAN_CANDIDATE_LIMIT
    assert diagnostic["absolute_batch_size_limit"] == CLUSTERING_SCAN_CANDIDATE_LIMIT
    assert diagnostic["configured_max_batches"] == 1
    assert diagnostic["completed_batches"] == 1
    assert diagnostic["maximum_first_stage_units"] == 479
    assert diagnostic["completed_within_configured_limit"] is True
    assert diagnostic["batch_size_contract_ok"] is True
    assert diagnostic["sequential_execution_ok"] is True
    assert diagnostic["trial_mode"] is True
    assert diagnostic["trial_contract_ok"] is True
    assert diagnostic["status"] == "P2 표본 계약 확인"


def test_p2_token_partitioned_human_output_uses_current_snapshot_contract(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "token-partitioned-human.duckdb"
    init_database(db_path)
    _insert_token_partitioned_trial(db_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(PROJECT_ROOT / "scripts" / "report_operation_diagnostics.py"),
            "--db",
            str(db_path),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert "상태: P2 표본 계약 확인 · P2 진단 1회 · 작업 partial" in completed.stdout
    assert (
        "1차 단위 최대/스캔 설정 상한/절대 상한: 479/50000/50000"
        in completed.stdout
    )
    assert "배치당 1차 군집 최대/설정 상한/절대 상한" not in completed.stdout
