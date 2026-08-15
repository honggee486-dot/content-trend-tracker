from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Streamlit이 없는 별도 프로세스에서도 화면 실행과 같은 운영 로그·정리 계약을 설치합니다.
from src.services.post_collection_cleanup_runtime import (
    install_post_collection_cleanup_contract,
)
from src.services.program_log_correlation_runtime import (
    install_program_log_correlation_contract,
)
from src.services.program_log_runtime import install_program_logging_contract
from src.services.source_collection_log_runtime import install_source_collection_logging
from src.services.topic_angle_candidate_diagnostic_service import (
    install_topic_angle_candidate_diagnostic_contract,
)
from src.services.trend_cluster_request_cap_runtime import (
    install_adaptive_gemini_batch_contract,
)
from src.services.trend_cluster_runtime_contract import (
    install_trend_cluster_runtime_contract,
)
from src.services.trend_stage_program_log_runtime import (
    install_precise_trend_stage_logging,
)

install_program_logging_contract()
install_topic_angle_candidate_diagnostic_contract()
install_source_collection_logging()
install_post_collection_cleanup_contract()
install_program_log_correlation_contract()
# 최신 데이터 수집용 별도 프로세스도 3.7 주제방향을 제외한 자동 Gemini 묶음에
# 공통 적응형 입력 토큰 예산을 명시적으로 설치합니다.
install_adaptive_gemini_batch_contract()
install_trend_cluster_runtime_contract()
install_precise_trend_stage_logging()

import src.services.trend_discovery_service as trend_discovery
from scripts import refresh_trends as base_refresh
from src.services.dashboard_cleanup_progress_service import (
    cleanup_preflight_progress_message,
    post_collection_cleanup_progress_message,
)
from src.services.dashboard_refresh_progress_service import (
    finish_dashboard_refresh_progress,
    start_dashboard_refresh_progress,
    update_dashboard_refresh_progress,
)
from src.services.topic_angle_backlog_resume_service import (
    resume_deferred_topic_angles,
)
from src.services.trend_cluster_persistence_safety_service import (
    finalize_prepared_trend_rankings_safely,
)

_RUN_TYPE = "manual_refresh"


_original_finalizer = trend_discovery.finalize_prepared_trend_rankings


def _safe_finalizer(con, calculation):
    return finalize_prepared_trend_rankings_safely(
        con,
        calculation,
        finalizer=_original_finalizer,
    )


# 수동 백그라운드 수집도 예약 수집·군집 전용 작업과 같은 중복 기본키
# 정리 계약을 사용해 동일 cluster_id가 한 계산에 중복돼도 전체 저장을 취소하지 않습니다.
trend_discovery.finalize_prepared_trend_rankings = _safe_finalizer


def _topic_angle_progress_message(result: Any) -> str:
    payload = result[0] if isinstance(result, tuple) and result else result
    if not isinstance(payload, dict):
        return "주제 방향 자동 생성 결과 정리 중"
    status = str(payload.get("status") or "").strip()
    if status == "nothing_to_generate":
        return "새로 생성할 주제 방향 없음"
    if status == "missing_api_key":
        return "Gemini API 키가 없어 주제 방향 생성 생략"
    if status == "deferred_for_clustering_backlog":
        remaining = int(payload.get("remaining_items") or 0)
        return f"군집 미처리 {remaining:,}개로 주제 방향 생성 보류"
    generated = int(payload.get("generated_clusters") or 0)
    return f"주제 방향 자동 생성 결과 {generated:,}개 정리 완료"


def _install_progress_wrappers(
    *,
    run_id: str,
    process_id: int,
) -> tuple[Callable[..., Any], Callable[..., Any], Callable[..., Any]]:
    original_cleanup = base_refresh.run_automatic_cleanup_if_due
    original_refresh = base_refresh.refresh_trend_sources_short_connections
    original_topic_angles = base_refresh._run_background_topic_angles
    cleanup_state: dict[str, Any] = {"result": None}

    def tracked_cleanup(*args, **kwargs):
        update_dashboard_refresh_progress(
            3,
            "저장 자료 자동 정리 조건 확인 중",
            run_id=run_id,
            pid=process_id,
        )
        result = original_cleanup(*args, **kwargs)
        cleanup_state["result"] = result
        update_dashboard_refresh_progress(
            6,
            cleanup_preflight_progress_message(result),
            run_id=run_id,
            pid=process_id,
        )
        return result

    def tracked_refresh(*args, **kwargs):
        previous_progress = kwargs.get("progress_callback")

        def progress(value: float, message: str) -> None:
            mapped = 8 + round(max(0.0, min(1.0, float(value))) * 80)
            update_dashboard_refresh_progress(
                mapped,
                str(message or "최신 데이터 수집·분석 중"),
                run_id=run_id,
                pid=process_id,
            )
            if callable(previous_progress):
                previous_progress(value, message)

        call_kwargs = dict(kwargs)
        call_kwargs["progress_callback"] = progress
        result = original_refresh(*args, **call_kwargs)
        update_dashboard_refresh_progress(
            89,
            post_collection_cleanup_progress_message(
                cleanup_state["result"],
                result,
            ),
            run_id=run_id,
            pid=process_id,
        )
        return result

    def tracked_topic_angles(*args, **kwargs):
        update_dashboard_refresh_progress(
            90,
            "주제 방향 자동 생성 대상 확인 중",
            run_id=run_id,
            pid=process_id,
        )
        result = original_topic_angles(*args, **kwargs)
        update_dashboard_refresh_progress(
            98,
            _topic_angle_progress_message(result),
            run_id=run_id,
            pid=process_id,
        )
        return result

    base_refresh.run_automatic_cleanup_if_due = tracked_cleanup
    base_refresh.refresh_trend_sources_short_connections = tracked_refresh
    base_refresh._run_background_topic_angles = tracked_topic_angles
    return original_cleanup, original_refresh, original_topic_angles


def _restore_progress_wrappers(
    originals: tuple[Callable[..., Any], Callable[..., Any], Callable[..., Any]],
) -> None:
    original_cleanup, original_refresh, original_topic_angles = originals
    base_refresh.run_automatic_cleanup_if_due = original_cleanup
    base_refresh.refresh_trend_sources_short_connections = original_refresh
    base_refresh._run_background_topic_angles = original_topic_angles


def _run_refresh() -> int:
    process_id = os.getpid()
    base_refresh.init_database(base_refresh.DEFAULT_DB_PATH)
    with base_refresh.connect_database(base_refresh.DEFAULT_DB_PATH) as con:
        run_id = base_refresh.start_collection_run(con, _RUN_TYPE)

    start_dashboard_refresh_progress(
        pid=process_id,
        run_id=run_id,
        message="최신 데이터 수집·분석 준비 중",
    )
    originals = _install_progress_wrappers(
        run_id=run_id,
        process_id=process_id,
    )
    try:
        exit_code, result = base_refresh._run_refresh_body(run_id)
        result, topic_angle_warning = resume_deferred_topic_angles(
            result,
            runner=base_refresh._run_background_topic_angles,
            db_path=base_refresh.DEFAULT_DB_PATH,
        )
        if topic_angle_warning:
            print(f"주의: {topic_angle_warning}")
    except Exception as exc:
        finish_dashboard_refresh_progress(
            success=False,
            message="최신 데이터 수집·분석 실패",
            summary="최신 데이터 수집·분석을 완료하지 못했습니다.",
            error_message=f"{type(exc).__name__}: {exc}",
            run_id=run_id,
            pid=process_id,
        )
        try:
            with base_refresh.connect_database(base_refresh.DEFAULT_DB_PATH) as con:
                base_refresh.finish_collection_run(con, run_id, error=exc)
        except Exception as history_exc:
            print(f"경고: 실패 실행 이력을 저장하지 못했습니다 - {history_exc}")
        raise
    finally:
        _restore_progress_wrappers(originals)

    try:
        with base_refresh.connect_database(base_refresh.DEFAULT_DB_PATH) as con:
            base_refresh.finish_collection_run(con, run_id, result=result)
    except Exception as exc:
        finish_dashboard_refresh_progress(
            success=False,
            message="수집 결과 이력 저장 실패",
            summary="수집은 끝났지만 실행 이력을 저장하지 못했습니다.",
            error_message=f"{type(exc).__name__}: {exc}",
            run_id=run_id,
            pid=process_id,
        )
        raise

    finish_dashboard_refresh_progress(
        success=True,
        message="최신 데이터 수집·분석 완료",
        summary=str(
            result.get("summary")
            or "최신 데이터 수집·분석을 완료했습니다."
        ),
        run_id=run_id,
        pid=process_id,
    )
    return exit_code


def _record_overlap(attempt) -> None:
    finish_dashboard_refresh_progress(
        success=False,
        message="기존 수집 작업과 겹쳐 새 실행을 시작하지 않았습니다.",
        summary=str(attempt.message or "중복 수집 요청을 생략했습니다."),
    )
    base_refresh.init_database(base_refresh.DEFAULT_DB_PATH)
    with base_refresh.connect_database(base_refresh.DEFAULT_DB_PATH) as con:
        base_refresh.record_skipped_overlap(
            con,
            _RUN_TYPE,
            summary=attempt.message,
        )


def main() -> int:
    return base_refresh.run_with_trend_refresh_lock(
        PROJECT_ROOT,
        launcher="dashboard_background",
        runner=_run_refresh,
        overlap_callback=_record_overlap,
    )


if __name__ == "__main__":
    raise SystemExit(main())
