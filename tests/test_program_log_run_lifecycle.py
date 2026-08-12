from __future__ import annotations

from src.services.program_log_context import current_program_log_correlation_id
from src.services.program_log_run_lifecycle import install_program_log_run_lifecycle


def test_collection_run_context_stays_active_until_finish(monkeypatch) -> None:
    from src.services import collection_history_service as history

    seen = []

    def fake_start(con, run_type, *args, **kwargs):
        seen.append(("start", current_program_log_correlation_id()))
        return "collection_123"

    def fake_finish(con, run_id, *args, **kwargs):
        seen.append(("finish", current_program_log_correlation_id()))
        return "success"

    monkeypatch.setattr(history, "start_collection_run", fake_start)
    monkeypatch.setattr(history, "finish_collection_run", fake_finish)
    install_program_log_run_lifecycle()

    run_id = history.start_collection_run(object(), "manual_refresh")
    assert run_id == "collection_123"
    assert current_program_log_correlation_id() == "collection_123"

    result = history.finish_collection_run(object(), run_id)
    assert result == "success"
    assert seen == [
        ("start", ""),
        ("finish", "collection_123"),
    ]
    assert current_program_log_correlation_id() == ""


def test_new_collection_run_clears_stale_context(monkeypatch) -> None:
    from src.services import collection_history_service as history
    from src.services.program_log_context import begin_program_log_correlation

    begin_program_log_correlation("stale_run")

    def fake_start(con, run_type, *args, **kwargs):
        assert current_program_log_correlation_id() == ""
        return "collection_new"

    def fake_finish(con, run_id, *args, **kwargs):
        assert current_program_log_correlation_id() == "collection_new"
        return "success"

    monkeypatch.setattr(history, "start_collection_run", fake_start)
    monkeypatch.setattr(history, "finish_collection_run", fake_finish)
    install_program_log_run_lifecycle()

    run_id = history.start_collection_run(object(), "manual_refresh")
    assert current_program_log_correlation_id() == "collection_new"

    assert history.finish_collection_run(object(), run_id) == "success"
    assert current_program_log_correlation_id() == ""
