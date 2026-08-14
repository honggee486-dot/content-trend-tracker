from __future__ import annotations

import json
from types import SimpleNamespace

import duckdb

from src.services.trend_candidate_ai_evaluation_service import (
    CandidateEvaluationChunk,
    CandidateEvaluationExecution,
    CandidateEvaluationPreparation,
    _parse_evaluations,
    ensure_trend_candidate_ai_evaluation_schema,
    finalize_prepared_candidate_ai_evaluation,
    get_candidate_ai_evaluation_summary,
    partition_candidate_evaluations,
)


class _Estimator:
    def estimate_characters(self, characters: int) -> int:
        return int(characters)

    def estimate_text(self, text: str) -> int:
        return len(text)


def _candidate(cluster_id: str, *, topic: str | None = None) -> dict[str, object]:
    return {
        "cluster_id": cluster_id,
        "topic": topic or f"주제 {cluster_id}",
        "item_count": 3,
        "independent_evidence_count": 3,
        "source_type_count": 2,
        "publisher_count": 2,
        "source_types": ["naver_news", "naver_blog"],
        "first_seen_at": "2026-08-14T08:00:00",
        "last_seen_at": "2026-08-15T07:00:00",
        "rediscovery_signal": 4.2,
        "evidence": [
            {
                "title": f"근거 {cluster_id}",
                "source_type": "naver_news",
                "publisher": "example",
                "observation_count": 2,
            }
        ],
        "content_hash": f"hash-{cluster_id}",
    }


def _evaluation(cluster_id: str) -> dict[str, object]:
    return {
        "cluster_id": cluster_id,
        "ai_trend_score": 71,
        "ai_opportunity_score": 82,
        "ai_evidence_quality_score": 76,
        "search_value_score": 84,
        "information_value_score": 79,
        "practicality_score": 81,
        "durability_score": 68,
        "fact_check_difficulty_score": 37,
        "recommendation_status": "recommended",
        "reason": "검색 목적과 독립 근거가 비교적 분명합니다.",
        "content_hash": f"hash-{cluster_id}",
        "request_hash": "request-1",
    }


def test_partition_uses_output_safety_item_cap_in_addition_to_token_target() -> None:
    candidates = [_candidate(f"c{index}") for index in range(5)]

    chunks, oversized = partition_candidate_evaluations(
        candidates,
        estimator=_Estimator(),
        target_tokens=1_000_000,
        max_items_per_request=2,
    )

    assert [len(chunk.candidates) for chunk in chunks] == [2, 2, 1]
    assert [chunk.batch_number for chunk in chunks] == [1, 2, 3]
    assert oversized == []
    assert all(chunk.estimated_tokens > 0 for chunk in chunks)


def test_parse_evaluations_accepts_valid_rows_and_reports_missing_ids() -> None:
    requested = {"c1": _candidate("c1"), "c2": _candidate("c2")}
    payload = {
        "evaluations": [
            {
                key: value
                for key, value in _evaluation("c1").items()
                if key not in {"content_hash", "request_hash"}
            }
        ]
    }

    rows, errors = _parse_evaluations(json.dumps(payload, ensure_ascii=False), requested)

    assert len(rows) == 1
    assert rows[0]["cluster_id"] == "c1"
    assert rows[0]["ai_opportunity_score"] == 82
    assert rows[0]["content_hash"] == "hash-c1"
    assert any("1개 cluster_id가 누락" in error for error in errors)


def test_finalize_stores_ai_scores_and_request_usage_without_touching_python_scores() -> None:
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            """
            CREATE TABLE trend_clusters (
                cluster_id VARCHAR PRIMARY KEY,
                trend_score DOUBLE,
                opportunity_score DOUBLE,
                quality_score DOUBLE,
                fact_risk_score DOUBLE
            )
            """
        )
        con.execute(
            "INSERT INTO trend_clusters VALUES ('c1', 61.5, 54.0, 72.0, 6.0)"
        )
        ensure_trend_candidate_ai_evaluation_schema(con)
        candidate = _candidate("c1")
        request_text = "request"
        preparation = CandidateEvaluationPreparation(
            status="ready",
            run_id="run-1",
            candidates=(candidate,),
            chunks=(
                CandidateEvaluationChunk(
                    batch_number=1,
                    candidates=(candidate,),
                    request_text=request_text,
                    estimated_tokens=100,
                ),
            ),
            current_cluster_count=1,
            reused_clusters=0,
            skipped_sensitive_clusters=0,
            oversized_cluster_ids=(),
        )
        execution = CandidateEvaluationExecution(
            preparation=preparation,
            evaluations=(_evaluation("c1"),),
            calls=(
                {
                    "batch_number": 1,
                    "request_hash": "request-1",
                    "request_text": request_text,
                    "response_text": "{}",
                    "requested_item_count": 1,
                    "status": "success",
                    "http_status": 200,
                    "error_type": "",
                    "error_message": "",
                    "input_tokens": 91,
                    "output_tokens": 30,
                    "thought_tokens": 7,
                    "total_tokens": 128,
                    "finish_reason": "STOP",
                    "finish_message": "",
                    "duration_ms": 1500,
                    "estimated_input_tokens": 100,
                    "tpm_wait_seconds": 2.5,
                },
            ),
        )
        config = SimpleNamespace(model="gemini-3.5-flash-lite", timeout_seconds=120)

        result = finalize_prepared_candidate_ai_evaluation(
            con,
            config=config,
            execution=execution,
            record_call=lambda *args, **kwargs: None,
        )

        assert result["status"] == "success"
        assert result["input_tokens"] == 91
        assert result["total_tokens"] == 128
        assert result["tpm_wait_seconds"] == 2.5
        saved = con.execute(
            """
            SELECT ai_trend_score, ai_opportunity_score, ai_evidence_quality_score,
                   recommendation_status, model_name
            FROM trend_cluster_ai_evaluations WHERE cluster_id = 'c1'
            """
        ).fetchone()
        assert saved == (71, 82, 76, "recommended", "gemini-3.5-flash-lite")
        python_scores = con.execute(
            "SELECT trend_score, opportunity_score, quality_score, fact_risk_score FROM trend_clusters WHERE cluster_id = 'c1'"
        ).fetchone()
        assert python_scores == (61.5, 54.0, 72.0, 6.0)
        metric = con.execute(
            """
            SELECT requested_item_count, estimated_input_tokens, input_tokens,
                   output_tokens, thought_tokens, total_tokens, tpm_wait_seconds,
                   duration_ms, status
            FROM trend_candidate_ai_evaluation_request_metrics
            WHERE request_hash = 'request-1'
            """
        ).fetchone()
        assert metric == (1, 100, 91, 30, 7, 128, 2.5, 1500, "success")
    finally:
        con.close()


def test_summary_reports_current_coverage_and_latest_run_usage() -> None:
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE TABLE trend_clusters (cluster_id VARCHAR PRIMARY KEY)")
        con.execute("INSERT INTO trend_clusters VALUES ('c1'), ('c2')")
        ensure_trend_candidate_ai_evaluation_schema(con)
        con.execute(
            """
            INSERT INTO trend_cluster_ai_evaluations VALUES (
                'c1', 70, 80, 75, 82, 79, 81, 68, 37,
                'recommended', 'reason', 'hash', 'model', '1', 'r1', NOW(), NOW()
            )
            """
        )
        con.execute(
            """
            INSERT INTO trend_candidate_ai_evaluation_request_metrics VALUES (
                'r1', 'run-1', 1, 'model', 2, 120, 100, 30, 10, 140,
                3.0, 2500, 'success', 200, '', '', 'STOP', '', NOW()
            )
            """
        )

        summary = get_candidate_ai_evaluation_summary(con)

        assert summary["current_clusters"] == 2
        assert summary["evaluated_clusters"] == 1
        assert summary["latest_run"]["request_count"] == 1
        assert summary["latest_run"]["requested_items"] == 2
        assert summary["latest_run"]["total_tokens"] == 140
    finally:
        con.close()
