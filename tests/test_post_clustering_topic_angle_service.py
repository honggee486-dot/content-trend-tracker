from __future__ import annotations

from contextlib import contextmanager

import src.services.post_clustering_topic_angle_service as service


class _Connection:
    def __init__(self, row) -> None:
        self.row = row
        self.arguments = None

    def execute(self, sql, arguments):
        self.arguments = (sql, list(arguments))
        return self

    def fetchone(self):
        return self.row


def _factory(row):
    connection = _Connection(row)

    @contextmanager
    def factory(_path):
        yield connection

    return connection, factory


def test_topic_angles_run_after_success_with_zero_remaining(monkeypatch, tmp_path) -> None:
    connection, factory = _factory(("success", 0))
    captured = []
    monkeypatch.setattr(
        service,
        "record_program_event",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    result = service.run_topic_angles_after_clustering(
        "cluster_job_123",
        db_path=tmp_path / "test.duckdb",
        runner=lambda _path: (
            {
                "status": "success",
                "generated_clusters": 4,
                "generated_angles": 12,
            },
            "",
        ),
        connection_factory=factory,
    )

    assert connection.arguments[1] == ["cluster_job_123"]
    assert result["status"] == "success"
    assert result["generated_clusters"] == 4
    assert result["clustering_remaining_items"] == 0
    assert result["resumed_with_clustering_backlog"] is False
    assert [row["status"] for row in captured] == ["started", "completed"]
    assert all(row["correlation_id"] == "cluster_job_123" for row in captured)
    assert captured[-1]["item_count"] == 4


def test_topic_angles_run_after_partial_clustering_with_backlog(monkeypatch, tmp_path) -> None:
    _connection, factory = _factory(("partial", 25))
    captured = []
    calls = []
    monkeypatch.setattr(
        service,
        "record_program_event",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    result = service.run_topic_angles_after_clustering(
        "cluster_job_123",
        db_path=tmp_path / "test.duckdb",
        runner=lambda path: calls.append(path) or (
            {
                "status": "success",
                "generated_clusters": 3,
                "generated_angles": 9,
            },
            "",
        ),
        connection_factory=factory,
    )

    assert len(calls) == 1
    assert result["status"] == "success"
    assert result["generated_clusters"] == 3
    assert result["clustering_remaining_items"] == 25
    assert result["resumed_with_clustering_backlog"] is True
    assert "군집 미처리 25개 남음" in captured[0]["detail"]
    assert "다음 작업에서 계속 처리" in captured[-1]["detail"]


def test_topic_angles_remain_deferred_when_clustering_did_not_store_results(tmp_path) -> None:
    _connection, factory = _factory(("skipped_overlap", 25))
    calls = []

    result = service.run_topic_angles_after_clustering(
        "cluster_job_123",
        db_path=tmp_path / "test.duckdb",
        runner=lambda _path: calls.append(True) or ({"status": "success"}, ""),
        connection_factory=factory,
    )

    assert result == {
        "status": "deferred_for_clustering_backlog",
        "job_status": "skipped_overlap",
        "remaining_items": 25,
        "generated_clusters": 0,
        "generated_angles": 0,
    }
    assert calls == []


def test_topic_angle_failure_does_not_raise_or_cancel_clustering(monkeypatch, tmp_path) -> None:
    _connection, factory = _factory(("success", 0))
    captured = []
    monkeypatch.setattr(
        service,
        "record_program_event",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    def fail(_path):
        raise RuntimeError("Gemini unavailable")

    result = service.run_topic_angles_after_clustering(
        "cluster_job_123",
        db_path=tmp_path / "test.duckdb",
        runner=fail,
        connection_factory=factory,
    )

    assert result["status"] == "unexpected_error"
    assert "Gemini unavailable" in str(result["error_message"])
    assert captured[-1]["status"] == "failed"
