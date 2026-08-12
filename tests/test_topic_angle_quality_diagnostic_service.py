from __future__ import annotations

import json
from pathlib import Path

from src.config import GeminiConfig
from src.database import connect_database, init_database
from src.services.gemini_service import record_gemini_api_call
from src.services.topic_angle_quality_diagnostic_service import (
    build_topic_angle_quality_diagnostic,
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


def _seed_cluster(con, cluster_id: str, title: str, *, complete: bool) -> None:
    con.execute(
        """
        INSERT INTO trend_clusters(
            cluster_id, canonical_title, trend_score, opportunity_score,
            fact_risk_score, quality_score, rediscovery_score,
            recommendation_status, item_count, source_type_count,
            publisher_count, source_types_json, score_reasons_json,
            quality_reasons_json, first_seen_at, last_seen_at, calculated_at
        ) VALUES (?, ?, 80, 75, 0, 80, 0, 'recommended', 1, 1, 1,
                  '["naver_news"]', '[]', '[]',
                  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [cluster_id, title],
    )
    if not complete:
        return

    source_id = f"source_{cluster_id}"
    con.execute(
        """
        INSERT INTO source_items(
            source_item_id, source_type, external_id, raw_title,
            normalized_title, source_url, normalized_url, source_name,
            published_at, observed_at, signal_value, metadata_json,
            first_imported_at, previous_imported_at, last_imported_at,
            observation_count, imported_at
        ) VALUES (?, 'naver_news', ?, ?, ?, ?, ?, '테스트뉴스',
                  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 10, '{}',
                  CURRENT_TIMESTAMP, NULL, CURRENT_TIMESTAMP, 2, CURRENT_TIMESTAMP)
        """,
        [
            source_id,
            cluster_id,
            title,
            title,
            f"https://example.com/{cluster_id}",
            f"https://example.com/{cluster_id}",
        ],
    )
    con.execute(
        """
        INSERT INTO trend_cluster_ai_profiles(
            cluster_id, canonical_title, display_title, summary,
            verification_points_json, content_plan_json,
            model_name, feature_version, created_at, updated_at
        ) VALUES (?, ?, ?, '요약', '["확인1","확인2","확인3"]',
                  '{"audience":"일반 독자"}', 'gemini-3.6-flash', '6',
                  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [cluster_id, title, f"{title} 정리"],
    )
    score_rows = [
        (1, 90, {"search_intent_fit": 32, "demand_signal_support": 27, "evidence_availability": 18, "differentiation": 9, "timeliness_practicality": 4}),
        (2, 82, {"search_intent_fit": 29, "demand_signal_support": 24, "evidence_availability": 17, "differentiation": 8, "timeliness_practicality": 4}),
        (3, 75, {"search_intent_fit": 27, "demand_signal_support": 22, "evidence_availability": 15, "differentiation": 7, "timeliness_practicality": 4}),
    ]
    for order, score, breakdown in score_rows:
        con.execute(
            """
            INSERT INTO trend_cluster_ai_angles(
                angle_id, cluster_id, canonical_title, angle_order,
                angle_label, angle_text, rationale, search_queries_json,
                search_intent, reader_question, demand_evidence_json,
                evidence_source_ids_json, score_breakdown_json, direction_score,
                score_reasons_json, model_name, feature_version,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, '근거 설명', ?,
                      '정확한 정보를 확인하려는 검색', '무엇을 먼저 확인해야 하나?',
                      '["반복 포착과 관련 검색어가 확인됨"]', ?, ?, ?,
                      '["입력 근거와 검색 질문이 직접 연결됨"]',
                      'gemini-3.6-flash', '6', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            [
                f"angle_{cluster_id}_{order}",
                cluster_id,
                title,
                order,
                f"방향 {order}",
                f"{title} 방향 {order}",
                json.dumps([f"{title} 공식 정보"], ensure_ascii=False),
                json.dumps([source_id], ensure_ascii=False),
                json.dumps(breakdown, ensure_ascii=False),
                score,
            ],
        )


def test_quality_diagnostic_reads_v6_contract_and_exact_runtime_sample(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality.duckdb"
    init_database(db_path)
    config = _config()

    with connect_database(db_path) as con:
        _seed_cluster(con, "complete", "완료 글감", complete=True)
        _seed_cluster(con, "pending", "대기 글감", complete=False)
        record_gemini_api_call(
            con,
            config=config,
            content_pack_id="topic_angle_batch_test",
            request_hash="request-v6",
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

        diagnostic = build_topic_angle_quality_diagnostic(
            con,
            app_id=config.app_id,
            items_per_request=15,
            thinking_level="high",
            timeout_seconds=600,
            min_opportunity_score=50,
        )

    assert diagnostic.status == "표본 수집 중"
    assert diagnostic.contract.cluster_count == 1
    assert diagnostic.contract.complete_cluster_count == 1
    assert diagnostic.contract.direction_count == 3
    assert diagnostic.contract.contract_complete_count == 3
    assert diagnostic.contract.contract_completion_rate == 1.0
    assert diagnostic.contract.evidence_link_rate == 1.0
    assert diagnostic.contract.ordering_issue_count == 0
    assert diagnostic.operation.successful_request_count == 1
    assert diagnostic.operation.requested_items == 15
    assert diagnostic.operation.matching_runtime_request_count == 1
    assert diagnostic.operation.average_generation_tokens == 500
    assert diagnostic.operation.sample_sufficient is False
    assert diagnostic.backlog.eligible_cluster_count == 2
    assert diagnostic.backlog.completed_cluster_count == 1
    assert diagnostic.backlog.pending_cluster_count == 1
    assert diagnostic.backlog.estimated_runs_to_clear == 1
    assert diagnostic.issue_examples == ()


def test_quality_diagnostic_excludes_mismatched_runtime_from_sample(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime-mismatch.duckdb"
    init_database(db_path)
    config = _config()

    with connect_database(db_path) as con:
        record_gemini_api_call(
            con,
            config=config,
            content_pack_id="topic_angle_batch_mismatch",
            request_hash="request-v6-mismatch",
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
            requested_item_count=25,
            configured_items_per_request=25,
            thinking_level="medium",
            request_timeout_seconds=600,
        )
        diagnostic = build_topic_angle_quality_diagnostic(
            con,
            app_id=config.app_id,
            items_per_request=15,
            thinking_level="high",
            timeout_seconds=600,
            min_opportunity_score=50,
        )

    assert diagnostic.operation.successful_request_count == 1
    assert diagnostic.operation.matching_runtime_request_count == 0
    assert diagnostic.operation.requested_items == 0
    assert diagnostic.operation.average_generation_tokens == 0
    assert diagnostic.operation.average_duration_ms == 0
    assert diagnostic.operation.sample_sufficient is False
    assert diagnostic.status == "표본 수집 중"


def test_quality_diagnostic_surfaces_score_order_and_broken_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "issues.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        _seed_cluster(con, "broken", "점검 글감", complete=True)
        con.execute(
            """
            UPDATE trend_cluster_ai_angles
            SET direction_score = 10,
                search_queries_json = '["정보"]',
                evidence_source_ids_json = '["missing-source"]'
            WHERE cluster_id = 'broken' AND angle_order = 1
            """
        )
        diagnostic = build_topic_angle_quality_diagnostic(
            con,
            app_id="content-trend-tracker",
            items_per_request=15,
            thinking_level="high",
            timeout_seconds=600,
            min_opportunity_score=50,
        )

    assert diagnostic.status == "저장 데이터 점검"
    assert diagnostic.contract.score_issue_count == 1
    assert diagnostic.contract.ordering_issue_count == 1
    assert diagnostic.contract.broken_evidence_link_count == 1
    assert diagnostic.contract.short_single_query_count == 1
    assert diagnostic.contract.contract_complete_count == 2
    assert diagnostic.issue_examples
    assert "점수 불일치" in diagnostic.issue_examples[0]["확인 항목"]
