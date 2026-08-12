from __future__ import annotations

from copy import deepcopy

from src.services import operation_diagnostic_report_service as report_service
from src.services import trend_clustering_quality_runtime as quality_runtime
from src.services.trend_clustering_quality_runtime import (
    build_reliable_deterministic_baseline,
    reconcile_clustering_quality_next_action,
)


def _report(label: str = "군집 표본 검토") -> dict:
    return {
        "next_action": {
            "label": label,
            "reason": "기존 안내",
        }
    }


def _reliable_quality() -> dict:
    return {
        "available": True,
        "reason": "",
        "reconstruction_reliable": True,
    }


def _complete_baseline(**overrides) -> dict:
    baseline = {
        "available": True,
        "comparison_complete": True,
        "comparison_incomplete_reasons": [],
        "baseline_merge_pair_count": 0,
        "same_cluster_agreement_pair_count": 0,
        "different_cluster_disagreement_pair_count": 0,
        "blocked_candidate_pair_count": 0,
    }
    baseline.update(overrides)
    return baseline


def test_operation_report_delegates_quality_wiring_to_runtime_wrapper() -> None:
    assert not hasattr(report_service, "_attach_clustering_quality_diagnostics")
    assert not hasattr(report_service, "build_trend_clustering_quality_sample")
    assert not hasattr(report_service, "build_deterministic_baseline_comparison")


def test_unreliable_quality_sample_requests_new_sample() -> None:
    report = _report()

    reconcile_clustering_quality_next_action(
        report,
        {
            "available": True,
            "reason": "cluster_snapshot_changed_since_job",
            "reconstruction_reliable": False,
        },
    )

    assert report["next_action"]["label"] == "새 군집 표본 확보"
    assert "현재 설정을 유지한 새 군집 표본" in report["next_action"]["reason"]


def test_reliable_quality_sample_keeps_review_action() -> None:
    report = _report()
    original = deepcopy(report["next_action"])

    reconcile_clustering_quality_next_action(
        report,
        _reliable_quality(),
        _complete_baseline(baseline_merge_pair_count=2),
    )

    assert report["next_action"] == original


def test_unreliable_quality_sample_keeps_higher_priority_action() -> None:
    report = _report("군집 실행 실패 점검")
    original = deepcopy(report["next_action"])

    reconcile_clustering_quality_next_action(
        report,
        {
            "available": True,
            "reason": "cluster_snapshot_changed_since_job",
            "reconstruction_reliable": False,
        },
    )

    assert report["next_action"] == original


def test_unavailable_quality_sample_keeps_existing_review_action() -> None:
    report = _report()
    original = deepcopy(report["next_action"])

    reconcile_clustering_quality_next_action(
        report,
        {
            "available": False,
            "reason": "processing_rows_not_found",
            "reconstruction_reliable": False,
        },
    )

    assert report["next_action"] == original


def test_current_settings_action_surfaces_unavailable_quality_sample() -> None:
    report = _report("현재 설정 유지")

    reconcile_clustering_quality_next_action(
        report,
        {
            "available": False,
            "reason": "missing_columns",
            "reconstruction_reliable": False,
        },
    )

    assert report["next_action"]["label"] == "군집 품질 표본 진단 점검"
    assert "재구성할 수 없어" in report["next_action"]["reason"]


def test_current_settings_action_requests_new_sample_when_quality_is_unreliable() -> None:
    report = _report("현재 설정 유지")

    reconcile_clustering_quality_next_action(
        report,
        {
            "available": True,
            "reason": "cluster_snapshot_changed_since_job",
            "reconstruction_reliable": False,
        },
    )

    assert report["next_action"]["label"] == "새 군집 표본 확보"


def test_current_settings_action_surfaces_unavailable_baseline() -> None:
    report = _report("현재 설정 유지")

    reconcile_clustering_quality_next_action(
        report,
        _reliable_quality(),
        {
            "available": False,
            "reason": "processing_rows_not_found",
        },
    )

    assert report["next_action"]["label"] == "결정론적 baseline 비교 점검"


def test_current_settings_action_blocks_on_incomplete_baseline() -> None:
    report = _report("현재 설정 유지")

    reconcile_clustering_quality_next_action(
        report,
        _reliable_quality(),
        {
            "available": True,
            "comparison_complete": False,
            "comparison_incomplete_reasons": ["pair_limit_reached"],
        },
    )

    assert report["next_action"]["label"] == "결정론적 baseline 비교 신뢰성 점검"
    assert "pair_limit_reached" in report["next_action"]["reason"]


def test_current_settings_action_requests_title_review_for_complete_baseline_pairs() -> None:
    report = _report("현재 설정 유지")

    reconcile_clustering_quality_next_action(
        report,
        _reliable_quality(),
        _complete_baseline(
            baseline_merge_pair_count=3,
            same_cluster_agreement_pair_count=2,
            different_cluster_disagreement_pair_count=1,
            blocked_candidate_pair_count=1,
        ),
    )

    assert report["next_action"]["label"] == "결정론적 baseline 제목 표본 검토"
    assert "일치 2쌍" in report["next_action"]["reason"]
    assert "불일치 1쌍" in report["next_action"]["reason"]
    assert "안전 차단 1쌍" in report["next_action"]["reason"]
    assert "정답으로 간주하지 않고" in report["next_action"]["reason"]


def test_complete_empty_baseline_keeps_current_settings_action() -> None:
    report = _report("현재 설정 유지")
    original = deepcopy(report["next_action"])

    reconcile_clustering_quality_next_action(
        report,
        _reliable_quality(),
        _complete_baseline(),
    )

    assert report["next_action"] == original


def test_deterministic_baseline_waits_for_reliable_quality_sample(monkeypatch) -> None:
    called = []

    def fake_build(_con, *, job_id: str):
        called.append(job_id)
        return {"available": True, "job_id": job_id}

    monkeypatch.setattr(
        quality_runtime,
        "build_deterministic_baseline_comparison",
        fake_build,
    )

    unavailable = build_reliable_deterministic_baseline(
        object(),
        job_id="job-1",
        quality={"available": True, "reconstruction_reliable": False},
    )
    reliable = build_reliable_deterministic_baseline(
        object(),
        job_id="job-2",
        quality={"available": True, "reconstruction_reliable": True},
    )

    assert unavailable["available"] is False
    assert unavailable["reason"] == "quality_sample_unreliable"
    assert called == ["job-2"]
    assert reliable == {"available": True, "job_id": "job-2"}
