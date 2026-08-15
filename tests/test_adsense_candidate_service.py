from __future__ import annotations

import json

import duckdb

from src.services.adsense_candidate_service import (
    ADSENSE_INITIAL_AVOID,
    ADSENSE_INITIAL_FIT,
    ADSENSE_INITIAL_REVIEW,
    assess_initial_adsense_candidate,
    build_adsense_candidate_assessments,
)


def test_helpful_policy_guide_is_initial_adsense_fit() -> None:
    result = assess_initial_adsense_candidate(
        {
            "주제": "정책대출 결혼 페널티 폐지 후 소득 기준과 신청 조건 정리",
            "콘텐츠품질": 68.0,
            "사실위험": 4.0,
            "서로다른출처": 4,
            "출처종류": 3,
        }
    )

    assert result["label"] == ADSENSE_INITIAL_FIT
    assert "승인" in result["reason"]


def test_fast_expiring_weather_topic_never_becomes_initial_fit() -> None:
    result = assess_initial_adsense_candidate(
        {
            "주제": "15호 태풍 경로와 동해안 영향 총정리",
            "콘텐츠품질": 85.0,
            "사실위험": 2.0,
            "서로다른출처": 10,
            "출처종류": 4,
        }
    )

    assert result["label"] == ADSENSE_INITIAL_REVIEW
    assert "시점 의존" in result["reason"]


def test_shocking_violence_topic_is_conservatively_avoided_before_review() -> None:
    result = assess_initial_adsense_candidate(
        {
            "주제": "트럼프 암살 위협 관련 보안 이동 경로",
            "콘텐츠품질": 90.0,
            "사실위험": 1.0,
            "서로다른출처": 8,
            "출처종류": 4,
        }
    )

    assert result["label"] == ADSENSE_INITIAL_AVOID
    assert "정책 위반 확정" in result["reason"]


def test_ai_profile_context_can_make_generic_cluster_more_helpful() -> None:
    con = duckdb.connect(":memory:")
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
        "INSERT INTO trend_cluster_ai_profiles VALUES (?, ?, ?, ?)",
        [
            "policy-1",
            "정책대출 변경 후 신청 기준 정리",
            "바뀐 소득 조건과 확인 절차를 독자 관점에서 설명합니다.",
            json.dumps({"category": "생활·정책"}, ensure_ascii=False),
        ],
    )

    assessments = build_adsense_candidate_assessments(
        con,
        [
            {
                "cluster_id": "policy-1",
                "주제": "정책대출 변경",
                "콘텐츠품질": 65.0,
                "사실위험": 4.0,
                "서로다른출처": 3,
                "출처종류": 2,
            }
        ],
    )

    assert assessments["policy-1"]["label"] == ADSENSE_INITIAL_FIT
