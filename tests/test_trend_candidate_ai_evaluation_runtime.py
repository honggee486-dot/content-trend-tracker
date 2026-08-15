from __future__ import annotations

from types import SimpleNamespace

import src.services.trend_candidate_ai_evaluation_runtime as runtime


def test_ranking_allows_candidate_evaluation_only_after_stored_ranking() -> None:
    assert runtime._ranking_allows_evaluation(
        {"ranking": {"ai_clustering": {"status": "success"}}}
    )
    assert not runtime._ranking_allows_evaluation(
        {"ranking": {"status": "skipped_overlap", "ai_clustering": {"status": "success"}}}
    )
    assert not runtime._ranking_allows_evaluation(
        {"ranking": {"ai_clustering": {"status": "skipped_source_failure"}}}
    )
    assert not runtime._ranking_allows_evaluation({})


def test_refresh_wrapper_runs_evaluation_after_original_and_attaches_usage(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    events: list[str] = []
    database = tmp_path / "test.duckdb"

    def original(*args, **kwargs):
        events.append("refresh")
        return {
            "ranking": {"ai_clustering": {"status": "success"}},
            "warnings": {},
        }

    module = SimpleNamespace(refresh_trend_sources_short_connections=original)

    def fake_evaluation(path, *, progress_callback=None):
        assert path == database.resolve()
        events.append("evaluation")
        if progress_callback is not None:
            progress_callback(1.0, "평가 완료")
        return (
            {
                "status": "success",
                "requested_clusters": 12,
                "evaluated_clusters": 12,
                "total_tokens": 3456,
            },
            "",
        )

    monkeypatch.setattr(runtime, "run_trend_candidate_ai_evaluation", fake_evaluation)
    runtime._install_refresh_evaluation(module)

    progress: list[tuple[float, str]] = []
    result = module.refresh_trend_sources_short_connections(
        database,
        collection_run_id="collection-1",
        progress_callback=lambda value, message: progress.append((value, message)),
    )

    assert events == ["refresh", "evaluation"]
    assert result["candidate_ai_evaluation"]["evaluated_clusters"] == 12
    assert result["candidate_ai_evaluation"]["total_tokens"] == 3456
    assert progress[-1] == (1.0, "평가 완료")


def test_refresh_wrapper_skips_evaluation_for_overlap(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    calls: list[object] = []
    module = SimpleNamespace(
        refresh_trend_sources_short_connections=lambda *args, **kwargs: {
            "ranking": {
                "status": "skipped_overlap",
                "ai_clustering": {"status": "skipped_overlap"},
            }
        }
    )
    monkeypatch.setattr(
        runtime,
        "run_trend_candidate_ai_evaluation",
        lambda *args, **kwargs: calls.append(True) or ({"status": "success"}, ""),
    )
    runtime._install_refresh_evaluation(module)

    result = module.refresh_trend_sources_short_connections(
        tmp_path / "test.duckdb",
        collection_run_id="collection-1",
    )

    assert calls == []
    assert "candidate_ai_evaluation" not in result


def test_post_clustering_runs_evaluation_and_blog_routing_before_angles(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    from src.services import post_clustering_topic_angle_service as post_module

    events: list[str] = []
    database = (tmp_path / "test.duckdb").resolve()

    def original(job_id: str, *args, **kwargs):
        assert job_id == "job-1"
        events.append("angles")
        return {"status": "success", "generated_angles": 3}

    monkeypatch.setattr(post_module, "run_topic_angles_after_clustering", original)
    monkeypatch.setattr(runtime, "_post_clustering_job_ready", lambda path, job_id: True)

    def fake_evaluation(path):
        assert path == database
        events.append("evaluation")
        return {"status": "success", "evaluated_clusters": 40}, ""

    def fake_routing(path):
        assert path == database
        events.append("routing")
        return {"status": "success", "routed_clusters": 12}, ""

    monkeypatch.setattr(runtime, "run_trend_candidate_ai_evaluation", fake_evaluation)
    monkeypatch.setattr(runtime, "run_trend_blog_ai_routing", fake_routing)

    runtime._install_post_clustering_evaluation()
    result = post_module.run_topic_angles_after_clustering(
        "job-1",
        db_path=database,
    )

    assert events == ["evaluation", "routing", "angles"]
    assert result["candidate_ai_evaluation"]["evaluated_clusters"] == 40
    assert result["blog_ai_routing"]["routed_clusters"] == 12
    assert result["generated_angles"] == 3


def test_post_clustering_keeps_routing_and_angles_when_evaluation_fails(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    from src.services import post_clustering_topic_angle_service as post_module

    events: list[str] = []
    database = (tmp_path / "test.duckdb").resolve()

    monkeypatch.setattr(
        post_module,
        "run_topic_angles_after_clustering",
        lambda job_id, *args, **kwargs: events.append("angles") or {"status": "success"},
    )
    monkeypatch.setattr(runtime, "_post_clustering_job_ready", lambda path, job_id: True)

    def failed_evaluation(path):
        events.append("evaluation")
        raise RuntimeError("evaluation failed")

    monkeypatch.setattr(runtime, "run_trend_candidate_ai_evaluation", failed_evaluation)
    monkeypatch.setattr(
        runtime,
        "run_trend_blog_ai_routing",
        lambda path: events.append("routing") or ({"status": "success"}, ""),
    )

    runtime._install_post_clustering_evaluation()
    result = post_module.run_topic_angles_after_clustering("job-2", db_path=database)

    assert events == ["evaluation", "routing", "angles"]
    assert result["candidate_ai_evaluation"]["status"] == "unexpected_error"
    assert result["candidate_ai_evaluation_warning"] == "evaluation failed"
    assert result["blog_ai_routing"]["status"] == "success"
