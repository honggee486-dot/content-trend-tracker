from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from scripts.report_operation_diagnostics import _print_human
from src.database import init_database
from src.services.operation_diagnostic_report_service import (
    build_operation_diagnostic_report,
)


APP_ID = "content-trend-tracker"


def _insert_call(
    con,
    *,
    call_id: str,
    request_hash: str,
    attempt_number: int,
    status: str,
    created_at: datetime,
    configured_items: int = 15,
    thinking_level: str = "high",
    timeout_seconds: int = 600,
    error_type: str = "",
    error_message: str = "",
    http_status: int | None = 200,
    finish_reason: str = "",
) -> None:
    con.execute(
        """
        INSERT INTO gemini_api_calls(
            call_id, app_id, quota_scope_id, feature_id, feature_version,
            content_pack_id, request_hash, model_name, attempt_number,
            cache_hit, status, http_status, error_type, retry_reason,
            retry_wait_seconds, input_tokens, output_tokens, thought_tokens,
            total_tokens, requested_item_count, configured_items_per_request,
            thinking_level, request_timeout_seconds, finish_reason,
            finish_message, duration_ms, error_message, created_at
        ) VALUES (?, ?, 'test-scope', 'trend_topic_angle_batch_v1', '6',
                  'test-pack', ?, 'gemini-3.6-flash', ?, FALSE, ?, ?, ?, '',
                  0, 100, 200, 300, 600, 15, ?, ?, ?, ?, '', 120000, ?, ?)
        """,
        [
            call_id,
            APP_ID,
            request_hash,
            attempt_number,
            status,
            http_status,
            error_type,
            configured_items,
            thinking_level,
            timeout_seconds,
            finish_reason,
            error_message,
            created_at,
        ],
    )


def _build_report(db_path: Path, *, now: datetime) -> dict:
    with duckdb.connect(str(db_path), read_only=True) as con:
        return build_operation_diagnostic_report(
            con,
            app_id=APP_ID,
            items_per_request=15,
            thinking_level="high",
            timeout_seconds=600,
            min_opportunity_score=50,
            now=now,
        )


def test_report_lists_only_terminal_failures_by_request_chain(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "failure-samples.duckdb"
    init_database(db_path)
    now = datetime(2026, 8, 5, 12, 0, 0)

    with duckdb.connect(str(db_path)) as con:
        _insert_call(
            con,
            call_id="current-failure",
            request_hash="current-failure",
            attempt_number=1,
            status="response_validation_error",
            error_type="response_validation_error",
            error_message="JSON 응답 형식 오류",
            finish_reason="STOP",
            created_at=now - timedelta(minutes=4),
        )
        _insert_call(
            con,
            call_id="recovered-retry",
            request_hash="recovered",
            attempt_number=1,
            status="retrying",
            error_type="rate_limited",
            http_status=429,
            created_at=now - timedelta(minutes=3),
        )
        _insert_call(
            con,
            call_id="recovered-success",
            request_hash="recovered",
            attempt_number=2,
            status="success_after_retry",
            created_at=now - timedelta(minutes=2),
        )
        _insert_call(
            con,
            call_id="other-timeout",
            request_hash="other-timeout",
            attempt_number=1,
            status="request_timeout",
            error_type="request_timeout",
            error_message="요청 제한시간 초과",
            http_status=None,
            configured_items=20,
            thinking_level="medium",
            timeout_seconds=300,
            created_at=now - timedelta(minutes=1),
        )

    before = (db_path.stat().st_size, db_path.stat().st_mtime_ns)
    report = _build_report(db_path, now=now)
    after = (db_path.stat().st_size, db_path.stat().st_mtime_ns)

    assert before == after
    failures = report["topic_angle"]["failure_diagnostics"]
    assert failures["available"] is True
    assert failures["terminal_failure_count"] == 2
    assert failures["current_runtime_failure_count"] == 1
    assert failures["other_runtime_failure_count"] == 1
    assert [item["status"] for item in failures["samples"]] == [
        "request_timeout",
        "response_validation_error",
    ]
    assert failures["samples"][0]["matches_current_runtime"] is False
    assert failures["samples"][1]["matches_current_runtime"] is True
    assert failures["samples"][1]["generation_tokens"] == 500
    assert failures["samples"][1]["error_message"] == "JSON 응답 형식 오류"
    assert report["next_action"]["label"] == "현재 조건 응답 검증 점검"


def test_human_report_prints_failure_cause_and_runtime_scope(
    tmp_path: Path,
    capsys,
) -> None:
    db_path = tmp_path / "failure-output.duckdb"
    init_database(db_path)
    now = datetime(2026, 8, 5, 12, 0, 0)
    with duckdb.connect(str(db_path)) as con:
        _insert_call(
            con,
            call_id="current-failure",
            request_hash="current-failure",
            attempt_number=1,
            status="response_validation_error",
            error_type="response_validation_error",
            error_message="JSON 응답 형식 오류",
            finish_reason="STOP",
            created_at=now,
        )

    report = _build_report(db_path, now=now)
    report["read_only_verification"] = {
        "verified": True,
        "message": "DB 크기·수정 시각과 WAL 상태가 유지되었습니다.",
    }
    _print_human(report)
    output = capsys.readouterr().out

    assert "[Gemini 주제 방향 최종 실패 · 최근 최대 10건]" in output
    assert "현재 조건" in output
    assert "response_validation_error" in output
    assert "JSON 응답 형식 오류" in output
    assert "종료 사유: STOP" in output
