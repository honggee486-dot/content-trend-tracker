from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.database import connect_database, init_database
from src.services.trend_clustering_quality_sample_service import (
    build_trend_clustering_quality_sample,
)


def _insert_source(con, source_id: str, title: str) -> None:
    con.execute(
        """
        INSERT INTO source_items(
            source_item_id, source_type, external_id, raw_title,
            normalized_title, source_name, published_at, imported_at
        ) VALUES (?, 'naver_news', ?, ?, ?, '테스트 뉴스', ?, ?)
        """,
        [source_id, source_id, title, title.casefold(), datetime(2026, 8, 9, 11, 30), datetime(2026, 8, 9, 11, 31)],
    )


def _insert_cluster(con, cluster_id: str, title: str, calculated_at: datetime) -> None:
    con.execute(
        """
        INSERT INTO trend_clusters(
            cluster_id, canonical_title, trend_score, opportunity_score,
            fact_risk_score, quality_score, rediscovery_score,
            recommendation_status, item_count, source_type_count,
            publisher_count, source_types_json, score_reasons_json,
            quality_reasons_json, first_seen_at, last_seen_at, calculated_at
        ) VALUES (?, ?, 50, 50, 0, 50, 0, 'review', 1, 1, 1,
                  '["naver_news"]', '[]', '[]', ?, ?, ?)
        """,
        [cluster_id, title, calculated_at, calculated_at, calculated_at],
    )


def _insert_processing(
    con,
    *,
    source_id: str,
    first_stage_key: str,
    cluster_id: str,
    status: str,
    updated_at: datetime,
    attempt_count: int = 1,
    last_error: str = "",
) -> None:
    con.execute(
        """
        INSERT INTO trend_cluster_processing(
            source_item_id, input_hash, feature_id, feature_version,
            model_name, first_stage_key, cluster_id, status, attempt_count,
            last_error, processed_at, updated_at
        ) VALUES (?, ?, 'trend_cluster_grouping_v3', '5', 'gemini-test',
                  ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            source_id,
            f"hash-{source_id}",
            first_stage_key,
            cluster_id,
            status,
            attempt_count,
            last_error,
            updated_at,
            updated_at,
        ],
    )


def _build_sample_database(db_path: Path) -> tuple[str, datetime, datetime]:
    init_database(db_path)
    job_id = "cluster_job_quality_sample"
    started_at = datetime(2026, 8, 9, 12, 0, 0)
    finished_at = datetime(2026, 8, 9, 12, 5, 0)
    calculated_at = datetime(2026, 8, 9, 12, 4, 0)

    with connect_database(db_path) as con:
        for source_id, title in (
            ("a", "같은 사건 첫 번째 기사"),
            ("b", "같은 사건 두 번째 기사"),
            ("c", "독립 사건 기사"),
            ("d", "기존 군집에 이어진 기사"),
            ("e", "재시도 대상 기사"),
            ("f", "수동 검토 대상 기사"),
            ("x", "기존 군집의 이전 기사"),
        ):
            _insert_source(con, source_id, title)

        con.execute(
            """
            INSERT INTO trend_clustering_jobs(
                job_id, status, launcher, model_name, scan_limit, batch_size,
                max_batches, completed_batches, processed_units,
                processed_source_items, remaining_items, existing_links,
                new_clusters, uncertain_units, conflict_units,
                needs_review_items, created_at, started_at, heartbeat_at,
                finished_at
            ) VALUES (?, 'partial', 'p2_diagnostic_trial', 'gemini-test',
                      50000, 50000, 1, 1, 4, 4, 2, 1, 2, 2, 0, 1,
                      ?, ?, ?, ?)
            """,
            [job_id, started_at, started_at, finished_at, finished_at],
        )

        for cluster_id, title in (
            ("cluster_ab", "같은 사건"),
            ("cluster_c", "독립 사건"),
            ("cluster_existing", "기존 사건"),
            ("cluster_e", "재시도 임시"),
            ("cluster_f", "검토 임시"),
        ):
            _insert_cluster(con, cluster_id, title, calculated_at)

        for cluster_id, source_id in (
            ("cluster_ab", "a"),
            ("cluster_ab", "b"),
            ("cluster_c", "c"),
            ("cluster_existing", "d"),
            ("cluster_existing", "x"),
            ("cluster_e", "e"),
            ("cluster_f", "f"),
        ):
            con.execute(
                "INSERT INTO trend_cluster_items(cluster_id, source_item_id, linked_at) VALUES (?, ?, ?)",
                [cluster_id, source_id, calculated_at],
            )

        current_processing_at = datetime(2026, 8, 9, 12, 3, 0)
        _insert_processing(
            con,
            source_id="a",
            first_stage_key="candidate-a",
            cluster_id="cluster_ab",
            status="processed",
            updated_at=current_processing_at,
        )
        _insert_processing(
            con,
            source_id="b",
            first_stage_key="candidate-b",
            cluster_id="cluster_ab",
            status="processed",
            updated_at=current_processing_at,
        )
        _insert_processing(
            con,
            source_id="c",
            first_stage_key="candidate-c",
            cluster_id="cluster_c",
            status="processed",
            updated_at=current_processing_at,
        )
        _insert_processing(
            con,
            source_id="d",
            first_stage_key="candidate-d",
            cluster_id="cluster_existing",
            status="processed",
            updated_at=current_processing_at,
        )
        _insert_processing(
            con,
            source_id="e",
            first_stage_key="candidate-e",
            cluster_id="cluster_e",
            status="retry",
            updated_at=current_processing_at,
            last_error="Gemini 2차 군집 판단 불확실",
        )
        _insert_processing(
            con,
            source_id="f",
            first_stage_key="candidate-f",
            cluster_id="cluster_f",
            status="needs_review",
            updated_at=current_processing_at,
            attempt_count=3,
            last_error="Gemini 2차 군집 판단 불확실",
        )
        _insert_processing(
            con,
            source_id="x",
            first_stage_key="old-candidate",
            cluster_id="cluster_existing",
            status="processed",
            updated_at=started_at - timedelta(hours=2),
        )

    return job_id, started_at, finished_at


def test_quality_sample_reconstructs_current_job_without_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "quality-sample.duckdb"
    job_id, _started_at, _finished_at = _build_sample_database(db_path)
    before_size = db_path.stat().st_size
    before_mtime = db_path.stat().st_mtime_ns

    with connect_database(db_path, read_only=True) as con:
        report = build_trend_clustering_quality_sample(
            con,
            job_id=job_id,
            sample_limit=5,
        )

    assert db_path.stat().st_size == before_size
    assert db_path.stat().st_mtime_ns == before_mtime
    assert report["available"] is True
    assert report["snapshot_matches_job"] is True
    assert report["reconstruction_reliable"] is True
    assert report["processed_candidate_count"] == 4
    assert report["singleton_candidate_count"] == 1
    assert report["singleton_candidate_percent"] == 25.0
    assert report["multi_candidate_cluster_count"] == 1
    assert report["multi_candidate_candidate_count"] == 2
    assert report["existing_link_cluster_count"] == 1
    assert report["existing_link_candidate_count"] == 1
    assert report["uncertain_candidate_count"] == 2
    assert report["needs_review_source_item_count"] == 1
    assert report["retry_source_item_count"] == 1
    assert report["consistency"]["all_match"] is True

    multi = report["samples"]["multi_candidate_clusters"][0]
    assert multi["cluster_id"] == "cluster_ab"
    assert multi["job_candidate_count"] == 2
    assert {item["source_item_id"] for item in multi["items"]} == {"a", "b"}

    existing = report["samples"]["existing_link_clusters"][0]
    assert existing["cluster_id"] == "cluster_existing"
    assert existing["preexisting_item_count"] == 1
    assert {item["source_item_id"] for item in existing["items"]} == {"d", "x"}

    singleton = report["samples"]["singleton_candidates"][0]
    assert singleton["cluster_id"] == "cluster_c"
    assert singleton["items"][0]["title"] == "독립 사건 기사"

    unresolved = report["samples"]["unresolved_candidates"]
    assert {status for row in unresolved for status in row["statuses"]} == {
        "needs_review",
        "retry",
    }


def test_quality_sample_marks_reconstruction_unreliable_after_cluster_snapshot_changes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality-sample-changed.duckdb"
    job_id, _started_at, finished_at = _build_sample_database(db_path)

    with connect_database(db_path) as con:
        con.execute(
            "UPDATE trend_clusters SET calculated_at = ? WHERE cluster_id = 'cluster_c'",
            [finished_at + timedelta(minutes=10)],
        )

    with connect_database(db_path, read_only=True) as con:
        report = build_trend_clustering_quality_sample(con, job_id=job_id)

    assert report["available"] is True
    assert report["snapshot_matches_job"] is False
    assert report["reconstruction_reliable"] is False
    assert report["reason"] == "cluster_snapshot_changed_since_job"
