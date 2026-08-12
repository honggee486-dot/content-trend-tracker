from __future__ import annotations

from src.services.operation_diagnostic_report_service import _next_action


def _collection() -> dict[str, object]:
    return {"source_problem_count": 0}


def _clustering(*, status: str) -> dict[str, object]:
    return {
        "available": True,
        "sample_available": True,
        "trial_contract_ok": True,
        "job_status": status,
        "review_signal_count": 0,
    }


def _throttle(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "provider_rate_limit_count": 0,
        "provider_daily_quota_count": 0,
        "local_tpm_wait_count": 0,
    }
    result.update(overrides)
    return result


def test_failed_clustering_with_recent_rate_limit_prioritizes_provider_check() -> None:
    label, reason = _next_action(
        topic_status="정상",
        sample_sufficient=True,
        current_validation_failures=0,
        collection=_collection(),
        clustering=_clustering(status="failed"),
        clustering_throttle=_throttle(provider_rate_limit_count=2),
    )

    assert label == "Gemini rate limit 점검"
    assert "최근 요청 표본" in reason


def test_failed_clustering_with_daily_quota_prioritizes_quota_check() -> None:
    label, reason = _next_action(
        topic_status="정상",
        sample_sufficient=True,
        current_validation_failures=0,
        collection=_collection(),
        clustering=_clustering(status="failed"),
        clustering_throttle=_throttle(
            provider_rate_limit_count=1,
            provider_daily_quota_count=1,
        ),
    )

    assert label == "Gemini 일일 quota 점검"
    assert "quota" in reason


def test_local_tpm_wait_is_not_treated_as_provider_failure() -> None:
    label, reason = _next_action(
        topic_status="정상",
        sample_sufficient=True,
        current_validation_failures=0,
        collection=_collection(),
        clustering=_clustering(status="failed"),
        clustering_throttle=_throttle(local_tpm_wait_count=3),
    )

    assert label == "군집 실행 실패 점검"
    assert "오류 메시지" in reason


def test_historical_rate_limit_does_not_override_successful_clustering() -> None:
    label, _ = _next_action(
        topic_status="정상",
        sample_sufficient=True,
        current_validation_failures=0,
        collection=_collection(),
        clustering=_clustering(status="success"),
        clustering_throttle=_throttle(provider_rate_limit_count=4),
    )

    assert label == "현재 설정 유지"
