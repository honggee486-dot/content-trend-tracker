from __future__ import annotations

from src.services.trend_candidate_ai_evaluation_ui_runtime import (
    build_ai_evaluation_comparison,
)


def test_ai_evaluation_comparison_keeps_python_and_ai_scores_separate() -> None:
    rows = build_ai_evaluation_comparison(
        {
            "trend_score": 61.5,
            "opportunity_score": 54.0,
            "quality_score": 72.0,
            "fact_risk_score": 6.0,
        },
        {
            "ai_trend_score": 71,
            "ai_opportunity_score": 82,
            "ai_evidence_quality_score": 76,
            "fact_check_difficulty_score": 37,
        },
    )

    assert rows == [
        ("트렌드", "데이터 61.5", "AI 71"),
        ("글감기회", "데이터 54.0", "AI 82"),
        ("자료완성도", "데이터 72.0", "AI 76"),
        ("사실확인", "데이터 위험 6.0/30", "AI 난이도 37/100"),
    ]
