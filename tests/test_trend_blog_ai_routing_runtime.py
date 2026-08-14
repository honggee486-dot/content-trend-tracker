from __future__ import annotations

from types import SimpleNamespace

import src.services.trend_blog_ai_routing_runtime as runtime


def test_refresh_contract_runs_only_after_real_collection_and_preserves_result(
    monkeypatch,
) -> None:
    module = SimpleNamespace()
    expected = {"ranking": {"clusters": 3}, "errors": {}}
    module.refresh_trend_sources_short_connections = lambda *args, **kwargs: expected
    calls: list[str] = []
    progress: list[tuple[float, str]] = []

    def fake_routing(db_path, *, progress_callback=None):
        calls.append(str(db_path))
        if progress_callback is not None:
            progress_callback(0.5, "Flash-Lite 블로그 분류 1/2 요청 중")
        return {"status": "success", "routed_clusters": 3}, ""

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(runtime, "run_trend_blog_ai_routing", fake_routing)
    runtime.install_trend_blog_ai_routing_contract(module)

    result = module.refresh_trend_sources_short_connections(
        "sample.duckdb",
        collection_run_id="collection-1",
        progress_callback=lambda value, message: progress.append((value, message)),
    )

    assert result is expected
    assert calls == ["sample.duckdb"]
    assert progress == [(1.0, "Flash-Lite 블로그 분류 1/2 요청 중")]


def test_refresh_contract_never_calls_external_routing_during_pytest(monkeypatch) -> None:
    module = SimpleNamespace()
    module.refresh_trend_sources_short_connections = lambda *args, **kwargs: {"ok": True}
    calls: list[str] = []

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/example.py::test_case (call)")
    monkeypatch.setattr(
        runtime,
        "run_trend_blog_ai_routing",
        lambda *args, **kwargs: calls.append("called"),
    )
    runtime.install_trend_blog_ai_routing_contract(module)

    result = module.refresh_trend_sources_short_connections(
        "sample.duckdb",
        collection_run_id="collection-1",
    )

    assert result == {"ok": True}
    assert calls == []


def test_refresh_contract_skips_helper_calls_without_collection_run_id(monkeypatch) -> None:
    module = SimpleNamespace()
    module.refresh_trend_sources_short_connections = lambda *args, **kwargs: {"ok": True}
    calls: list[str] = []

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(
        runtime,
        "run_trend_blog_ai_routing",
        lambda *args, **kwargs: calls.append("called"),
    )
    runtime.install_trend_blog_ai_routing_contract(module)

    result = module.refresh_trend_sources_short_connections("sample.duckdb")

    assert result == {"ok": True}
    assert calls == []
