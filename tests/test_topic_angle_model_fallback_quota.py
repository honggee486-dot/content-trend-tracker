from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.topic_angle_model_fallback_runtime import (
    PROTECTED_TOPIC_ANGLE_MODEL,
    _fallback_allowed,
)


@pytest.mark.parametrize(
    "error_type",
    [
        "service_unavailable",
        "request_timeout",
        "rate_limited",
        "daily_quota_exhausted",
    ],
)
def test_gemini_37_fallback_policy_accepts_service_timeout_and_quota_errors(
    error_type: str,
) -> None:
    config = SimpleNamespace(model=PROTECTED_TOPIC_ANGLE_MODEL)
    result = SimpleNamespace(enrichments={}, error_type=error_type)

    assert _fallback_allowed(config, result) is True


@pytest.mark.parametrize(
    "error_type",
    [
        "invalid_request",
        "authentication_error",
        "permission_error",
        "model_not_found",
        "response_validation_error",
    ],
)
def test_gemini_37_fallback_policy_rejects_configuration_and_validation_errors(
    error_type: str,
) -> None:
    config = SimpleNamespace(model=PROTECTED_TOPIC_ANGLE_MODEL)
    result = SimpleNamespace(enrichments={}, error_type=error_type)

    assert _fallback_allowed(config, result) is False


def test_fallback_policy_does_not_replace_other_primary_models() -> None:
    config = SimpleNamespace(model="gemini-3.6-flash")
    result = SimpleNamespace(enrichments={}, error_type="rate_limited")

    assert _fallback_allowed(config, result) is False


def test_fallback_policy_preserves_any_valid_primary_enrichment() -> None:
    config = SimpleNamespace(model=PROTECTED_TOPIC_ANGLE_MODEL)
    result = SimpleNamespace(
        enrichments={"cluster-1": {"display_title": "saved"}},
        error_type="rate_limited",
    )

    assert _fallback_allowed(config, result) is False
