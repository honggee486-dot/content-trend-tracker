from __future__ import annotations

from functools import wraps
from typing import Any

from src.services.trend_clustering_deterministic_baseline_service import (
    build_deterministic_baseline_comparison,
    unavailable_deterministic_baseline,
)
from src.services.trend_clustering_quality_sample_service import (
    build_trend_clustering_quality_sample,
)


def _set_new_sample_action(report: dict[str, Any]) -> None:
    report["next_action"] = {
        "label": "새 군집 표본 확보",
        "reason": (
            "최신 작업 원장과 현재 군집 스냅샷의 정합성이 확인되지 않아 당시 품질 표본을 "
            "신뢰할 수 없으므로, 현재 설정을 유지한 새 군집 표본에서 다시 확인합니다."
        ),
    }


def reconcile_clustering_quality_next_action(
    report: dict[str, Any],
    quality: dict[str, Any],
    baseline: dict[str, Any] | None = None,
) -> None:
    """기존 우선순위를 보존하면서 품질·baseline 진단을 최종 판단에 반영합니다."""
    next_action = report.get("next_action")
    if not isinstance(next_action, dict):
        return

    label = str(next_action.get("label") or "")
    if label == "군집 표본 검토":
        if bool(quality.get("available")) and not bool(
            quality.get("reconstruction_reliable")
        ):
            _set_new_sample_action(report)
        return

    if label != "현재 설정 유지":
        return

    if not bool(quality.get("available")):
        report["next_action"] = {
            "label": "군집 품질 표본 진단 점검",
            "reason": (
                "최신 군집 작업의 저장 결과를 읽기 전용으로 재구성할 수 없어 "
                "진단 누락 원인을 먼저 확인합니다."
            ),
        }
        return

    if not bool(quality.get("reconstruction_reliable")):
        _set_new_sample_action(report)
        return

    normalized_baseline = dict(baseline or {})
    if not bool(normalized_baseline.get("available")):
        report["next_action"] = {
            "label": "결정론적 baseline 비교 점검",
            "reason": (
                "신뢰 가능한 군집 품질 표본은 있지만 deterministic baseline 비교를 "
                "만들 수 없어 진단 원인을 먼저 확인합니다."
            ),
        }
        return

    if not bool(normalized_baseline.get("comparison_complete")):
        reasons = ", ".join(
            str(value)
            for value in normalized_baseline.get("comparison_incomplete_reasons") or ()
        )
        detail = f" ({reasons})" if reasons else ""
        report["next_action"] = {
            "label": "결정론적 baseline 비교 신뢰성 점검",
            "reason": (
                "deterministic baseline 비교가 불완전해 일치율을 채택 근거로 "
                f"사용할 수 없습니다{detail}."
            ),
        }
        return

    agreement_count = int(
        normalized_baseline.get("same_cluster_agreement_pair_count") or 0
    )
    disagreement_count = int(
        normalized_baseline.get("different_cluster_disagreement_pair_count") or 0
    )
    blocked_count = int(normalized_baseline.get("blocked_candidate_pair_count") or 0)
    merge_pair_count = int(normalized_baseline.get("baseline_merge_pair_count") or 0)
    if merge_pair_count > 0 or blocked_count > 0:
        report["next_action"] = {
            "label": "결정론적 baseline 제목 표본 검토",
            "reason": (
                "완전 비교에서 현재 군집 일치 "
                f"{agreement_count}쌍·불일치 {disagreement_count}쌍·"
                f"안전 차단 {blocked_count}쌍을 확인했습니다. "
                "현재 저장 군집은 비교 기준일 뿐 정답으로 간주하지 않고 실제 제목을 "
                "함께 검토합니다."
            ),
        }


def build_reliable_deterministic_baseline(
    con: Any,
    *,
    job_id: str,
    quality: dict[str, Any],
) -> dict[str, Any]:
    """같은 작업의 품질 재구성이 신뢰 가능할 때만 baseline 비교를 노출합니다."""
    if not bool(quality.get("available")):
        return unavailable_deterministic_baseline(
            "quality_sample_unavailable",
            job_id=job_id,
        )
    if not bool(quality.get("reconstruction_reliable")):
        return unavailable_deterministic_baseline(
            "quality_sample_unreliable",
            job_id=job_id,
        )
    return build_deterministic_baseline_comparison(con, job_id=job_id)


def install_trend_clustering_quality_diagnostic_contract() -> None:
    """P2 운영 진단에 최신 군집 작업의 읽기 전용 품질 표본을 추가합니다."""
    from src.services import operation_diagnostic_report_service as report_module

    original = getattr(report_module, "build_operation_diagnostic_report", None)
    if not callable(original) or getattr(
        original,
        "_trend_clustering_quality_sample_contract",
        False,
    ):
        return

    @wraps(original)
    def quality_report(con, *args, **kwargs):
        report = dict(original(con, *args, **kwargs))
        clustering = dict(report.get("trend_clustering") or {})
        job_id = str(clustering.get("job_id") or "")
        quality = build_trend_clustering_quality_sample(
            con,
            job_id=job_id,
        )
        baseline = build_reliable_deterministic_baseline(
            con,
            job_id=job_id,
            quality=quality,
        )
        clustering["quality_sample"] = quality
        clustering["deterministic_baseline"] = baseline
        report["trend_clustering"] = clustering
        reconcile_clustering_quality_next_action(report, quality, baseline)
        return report

    quality_report._trend_clustering_quality_sample_contract = True  # type: ignore[attr-defined]
    report_module.build_operation_diagnostic_report = quality_report
