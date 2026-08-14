from __future__ import annotations

from pathlib import Path

import pytest

from src.config import GeminiConfig
from src.services.gemini_rate_limit_runtime import build_rate_limited_structured_call
from src.services.gemini_rate_limit_service import (
    GeminiRateLimitError,
    SharedGeminiRateLimiter,
    resolve_gemini_rate_limit_policy,
)


class FakeClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = float(value)
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.value += float(seconds)


def _config(model: str = "gemini-3.5-flash-lite", scope: str = "scope-a") -> GeminiConfig:
    return GeminiConfig(
        api_key="test-key",
        model=model,
        app_id="content-trend-tracker",
        quota_scope_id=scope,
        timeout_seconds=60,
        retry_wait_seconds=2.0,
        retry_max_wait_seconds=30.0,
    )


def _limiter(
    tmp_path: Path,
    clock: FakeClock,
    *,
    token_estimator,
) -> SharedGeminiRateLimiter:
    return SharedGeminiRateLimiter(
        state_path=tmp_path / "rate.json",
        clock=clock.now,
        sleeper=clock.sleep,
        boundary_guard_seconds=0.01,
        token_estimator=token_estimator,
    )


def test_policy_defaults_to_conservative_five_rpm_and_safe_tpm(monkeypatch) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("GEMINI_RATE_LIMIT_"):
            monkeypatch.delenv(key, raising=False)
    policy = resolve_gemini_rate_limit_policy(_config())
    assert policy.rpm_limit == 5
    assert policy.tpm_limit == 250_000
    assert policy.effective_tpm_limit == 240_000


def test_model_specific_policy_can_override_verified_limit(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RATE_LIMIT_RPM", "5")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_GEMINI_3_5_FLASH_LITE_RPM", "15")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_GEMINI_3_5_FLASH_LITE_TPM", "300000")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM_SAFETY_MARGIN", "5000")
    policy = resolve_gemini_rate_limit_policy(_config())
    assert policy.rpm_limit == 15
    assert policy.tpm_limit == 300_000
    assert policy.effective_tpm_limit == 295_000


def test_sixth_request_waits_for_five_rpm_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RATE_LIMIT_RPM", "5")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM", "1000000")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM_SAFETY_MARGIN", "0")
    clock = FakeClock()
    limiter = _limiter(tmp_path, clock, token_estimator=lambda _text: 1)

    for _ in range(5):
        reservation = limiter.reserve(_config(), "x")
        assert reservation.wait_seconds == 0
    sixth = limiter.reserve(_config(), "x")

    assert clock.sleeps
    assert clock.sleeps[-1] >= 60.0
    assert sixth.wait_seconds >= 60.0


def test_tpm_window_waits_even_when_rpm_has_room(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RATE_LIMIT_RPM", "100")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM", "100")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM_SAFETY_MARGIN", "0")
    clock = FakeClock()
    limiter = _limiter(tmp_path, clock, token_estimator=lambda text: int(text))

    limiter.reserve(_config(), "80")
    second = limiter.reserve(_config(), "30")

    assert clock.sleeps[-1] >= 60.0
    assert second.wait_seconds >= 60.0


def test_two_limiter_instances_share_same_state_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RATE_LIMIT_RPM", "1")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM", "1000")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM_SAFETY_MARGIN", "0")
    clock = FakeClock()
    first = _limiter(tmp_path, clock, token_estimator=lambda _text: 1)
    second = _limiter(tmp_path, clock, token_estimator=lambda _text: 1)

    first.reserve(_config(), "x")
    reservation = second.reserve(_config(), "x")

    assert reservation.wait_seconds >= 60.0


def test_different_models_have_independent_windows(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RATE_LIMIT_RPM", "1")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM", "1000")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM_SAFETY_MARGIN", "0")
    clock = FakeClock()
    limiter = _limiter(tmp_path, clock, token_estimator=lambda _text: 1)

    limiter.reserve(_config("gemini-3.5-flash-lite"), "x")
    other = limiter.reserve(_config("gemini-3.6-flash"), "x")

    assert other.wait_seconds == 0
    assert not clock.sleeps


def test_different_quota_scopes_have_independent_windows(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RATE_LIMIT_RPM", "1")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM", "1000")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM_SAFETY_MARGIN", "0")
    clock = FakeClock()
    limiter = _limiter(tmp_path, clock, token_estimator=lambda _text: 1)

    limiter.reserve(_config(scope="scope-a"), "x")
    other = limiter.reserve(_config(scope="scope-b"), "x")

    assert other.wait_seconds == 0
    assert not clock.sleeps


def test_reconcile_actual_tokens_affects_following_request(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RATE_LIMIT_RPM", "100")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM", "100")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM_SAFETY_MARGIN", "0")
    clock = FakeClock()
    limiter = _limiter(tmp_path, clock, token_estimator=lambda _text: 40)

    first = limiter.reserve(_config(), "x")
    limiter.reconcile(first, 90)
    second = limiter.reserve(_config(), "x")

    assert second.wait_seconds >= 60.0


def test_single_request_over_internal_safe_tpm_fails_without_sleep(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RATE_LIMIT_RPM", "5")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM", "100")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM_SAFETY_MARGIN", "5")
    clock = FakeClock()
    limiter = _limiter(tmp_path, clock, token_estimator=lambda _text: 96)

    with pytest.raises(GeminiRateLimitError, match="안전 상한"):
        limiter.reserve(_config(), "x")
    assert not clock.sleeps


def test_corrupt_state_file_recovers_as_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RATE_LIMIT_RPM", "5")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM", "1000")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM_SAFETY_MARGIN", "0")
    state_path = tmp_path / "rate.json"
    state_path.write_text("not-json", encoding="utf-8")
    clock = FakeClock()
    limiter = SharedGeminiRateLimiter(
        state_path=state_path,
        clock=clock.now,
        sleeper=clock.sleep,
        token_estimator=lambda _text: 1,
    )

    reservation = limiter.reserve(_config(), "x")

    assert reservation.wait_seconds == 0


def test_structurally_corrupt_token_value_is_sanitized(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RATE_LIMIT_RPM", "5")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM", "1000")
    monkeypatch.setenv("GEMINI_RATE_LIMIT_TPM_SAFETY_MARGIN", "0")
    state_path = tmp_path / "rate.json"
    state_path.write_text(
        '{"version":1,"reservations":[{"request_id":"bad","scope_key":"x",'
        '"model_name":"gemini-3.5-flash-lite","input_tokens":"bad",'
        '"reserved_at_epoch":1000.0}]}',
        encoding="utf-8",
    )
    clock = FakeClock()
    limiter = SharedGeminiRateLimiter(
        state_path=state_path,
        clock=clock.now,
        sleeper=clock.sleep,
        token_estimator=lambda _text: 1,
    )

    reservation = limiter.reserve(_config(), "x")

    assert reservation.wait_seconds == 0


def test_common_wrapper_reserves_and_reconciles_actual_input(monkeypatch) -> None:
    import src.services.gemini_rate_limit_runtime as runtime

    monkeypatch.setattr(runtime, "gemini_common_rate_limit_enabled", lambda: True)

    class FakeLimiter:
        def __init__(self) -> None:
            self.reserved = []
            self.reconciled = []

        def reserve(self, config, request_text):
            self.reserved.append((config.model, request_text))
            return object()

        def reconcile(self, reservation, actual_input_tokens):
            self.reconciled.append((reservation, actual_input_tokens))

    fake_limiter = FakeLimiter()
    calls = []

    def original(config, request_text, request_hash, **kwargs):
        calls.append((config.model, request_hash, kwargs["feature_id"]))
        return ("{}", 123, 5, 0, 128, "STOP", "")

    wrapped = build_rate_limited_structured_call(original, limiter=fake_limiter)
    result = wrapped(
        _config(),
        "request",
        "hash",
        feature_id="feature",
        response_schema={"type": "object"},
    )

    assert result[1] == 123
    assert len(calls) == 1
    assert fake_limiter.reserved == [("gemini-3.5-flash-lite", "request")]
    assert fake_limiter.reconciled[0][1] == 123


def test_common_wrapper_does_not_retry_or_release_failed_provider_request(monkeypatch) -> None:
    import src.services.gemini_rate_limit_runtime as runtime

    monkeypatch.setattr(runtime, "gemini_common_rate_limit_enabled", lambda: True)

    class FakeLimiter:
        def __init__(self) -> None:
            self.reserve_count = 0
            self.reconcile_count = 0

        def reserve(self, config, request_text):
            self.reserve_count += 1
            return object()

        def reconcile(self, reservation, actual_input_tokens):
            self.reconcile_count += 1

    fake_limiter = FakeLimiter()
    provider_calls = 0

    def original(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise RuntimeError("429")

    wrapped = build_rate_limited_structured_call(original, limiter=fake_limiter)
    with pytest.raises(RuntimeError, match="429"):
        wrapped(
            _config(),
            "request",
            "hash",
            feature_id="feature",
            response_schema={"type": "object"},
        )

    assert provider_calls == 1
    assert fake_limiter.reserve_count == 1
    assert fake_limiter.reconcile_count == 0


def test_src_initialization_installs_common_gate_before_runtime_aliases() -> None:
    import src.services.gemini_service as gemini_service

    assert getattr(
        gemini_service.call_gemini_structured_output,
        "_gemini_common_rate_limited",
        False,
    )
    assert (
        gemini_service._call_interactions_api.__globals__["call_gemini_structured_output"]
        is gemini_service.call_gemini_structured_output
    )
