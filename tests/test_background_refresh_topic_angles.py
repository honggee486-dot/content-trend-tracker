from contextlib import contextmanager
from types import SimpleNamespace

from scripts import refresh_trends
from src.config import GeminiConfig


def _config(*, batch_limit: int = 25, max_parallel: int = 4) -> GeminiConfig:
    return GeminiConfig(
        api_key="test-key",
        model="gemini-3.6-flash",
        app_id="content-trend-tracker",
        quota_scope_id="test-scope",
        timeout_seconds=60,
        retry_wait_seconds=2.0,
        retry_max_wait_seconds=30.0,
        topic_angle_timeout_seconds=360,
        topic_angle_batch_limit=batch_limit,
        topic_angle_max_parallel_requests=max_parallel,
        topic_angle_request_stagger_seconds=5.0,
        topic_angle_min_opportunity_score=50.0,
        daily_request_reference_limit=20,
        draft_thinking_level="high",
        topic_angle_thinking_level="high",
    )


def _generated_result(**overrides):
    values = {
        "status": "success_after_retry",
        "requested_clusters": 50,
        "generated_clusters": 24,
        "generated_angles": 72,
        "skipped_sensitive_clusters": 3,
        "attempts": 3,
        "error_type": "",
        "error_message": "",
        "requested_batches": 2,
        "completed_batches": 2,
        "failed_batches": 0,
        "items_per_request": 25,
        "max_parallel_requests": 4,
        "duration_seconds": 12.5,
        "min_opportunity_score": 50.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_background_topic_angles_closes_db_during_gemini_request(monkeypatch):
    events: list[str] = []
    config = _config(batch_limit=23, max_parallel=4)
    preparation = SimpleNamespace(status="ready")
    execution = SimpleNamespace(preparation=preparation)
    generated = _generated_result(items_per_request=23)
    connection_count = 0

    @contextmanager
    def fake_connect(_db_path):
        nonlocal connection_count
        connection_count += 1
        connection = f"connection-{connection_count}"
        events.append(f"open:{connection}")
        try:
            yield connection
        finally:
            events.append(f"close:{connection}")

    monkeypatch.setattr(refresh_trends, "get_gemini_config", lambda: config)
    monkeypatch.setattr(
        refresh_trends,
        "build_gemini_config_for_purpose",
        lambda con, purpose, base_config: config,
    )
    monkeypatch.setattr(refresh_trends, "connect_database", fake_connect)

    def fake_prepare(con, *, config: GeminiConfig, limit: int):
        assert config is not None
        assert config.topic_angle_batch_limit == 23
        assert config.topic_angle_max_parallel_requests == 4
        assert config.topic_angle_thinking_level == "high"
        events.append(f"prepare:{con}:{limit}")
        return preparation

    def fake_execute(prepared, *, config: GeminiConfig):
        assert prepared is preparation
        assert config.topic_angle_batch_limit == 23
        assert config.topic_angle_thinking_level == "high"
        assert events[-1] == "close:connection-1"
        events.append("execute:no-db")
        return execution

    def fake_finalize(con, *, config: GeminiConfig, execution):
        assert config.topic_angle_batch_limit == 23
        assert config.topic_angle_thinking_level == "high"
        events.append(f"finalize:{con}")
        return generated

    monkeypatch.setattr(refresh_trends, "prepare_missing_topic_angles", fake_prepare)
    monkeypatch.setattr(refresh_trends, "execute_prepared_topic_angles", fake_execute)
    monkeypatch.setattr(refresh_trends, "finalize_prepared_topic_angles", fake_finalize)

    payload, warning = refresh_trends._run_background_topic_angles("test.duckdb")

    assert events == [
        "open:connection-1",
        "prepare:connection-1:23",
        "close:connection-1",
        "execute:no-db",
        "open:connection-2",
        "finalize:connection-2",
        "close:connection-2",
    ]
    assert payload["status"] == "success_after_retry"
    assert payload["generated_clusters"] == 24
    assert payload["generated_angles"] == 72
    assert payload["skipped_sensitive_clusters"] == 3
    assert payload["attempts"] == 3
    assert payload["requested_batches"] == 2
    assert payload["duration_seconds"] == 12.5
    assert warning == ""


def test_background_topic_angles_uses_configured_items_per_request(monkeypatch):
    config = _config(batch_limit=17, max_parallel=1)
    captured: dict[str, object] = {}
    preparation = SimpleNamespace(status="nothing_to_generate")
    execution = SimpleNamespace(preparation=preparation)

    @contextmanager
    def fake_connect(_db_path):
        yield object()

    monkeypatch.setattr(refresh_trends, "get_gemini_config", lambda: config)
    monkeypatch.setattr(
        refresh_trends,
        "build_gemini_config_for_purpose",
        lambda con, purpose, base_config: config,
    )
    monkeypatch.setattr(refresh_trends, "connect_database", fake_connect)

    def fake_prepare(con, *, config: GeminiConfig, limit: int):
        captured["limit"] = limit
        captured["config"] = config
        return preparation

    monkeypatch.setattr(refresh_trends, "prepare_missing_topic_angles", fake_prepare)
    monkeypatch.setattr(
        refresh_trends,
        "execute_prepared_topic_angles",
        lambda preparation, *, config: execution,
    )
    monkeypatch.setattr(
        refresh_trends,
        "finalize_prepared_topic_angles",
        lambda con, *, config, execution: _generated_result(
            status="nothing_to_generate",
            requested_clusters=0,
            generated_clusters=0,
            generated_angles=0,
            attempts=0,
            requested_batches=0,
            completed_batches=0,
            items_per_request=17,
            max_parallel_requests=1,
            duration_seconds=0.0,
        ),
    )

    refresh_trends._run_background_topic_angles("test.duckdb")

    assert captured["limit"] == 17
    assert captured["config"] is config
    assert config.topic_angle_thinking_level == "high"


def test_background_topic_angle_failure_does_not_raise(monkeypatch):
    config = _config(batch_limit=25, max_parallel=4)

    @contextmanager
    def fake_connect(_db_path):
        yield object()

    monkeypatch.setattr(refresh_trends, "get_gemini_config", lambda: config)
    monkeypatch.setattr(
        refresh_trends,
        "build_gemini_config_for_purpose",
        lambda con, purpose, base_config: config,
    )
    monkeypatch.setattr(refresh_trends, "connect_database", fake_connect)

    def fail_prepare(*args, **kwargs):
        raise RuntimeError("temporary Gemini failure")

    monkeypatch.setattr(refresh_trends, "prepare_missing_topic_angles", fail_prepare)

    payload, warning = refresh_trends._run_background_topic_angles("test.duckdb")

    assert payload["status"] == "unexpected_error"
    assert payload["generated_clusters"] == 0
    assert "temporary Gemini failure" in payload["error_message"]
    assert "Gemini 글감 자동 분석 실패" in warning


def test_topic_angle_summary_handles_no_work_and_saved_results():
    assert (
        refresh_trends._topic_angle_summary(
            {
                "status": "nothing_to_generate",
                "requested_clusters": 0,
                "generated_clusters": 0,
                "generated_angles": 0,
            }
        )
        == "Gemini 새 분석 없음"
    )
    assert (
        refresh_trends._topic_angle_summary(
            SimpleNamespace(
                status="partial_success",
                requested_clusters=12,
                generated_clusters=10,
                generated_angles=30,
            )
        )
        == "Gemini 대상 12개·글감 10개·방향 30개 저장"
    )
