from __future__ import annotations

from functools import wraps
import os
from pathlib import Path
import sys
from typing import Any

from src.config import DEFAULT_DB_PATH
from src.database import connect_database
from src.services.trend_candidate_ai_evaluation_service import (
    run_trend_candidate_ai_evaluation,
)


def _db_path_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Path:
    value = (
        args[0]
        if args
        else kwargs.get("db_path")
        or kwargs.get("database_path")
        or DEFAULT_DB_PATH
    )
    return Path(value).resolve()


def _ranking_allows_evaluation(result: dict[str, Any]) -> bool:
    ranking = result.get("ranking")
    if not isinstance(ranking, dict):
        return False
    ranking_status = str(ranking.get("status") or "").strip()
    if ranking_status in {"skipped_source_failure", "skipped_overlap"}:
        return False
    ai_status = str((ranking.get("ai_clustering") or {}).get("status") or "").strip()
    return ai_status not in {"skipped_source_failure", "skipped_overlap"}


def _install_refresh_evaluation(discovery_module: Any) -> None:
    original = getattr(discovery_module, "refresh_trend_sources_short_connections", None)
    if not callable(original) or getattr(original, "_trend_candidate_ai_evaluation_contract", False):
        return

    @wraps(original)
    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        if not isinstance(result, dict):
            return result

        collection_run_id = str(kwargs.get("collection_run_id") or "").strip()
        if (
            not collection_run_id
            or os.environ.get("PYTEST_CURRENT_TEST")
            or not _ranking_allows_evaluation(result)
        ):
            return result

        progress_callback = kwargs.get("progress_callback")

        def evaluation_progress(_value: float, message: str) -> None:
            if callable(progress_callback):
                progress_callback(
                    1.0,
                    str(message or "Flash-Lite 전체 글감 평가 중"),
                )

        try:
            evaluation, warning = run_trend_candidate_ai_evaluation(
                _db_path_from_call(args, kwargs),
                progress_callback=evaluation_progress,
            )
            result["candidate_ai_evaluation"] = evaluation
            if warning:
                result.setdefault("warnings", {})["candidate_ai_evaluation"] = warning
        except Exception as exc:
            # 후처리 실패는 이미 저장된 수집·군집을 취소하지 않습니다.
            result["candidate_ai_evaluation"] = {
                "status": "unexpected_error",
                "error_message": str(exc),
            }
        return result

    wrapped._trend_candidate_ai_evaluation_contract = True  # type: ignore[attr-defined]
    discovery_module.refresh_trend_sources_short_connections = wrapped


def _post_clustering_job_ready(db_path: Path, job_id: str) -> bool:
    try:
        with connect_database(db_path) as con:
            row = con.execute(
                """
                SELECT status
                FROM trend_clustering_jobs
                WHERE job_id = ?
                """,
                [str(job_id)],
            ).fetchone()
    except Exception:
        return False
    return bool(row and str(row[0] or "") in {"success", "partial"})


def _install_post_clustering_evaluation() -> None:
    from src.services import post_clustering_topic_angle_service as post_module

    original = getattr(post_module, "run_topic_angles_after_clustering", None)
    if not callable(original) or getattr(original, "_trend_candidate_ai_evaluation_contract", False):
        return

    @wraps(original)
    def wrapped(job_id: str, *args, **kwargs):
        # pytest에서는 기존 서비스의 주입 가능한 fake 연결/runner 계약을 그대로 보존합니다.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return original(job_id, *args, **kwargs)

        db_path = Path(kwargs.get("db_path") or DEFAULT_DB_PATH).resolve()
        evaluation: dict[str, Any] | None = None
        warning = ""
        if _post_clustering_job_ready(db_path, str(job_id)):
            try:
                evaluation, warning = run_trend_candidate_ai_evaluation(db_path)
            except Exception as exc:
                evaluation = {
                    "status": "unexpected_error",
                    "error_message": str(exc),
                }
                warning = str(exc)

        # AI 평가의 성공 여부와 무관하게 기존 주제방향 생성은 계속합니다.
        result = original(job_id, *args, **kwargs)
        if isinstance(result, dict) and evaluation is not None:
            result = dict(result)
            result["candidate_ai_evaluation"] = evaluation
            if warning:
                result["candidate_ai_evaluation_warning"] = warning
        return result

    wrapped._trend_candidate_ai_evaluation_contract = True  # type: ignore[attr-defined]
    post_module.run_topic_angles_after_clustering = wrapped


def install_trend_candidate_ai_evaluation_contract(
    discovery_module: Any | None = None,
) -> None:
    """Run data-review-model evaluation after final cluster storage and before angles."""
    if discovery_module is None:
        from src.services import trend_discovery_service as discovery_module

    _install_refresh_evaluation(discovery_module)
    _install_post_clustering_evaluation()

    # 앱에서는 기존 page_header hook에 비교 패널을 덧붙입니다. CLI/예약 실행은 UI를 로드하지 않습니다.
    if "streamlit" in sys.modules:
        try:
            import src.ui as ui_module
            from src.services.trend_candidate_ai_evaluation_ui_runtime import (
                install_trend_candidate_ai_evaluation_ui_contract,
            )

            install_trend_candidate_ai_evaluation_ui_contract(ui_module)
        except Exception:
            # UI 보조 기능 실패가 수집·군집 계약 설치를 막지 않게 합니다.
            pass
