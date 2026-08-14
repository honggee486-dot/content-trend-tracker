from __future__ import annotations

from typing import Any, Callable

from src.services.gemini_rate_limit_service import (
    GLOBAL_GEMINI_RATE_LIMITER,
    SharedGeminiRateLimiter,
    gemini_common_rate_limit_enabled,
)


def build_rate_limited_structured_call(
    original: Callable[..., tuple[Any, ...]],
    *,
    limiter: SharedGeminiRateLimiter = GLOBAL_GEMINI_RATE_LIMITER,
) -> Callable[..., tuple[Any, ...]]:
    def wrapped(
        config: Any,
        request_text: str,
        request_hash: str,
        *,
        feature_id: str,
        response_schema: dict[str, Any],
        use_google_search: bool = False,
        thinking_level: str | None = None,
        timeout_seconds: int | None = None,
    ) -> tuple[Any, ...]:
        if not gemini_common_rate_limit_enabled():
            return original(
                config,
                request_text,
                request_hash,
                feature_id=feature_id,
                response_schema=response_schema,
                use_google_search=use_google_search,
                thinking_level=thinking_level,
                timeout_seconds=timeout_seconds,
            )

        reservation = limiter.reserve(config, request_text)
        try:
            result = original(
                config,
                request_text,
                request_hash,
                feature_id=feature_id,
                response_schema=response_schema,
                use_google_search=use_google_search,
                thinking_level=thinking_level,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            # 실패한 요청도 공급자 측에서 RPM/TPM에 포함될 수 있으므로
            # 예약은 60초 창이 끝날 때까지 보수적으로 유지합니다.
            raise

        actual_input_tokens: int | None = None
        if isinstance(result, tuple) and len(result) >= 2:
            try:
                raw_tokens = result[1]
                actual_input_tokens = None if raw_tokens is None else int(raw_tokens)
            except (TypeError, ValueError):
                actual_input_tokens = None
        limiter.reconcile(reservation, actual_input_tokens)
        return result

    setattr(wrapped, "_gemini_common_rate_limited", True)
    setattr(wrapped, "_gemini_common_rate_limit_original", original)
    return wrapped


def install_gemini_common_rate_limit_contract() -> None:
    """모든 Gemini 구조화 생성 요청이 같은 RPM·TPM 관문을 통과하게 합니다."""
    import src.services.gemini_service as gemini_service

    current = gemini_service.call_gemini_structured_output
    if getattr(current, "_gemini_common_rate_limited", False):
        return
    gemini_service.call_gemini_structured_output = build_rate_limited_structured_call(current)
