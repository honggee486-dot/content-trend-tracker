from __future__ import annotations

from pathlib import Path

from src.services.trend_cluster_persistence_safety_service import (
    finalize_prepared_trend_rankings_safely,
    normalize_trend_ranking_calculation,
)
from src.services.trend_discovery_service import (
    TrendRankingCalculation,
    TrendRankingPreparation,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _calculation() -> TrendRankingCalculation:
    preparation = TrendRankingPreparation(
        status="ready",
        items=(),
        signature="test-signature",
        source_item_count=0,
        existing_cluster_count=0,
        started_at=0.0,
    )
    shared = {
        "cluster_id": "trend_duplicate",
        "canonical_title": "중복 군집",
        "trend_score": 60.0,
        "opportunity_score": 60.0,
        "fact_risk_score": 10.0,
        "quality_score": 70.0,
        "rediscovery_score": 0.0,
        "recommendation_status": "review",
        "source_type_count": 1,
        "publisher_count": 1,
        "source_types_json": "[]",
        "score_reasons_json": "[]",
        "quality_reasons_json": "[]",
        "first_seen_at": None,
        "last_seen_at": None,
        "calculated_at": None,
    }
    return TrendRankingCalculation(
        preparation=preparation,
        cluster_rows=(
            {**shared, "item_count": 1},
            {**shared, "canonical_title": "더 큰 중복 군집", "item_count": 2},
        ),
        cluster_item_rows=(
            {
                "cluster_id": "trend_duplicate",
                "source_item_id": "source-a",
                "linked_at": None,
            },
            {
                "cluster_id": "trend_duplicate",
                "source_item_id": "source-a",
                "linked_at": None,
            },
            {
                "cluster_id": "trend_duplicate",
                "source_item_id": "source-b",
                "linked_at": None,
            },
        ),
        analysis_seconds=0.0,
        ai_clustering={"status": "success"},
        batch_log={"status": "success"},
    )


def test_duplicate_cluster_rows_and_links_are_collapsed_before_storage() -> None:
    calculation = _calculation()

    normalized = normalize_trend_ranking_calculation(calculation)

    assert len(normalized.cluster_rows) == 1
    assert normalized.cluster_rows[0]["canonical_title"] == "더 큰 중복 군집"
    assert normalized.cluster_rows[0]["item_count"] == 2
    assert len(normalized.cluster_item_rows) == 2
    assert normalized.ai_clustering["persistence_deduplicated"] is True
    assert normalized.ai_clustering["duplicate_cluster_rows_collapsed"] == 1
    assert normalized.ai_clustering["duplicate_cluster_item_rows_collapsed"] == 1
    assert "중복 군집 행 1개" in normalized.batch_log["persistence_warning"]
    assert "error_message" not in normalized.batch_log
    assert len(calculation.cluster_rows) == 2


def test_safe_finalizer_receives_only_unique_primary_keys() -> None:
    captured = {}

    def finalizer(con, calculation):
        captured["calculation"] = calculation
        return {"status": "stored"}

    result = finalize_prepared_trend_rankings_safely(
        object(),
        _calculation(),
        finalizer=finalizer,
    )

    assert result == {"status": "stored"}
    normalized = captured["calculation"]
    assert len(normalized.cluster_rows) == 1
    assert len(normalized.cluster_item_rows) == 2


def test_background_worker_installs_safe_finalizer() -> None:
    source = (PROJECT_ROOT / "scripts" / "process_cluster_backlog.py").read_text(
        encoding="utf-8"
    )

    assert "finalize_prepared_trend_rankings_safely" in source
    assert "clustering_jobs.finalize_prepared_trend_rankings" in source


def test_scheduled_refresh_uses_same_safe_persistence_contract() -> None:
    launcher = (PROJECT_ROOT / "run_trend_refresh.bat").read_text(encoding="utf-8")
    wrapper = (PROJECT_ROOT / "scripts" / "refresh_trends_safe.py").read_text(
        encoding="utf-8"
    )

    assert "scripts\\refresh_trends_safe.py" in launcher
    assert "finalize_prepared_trend_rankings_safely" in wrapper
    assert "trend_discovery.finalize_prepared_trend_rankings" in wrapper


def test_manual_dashboard_refresh_uses_same_safe_persistence_contract() -> None:
    wrapper = (PROJECT_ROOT / "scripts" / "refresh_trends_dashboard.py").read_text(
        encoding="utf-8"
    )

    assert "finalize_prepared_trend_rankings_safely" in wrapper
    assert "trend_discovery.finalize_prepared_trend_rankings = _safe_finalizer" in wrapper
    assert "install_trend_cluster_runtime_contract()" in wrapper
