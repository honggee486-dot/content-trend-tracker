from __future__ import annotations

from functools import wraps
import os
from pathlib import Path
import sys
from typing import Any

from src.config import DEFAULT_DB_PATH
from src.database import connect_database
from src.services.trend_ai_active_scope_runtime import (
    install_trend_ai_active_scope_contract,
)
from src.services.trend_blog_ai_routing_service import run_trend_blog_ai_routing
from src.services.trend_candidate_ai_evaluation_service import (
    run_trend_candidate_ai_evaluation,
)
from src.services.trend_cluster_request_cap_runtime import (
    install_trend_cluster_request_cap_contract,
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
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return original(job_id, *args, **kwargs)

        db_path = Path(kwargs.get("db_path") or DEFAULT_DB_PATH).resolve()
        evaluation: dict[str, Any] | None = None
        evaluation_warning = ""
        routing: dict[str, Any] | None = None
        routing_warning = ""
        if _post_clustering_job_ready(db_path, str(job_id)):
            try:
                evaluation, evaluation_warning = run_trend_candidate_ai_evaluation(db_path)
            except Exception as exc:
                evaluation = {
                    "status": "unexpected_error",
                    "error_message": str(exc),
                }
                evaluation_warning = str(exc)

            try:
                routing, routing_warning = run_trend_blog_ai_routing(db_path)
            except Exception as exc:
                routing = {
                    "status": "unexpected_error",
                    "error_message": str(exc),
                }
                routing_warning = str(exc)

        result = original(job_id, *args, **kwargs)
        if isinstance(result, dict) and (evaluation is not None or routing is not None):
            result = dict(result)
            if evaluation is not None:
                result["candidate_ai_evaluation"] = evaluation
                if evaluation_warning:
                    result["candidate_ai_evaluation_warning"] = evaluation_warning
            if routing is not None:
                result["blog_ai_routing"] = routing
                if routing_warning:
                    result["blog_ai_routing_warning"] = routing_warning
        return result

    wrapped._trend_candidate_ai_evaluation_contract = True  # type: ignore[attr-defined]
    post_module.run_topic_angles_after_clustering = wrapped


def install_trend_candidate_ai_evaluation_contract(
    discovery_module: Any | None = None,
) -> None:
    """Run data-review-model evaluation after final cluster storage and before angles."""
    if discovery_module is None:
        from src.services import trend_discovery_service as discovery_module

    install_trend_cluster_request_cap_contract()
    install_trend_ai_active_scope_contract()

    _install_refresh_evaluation(discovery_module)
    _install_post_clustering_evaluation()

    if "streamlit" in sys.modules:
        try:
            import src.ui as ui_module
            from src.services.trend_candidate_ai_evaluation_ui_runtime import (
                install_trend_candidate_ai_evaluation_ui_contract,
            )

            install_trend_candidate_ai_evaluation_ui_contract(ui_module)
        except Exception:
            pass
