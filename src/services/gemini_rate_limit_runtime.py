from __future__ import annotations

from typing import Any, Callable

from src.services.gemini_call_lifecycle_service import (
    begin_gemini_api_call,
    build_lifecycle_record_call,
    gemini_call_lifecycle_enabled,
    mark_gemini_api_provider_complete,
)
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
        reservation = None
        wait_seconds = 0.0
        if gemini_common_rate_limit_enabled():
            reservation = limiter.reserve(config, request_text)
            wait_seconds = max(0.0, float(reservation.wait_seconds or 0.0))

        # 공통 제한기의 대기가 끝난 뒤, 실제 HTTP 호출 직전에 먼저 원장에 남깁니다.
        call_id = ""
        if gemini_call_lifecycle_enabled():
            call_id = begin_gemini_api_call(
                config,
                request_text,
                request_hash,
                feature_id=feature_id,
                thinking_level=str(thinking_level or ""),
                timeout_seconds=timeout_seconds,
                rate_limit_wait_seconds=wait_seconds,
            )

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
        except Exception as exc:
            mark_gemini_api_provider_complete(call_id, error=exc)
            # 실패한 요청도 공급자 측에서 RPM/TPM에 포함될 수 있으므로 예약은 유지합니다.
            raise

        # HTTP 응답을 받은 즉시 완료 시각·토큰을 먼저 갱신하고 기능별 후처리와 분리합니다.
        mark_gemini_api_provider_complete(call_id, result=result)

        if reservation is not None:
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
    """모든 Gemini 구조화 생성 요청이 같은 RPM·TPM 관문과 전송 원장을 사용하게 합니다."""
    import src.services.gemini_service as gemini_service

    current_call = gemini_service.call_gemini_structured_output
    if not getattr(current_call, "_gemini_common_rate_limited", False):
        gemini_service.call_gemini_structured_output = build_rate_limited_structured_call(
            current_call
        )

    # 다른 서비스가 record_gemini_api_call을 직접 가져가기 전에 설치합니다.
    current_record = gemini_service.record_gemini_api_call
    if not getattr(current_record, "_gemini_call_lifecycle_record", False):
        gemini_service.record_gemini_api_call = build_lifecycle_record_call(current_record)
