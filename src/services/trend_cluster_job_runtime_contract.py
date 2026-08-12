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

    install_job_progress_contract(job_module)
