from __future__ import annotations

from functools import wraps
import sys
from typing import Any

from src.services.trend_cluster_existing_index import install_existing_cluster_index
from src.services.trend_cluster_live_progress import install_job_progress_contract
from src.services.trend_cluster_token_runtime import (
    CLUSTERING_TARGET_INPUT_TOKENS,
    CLUSTERING_TPM_LIMIT,
)

CLUSTERING_ADAPTIVE_JOB_STALE_MINUTES = 90


def _install_ranking_only_refresh_contract(job_module: Any) -> None:
    """미처리 원문이 없어도 순위 서명 변경은 빠른 종료 전에 반영합니다.

    정책·점수 규칙만 바뀐 경우 준비 단계는 ``ready``지만 미처리 원문 수는 0일 수
    있습니다. 기존 작업 루프는 이 상태를 바로 종료하므로 새 순위 서명과 기존 군집의
    재점수가 저장되지 않았습니다. 선택 원문이 비어 있으면 계산 단계가 외부 군집 API를
    호출하지 않으므로, 같은 짧은 DB 연결 안에서 기존 군집만 재점수하고 서명을 저장한
    뒤 원래 빠른 종료 흐름을 그대로 사용합니다.
    """
    original_prepare = getattr(job_module, "prepare_trend_ranking_rebuild", None)
    if not callable(original_prepare) or getattr(
        original_prepare,
        "_trend_cluster_ranking_only_refresh_contract",
        False,
    ):
        return

    @wraps(original_prepare)
    def prepare_with_ranking_only_refresh(con, *args, **kwargs):
        preparation = original_prepare(con, *args, **kwargs)
        if str(getattr(preparation, "status", "") or "") != "ready":
            return preparation
        if int(getattr(preparation, "pending_item_count", 0) or 0) > 0:
            return preparation
        if tuple(getattr(preparation, "selected_items", ()) or ()):
            return preparation

        calculate = getattr(job_module, "calculate_prepared_trend_rankings", None)
        finalize = getattr(job_module, "finalize_prepared_trend_rankings", None)
        if not callable(calculate) or not callable(finalize):
            return preparation

        calculation = calculate(preparation)
        finalize(con, calculation)
        return preparation

    prepare_with_ranking_only_refresh._trend_cluster_ranking_only_refresh_contract = True  # type: ignore[attr-defined]
    job_module.prepare_trend_ranking_rebuild = prepare_with_ranking_only_refresh


def install_job_token_contract(
    job_module: Any,
    *,
    scan_limit: int,
    max_batches: int,
) -> None:
    """작업 생성 이력은 유지하고 사용자 안내를 토큰 기준으로 바꿉니다."""
    discovery_module = sys.modules.get("src.services.trend_discovery_service")
    if discovery_module is not None:
        install_existing_cluster_index(discovery_module)
    job_module.JOB_STALE_AFTER_MINUTES = max(
        int(getattr(job_module, "JOB_STALE_AFTER_MINUTES", 0) or 0),
        CLUSTERING_ADAPTIVE_JOB_STALE_MINUTES,
    )

    original_settings = getattr(job_module, "get_clustering_job_settings", None)
    if callable(original_settings) and not getattr(
        original_settings,
        "_trend_cluster_token_contract",
        False,
    ):

        @wraps(original_settings)
        def token_settings(con):
            settings = dict(original_settings(con))
            settings["scan_limit"] = int(scan_limit)
            settings["batch_size"] = int(scan_limit)
            settings["max_batches"] = int(max_batches)
            return settings

        token_settings._trend_cluster_token_contract = True  # type: ignore[attr-defined]
        job_module.get_clustering_job_settings = token_settings

    original_create = getattr(job_module, "create_clustering_job", None)
    if callable(original_create) and not getattr(
        original_create,
        "_trend_cluster_token_contract",
        False,
    ):

        @wraps(original_create)
        def token_create(*args, **kwargs):
            result = dict(original_create(*args, **kwargs))
            if result.get("created"):
                result["message"] = (
                    "2차 군집 작업을 시작했습니다. 미처리 전체 스냅샷을 한 번 준비한 뒤 "
                    "제목·사건·식별·기존 군집 관점을 순서대로 처리합니다. 각 관점은 "
                    f"요청당 예상 입력 {CLUSTERING_TARGET_INPUT_TOKENS:,}토큰 이하로 자동 분할하고, "
                    f"최근 60초 입력은 {CLUSTERING_TPM_LIMIT:,}토큰을 넘지 않게 한 요청씩 전송합니다."
                )
            return result

        token_create._trend_cluster_token_contract = True  # type: ignore[attr-defined]
        job_module.create_clustering_job = token_create

    _install_ranking_only_refresh_contract(job_module)
    install_job_progress_contract(job_module)
