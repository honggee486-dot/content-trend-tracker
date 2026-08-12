from __future__ import annotations

from types import SimpleNamespace

from src.services.trend_cluster_job_runtime_contract import (
    CLUSTERING_ADAPTIVE_JOB_STALE_MINUTES,
    install_job_token_contract,
)


def test_job_contract_replaces_candidate_count_guidance_with_token_guidance() -> None:
    module = SimpleNamespace(
        JOB_STALE_AFTER_MINUTES=20,
        get_clustering_job_settings=lambda _con: {
            "model_name": "gemini-test",
            "scan_limit": 4_000,
            "batch_size": 350,
            "max_batches": 5,
        },
        create_clustering_job=lambda *_args, **_kwargs: {
            "created": True,
            "job_id": "job-1",
            "message": "요청당 최대 350개",
        },
    )

    install_job_token_contract(module, scan_limit=50_000, max_batches=1)

    settings = module.get_clustering_job_settings(object())
    created = module.create_clustering_job(object(), launcher="dashboard")
    assert settings["scan_limit"] == 50_000
    assert settings["batch_size"] == 50_000
    assert settings["max_batches"] == 1
    assert module.JOB_STALE_AFTER_MINUTES == CLUSTERING_ADAPTIVE_JOB_STALE_MINUTES
    assert "225,000토큰" in created["message"]
    assert "250,000토큰" in created["message"]
    assert "전체 스냅샷을 한 번" in created["message"]
    assert "한 요청씩" in created["message"]
    assert "요청당 최대 350개" not in created["message"]


def test_job_contract_preserves_existing_active_job_message() -> None:
    module = SimpleNamespace(
        JOB_STALE_AFTER_MINUTES=120,
        get_clustering_job_settings=lambda _con: {},
        create_clustering_job=lambda *_args, **_kwargs: {
            "created": False,
            "job_id": "job-existing",
            "message": "이미 군집 처리 작업이 실행 중입니다.",
        },
    )

    install_job_token_contract(module, scan_limit=50_000, max_batches=1)

    result = module.create_clustering_job(object(), launcher="dashboard")
    assert result["created"] is False
    assert result["message"] == "이미 군집 처리 작업이 실행 중입니다."
    assert module.JOB_STALE_AFTER_MINUTES == 120
