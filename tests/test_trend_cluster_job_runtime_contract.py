from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import src.services.trend_clustering_job_service as job_service
from src.services.trend_cluster_job_runtime_contract import (
    CLUSTERING_ADAPTIVE_JOB_STALE_MINUTES,
    _install_ranking_only_refresh_contract,
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


def test_job_contract_applies_ranking_only_refresh_before_no_pending_fast_exit() -> None:
    preparation = SimpleNamespace(
        status="ready",
        pending_item_count=0,
        selected_items=(),
    )
    calculation = SimpleNamespace(status="calculated")
    calls: list[tuple[str, object]] = []

    def calculate(received):
        calls.append(("calculate", received))
        return calculation

    def finalize(con, received):
        calls.append(("finalize", (con, received)))
        return {"status": "success"}

    module = SimpleNamespace(
        JOB_STALE_AFTER_MINUTES=20,
        get_clustering_job_settings=lambda _con: {},
        create_clustering_job=lambda *_args, **_kwargs: {"created": False},
        prepare_trend_ranking_rebuild=lambda _con, **_kwargs: preparation,
        calculate_prepared_trend_rankings=calculate,
        finalize_prepared_trend_rankings=finalize,
    )
    con = object()

    install_job_token_contract(module, scan_limit=50_000, max_batches=1)
    returned = module.prepare_trend_ranking_rebuild(con, lookback_hours=72)

    assert returned is preparation
    assert calls == [
        ("calculate", preparation),
        ("finalize", (con, calculation)),
    ]


def test_job_contract_does_not_precalculate_reused_or_pending_work() -> None:
    state = {
        "preparation": SimpleNamespace(
            status="reused",
            pending_item_count=0,
            selected_items=(),
        )
    }
    calls: list[str] = []
    module = SimpleNamespace(
        JOB_STALE_AFTER_MINUTES=20,
        get_clustering_job_settings=lambda _con: {},
        create_clustering_job=lambda *_args, **_kwargs: {"created": False},
        prepare_trend_ranking_rebuild=lambda _con, **_kwargs: state["preparation"],
        calculate_prepared_trend_rankings=lambda _preparation: calls.append("calculate"),
        finalize_prepared_trend_rankings=lambda _con, _calculation: calls.append("finalize"),
    )

    install_job_token_contract(module, scan_limit=50_000, max_batches=1)
    module.prepare_trend_ranking_rebuild(object(), lookback_hours=72)

    state["preparation"] = SimpleNamespace(
        status="ready",
        pending_item_count=2,
        selected_items=({"source_item_id": "item-1"},),
    )
    module.prepare_trend_ranking_rebuild(object(), lookback_hours=72)

    assert calls == []


def test_ranking_only_refresh_runs_through_job_loop_without_regular_batch(
    monkeypatch,
    tmp_path,
) -> None:
    preparation = SimpleNamespace(
        status="ready",
        pending_item_count=0,
        selected_items=(),
    )
    calculation = SimpleNamespace(status="calculated")
    calls: list[str] = []
    statuses: list[tuple[str, bool]] = []
    released: list[bool] = []

    class DummyConnection:
        def execute(self, _query, _params=None):
            return self

        def fetchone(self):
            return (50_000, 1)

    class DummyLock:
        def release(self) -> None:
            released.append(True)

    @contextmanager
    def fake_connect_database(_db_path):
        yield DummyConnection()

    def calculate(received):
        assert received is preparation
        calls.append("calculate")
        return calculation

    def finalize(_con, received):
        assert received is calculation
        calls.append("finalize")
        return {"status": "success"}

    def update_status(_con, _job_id, *, status, finished=False, **_kwargs):
        statuses.append((str(status), bool(finished)))

    def forbidden_regular_batch(*_args, **_kwargs):
        raise AssertionError("순위 전용 갱신은 일반 군집 배치 경로로 들어가면 안 됩니다.")

    monkeypatch.setattr(
        job_service,
        "acquire_trend_clustering_lock",
        lambda **_kwargs: SimpleNamespace(
            acquired=True,
            lock=DummyLock(),
            message="",
        ),
    )
    monkeypatch.setattr(job_service, "connect_database", fake_connect_database)
    monkeypatch.setattr(job_service, "_update_job_status", update_status)
    monkeypatch.setattr(
        job_service,
        "prepare_trend_ranking_rebuild",
        lambda _con, **_kwargs: preparation,
    )
    monkeypatch.setattr(job_service, "calculate_prepared_trend_rankings", calculate)
    monkeypatch.setattr(job_service, "finalize_prepared_trend_rankings", finalize)
    monkeypatch.setattr(job_service, "_apply_job_limits", forbidden_regular_batch)
    monkeypatch.setattr(job_service, "_mark_batch_started", forbidden_regular_batch)
    monkeypatch.setattr(job_service, "_record_batch", forbidden_regular_batch)

    _install_ranking_only_refresh_contract(job_service)
    exit_code = job_service.run_clustering_job(
        "job-ranking-only",
        db_path=tmp_path / "ranking-only.duckdb",
        project_root=tmp_path,
        lookback_hours=72,
    )

    assert exit_code == 0
    assert calls == ["calculate", "finalize"]
    assert statuses == [("running", False), ("success", True)]
    assert released == [True]
