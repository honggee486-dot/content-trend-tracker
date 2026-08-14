from __future__ import annotations

from functools import wraps
from pathlib import Path
from typing import Any

from src.config import DEFAULT_DB_PATH
from src.services.trend_blog_ai_routing_service import run_trend_blog_ai_routing


def install_trend_blog_ai_routing_contract(discovery_module: Any | None = None) -> None:
    """최신 수집·군집 저장이 끝난 뒤 전체 글감의 Gemini 블로그 분류를 보완합니다.

    외부 API 대기 중에는 기존 수집용 DuckDB 연결을 잡지 않습니다. 분류 실패도 이미
    성공한 출처 수집·군집 결과를 취소하지 않으며 화면은 기존 로컬 추천으로 fallback합니다.
    """
    if discovery_module is None:
        from src.services import trend_discovery_service as discovery_module

    original = getattr(discovery_module, "refresh_trend_sources_short_connections", None)
    if not callable(original) or getattr(original, "_trend_blog_ai_routing_contract", False):
        return

    @wraps(original)
    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        if not isinstance(result, dict):
            return result

        db_path = (
            args[0]
            if args
            else kwargs.get("db_path")
            or kwargs.get("database_path")
            or DEFAULT_DB_PATH
        )
        progress_callback = kwargs.get("progress_callback")

        def routing_progress(_value: float, message: str) -> None:
            # 기존 수집 진행률 100% 뒤에 붙는 후처리이므로 퍼센트를 되돌리지 않고
            # 같은 끝 지점에서 현재 Flash-Lite 분류 묶음만 갱신합니다.
            if callable(progress_callback):
                progress_callback(1.0, str(message or "Flash-Lite 블로그 자동 분류 중"))

        try:
            routing_result, warning = run_trend_blog_ai_routing(
                Path(db_path),
                progress_callback=routing_progress,
            )
        except Exception as exc:
            routing_result = {
                "status": "unexpected_error",
                "requested_clusters": 0,
                "routed_clusters": 0,
                "reused_clusters": 0,
                "failed_clusters": 0,
                "requested_batches": 0,
                "completed_batches": 0,
                "failed_batches": 0,
                "error_message": str(exc),
            }
            warning = str(exc)

        enriched = dict(result)
        enriched["blog_routes"] = dict(routing_result or {})
        if warning:
            enriched["blog_route_warning"] = str(warning)
        return enriched

    wrapped._trend_blog_ai_routing_contract = True  # type: ignore[attr-defined]
    discovery_module.refresh_trend_sources_short_connections = wrapped
