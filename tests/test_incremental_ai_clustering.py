from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.config import GeminiConfig
from src.database import connect_database, get_setting, init_database, set_setting
from src.services import trend_discovery_service as service
from src.services.topic_service import add_manual_topic, upsert_source_signal
from src.services.trend_cluster_ai_review_service import ClusterGroupingExecution


def _config() -> GeminiConfig:
    return GeminiConfig(
        api_key="test-key",
        model="gemini-3.5-flash-lite",
        app_id="content-trend-tracker",
        quota_scope_id="test",
        timeout_seconds=60,
        retry_wait_seconds=1.0,
        retry_max_wait_seconds=10.0,
        topic_angle_timeout_seconds=600,
        topic_angle_batch_limit=15,
        topic_angle_max_parallel_requests=1,
        topic_angle_request_stagger_seconds=0.0,
        topic_angle_min_opportunity_score=50.0,
        daily_request_reference_limit=500,
        draft_thinking_level="minimal",
        topic_angle_thinking_level="medium",
    )


def _signal(index: int, *, title: str | None = None) -> dict:
    item_title = title or f"2단계 군집 테스트 주제 {index:04d}"
    observed = datetime.now() - timedelta(minutes=index)
    return {
        "source_type": "naver_news",
        "external_id": f"two-stage-{index:04d}",
        "title": item_title,
        "source_name": f"publisher-{index % 7}.example",
        "source_url": f"https://example.com/two-stage/{index:04d}",
        "published_at": observed,
        "observed_at": observed,
        "metadata": {
            "item_title": item_title,
            "discovery_query": item_title,
        },
    }


def _prepared_item(source_id: str, title: str) -> dict:
    now = datetime.now()
    tokens = service.identity_tokens(title)
    return {
        "source_item_id": source_id,
        "source_type": "naver_news",
        "canonical_title": title,
        "raw_title": title,
        "item_title": title,
        "normalized_title": service.normalize_title(title),
        "compact_title": service.compact_title(title),
        "identity_tokens": tokens,
        "editorial_identity_tokens": service._editorial_identity_tokens(tokens),
        "calendar_identity_tokens": service._calendar_identity_tokens(tokens),
        "tokens": set(service._tokens(title)),
        "normalized_url": f"https://example.com/{source_id}",
        "source_name": "publisher.example",
        "domain": "example.com",
        "query": title,
        "query_supported": True,
        "published_at": now,
        "observed_at": now,
        "imported_at": now,
        "metadata": {},
        "signal_value": 1,
        "observation_count": 1,
    }


def test_two_stage_schema_and_trial_defaults(tmp_path: Path) -> None:
    db_path = tmp_path / "two-stage-schema.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        tables = {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}
        assert {
            "trend_cluster_processing",
            "trend_clustering_jobs",
            "trend_clustering_job_batches",
        } <= tables
        assert get_setting(con, service.AI_CLUSTERING_MAX_ITEMS_SETTING) == "4000"
        assert get_setting(con, service.AI_CLUSTERING_BATCH_SIZE_SETTING) == "200"
        assert get_setting(con, service.AI_CLUSTERING_MAX_BATCHES_SETTING) == "5"


def test_prepare_scans_recent_unprocessed_items_and_preserves_user_topic(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "two-stage-prepare.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        set_setting(con, service.AI_CLUSTERING_MAX_ITEMS_SETTING, "200")
        topic_id, _created = add_manual_topic(
            con,
            title="사용자가 편집한 기존 글감",
            summary="군집 작업이 바꾸면 안 되는 글감",
        )
        for index in range(205):
            upsert_source_signal(con, _signal(index), create_topic=False)
        source_ids = {
            str(external_id): str(source_item_id)
            for external_id, source_item_id in con.execute(
                "SELECT external_id, source_item_id FROM source_items"
            ).fetchall()
        }
        preparation = service.prepare_trend_ranking_rebuild(
            con,
            lookback_hours=400,
        )
        title = con.execute(
            "SELECT title FROM topics WHERE topic_id = ?",
            [topic_id],
        ).fetchone()[0]

    assert preparation.pending_item_count == 205
    assert len(preparation.selected_source_item_ids) == 200
    assert source_ids["two-stage-0000"] in preparation.selected_source_item_ids
    assert source_ids["two-stage-0204"] not in preparation.selected_source_item_ids
    assert title == "사용자가 편집한 기존 글감"


def test_calculation_never_opens_database_and_uncertain_moves_to_review_after_three(
    monkeypatch,
) -> None:
    item = _prepared_item("source-a", "삼성전자 신규 공장 투자")

    def forbidden_connection(*args, **kwargs):
        raise AssertionError("Gemini 군집 계산 중 DuckDB를 열면 안 됩니다.")

    monkeypatch.setattr(service, "connect_database", forbidden_connection)
    monkeypatch.setattr(service, "get_gemini_config", _config)
    monkeypatch.setattr(
        service,
        "classify_cluster_batch",
        lambda *args, **kwargs: ClusterGroupingExecution(
            status="uncertain",
            assignments=(
                {
                    "candidate_id": args[1][0]["candidate_id"],
                    "decision": "uncertain",
                    "existing_cluster_id": "",
                    "new_group_id": "",
                    "representative_title": "",
                    "confidence": 40,
                },
            ),
            calls=(),
            requested_candidates=1,
            completed_candidates=0,
            uncertain_candidates=1,
            error_message="판단 불확실",
        ),
    )

    statuses = []
    for previous_attempts in (0, 1, 2):
        preparation = service.TrendRankingPreparation(
            status="ready",
            items=(item,),
            signature=f"signature-{previous_attempts}",
            source_item_count=1,
            existing_cluster_count=0,
            started_at=0.0,
            ai_clustering_enabled=True,
            ai_clustering_model="gemini-3.5-flash-lite",
            ai_clustering_max_items=4000,
            ai_clustering_batch_size=200,
            ai_clustering_max_batches=5,
            ai_clustering_api_key_configured=True,
            existing_clusters=(),
            selected_source_item_ids=("source-a",),
            pending_item_count=1,
            processing_attempts=(("source-a", previous_attempts),),
            processing_feature_id="trend_cluster_grouping_v3",
            processing_feature_version="3",
            processing_model="gemini-3.5-flash-lite",
            processing_hash_prefix="",
        )
        calculation = service.calculate_prepared_trend_rankings(preparation)
        statuses.append(calculation.processing_rows[0]["status"])
        assert calculation.processing_rows[0]["attempt_count"] == previous_attempts + 1

    assert statuses == ["retry", "retry", "needs_review"]


def test_successful_assignment_is_marked_processed_and_all_items_remain_visible(
    monkeypatch,
) -> None:
    first = _prepared_item("a", "제1235회 로또 당첨번호 발표")
    second = _prepared_item("b", "제1235회 로또 당첨번호 발표")
    unselected = _prepared_item("c", "전혀 다른 최신 정책 안내")
    monkeypatch.setattr(service, "get_gemini_config", _config)

    def grouping(_config_value, candidates, **kwargs):
        candidate = candidates[0]
        return ClusterGroupingExecution(
            status="success",
            assignments=(
                {
                    "candidate_id": candidate["candidate_id"],
                    "decision": "new",
                    "existing_cluster_id": "",
                    "new_group_id": "lotto-1235",
                    "representative_title": "제1235회 로또 당첨번호 발표",
                    "confidence": 98,
                },
            ),
            calls=(),
            requested_candidates=1,
            completed_candidates=1,
            uncertain_candidates=0,
        )

    monkeypatch.setattr(service, "classify_cluster_batch", grouping)
    preparation = service.TrendRankingPreparation(
        status="ready",
        items=(first, second, unselected),
        signature="signature",
        source_item_count=3,
        existing_cluster_count=0,
        started_at=0.0,
        ai_clustering_enabled=True,
        ai_clustering_model="gemini-3.5-flash-lite",
        ai_clustering_max_items=4000,
        ai_clustering_batch_size=1,
        ai_clustering_max_batches=5,
        ai_clustering_api_key_configured=True,
        selected_source_item_ids=("a", "b", "c"),
        pending_item_count=3,
        processing_feature_id="trend_cluster_grouping_v3",
        processing_feature_version="3",
        processing_model="gemini-3.5-flash-lite",
        processing_hash_prefix="",
    )

    calculation = service.calculate_prepared_trend_rankings(preparation)

    assert calculation.ai_clustering["processed_items"] == 2
    assert calculation.ai_clustering["remaining_items"] == 1
    assert {row["source_item_id"] for row in calculation.processing_rows} == {"a", "b"}
    assert {row["status"] for row in calculation.processing_rows} == {"processed"}
    assert {row["source_item_id"] for row in calculation.cluster_item_rows} == {
        "a",
        "b",
        "c",
    }


def test_dashboard_starts_detached_job_instead_of_waiting_for_gemini() -> None:
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "create_clustering_job(con, launcher=\"dashboard\")" in source
    assert "launch_clustering_job(" in source
    assert "2단계 군집 작업을 백그라운드에서 시작했습니다" in source
    assert "최근 2단계 군집 작업" in source
