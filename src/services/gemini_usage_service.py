from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import duckdb


@dataclass(frozen=True)
class TextCharacterCounts:
    total: int
    non_whitespace: int
    hangul: int


def _is_hangul(character: str) -> bool:
    codepoint = ord(character)
    return (
        0xAC00 <= codepoint <= 0xD7A3
        or 0x3131 <= codepoint <= 0x318E
        or 0x1100 <= codepoint <= 0x11FF
    )


def count_text_characters(value: Any) -> TextCharacterCounts:
    text = str(value or "")
    return TextCharacterCounts(
        total=len(text),
        non_whitespace=sum(1 for character in text if not character.isspace()),
        hangul=sum(1 for character in text if _is_hangul(character)),
    )


def model_token_limits(model_name: str) -> tuple[int | None, int | None]:
    normalized = str(model_name or "").strip().casefold().split("/")[-1]
    known_limits = {
        "gemini-3.6-flash": (1_048_576, 65_536),
        "gemini-3.5-flash-lite": (1_048_576, 65_536),
    }
    return known_limits.get(normalized, (None, None))


def get_daily_gemini_usage(
    con: duckdb.DuckDBPyConnection,
    *,
    app_id: str,
    reference_limit: int,
    model_name: str,
    usage_date: date | None = None,
) -> dict[str, int | date]:
    current_date = usage_date or datetime.now().date()
    row = con.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN cache_hit = FALSE AND attempt_number > 0 THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN cache_hit = FALSE THEN COALESCE(input_tokens, 0) ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN cache_hit = FALSE THEN COALESCE(output_tokens, 0) ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN cache_hit = FALSE THEN COALESCE(thought_tokens, 0) ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN cache_hit = FALSE THEN COALESCE(total_tokens, 0) ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN cache_hit = FALSE THEN COALESCE(request_char_count, 0) ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN cache_hit = FALSE THEN COALESCE(response_char_count, 0) ELSE 0 END), 0),
            COALESCE(MAX(CASE WHEN cache_hit = FALSE AND attempt_number > 0 THEN input_tokens ELSE NULL END), 0),
            COALESCE(MAX(CASE WHEN cache_hit = FALSE AND attempt_number > 0 THEN output_tokens ELSE NULL END), 0),
            COALESCE(MAX(CASE WHEN cache_hit = FALSE AND attempt_number > 0
                              THEN COALESCE(output_tokens, 0) + COALESCE(thought_tokens, 0)
                              ELSE NULL END), 0)
        FROM gemini_api_calls
        WHERE app_id = ? AND model_name = ? AND CAST(created_at AS DATE) = ?
        """,
        [str(app_id), str(model_name), current_date],
    ).fetchone()
    values = [
        int(item or 0)
        for item in (row or (0, 0, 0, 0, 0, 0, 0, 0, 0, 0))
    ]
    limit = max(1, int(reference_limit or 1))
    return {
        "usage_date": current_date,
        "request_count": values[0],
        "input_tokens": values[1],
        "output_tokens": values[2],
        "thought_tokens": values[3],
        "total_tokens": values[4],
        "request_char_count": values[5],
        "response_char_count": values[6],
        "max_input_tokens": values[7],
        "max_output_tokens": values[8],
        "max_generation_tokens": values[9],
        "reference_limit": limit,
        "remaining_reference_requests": max(0, limit - values[0]),
    }


def list_recent_gemini_usage(
    con: duckdb.DuckDBPyConnection,
    *,
    app_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT created_at, feature_id, model_name, attempt_number, cache_hit,
               status, http_status, error_type, error_message, retry_wait_seconds,
               request_char_count, request_non_whitespace_char_count,
               request_hangul_char_count, input_tokens,
               response_char_count, response_non_whitespace_char_count,
               response_hangul_char_count, output_tokens, thought_tokens,
               total_tokens, requested_item_count,
               configured_items_per_request, thinking_level,
               request_timeout_seconds, finish_reason, finish_message,
               duration_ms
        FROM gemini_api_calls
        WHERE app_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        [str(app_id), max(1, min(int(limit), 100))],
    ).fetchall()
    columns = [item[0] for item in con.description]
    return [dict(zip(columns, row)) for row in rows]
