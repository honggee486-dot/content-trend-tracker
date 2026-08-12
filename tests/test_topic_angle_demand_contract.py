from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.config import GeminiConfig
from src.database import connect_database, init_database
from src.services.topic_angle_ai_service import _build_request, list_cluster_ai_angles
from src.services.topic_angle_demand_contract import (
    build_evidence_contract,
    stable_score_sort,
    validate_direction_contract,
)


def _safe(value, _field: str) -> str:
    return str(value or "").strip()


def _direction(*, evidence_id: str = "E1", intent_score: int = 30) -> dict:
    return {
        "label": "핵심 설명",
        "angle": "독자가 궁금한 핵심을 근거와 함께 설명",
        "rationale": "발견 검색어와 반복 신호가 직접 연결됩니다.",
        "search_queries": ["공식 자료 최신"],
        "search_intent": "현재 상황과 적용 범위를 정확히 이해하려는 검색",
        "reader_question": "지금 실제로 무엇이 달라졌고 어디까지 적용되는가?",
        "demand_evidence": ["같은 질문이 발견 검색어와 제목에서 반복됨"],
        "evidence_source_ids": [evidence_id],
        "score_breakdown": {
            "search_intent_fit": intent_score,
            "demand_signal_support": 25,
            "evidence_availability": 18,
            "differentiation": 8,
            "timeliness_practicality": 4,
        },
        "score_reasons": ["입력 신호와 독자 질문이 직접 연결됨"],
    }


def test_evidence_payload_excludes_internal_ids_and_urls() -> None:
    rows, evidence_map = build_evidence_contract(
        [
            {
                "source_item_id": "source-secret-id",
                "source_type": "naver_news",
                "raw_title": "정책 변경 핵심",
                "source_url": "https://example.com/private-path",
                "source_name": "테스트뉴스",
                "published_at": datetime(2026, 8, 2, 1, 0),
                "signal_value": 120,
                "observation_count": 3,
                "metadata": {
                    "item_title": "정책 변경 핵심",
                    "description": "변경 배경과 적용 시점을 설명합니다.",
                    "discovery_query": "정책 변경 적용 시점",
                    "result_rank": 2,
                },
            }
        ],
        safe_public_text=_safe,
    )

    assert evidence_map == {"E1": "source-secret-id"}
    assert rows[0]["evidence_id"] == "E1"
    assert rows[0]["discovery_query"] == "정책 변경 적용 시점"
    assert rows[0]["observation_count"] == 3
    assert rows[0]["signal_value"] == 120
    serialized = json.dumps(rows, ensure_ascii=False)
    assert "source-secret-id" not in serialized
    assert "https://" not in serialized
    assert "search_volume" not in serialized


def test_direction_score_is_python_sum_and_sort_is_stable() -> None:
    first, error = validate_direction_contract(
        _direction(intent_score=25), evidence_map={"E1": "source-1"}
    )
    second, error2 = validate_direction_contract(
        _direction(intent_score=35), evidence_map={"E1": "source-1"}
    )
    tied, error3 = validate_direction_contract(
        _direction(intent_score=35), evidence_map={"E1": "source-1"}
    )
    assert not error and not error2 and not error3
    assert first is not None and second is not None and tied is not None
    assert second["direction_score"] == 90
    ordered = stable_score_sort([first, second, tied])
    assert ordered == [second, tied, first]
    assert ordered[0]["evidence_source_ids"] == ["source-1"]


def test_unrequested_evidence_id_is_rejected() -> None:
    normalized, error = validate_direction_contract(
        _direction(evidence_id="E9"), evidence_map={"E1": "source-1"}
    )
    assert normalized is None
    assert "요청에 없는 근거 ID" in error


def test_request_contains_public_demand_signals_only() -> None:
    config = GeminiConfig(
        api_key="test",
        model="gemini-3.6-flash",
        app_id="content-trend-tracker",
        quota_scope_id="test",
        timeout_seconds=60,
        retry_wait_seconds=2,
        retry_max_wait_seconds=30,
    )
    request_text, _request_hash = _build_request(
        config,
        [
            {
                "cluster_id": "cluster-1",
                "topic": "정책 변경",
                "trend_score": 80,
                "opportunity_score": 90,
                "signals": [
                    {
                        "evidence_id": "E1",
                        "title": "정책 변경 적용 시점",
                        "discovery_query": "정책 변경 언제",
                        "observation_count": 2,
                    }
                ],
                "evidence_source_map": {"E1": "source-internal-id"},
            }
        ],
    )
    assert '"evidence_id": "E1"' in request_text
    assert '"discovery_query": "정책 변경 언제"' in request_text
    assert "source-internal-id" not in request_text
    assert "검색량을 만들거나 추정하지 마세요" in request_text


def test_old_angle_rows_remain_readable_after_additive_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "angles.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        columns = {
            row[1]
            for row in con.execute(
                "PRAGMA table_info('trend_cluster_ai_angles')"
            ).fetchall()
        }
        assert {
            "search_intent",
            "reader_question",
            "demand_evidence_json",
            "evidence_source_ids_json",
            "score_breakdown_json",
            "direction_score",
            "score_reasons_json",
        } <= columns
        now = datetime.now()
        con.execute(
            """
            INSERT INTO trend_cluster_ai_angles(
                angle_id, cluster_id, canonical_title, angle_order,
                angle_label, angle_text, rationale, search_queries_json,
                model_name, feature_version, created_at, updated_at
            ) VALUES ('old-angle', 'old-cluster', '기존 글감', 1,
                      '핵심 설명', '기존 방향', '기존 이유', '["기존 검색어"]',
                      'old-model', '5', ?, ?)
            """,
            [now, now],
        )
        rows = list_cluster_ai_angles(con, "old-cluster")

    assert len(rows) == 1
    assert rows[0]["direction_score"] is None
    assert rows[0]["search_intent"] is None
    assert rows[0]["demand_evidence"] == []
    assert rows[0]["score_breakdown"] == {}
