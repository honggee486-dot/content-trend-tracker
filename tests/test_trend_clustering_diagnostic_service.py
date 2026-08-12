from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys

import duckdb

from src.database import connect_database, init_database
from src.services.operation_diagnostic_report_service import (
    build_operation_diagnostic_report,
)
from src.services.trend_clustering_diagnostic_service import (
    build_trend_clustering_trial_diagnostic,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _insert_trial(con, *, now: datetime) -> None:
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
            'trial-job', 'partial', 'dashboard', 'gemini-3.5-flash-lite',
            4000, 200, 5, 2, 300, 420, 120, 100, 80, 5, 3, 2,
            1000, 600, 400, 2000, '', ?, ?, ?, ?
        )
        """,
        [
            now - timedelta(minutes=10),
            now - timedelta(minutes=9),
            now,
            now,
        ],
    )
    rows = [
        (1, "success", 180, 150, 220, 60, 40, 3, 1, 1, 500, 300, 200, 1000, 120000),
        (2, "partial", 170, 150, 200, 40, 40, 2, 2, 1, 500, 300, 200, 1000, 180000),
    ]
    for row in rows:
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
                'trial-job', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                '', ?, ?
            )
            """,
            [
                *row,
                now - timedelta(minutes=8 - int(row[0])),
                now - timedelta(minutes=7 - int(row[0])),
            ],
        )


def test_clustering_trial_diagnostic_aggregates_contract_tokens_and_review(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "clustering-trial.duckdb"
    init_database(db_path)
    now = datetime(2026, 8, 5, 10, 0, 0)
    with connect_database(db_path) as con:
        _insert_trial(con, now=now)

    before = db_path.stat()
    with duckdb.connect(str(db_path), read_only=True) as con:
        diagnostic = build_trend_clustering_trial_diagnostic(con)
        report = build_operation_diagnostic_report(
            con,
            app_id="content-trend-tracker",
            items_per_request=15,
            thinking_level="high",
            timeout_seconds=600,
            min_opportunity_score=50,
            now=now,
        )
    after = db_path.stat()

    assert (before.st_size, before.st_mtime_ns) == (
        after.st_size,
        after.st_mtime_ns,
    )
    assert diagnostic["available"] is True
    assert diagnostic["sample_available"] is True
    assert diagnostic["status"] == "5배치 시험 확인"
    assert diagnostic["trial_mode"] is True
    assert diagnostic["trial_contract_ok"] is True
    assert diagnostic["batch_timing_complete"] is True
    assert diagnostic["sequential_execution_ok"] is True
    assert diagnostic["invalid_batch_interval_count"] == 0
    assert diagnostic["overlapping_batch_count"] == 0
    assert diagnostic["minimum_inter_batch_gap_ms"] == 0
    assert diagnostic["maximum_inter_batch_gap_ms"] == 0
    assert diagnostic["batch_count"] == 2
    assert diagnostic["maximum_batch_number"] == 2
    assert diagnostic["maximum_first_stage_units"] == 180
    assert diagnostic["processed_units"] == 300
    assert diagnostic["processed_source_items"] == 420
    assert diagnostic["existing_links"] == 100
    assert diagnostic["new_clusters"] == 80
    assert diagnostic["review_signal_count"] == 10
    assert diagnostic["total_tokens"] == 2000
    assert diagnostic["estimated_total_tokens_per_1000_units"] == 6667
    assert diagnostic["average_duration_ms"] == 150000
    assert diagnostic["maximum_duration_ms"] == 180000
    assert len(diagnostic["batches"]) == 2
    assert report["trend_clustering"]["job_id"] == "trial-job"
    assert report["next_action"]["label"] == "군집 표본 검토"


def test_clustering_diagnostic_detects_overlapping_batches(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "clustering-overlap.duckdb"
    init_database(db_path)
    now = datetime(2026, 8, 5, 10, 0, 0)
    with connect_database(db_path) as con:
        _insert_trial(con, now=now)
        con.execute(
            """
            UPDATE trend_clustering_job_batches
            SET started_at = ?, finished_at = ?
            WHERE job_id = 'trial-job' AND batch_number = 2
            """,
            [
                now - timedelta(minutes=6, seconds=30),
                now - timedelta(minutes=5),
            ],
        )
        diagnostic = build_trend_clustering_trial_diagnostic(con)

    assert diagnostic["batch_timing_complete"] is True
    assert diagnostic["sequential_execution_ok"] is False
    assert diagnostic["overlapping_batch_count"] == 1
    assert diagnostic["minimum_inter_batch_gap_ms"] == -30000
    assert diagnostic["maximum_inter_batch_gap_ms"] == -30000
    assert diagnostic["trial_contract_ok"] is False
    assert diagnostic["status"] == "순차 실행 점검"


def test_clustering_diagnostic_enforces_current_three_hundred_twenty_contract(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "clustering-current-cap.duckdb"
    init_database(db_path)
    now = datetime(2026, 8, 5, 10, 0, 0)
    with connect_database(db_path) as con:
        _insert_trial(con, now=now)
        con.execute(
            """
            UPDATE trend_clustering_jobs
            SET batch_size = 300, max_batches = 20
            WHERE job_id = 'trial-job'
            """
        )
        con.execute(
            """
            UPDATE trend_clustering_job_batches
            SET first_stage_units = 300
            WHERE job_id = 'trial-job' AND batch_number = 1
            """
        )
        current = build_trend_clustering_trial_diagnostic(con)
        con.execute(
            """
            UPDATE trend_clustering_job_batches
            SET first_stage_units = 301
            WHERE job_id = 'trial-job' AND batch_number = 1
            """
        )
        over_limit = build_trend_clustering_trial_diagnostic(con)

    assert current["configured_batch_size"] == 300
    assert current["configured_max_batches"] == 20
    assert current["maximum_first_stage_units"] == 300
    assert current["batch_size_contract_ok"] is True
    assert current["sequential_execution_ok"] is True
    assert current["trial_contract_ok"] is True
    assert current["status"] == "확대 설정 관찰"

    assert over_limit["maximum_first_stage_units"] == 301
    assert over_limit["batch_size_contract_ok"] is False
    assert over_limit["trial_contract_ok"] is False
    assert over_limit["status"] == "시험 계약 점검"


def test_clustering_trial_diagnostic_handles_missing_or_empty_history(
    tmp_path: Path,
) -> None:
    with duckdb.connect(":memory:") as con:
        missing = build_trend_clustering_trial_diagnostic(con)
    assert missing["available"] is False
    assert missing["reason"] == "missing_tables"
    assert missing["sequential_execution_ok"] is False

    db_path = tmp_path / "empty-history.duckdb"
    init_database(db_path)
    with duckdb.connect(str(db_path), read_only=True) as con:
        empty = build_trend_clustering_trial_diagnostic(con)
    assert empty["available"] is True
    assert empty["sample_available"] is False
    assert empty["reason"] == "job_not_found"
    assert empty["batch_timing_complete"] is False


def test_operation_diagnostic_json_includes_clustering_trial_section(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "clustering-cli.duckdb"
    init_database(db_path)
    now = datetime(2026, 8, 5, 10, 0, 0)
    with connect_database(db_path) as con:
        _insert_trial(con, now=now)

    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(PROJECT_ROOT / "scripts" / "report_operation_diagnostics.py"),
            "--db",
            str(db_path),
            "--json",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["read_only_verification"]["verified"] is True
    assert report["trend_clustering"]["job_id"] == "trial-job"
    assert report["trend_clustering"]["trial_contract_ok"] is True
    assert report["trend_clustering"]["sequential_execution_ok"] is True
    assert report["trend_clustering"]["overlapping_batch_count"] == 0
    assert report["trend_clustering"]["estimated_total_tokens_per_1000_units"] == 6667


def test_operation_diagnostic_human_output_keeps_legacy_absolute_cap(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "clustering-human.duckdb"
    init_database(db_path)
    now = datetime(2026, 8, 5, 10, 0, 0)
    with connect_database(db_path) as con:
        _insert_trial(con, now=now)

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
    assert "1차 단위 최대/스캔 설정 상한/절대 상한: 180/200/300" in completed.stdout
    assert "순차 실행/시각 완전성/겹침/역전: 통과/완전/0/0" in completed.stdout
    assert "배치 간 최소/최대 간격: 0.0초/0.0초" in completed.stdout
