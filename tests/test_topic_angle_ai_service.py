from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from src.config import GeminiConfig
from src.database import connect_database, init_database
from src.services import topic_angle_ai_service
from src.services.gemini_service import GeminiHttpError, _ApiErrorInfo
from src.services.topic_angle_ai_service import (
    generate_missing_topic_angles,
    get_cluster_ai_profile,
    list_cluster_ai_angles,
)


def _config(**overrides) -> GeminiConfig:
    values = {
        "api_key": "test-key",
        "model": "gemini-3.6-flash",
        "app_id": "content-trend-tracker",
        "quota_scope_id": "honggee-gemini-main",
        "timeout_seconds": 60,
        "retry_wait_seconds": 2.0,
        "retry_max_wait_seconds": 30.0,
        "topic_angle_timeout_seconds": 360,
        "topic_angle_batch_limit": 25,
        "topic_angle_max_parallel_requests": 4,
        "topic_angle_request_stagger_seconds": 5.0,
        "topic_angle_min_opportunity_score": 50.0,
    }
    values.update(overrides)
    return GeminiConfig(**values)


def _seed_cluster(
    con,
    cluster_id: str,
    title: str,
    score: float,
    opportunity_score: float = 70.0,
) -> None:
    con.execute(
        """
        INSERT INTO trend_clusters(
            cluster_id, canonical_title, trend_score, opportunity_score,
            fact_risk_score, quality_score, rediscovery_score,
            recommendation_status, item_count, source_type_count,
            publisher_count, source_types_json, score_reasons_json,
            quality_reasons_json, first_seen_at, last_seen_at, calculated_at
        ) VALUES (?, ?, ?, ?, 0, 80, 0, 'recommended', 1, 1, 1,
                  '["naver_news"]', '[]', '[]',
                  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [cluster_id, title, score, opportunity_score],
    )
    source_id = f"src_{cluster_id}"
    con.execute(
        """
        INSERT INTO source_items(
            source_item_id, source_type, external_id, raw_title,
            normalized_title, source_url, normalized_url, source_name,
            published_at, observed_at, signal_value, metadata_json,
            first_imported_at, previous_imported_at, last_imported_at,
            observation_count, imported_at
        ) VALUES (?, 'naver_news', ?, ?, ?, ?, ?, '테스트뉴스',
                  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 10, ?,
                  CURRENT_TIMESTAMP, NULL, CURRENT_TIMESTAMP, 1, CURRENT_TIMESTAMP)
        """,
        [
            source_id,
            cluster_id,
            f"{title} 관련 최신 소식",
            title,
            f"https://example.com/{cluster_id}",
            f"https://example.com/{cluster_id}",
            json.dumps({"item_title": f"{title} 관련 최신 소식"}, ensure_ascii=False),
        ],
    )
    con.execute(
        "INSERT INTO trend_cluster_items VALUES (?, ?, CURRENT_TIMESTAMP)",
        [cluster_id, source_id],
    )


def _response_for_request(request_text: str) -> str:
    payload = json.loads(request_text.split("[글감 목록]\n", 1)[1])
    return json.dumps(
        {
            "clusters": [
                {
                    "cluster_id": item["cluster_id"],
                    "display_title": f"{item['topic']} 핵심 정리",
                    "summary": f"{item['topic']} 관련 공개 관심 신호가 확인된 글감입니다.",
                    "content_plan": {
                        "audience": f"{item['topic']} 정보를 찾는 일반 독자",
                        "purpose": f"{item['topic']}의 핵심과 확인할 점을 이해하기 쉽게 정리",
                        "category": "생활 정보",
                        "target_length": 2200,
                        "title_rules": [
                            "핵심 검색 의도를 제목 앞부분에 둔다",
                            "확인되지 않은 결과를 단정하지 않는다",
                        ],
                        "outline": [
                            "도입: 관심이 커진 배경",
                            "핵심 내용",
                            "최근 달라진 점",
                            "실제로 확인할 방법",
                            "정리와 주의사항",
                        ],
                        "forbidden_expressions": [
                            "무조건",
                            "완벽한 해결",
                            "공식 확정",
                        ],
                        "timeliness": {
                            "type": "short_lived",
                            "publish_priority": 4,
                            "freshness_window_hours": 48,
                            "recheck_before_writing": True,
                            "reason": "최신 공식 상태를 작성 전에 다시 확인해야 하는 글감입니다.",
                        },
                        "evidence_plan": {
                            "required_source_types": ["공식 기관 발표", "최신 안내문"],
                            "evidence_gaps": ["정확한 적용 시점 확인 필요"],
                            "official_search_queries": [f"{item['topic']} 공식 발표"],
                        },
                        "primary_direction_reason": "현재 신호만으로 핵심 설명 방향이 가장 안전합니다.",
                    },
                    "verification_points": [
                        f"{item['topic']}의 공식 발표 여부",
                        f"{item['topic']}의 기준 시각과 적용 범위",
                        f"{item['topic']} 관련 최신 상태",
                    ],
                    "directions": [
                        {
                            "label": "핵심 설명",
                            "angle": f"{item['topic']}의 핵심 내용을 독자가 이해하기 쉽게 정리",
                            "rationale": "관련 신호가 반복 확인됐습니다.",
                            "search_queries": [f"{item['topic']} 공식 자료"],
                            "search_intent": f"{item['topic']} 정보를 정확히 이해하려는 검색",
                            "reader_question": f"{item['topic']}에서 가장 먼저 확인할 내용은 무엇인가?",
                            "demand_evidence": ["연결 신호 제목과 발견 검색어에서 관심이 확인됨"],
                            "evidence_source_ids": ["E1"],
                            "score_breakdown": {
                                "search_intent_fit": 32,
                                "demand_signal_support": 27,
                                "evidence_availability": 18,
                                "differentiation": 8,
                                "timeliness_practicality": 4,
                            },
                            "score_reasons": ["검색 질문과 입력 근거가 직접 연결됨"],
                        },
                        {
                            "label": "변화 분석",
                            "angle": f"{item['topic']}에서 최근 달라진 점과 영향을 비교",
                            "rationale": "최신 변화 확인이 필요합니다.",
                            "search_queries": [f"{item['topic']} 최근 변화"],
                            "search_intent": f"{item['topic']} 정보를 정확히 이해하려는 검색",
                            "reader_question": f"{item['topic']}에서 가장 먼저 확인할 내용은 무엇인가?",
                            "demand_evidence": ["연결 신호 제목과 발견 검색어에서 관심이 확인됨"],
                            "evidence_source_ids": ["E1"],
                            "score_breakdown": {
                                "search_intent_fit": 28,
                                "demand_signal_support": 24,
                                "evidence_availability": 17,
                                "differentiation": 9,
                                "timeliness_practicality": 5,
                            },
                            "score_reasons": ["검색 질문과 입력 근거가 직접 연결됨"],
                        },
                        {
                            "label": "실용 정보",
                            "angle": f"{item['topic']}를 실제로 확인하거나 활용하는 방법 정리",
                            "rationale": "검색 독자가 행동으로 옮길 정보가 필요합니다.",
                            "search_queries": [f"{item['topic']} 이용 방법"],
                            "search_intent": f"{item['topic']} 정보를 정확히 이해하려는 검색",
                            "reader_question": f"{item['topic']}에서 가장 먼저 확인할 내용은 무엇인가?",
                            "demand_evidence": ["연결 신호 제목과 발견 검색어에서 관심이 확인됨"],
                            "evidence_source_ids": ["E1"],
                            "score_breakdown": {
                                "search_intent_fit": 30,
                                "demand_signal_support": 22,
                                "evidence_availability": 16,
                                "differentiation": 10,
                                "timeliness_practicality": 5,
                            },
                            "score_reasons": ["검색 질문과 입력 근거가 직접 연결됨"],
                        },
                    ],
                }
                for item in payload["clusters"]
            ]
        },
        ensure_ascii=False,
    )


def test_generates_profile_and_three_angles_per_cluster(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "angles.duckdb"
    init_database(db_path)
    calls = 0

    def fake_call(config, request_text, request_hash, **kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["feature_id"] == "trend_topic_angle_batch_v1"
        assert kwargs["use_google_search"] is False
        assert kwargs["thinking_level"] == "medium"
        assert kwargs["timeout_seconds"] == 360
        return _response_for_request(request_text), 100, 200, 300

    monkeypatch.setattr(topic_angle_ai_service, "call_gemini_structured_output", fake_call)

    with connect_database(db_path) as con:
        _seed_cluster(con, "trend_a", "프로야구 순위", 90)
        _seed_cluster(con, "trend_b", "새로운 AI 서비스", 80)
        first = generate_missing_topic_angles(con, config=_config(), sleep_func=lambda _: None)
        second = generate_missing_topic_angles(con, config=_config(), sleep_func=lambda _: None)
        a_angles = list_cluster_ai_angles(con, "trend_a")
        profile = get_cluster_ai_profile(con, "trend_a")
        log_rows = con.execute(
            "SELECT feature_id, feature_version, status FROM gemini_api_calls ORDER BY created_at"
        ).fetchall()

    assert first.status == "success"
    assert first.requested_clusters == 2
    assert first.generated_clusters == 2
    assert first.generated_angles == 6
    assert first.requested_batches == 1
    assert second.status == "nothing_to_generate"
    assert calls == 1
    assert len(a_angles) == 3
    assert a_angles[0]["direction_score"] >= a_angles[1]["direction_score"]
    assert a_angles[0]["search_intent"]
    assert a_angles[0]["reader_question"]
    assert a_angles[0]["demand_evidence"]
    assert a_angles[0]["evidence_source_ids"] == ["src_trend_a"]
    assert profile is not None
    assert profile["display_title"] == "프로야구 순위 핵심 정리"
    assert profile["feature_version"] == "6"
    assert profile["content_plan"]["audience"] == "프로야구 순위 정보를 찾는 일반 독자"
    assert profile["content_plan"]["target_length"] == 2200
    assert profile["content_plan"]["timeliness"]["publish_priority"] == 4
    assert profile["content_plan"]["evidence_plan"]["evidence_gaps"] == [
        "정확한 적용 시점 확인 필요"
    ]
    assert profile["content_plan"]["primary_direction_reason"].startswith("검증된 방향 점수")
    assert len(profile["content_plan"]["outline"]) == 5
    assert len(profile["verification_points"]) == 3
    assert log_rows == [("trend_topic_angle_batch_v1", "6", "success")]


def test_old_profile_without_content_plan_is_reprocessed(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "content-plan-backfill.duckdb"
    init_database(db_path)
    calls = 0

    def fake_call(config, request_text, request_hash, **kwargs):
        nonlocal calls
        calls += 1
        return _response_for_request(request_text), 10, 20, 30

    monkeypatch.setattr(topic_angle_ai_service, "call_gemini_structured_output", fake_call)
    with connect_database(db_path) as con:
        _seed_cluster(con, "trend_backfill", "기존 분석 글감", 88)
        first = generate_missing_topic_angles(
            con,
            config=_config(),
            sleep_func=lambda _: None,
        )
        con.execute(
            """
            UPDATE trend_cluster_ai_profiles
            SET feature_version = '3', content_plan_json = '{}'
            WHERE cluster_id = 'trend_backfill'
            """
        )
        second = generate_missing_topic_angles(
            con,
            config=_config(),
            sleep_func=lambda _: None,
        )
        profile = get_cluster_ai_profile(con, "trend_backfill")

    assert first.status == "success"
    assert second.status == "success"
    assert second.requested_clusters == 1
    assert calls == 2
    assert profile is not None
    assert profile["feature_version"] == "6"
    assert profile["content_plan"]["target_length"] == 2200


def test_splits_into_needed_parallel_requests_only(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "parallel.duckdb"
    init_database(db_path)
    requested_sizes: list[int] = []
    lock = Lock()

    def fake_call(config, request_text, request_hash, **kwargs):
        payload = json.loads(request_text.split("[글감 목록]\n", 1)[1])
        with lock:
            requested_sizes.append(len(payload["clusters"]))
        return _response_for_request(request_text), 10, 20, 30

    monkeypatch.setattr(topic_angle_ai_service, "call_gemini_structured_output", fake_call)

    with connect_database(db_path) as con:
        for index in range(35):
            _seed_cluster(con, f"trend_{index:02d}", f"테스트 글감 {index:02d}", 100 - index)
        result = generate_missing_topic_angles(
            con,
            config=_config(),
            sleep_func=lambda _: None,
            poll_interval_seconds=0.01,
        )

    assert result.requested_clusters == 35
    assert result.requested_batches == 2
    assert result.completed_batches == 2
    assert result.failed_batches == 0
    assert sorted(requested_sizes) == [10, 25]


def test_records_actual_batch_runtime_conditions_and_stop_reason(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "runtime-metadata.duckdb"
    init_database(db_path)

    def fake_call(config, request_text, request_hash, **kwargs):
        return (
            _response_for_request(request_text),
            100,
            200,
            50,
            350,
            "STOP",
            "",
        )

    monkeypatch.setattr(topic_angle_ai_service, "call_gemini_structured_output", fake_call)
    config = _config(
        topic_angle_batch_limit=25,
        topic_angle_thinking_level="high",
        topic_angle_timeout_seconds=600,
        topic_angle_max_parallel_requests=1,
    )
    with connect_database(db_path) as con:
        for index in range(18):
            _seed_cluster(con, f"trend_{index:02d}", f"테스트 글감 {index:02d}", 100 - index)
        result = generate_missing_topic_angles(
            con,
            config=config,
            sleep_func=lambda _: None,
            poll_interval_seconds=0.01,
        )
        row = con.execute(
            """
            SELECT feature_version, requested_item_count, configured_items_per_request,
                   thinking_level, request_timeout_seconds,
                   finish_reason, finish_message
            FROM gemini_api_calls
            """
        ).fetchone()

    assert result.generated_clusters == 18
    assert row == ("6", 18, 25, "high", 600, "STOP", "")


def test_retry_attempts_preserve_conditions_and_max_tokens_validation_reason(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "retry-runtime-metadata.duckdb"
    init_database(db_path)
    calls = 0

    def fake_call(config, request_text, request_hash, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise GeminiHttpError(
                _ApiErrorInfo(
                    http_status=503,
                    error_type="service_unavailable",
                    message="temporary outage",
                    retryable=True,
                    retry_delay_seconds=0,
                )
            )
        return '{"clusters":[]}', 100, 200, 50, 350, "MAX_TOKENS", "x" * 1200

    monkeypatch.setattr(topic_angle_ai_service, "call_gemini_structured_output", fake_call)
    config = _config(
        retry_wait_seconds=0,
        retry_max_wait_seconds=1,
        topic_angle_batch_limit=25,
        topic_angle_thinking_level="high",
        topic_angle_timeout_seconds=600,
        topic_angle_max_parallel_requests=1,
    )
    with connect_database(db_path) as con:
        for index in range(18):
            _seed_cluster(con, f"trend_{index:02d}", f"재시도 글감 {index:02d}", 100 - index)
        result = generate_missing_topic_angles(
            con,
            config=config,
            sleep_func=lambda _: None,
            poll_interval_seconds=0.01,
        )
        rows = con.execute(
            """
            SELECT feature_version, status, error_type, requested_item_count,
                   configured_items_per_request, thinking_level,
                   request_timeout_seconds, finish_reason,
                   LENGTH(finish_message)
            FROM gemini_api_calls
            ORDER BY attempt_number
            """
        ).fetchall()

    assert result.status == "response_validation_error"
    assert rows == [
        ("6", "retrying", "service_unavailable", 18, 25, "high", 600, "", 0),
        (
            "6",
            "response_validation_error",
            "response_validation_error",
            18,
            25,
            "high",
            600,
            "MAX_TOKENS",
            1000,
        ),
    ]


def test_limits_one_run_to_items_per_request_times_parallel_count(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "limited.duckdb"
    init_database(db_path)

    def fake_call(config, request_text, request_hash, **kwargs):
        return _response_for_request(request_text), 10, 20, 30

    monkeypatch.setattr(topic_angle_ai_service, "call_gemini_structured_output", fake_call)
    with connect_database(db_path) as con:
        for index in range(105):
            _seed_cluster(con, f"trend_{index:02d}", f"테스트 글감 {index:02d}", 100 - index)
        result = generate_missing_topic_angles(
            con,
            config=_config(),
            sleep_func=lambda _: None,
            poll_interval_seconds=0.01,
        )
        remaining = con.execute(
            """
            SELECT COUNT(*) FROM trend_clusters tc
            LEFT JOIN trend_cluster_ai_profiles p ON p.cluster_id = tc.cluster_id
            WHERE p.cluster_id IS NULL
            """
        ).fetchone()[0]

    assert result.requested_clusters == 100
    assert result.requested_batches == 4
    assert result.generated_clusters == 100
    assert remaining == 5



def test_skips_clusters_below_minimum_opportunity_score(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "minimum-score.duckdb"
    init_database(db_path)
    requested_ids: list[str] = []

    def fake_call(config, request_text, request_hash, **kwargs):
        payload = json.loads(request_text.split("[글감 목록]\n", 1)[1])
        requested_ids.extend(item["cluster_id"] for item in payload["clusters"])
        return _response_for_request(request_text), 10, 20, 30

    monkeypatch.setattr(topic_angle_ai_service, "call_gemini_structured_output", fake_call)
    with connect_database(db_path) as con:
        _seed_cluster(
            con,
            "high_opportunity",
            "트렌드는 낮아도 글감 기회가 높은 후보",
            44.0,
            opportunity_score=60.0,
        )
        _seed_cluster(
            con,
            "low_opportunity",
            "트렌드는 높아도 글감 기회가 낮은 후보",
            90.0,
            opportunity_score=49.9,
        )
        result = generate_missing_topic_angles(
            con,
            config=_config(topic_angle_min_opportunity_score=50),
            sleep_func=lambda _: None,
            poll_interval_seconds=0.01,
        )
        low_profile = get_cluster_ai_profile(con, "low_opportunity")

    assert result.requested_clusters == 1
    assert requested_ids == ["high_opportunity"]
    assert low_profile is None
    assert result.min_opportunity_score == 50

def test_progress_reports_elapsed_and_timeout(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "progress.duckdb"
    init_database(db_path)
    messages: list[str] = []

    def fake_call(config, request_text, request_hash, **kwargs):
        return _response_for_request(request_text), 10, 20, 30

    monkeypatch.setattr(topic_angle_ai_service, "call_gemini_structured_output", fake_call)
    with connect_database(db_path) as con:
        _seed_cluster(con, "trend_a", "프로야구 순위", 90)
        generate_missing_topic_angles(
            con,
            config=_config(),
            progress_callback=lambda value, message: messages.append(message),
            sleep_func=lambda _: None,
            poll_interval_seconds=0.01,
        )

    assert any("전체 경과" in message for message in messages)
    assert any("최대 06:00" in message for message in messages)


def test_missing_key_skips_without_modifying_database(tmp_path: Path) -> None:
    db_path = tmp_path / "missing-key.duckdb"
    init_database(db_path)
    config = _config(api_key="")
    with connect_database(db_path) as con:
        _seed_cluster(con, "trend_a", "프로야구 순위", 90)
        result = generate_missing_topic_angles(con, config=config)
        count = con.execute("SELECT COUNT(*) FROM trend_cluster_ai_angles").fetchone()[0]
    assert result.status == "missing_api_key"
    assert count == 0
