from __future__ import annotations

from functools import wraps
import os
from pathlib import Path
from typing import Any

from src.config import DEFAULT_DB_PATH
from src.services.trend_blog_ai_routing_service import run_trend_blog_ai_routing
from src.services.trend_candidate_ai_evaluation_runtime import (
    install_trend_candidate_ai_evaluation_contract,
)


def install_trend_blog_ai_routing_contract(discovery_module: Any | None = None) -> None:
    """최신 수집·군집 저장이 끝난 뒤 전체 글감의 Gemini 블로그 분류를 보완합니다.

    외부 API 대기 중에는 기존 수집용 DuckDB 연결을 잡지 않습니다. 분류 실패도 이미
    성공한 출처 수집·군집 결과를 취소하지 않으며 화면은 기존 로컬 추천으로 fallback합니다.
    기존 수집 함수의 반환값 계약도 그대로 유지합니다.
    """
    if discovery_module is None:
        from src.services import trend_discovery_service as discovery_module

    # 같은 데이터 검토 모델의 전체 글감 평가는 최종 2차 군집 저장 뒤, 블로그 분류와
    # 주제방향 생성보다 먼저 실행되도록 내부 래퍼를 먼저 설치합니다.
    install_trend_candidate_ai_evaluation_contract(discovery_module)

    original = getattr(discovery_module, "refresh_trend_sources_short_connections", None)
    if not callable(original) or getattr(original, "_trend_blog_ai_routing_contract", False):
        return

    @wraps(original)
    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        if not isinstance(result, dict):
            return result

        # 실제 수집 실행 ID가 있는 운영 경로만 자동 분류합니다. 로컬 pytest는 .env의
        # 실제 API 키를 읽을 수 있으므로 테스트 중에는 외부 Gemini 호출을 절대 하지 않습니다.
        collection_run_id = str(kwargs.get("collection_run_id") or "").strip()
        if not collection_run_id or os.environ.get("PYTEST_CURRENT_TEST"):
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
            run_trend_blog_ai_routing(
                Path(db_path),
                progress_callback=routing_progress,
            )
        except Exception:
            # 블로그 분류는 부가 후처리입니다. 실패해도 성공한 출처 수집·군집 결과를
            # 취소하거나 기존 반환 구조를 바꾸지 않습니다.
            pass
        return result

    wrapped._trend_blog_ai_routing_contract = True  # type: ignore[attr-defined]
    discovery_module.refresh_trend_sources_short_connections = wrapped
