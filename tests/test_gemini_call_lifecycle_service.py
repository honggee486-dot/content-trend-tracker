from __future__ import annotations

from types import SimpleNamespace

from src.database import connect_database, init_database
from src.services.gemini_call_lifecycle_service import (
    begin_gemini_api_call,
    build_lifecycle_record_call,
    mark_gemini_api_provider_complete,
)


def _config():
    return SimpleNamespace(
        app_id="content-trend-tracker",
        quota_scope_id="test-scope",
        model="gemini-3.5-flash-lite",
    )


def test_call_is_inserted_before_send_then_same_row_is_completed(tmp_path) -> None:
    db_path = tmp_path / "lifecycle.duckdb"
    init_database(db_path)
    config = _config()

    call_id = begin_gemini_api_call(
        config,
        "request body",
        "request-hash",
        feature_id="trend_cluster_grouping_v3",
        thinking_level="minimal",
        timeout_seconds=120,
        rate_limit_wait_seconds=12.5,
        db_path=db_path,
    )
    assert call_id

    with connect_database(db_path) as con:
        started = con.execute(
            """
            SELECT status, started_at, finished_at, rate_limit_wait_seconds
            FROM gemini_api_calls WHERE call_id = ?
            """,
            [call_id],
        ).fetchone()
    assert started[0] == "in_progress"
    assert started[1] is not None
    assert started[2] is None
    assert started[3] == 12.5

    mark_gemini_api_provider_complete(
        call_id,
        result=("{}", 91, 30, 7, 128, "STOP", ""),
        db_path=db_path,
    )
    with connect_database(db_path) as con:
        provider_done = con.execute(
            """
            SELECT status, input_tokens, total_tokens, finished_at, duration_ms
            FROM gemini_api_calls WHERE call_id = ?
            """,
            [call_id],
        ).fetchone()
    assert provider_done[0] == "response_received"
    assert provider_done[1] == 91
    assert provider_done[2] == 128
    assert provider_done[3] is not None
    assert provider_done[4] >= 0

    original_calls = []

    def original(*args, **kwargs):
        original_calls.append(True)

    wrapped = build_lifecycle_record_call(original)
    with connect_database(db_path) as con:
        wrapped(
            con,
            config=config,
            content_pack_id="cluster-request",
            request_hash="request-hash",
            feature_id="trend_cluster_grouping_v3",
            feature_version="7",
            attempt_number=1,
            cache_hit=False,
            status="success",
            http_status=200,
            error_type="",
            retry_reason="",
            retry_wait_seconds=0,
            input_tokens=91,
            output_tokens=30,
            thought_tokens=7,
            total_tokens=128,
            duration_ms=99999,
            error_message="",
            request_text="request body",
            response_text="{}",
            requested_item_count=25,
            configured_items_per_request=300,
            thinking_level="minimal",
            request_timeout_seconds=120,
            finish_reason="STOP",
            finish_message="",
        )
        final = con.execute(
            """
            SELECT COUNT(*), MAX(status), MAX(feature_version), MAX(requested_item_count),
                   MAX(started_at), MAX(finished_at), MAX(rate_limit_wait_seconds)
            FROM gemini_api_calls WHERE request_hash = 'request-hash'
            """
        ).fetchone()

    assert original_calls == []
    assert final[0] == 1
    assert final[1] == "success"
    assert final[2] == "7"
    assert final[3] == 25
    assert final[4] is not None
    assert final[5] is not None
    assert final[6] == 12.5
