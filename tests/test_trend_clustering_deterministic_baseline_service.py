from __future__ import annotations

import duckdb

from src.services.trend_clustering_deterministic_baseline_service import (
    BASELINE_SIMILARITY_THRESHOLD,
    build_deterministic_baseline_comparison,
)


def _connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE trend_clustering_jobs (
            job_id VARCHAR,
            model_name VARCHAR,
            started_at TIMESTAMP,
            finished_at TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE TABLE trend_cluster_processing (
            source_item_id VARCHAR,
            model_name VARCHAR,
            first_stage_key VARCHAR,
            cluster_id VARCHAR,
            status VARCHAR,
            processed_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE TABLE source_items (
            source_item_id VARCHAR,
            raw_title VARCHAR
        )
        """
    )
    con.execute(
        "INSERT INTO trend_clustering_jobs VALUES (?, ?, ?, ?)",
        ["job-1", "gemini-test", "2026-08-10 09:00:00", "2026-08-10 09:10:00"],
    )
    rows = [
        (
            "item-1",
            "k1",
            "cluster-s26",
            "삼성 갤럭시 스마트폰 S26 국내 신제품 출시 일정 안내",
        ),
        (
            "item-2",
            "k2",
            "cluster-s26",
            "삼성 갤럭시 스마트폰 S26 신제품 출시 일정 안내",
        ),
        (
            "item-3",
            "k3",
            "cluster-s27",
            "삼성 갤럭시 스마트폰 S27 국내 신제품 출시 일정 안내",
        ),
        (
            "item-4",
            "k4",
            "cluster-baseball",
            "KBO 주말 야구 경기 결과",
        ),
    ]
    for source_item_id, first_stage_key, cluster_id, title in rows:
        con.execute("INSERT INTO source_items VALUES (?, ?)", [source_item_id, title])
        con.execute(
            """
            INSERT INTO trend_cluster_processing
            VALUES (?, ?, ?, ?, 'processed', ?, ?)
            """,
            [
                source_item_id,
                "gemini-test",
                first_stage_key,
                cluster_id,
                "2026-08-10 09:05:00",
                "2026-08-10 09:05:00",
            ],
        )
    return con


def test_deterministic_baseline_compares_same_job_without_writes() -> None:
    con = _connection()
    try:
        before = con.execute("SELECT COUNT(*) FROM trend_cluster_processing").fetchone()[0]

        result = build_deterministic_baseline_comparison(con, job_id="job-1")

        after = con.execute("SELECT COUNT(*) FROM trend_cluster_processing").fetchone()[0]
    finally:
        con.close()

    assert before == after == 4
    assert result["available"] is True
    assert result["comparison_complete"] is True
    assert result["comparison_incomplete_reasons"] == []
    assert result["similarity_threshold"] == BASELINE_SIMILARITY_THRESHOLD
    assert result["evaluable_candidate_count"] == 4
    assert result["baseline_merge_pair_count"] >= 1
    assert result["same_cluster_agreement_pair_count"] >= 1
    assert result["stored_same_cluster_pair_count"] == 1
    assert result["precision_vs_current_percent"] == 100.0
    assert result["recall_vs_current_percent"] == 100.0
    assert result["blocked_candidate_pair_count"] >= 1
    assert result["samples"]["safety_blocks"]
    assert "정답률이 아닙니다" in result["interpretation_note"]


def test_deterministic_baseline_marks_skipped_common_blocks_incomplete() -> None:
    con = _connection()
    try:
        result = build_deterministic_baseline_comparison(
            con,
            job_id="job-1",
            block_size_limit=2,
        )
    finally:
        con.close()

    assert result["available"] is True
    assert result["skipped_common_block_count"] >= 1
    assert result["pair_limit_reached"] is False
    assert result["comparison_complete"] is False
    assert "oversized_blocks_skipped" in result["comparison_incomplete_reasons"]
    assert result["precision_vs_current_percent"] is None
    assert result["recall_vs_current_percent"] is None
    assert "비교 범위가 불완전" in result["interpretation_note"]


def test_deterministic_baseline_exact_pair_limit_is_not_false_incomplete() -> None:
    con = _connection()
    try:
        con.execute(
            "DELETE FROM trend_cluster_processing WHERE source_item_id IN ('item-3', 'item-4')"
        )
        con.execute("DELETE FROM source_items WHERE source_item_id IN ('item-3', 'item-4')")

        result = build_deterministic_baseline_comparison(
            con,
            job_id="job-1",
            pair_limit=1,
        )
    finally:
        con.close()

    assert result["evaluated_candidate_pair_count"] == 1
    assert result["pair_limit_reached"] is False
    assert result["comparison_complete"] is True
    assert result["precision_vs_current_percent"] == 100.0
    assert result["recall_vs_current_percent"] == 100.0


def test_deterministic_baseline_withholds_metrics_for_unresolved_input() -> None:
    con = _connection()
    try:
        con.execute(
            "UPDATE trend_cluster_processing SET first_stage_key = '' WHERE source_item_id = 'item-4'"
        )

        result = build_deterministic_baseline_comparison(con, job_id="job-1")
    finally:
        con.close()

    assert result["missing_first_stage_key_count"] == 1
    assert result["comparison_complete"] is False
    assert "missing_first_stage_key" in result["comparison_incomplete_reasons"]
    assert result["precision_vs_current_percent"] is None
    assert result["recall_vs_current_percent"] is None


def test_deterministic_baseline_reports_missing_job_without_guessing() -> None:
    con = _connection()
    try:
        result = build_deterministic_baseline_comparison(con, job_id="missing")
    finally:
        con.close()

    assert result["available"] is False
    assert result["reason"] == "job_not_found"
    assert result["baseline_merge_pair_count"] == 0
