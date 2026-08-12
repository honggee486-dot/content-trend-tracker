from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from src.database import connect_database, init_database
from src.services.gemini_usage_service import (
    count_text_characters,
    get_daily_gemini_usage,
    list_recent_gemini_usage,
    model_token_limits,
)


def test_counts_total_non_whitespace_and_hangul_characters() -> None:
    counts = count_text_characters("한글 ABC\nㄱㅏ")
    assert counts.total == 9
    assert counts.non_whitespace == 7
    assert counts.hangul == 4


def test_known_flash_model_limits() -> None:
    assert model_token_limits("gemini-3.6-flash") == (1_048_576, 65_536)
    assert model_token_limits("models/gemini-3.6-flash") == (1_048_576, 65_536)
    assert model_token_limits("unknown") == (None, None)


def test_daily_usage_excludes_cache_hits_and_sums_new_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "usage.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        con.executemany(
            """
            INSERT INTO gemini_api_calls(
                call_id, app_id, quota_scope_id, feature_id, content_pack_id,
                request_hash, model_name, attempt_number, cache_hit, status,
                retry_wait_seconds, input_tokens, output_tokens, thought_tokens,
                total_tokens, request_char_count, response_char_count,
                duration_ms, created_at
            ) VALUES (?, 'content-trend-tracker', 'scope', 'feature', 'pack',
                      ?, 'gemini-3.6-flash', ?, ?, 'success', 0, ?, ?, ?, ?, ?, ?, 10, ?)
            """,
            [
                ["call_1", "hash_1", 1, False, 100, 20, 5, 125, 500, 200, datetime(2026, 7, 26, 9, 0)],
                ["call_2", "hash_2", 0, True, None, None, None, None, 500, 200, datetime(2026, 7, 26, 9, 5)],
                ["call_3", "hash_3", 2, False, 80, 10, 3, 93, 400, 100, datetime(2026, 7, 26, 10, 0)],
                ["call_old", "hash_old", 1, False, 999, 999, 999, 2997, 999, 999, datetime(2026, 7, 25, 23, 59)],
            ],
        )
        usage = get_daily_gemini_usage(
            con,
            app_id="content-trend-tracker",
            reference_limit=1500,
            model_name="gemini-3.6-flash",
            usage_date=date(2026, 7, 26),
        )
        recent = list_recent_gemini_usage(
            con, app_id="content-trend-tracker", limit=2
        )

    assert usage["request_count"] == 2
    assert usage["input_tokens"] == 180
    assert usage["output_tokens"] == 30
    assert usage["thought_tokens"] == 8
    assert usage["total_tokens"] == 218
    assert usage["request_char_count"] == 900
    assert usage["response_char_count"] == 300
    assert usage["max_input_tokens"] == 100
    assert usage["max_output_tokens"] == 20
    assert usage["max_generation_tokens"] == 25
    assert usage["remaining_reference_requests"] == 1498
    assert len(recent) == 2
    assert "error_message" in recent[0]
