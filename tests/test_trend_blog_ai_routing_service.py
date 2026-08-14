from __future__ import annotations

import json
from types import SimpleNamespace

import duckdb

from src.config import GeminiConfig
from src.services.gemini_service import GeminiHttpError
from src.services.trend_blog_ai_routing_service import (
    BlogRoutingPreparation,
    ensure_trend_blog_ai_route_schema,
    execute_prepared_blog_routing,
    finalize_prepared_blog_routing,
    partition_blog_route_candidates,
    prepare_trend_blog_ai_routing,
)
from src.services.trend_blog_recommendation_service import (
    build_trend_blog_recommendation_labels,
    set_recommendation_display_name,
)
from src.services.trend_cluster_token_runtime import (
    AdaptiveInputTokenEstimator,
    SlidingWindowTpmLimiter,
)


def _config(model: str = "gemini-3.5-flash-lite") -> GeminiConfig:
    return GeminiConfig(
        api_key="test-key",
        model=model,
        app_id="content-trend-tracker-test",
        quota_scope_id="test-scope",
        timeout_seconds=60,
        retry_wait_seconds=1.0,
        retry_max_wait_seconds=0.0,
    )


def _candidate(index: int, *, summary_size: int = 0) -> dict[str, object]:
    candidate = {
        "cluster_id": f"cluster-{index}",
        "title": f"생활 정보 후보 {index}",
        "display_title": "",
        "summary": "가" * summary_size,
        "category": "생활정보",
        "source_titles": [f"지원 제도 안내 {index}"],
        "source_types": ["naver_news"],
    }
    import hashlib

    candidate["content_hash"] = hashlib.sha256(
        json.dumps(candidate, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return candidate


def _routing_connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE app_settings (
            setting_key VARCHAR PRIMARY KEY,
            setting_value VARCHAR,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE trend_clusters (
            cluster_id VARCHAR PRIMARY KEY,
            canonical_title VARCHAR,
            recommendation_status VARCHAR,
            opportunity_score DOUBLE,
            trend_score DOUBLE,
            last_seen_at TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE TABLE trend_cluster_ai_profiles (
            cluster_id VARCHAR PRIMARY KEY,
            display_title VARCHAR,
            summary VARCHAR,
            content_plan_json VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE trend_cluster_items (
            cluster_id VARCHAR,
            source_item_id VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE source_items (
            source_item_id VARCHAR PRIMARY KEY,
            raw_title VARCHAR,
            source_type VARCHAR,
            signal_value DOUBLE,
            published_at TIMESTAMP,
            observed_at TIMESTAMP,
            imported_at TIMESTAMP
        )
        """
    )
    return con


def test_partition_fills_token_budget_then_moves_remaining_candidates() -> None:
    estimator = AdaptiveInputTokenEstimator(tokens_per_character=1.0)
    candidates = [_candidate(index, summary_size=900) for index in range(1, 31)]

    chunks, oversized = partition_blog_route_candidates(
        candidates,
        estimator=estimator,
        target_tokens=8_000,
    )

    assert not oversized
    assert len(chunks) > 1
    flattened = [
        candidate["cluster_id"]
        for chunk in chunks
        for candidate in chunk.candidates
    ]
    assert flattened == [candidate["cluster_id"] for candidate in candidates]
    assert all(chunk.estimated_tokens <= 8_000 for chunk in chunks)


def test_execution_keeps_completed_batch_when_later_batch_fails() -> None:
    estimator = AdaptiveInputTokenEstimator(tokens_per_character=1.0)
    limiter = SlidingWindowTpmLimiter(limit=1_000_000)
    candidates = [_candidate(index, summary_size=900) for index in range(1, 15)]
    chunks, oversized = partition_blog_route_candidates(
        candidates,
        estimator=estimator,
        target_tokens=8_000,
    )
    assert not oversized
    assert len(chunks) >= 2
    preparation = BlogRoutingPreparation(
        "ready",
        tuple(candidates),
        tuple(chunks),
        0,
        0,
        (),
    )
    calls = 0

    def fake_api_call(_config, request_text, _request_hash, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise GeminiHttpError(
                SimpleNamespace(
                    message="temporary busy",
                    http_status=500,
                    error_type="service_unavailable",
                    finish_reason="",
                    finish_message="",
                )
            )
        marker = request_text.index('{"channels"')
        payload = json.loads(request_text[marker:])
        response = {
            "routes": [
                {
                    "cluster_id": item["cluster_id"],
                    "strategy_code": "blogger_life",
                    "confidence": 92,
                    "reason": "생활 기준과 절차를 확인하는 정보",
                }
                for item in payload["candidates"]
            ]
        }
        return json.dumps(response, ensure_ascii=False), 5_000, 300, 0, 5_300, "STOP", ""

    execution = execute_prepared_blog_routing(
        preparation,
        config=_config(),
        api_call=fake_api_call,
        estimator=estimator,
        limiter=limiter,
    )

    assert calls == len(chunks)
    assert execution.calls[0]["status"] == "success"
    assert execution.calls[1]["status"] == "failed"
    assert len(execution.routes) < len(candidates)
    assert len(execution.routes) > 0


def test_prepare_reuses_same_model_version_and_content_without_api_work() -> None:
    con = _routing_connection()
    con.execute(
        """
        INSERT INTO trend_clusters VALUES
            ('life-1', '정부 지원금 신청 자격', 'review', 70, 65, CURRENT_TIMESTAMP)
        """
    )
    con.execute(
        """
        INSERT INTO source_items VALUES
            ('source-1', '정부 지원금 신청 기준과 대상', 'naver_news', 10,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
    )
    con.execute("INSERT INTO trend_cluster_items VALUES ('life-1', 'source-1')")

    first = prepare_trend_blog_ai_routing(con, config=_config())
    assert first.status == "ready"
    assert len(first.candidates) == 1
    ensure_trend_blog_ai_route_schema(con)
    candidate = first.candidates[0]
    con.execute(
        """
        INSERT INTO trend_blog_ai_routes VALUES
            (?, 'blogger_life', 95, '생활 제도', ?, ?, '1', 'hash',
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [candidate["cluster_id"], candidate["content_hash"], _config().model],
    )

    second = prepare_trend_blog_ai_routing(con, config=_config())

    assert second.status == "nothing_to_route"
    assert second.reused_clusters == 1
    assert second.candidates == ()
    assert second.chunks == ()


def test_saved_gemini_route_overrides_local_keyword_fallback() -> None:
    con = _routing_connection()
    ensure_trend_blog_ai_route_schema(con)
    set_recommendation_display_name(con, "blogger_life", "생활자료")
    set_recommendation_display_name(con, "blogger_current", "요즘화제")
    con.execute(
        """
        INSERT INTO trend_blog_ai_routes VALUES
            ('route-1', 'blogger_life', 91, '생활 절차 중심', 'content',
             'gemini-3.5-flash-lite', '1', 'request',
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
    )

    labels = build_trend_blog_recommendation_labels(
        con,
        [{"cluster_id": "route-1", "주제": "오늘 발표된 새로운 검색 주제"}],
    )

    assert labels == {"route-1": "B:생활자료"}


def test_finalize_saves_valid_routes_without_requiring_live_api_log_table() -> None:
    con = _routing_connection()
    candidate = _candidate(1)
    chunks, _ = partition_blog_route_candidates([candidate])
    preparation = BlogRoutingPreparation(
        "ready", (candidate,), tuple(chunks), 0, 0, ()
    )
    execution = SimpleNamespace(
        preparation=preparation,
        routes=(
            {
                "cluster_id": candidate["cluster_id"],
                "strategy_code": "blogger_tech",
                "confidence": 88,
                "reason": "디지털 도구 사용 목적",
                "content_hash": candidate["content_hash"],
                "request_hash": "request-hash",
            },
        ),
        calls=(),
    )

    result = finalize_prepared_blog_routing(
        con,
        config=_config(),
        execution=execution,
        record_call=lambda *_args, **_kwargs: None,
    )

    assert result["status"] == "success"
    assert result["routed_clusters"] == 1
    row = con.execute(
        "SELECT strategy_code, confidence, model_name FROM trend_blog_ai_routes"
    ).fetchone()
    assert row == ("blogger_tech", 88, "gemini-3.5-flash-lite")
