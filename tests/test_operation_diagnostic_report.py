from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import duckdb

import src.services.operation_diagnostic_report_service as report_service
from src.config import GeminiConfig
from src.database import connect_database, init_database
from src.services.collection_history_service import (
    finish_collection_run,
    start_collection_run,
)
from src.services.gemini_service import record_gemini_api_call
from src.services.operation_diagnostic_report_service import (
    build_operation_diagnostic_report,
)
from src.services.topic_angle_candidate_diagnostic_service import (
    TopicAngleCandidateDiagnostics,
)


def _config() -> GeminiConfig:
    return GeminiConfig(
        api_key="test-key",
        model="gemini-3.6-flash",
        app_id="content-trend-tracker",
        quota_scope_id="test-scope",
        timeout_seconds=60,
        retry_wait_seconds=2.0,
        retry_max_wait_seconds=30.0,
        topic_angle_batch_limit=15,
        topic_angle_thinking_level="high",
        topic_angle_timeout_seconds=600,
    )


def _refresh_result(*, source_partial: bool, gemini_failure: bool) -> dict[str, object]:
    result: dict[str, object] = {
        "youtube": {
            "status": "skipped",
            "items_added": 0,
            "items_updated": 0,
            "items_skipped": 0,
        },
        "naver": {
            "status": "success",
            "items_added": 2,
            "items_updated": 3,
            "items_skipped": 1,
            "request_count": 4,
            "retry_count": 0,
        },
        "daum": {
            "status": "partial" if source_partial else "success",
            "items_added": 1,
            "items_updated": 1,
            "items_skipped": 0,
            "request_count": 3,
            "retry_count": 1 if source_partial else 0,
        },
        "errors": {},
        "warnings": {"daum": "일부 실패"} if source_partial else {},
        "timings": {"youtube": 0.01, "naver": 0.2, "daum": 0.2},
    }
    result["topic_angles"] = {
        "status": "response_validation_error" if gemini_failure else "success",
        "requested_clusters": 15,
        "generated_clusters": 0 if gemini_failure else 15,
        "generated_angles": 0 if gemini_failure else 45,
        "attempts": 1,
        "requested_batches": 1,
        "duration_seconds": 12,
        "error_message": "응답 형식 오류" if gemini_failure else "",
    }
    return result


def _insert_portal_request(
    con,
    *,
    run_id: str,
    source_name: str,
    status: str,
    result_count: int,
    new_count: int,
    finished_at: datetime,
) -> None:
    con.execute(
        """
        INSERT INTO collection_query_requests(
            run_id, source_name, source_type, discovery_query, request_page,
            requested_result_count, status, attempt_count, retry_count,
            result_count, newly_saved_count, updated_count, skipped_count,
            http_status, error_type, error_message, duration_ms,
            started_at, finished_at, created_at
        ) VALUES (?, ?, ?, ?, 1, 10, ?, 2, 1, ?, ?, 0, 0,
                  NULL, NULL, NULL, 1000, ?, ?, ?)
        """,
        [
            run_id,
            source_name,
            f"{source_name}_type",
            f"{source_name} 검색어",
            status,
            result_count,
            new_count,
            finished_at - timedelta(seconds=1),
            finished_at,
            finished_at,
        ],
    )


def test_operation_report_aggregates_runtime_portals_and_separation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "operation-report.duckdb"
    init_database(db_path)
    config = _config()
    now = datetime(2026, 8, 2, 20, 0, 0)

    with connect_database(db_path) as con:
        record_gemini_api_call(
            con,
            config=config,
            content_pack_id="topic_angle_batch_current",
            request_hash="current-runtime",
            feature_id="trend_topic_angle_batch_v1",
            feature_version="6",
            attempt_number=1,
            cache_hit=False,
            status="success",
            http_status=200,
            error_type="",
            retry_reason="",
            retry_wait_seconds=0,
            input_tokens=100,
            output_tokens=200,
            thought_tokens=300,
            total_tokens=600,
            duration_ms=120000,
            error_message="",
            requested_item_count=15,
            configured_items_per_request=15,
            thinking_level="high",
            request_timeout_seconds=600,
        )

        success_id = start_collection_run(
            con,
            "background_refresh",
            started_at=now - timedelta(hours=2),
        )
        finish_collection_run(
            con,
            success_id,
            result=_refresh_result(source_partial=False, gemini_failure=True),
            finished_at=now - timedelta(hours=2) + timedelta(minutes=1),
        )
        partial_id = start_collection_run(
            con,
            "manual_refresh",
            started_at=now - timedelta(hours=1),
        )
        finish_collection_run(
            con,
            partial_id,
            result=_refresh_result(source_partial=True, gemini_failure=False),
            finished_at=now - timedelta(hours=1) + timedelta(minutes=1),
        )

        _insert_portal_request(
            con,
            run_id=success_id,
            source_name="naver",
            status="success",
            result_count=8,
            new_count=3,
            finished_at=now - timedelta(hours=2),
        )
        _insert_portal_request(
            con,
            run_id=success_id,
            source_name="daum",
            status="failure",
            result_count=0,
            new_count=0,
            finished_at=now - timedelta(hours=2),
        )

    before_size = db_path.stat().st_size
    with duckdb.connect(str(db_path), read_only=True) as con:
        report = build_operation_diagnostic_report(
            con,
            app_id=config.app_id,
            items_per_request=15,
            thinking_level="high",
            timeout_seconds=600,
            min_opportunity_score=50,
            portal_days=7,
            refresh_run_limit=10,
            now=now,
        )
    after_size = db_path.stat().st_size

    assert before_size == after_size
    assert report["read_only"] is True
    assert report["topic_angle"]["matching_successful_requests"] == 1
    assert report["topic_angle"]["requested_items"] == 15
    selection = report["topic_angle"]["candidate_selection"]
    assert selection["available"] is True
    assert selection["selected_is_estimate"] is True
    assert selection["selection_limit"] == 15
    assert selection["selected_clusters"] == 0
    assert report["portal_requests"]["request_count"] == 2
    assert report["portal_requests"]["attempt_count"] == 4
    assert report["portal_requests"]["retry_count"] == 2
    assert report["portal_requests"]["failed_request_count"] == 1
    assert report["portal_requests"]["sources"]["naver"]["newly_saved_count"] == 3
    assert report["collection_separation"]["run_count"] == 2
    assert report["collection_separation"]["source_success_count"] == 1
    assert report["collection_separation"]["source_problem_count"] == 1
    assert report["collection_separation"]["gemini_problem_count"] == 1
    assert report["collection_separation"]["isolated_gemini_problem_count"] == 1
    assert report["collection_separation"]["status"] == "분리 보존 확인"
    assert report["next_action"]["label"] == "출처 수집 점검"


class _ShowTablesConnection:
    def execute(self, sql: str):
        assert sql == "SHOW TABLES"
        return self

    def fetchall(self):
        return [(name,) for name in report_service._TOPIC_ANGLE_SELECTION_TABLES]


def test_candidate_selection_report_estimates_selected_without_generation(
    monkeypatch,
) -> None:
    diagnostics = TopicAngleCandidateDiagnostics(
        total_clusters=8409,
        eligible_status_clusters=300,
        score_eligible_clusters=126,
        already_complete_clusters=121,
        generation_needed_clusters=5,
        inspected_clusters=3,
        skipped_sensitive_clusters=1,
        skipped_no_evidence_clusters=1,
        selected_clusters=0,
        deferred_uninspected_clusters=2,
        min_opportunity_score=50.0,
        selection_limit=3,
    )
    monkeypatch.setattr(
        report_service,
        "collect_topic_angle_candidate_diagnostics",
        lambda *args, **kwargs: diagnostics,
    )

    result = report_service._load_topic_angle_candidate_selection(
        _ShowTablesConnection(),
        min_opportunity_score=50,
        selection_limit=3,
    )

    assert result["available"] is True
    assert result["selected_is_estimate"] is True
    assert result["selected_clusters"] == 1
    assert result["skipped_sensitive_clusters"] == 1
    assert result["skipped_no_evidence_clusters"] == 1
    assert result["deferred_uninspected_clusters"] == 2
