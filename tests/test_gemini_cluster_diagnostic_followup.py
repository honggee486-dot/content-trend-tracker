from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import time

from src.database import connect_database, init_database, set_setting
from src.services.cluster_case_candidate_expansion_service import (
    analyze_cluster_cases_with_expanded_candidates,
)
from src.services.cluster_case_diagnostic_service import analyze_cluster_cases
from src.services.source_analysis_limit_service import (
    analyze_source_analysis_limits,
)
from src.services.topic_angle_ai_service import (
    TopicAngleBatchResult,
    TopicAngleExecution,
    TopicAnglePreparation,
    _AttemptRecord,
    _BatchExecutionResult,
)
from src.services.topic_angle_response_integrity_service import (
    RESPONSE_PARTIAL_ERROR_TYPE,
    annotate_missing_topic_angle_ids,
    apply_integrity_to_batch_result,
)


NOW = datetime(2026, 8, 1, 1, 0, 0)


def _attempt() -> _AttemptRecord:
    return _AttemptRecord(
        attempt_number=1,
        status="success",
        http_status=200,
        error_type="",
        retry_reason="",
        retry_wait_seconds=0,
        input_tokens=100,
        output_tokens=200,
        total_tokens=300,
        duration_ms=1000,
        error_message="",
    )


def _preparation() -> TopicAnglePreparation:
    clusters = (
        {"cluster_id": "cluster_a", "topic": "A"},
        {"cluster_id": "cluster_b", "topic": "B"},
    )
    return TopicAnglePreparation(
        status="ready",
        clusters=clusters,
        batches=(clusters,),
        skipped_sensitive_clusters=0,
        items_per_request=30,
        max_parallel_requests=1,
        min_opportunity_score=50,
        started_at=time.perf_counter(),
    )


def test_missing_gemini_cluster_ids_become_explicit_partial_result() -> None:
    preparation = _preparation()
    execution = TopicAngleExecution(
        preparation=preparation,
        results=(
            _BatchExecutionResult(
                batch_number=1,
                clusters=preparation.clusters,
                request_hash="hash",
                enrichments={"cluster_a": {"directions": []}},
                validation_errors=(),
                attempts=(_attempt(),),
                status="success",
            ),
        ),
    )

    annotated = annotate_missing_topic_angle_ids(execution)
    result = annotated.results[0]

    assert result.status == RESPONSE_PARTIAL_ERROR_TYPE
    assert result.error_type == RESPONSE_PARTIAL_ERROR_TYPE
    assert "cluster_b" in result.error_message
    assert result.attempts[-1].status == RESPONSE_PARTIAL_ERROR_TYPE
    assert result.attempts[-1].error_type == RESPONSE_PARTIAL_ERROR_TYPE
    assert result.enrichments == {"cluster_a": {"directions": []}}

    batch_result = TopicAngleBatchResult(
        status="partial_success",
        requested_clusters=2,
        generated_clusters=1,
        generated_angles=3,
        skipped_sensitive_clusters=0,
        attempts=1,
        error_message="이번 실행에서 저장되지 않은 글감 1개",
    )
    updated = apply_integrity_to_batch_result(batch_result, annotated)
    assert updated.status == "partial_success"
    assert updated.error_type == RESPONSE_PARTIAL_ERROR_TYPE
    assert "cluster_b" in updated.error_message
    assert "저장되지 않은 글감 1개" in updated.error_message


def test_complete_gemini_response_is_not_reclassified() -> None:
    preparation = _preparation()
    execution = TopicAngleExecution(
        preparation=preparation,
        results=(
            _BatchExecutionResult(
                batch_number=1,
                clusters=preparation.clusters,
                request_hash="hash",
                enrichments={"cluster_a": {}, "cluster_b": {}},
                validation_errors=(),
                attempts=(_attempt(),),
                status="success",
            ),
        ),
    )

    annotated = annotate_missing_topic_angle_ids(execution)
    assert annotated.results[0].status == "success"
    assert annotated.results[0].attempts[-1].error_type == ""


def _insert_item(
    con,
    *,
    item_id: str,
    source_type: str,
    title: str,
    observed_at: datetime,
) -> None:
    con.execute(
        """
        INSERT INTO source_items(
            source_item_id, source_type, external_id, raw_title,
            normalized_title, source_name, observed_at, signal_value,
            metadata_json, first_imported_at, last_imported_at,
            observation_count, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 10, '{}', ?, ?, 1, ?)
        """,
        [
            item_id,
            source_type,
            f"external_{item_id}",
            title,
            title.casefold(),
            source_type,
            observed_at,
            observed_at,
            observed_at,
            observed_at,
        ],
    )


def _insert_cluster(
    con,
    *,
    cluster_id: str,
    title: str,
    source_type: str,
    item_id: str,
    last_seen_at: datetime,
) -> None:
    con.execute(
        """
        INSERT INTO trend_clusters(
            cluster_id, canonical_title, trend_score, opportunity_score,
            fact_risk_score, item_count, source_type_count, publisher_count,
            source_types_json, score_reasons_json,
            first_seen_at, last_seen_at, calculated_at
        ) VALUES (?, ?, 60, 60, 20, 1, 1, 1, ?, '[]', ?, ?, ?)
        """,
        [
            cluster_id,
            title,
            json.dumps([source_type], ensure_ascii=False),
            last_seen_at,
            last_seen_at,
            last_seen_at,
        ],
    )
    con.execute(
        """
        INSERT INTO trend_cluster_items(cluster_id, source_item_id, linked_at)
        VALUES (?, ?, ?)
        """,
        [cluster_id, item_id, last_seen_at],
    )


def test_keyword_expansion_finds_candidate_outside_recent_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import src.services.cluster_case_diagnostic_service as base_service

    db_path = tmp_path / "expanded-candidates.duckdb"
    init_database(db_path)
    monkeypatch.setattr(base_service, "MAX_CANDIDATE_CLUSTERS", 1)

    with connect_database(db_path) as con:
        _insert_item(
            con,
            item_id="unclustered_income",
            source_type="daum_web",
            title="2027년 기준 중위소득 6.7% 인상",
            observed_at=NOW - timedelta(hours=1),
        )
        _insert_item(
            con,
            item_id="matching_income",
            source_type="naver_news",
            title="내년 기준 중위소득 6.7% 역대급 인상",
            observed_at=NOW - timedelta(hours=10),
        )
        _insert_cluster(
            con,
            cluster_id="cluster_matching_income",
            title="내년 기준 중위소득 6.7% 역대급 인상",
            source_type="naver_news",
            item_id="matching_income",
            last_seen_at=NOW - timedelta(hours=10),
        )
        _insert_item(
            con,
            item_id="latest_unrelated",
            source_type="youtube",
            title="완전히 다른 최신 게임 소식",
            observed_at=NOW,
        )
        _insert_cluster(
            con,
            cluster_id="cluster_latest_unrelated",
            title="완전히 다른 최신 게임 소식",
            source_type="youtube",
            item_id="latest_unrelated",
            last_seen_at=NOW,
        )

        base_report = analyze_cluster_cases(con, lookback_hours=72, now=NOW)
        before = {
            table: int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("source_items", "trend_clusters", "trend_cluster_items")
        }
        expanded = analyze_cluster_cases_with_expanded_candidates(
            con,
            lookback_hours=72,
            now=NOW,
        )
        after = {
            table: int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in before
        }

    assert base_report.unclustered_cases[0].candidate is None
    candidate = expanded.unclustered_cases[0].candidate
    assert candidate is not None
    assert candidate.cluster_id == "cluster_matching_income"
    assert candidate.similarity >= 0.72
    assert before == after


def test_analysis_limit_estimate_uses_current_ranking_window(tmp_path: Path) -> None:
    db_path = tmp_path / "analysis-limits.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        set_setting(con, "trend_lookback_hours", "72")
        set_setting(con, "trend_analysis_naver_limit", "3")
        for index in range(5):
            _insert_item(
                con,
                item_id=f"recent_naver_{index}",
                source_type="naver_news" if index % 2 == 0 else "naver_blog",
                title=f"최근 네이버 원문 {index}",
                observed_at=NOW - timedelta(hours=1),
            )
        for index in range(4):
            _insert_item(
                con,
                item_id=f"old_naver_{index}",
                source_type="naver_news",
                title=f"오래된 네이버 원문 {index}",
                observed_at=NOW - timedelta(hours=100),
            )

        before = int(con.execute("SELECT COUNT(*) FROM source_items").fetchone()[0])
        report = analyze_source_analysis_limits(
            con,
            lookback_hours=168,
            now=NOW,
        )
        after = int(con.execute("SELECT COUNT(*) FROM source_items").fetchone()[0])

    naver = next(row for row in report.rows if row.source_group == "naver")
    assert report.requested_lookback_hours == 168
    assert report.effective_lookback_hours == 72
    assert naver.collected_item_count == 5
    assert naver.configured_limit == 3
    assert naver.estimated_excluded_count == 2
    assert report.estimated_excluded_count == 2
    assert before == after


def test_ui_keeps_collection_and_gemini_states_separate() -> None:
    root = Path(__file__).resolve().parents[1]
    collection_ui = (root / "src" / "collection_history_ui.py").read_text(
        encoding="utf-8"
    )
    source_ui = (root / "src" / "source_diversity_ui.py").read_text(
        encoding="utf-8"
    )
    refresh_script = (root / "scripts" / "refresh_trends.py").read_text(
        encoding="utf-8"
    )

    assert '"Gemini 결과"' in collection_ui
    assert "Gemini 결과는 출처 수집의 전체 성공 여부와 별도로 표시" in collection_ui
    assert "annotate_missing_topic_angle_ids" in refresh_script
    assert "apply_integrity_to_batch_result" in refresh_script
    assert '"입력 상한 초과 추정"' in source_ui
    assert '"상한 외 미연결"' in source_ui
    assert "analyze_cluster_cases_with_expanded_candidates" in source_ui
