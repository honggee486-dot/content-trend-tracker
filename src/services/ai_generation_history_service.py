"""저장된 AI 생성 결과를 조회하고 현재 검사 규칙으로 다시 검증합니다."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import duckdb

from src.services.ai_result_parser import (
    ParseResult,
    parse_ai_result,
    validate_ai_result_against_references,
)


@dataclass(frozen=True)
class GenerationRevalidation:
    generation_id: str
    stored_is_valid: bool
    current_is_valid: bool
    stored_errors: tuple[str, ...]
    stored_warnings: tuple[str, ...]
    current_errors: tuple[str, ...]
    current_warnings: tuple[str, ...]
    stored_schema_version: str
    current_schema_version: str
    current_data: dict[str, Any] | None

    @property
    def status_changed(self) -> bool:
        return self.stored_is_valid != self.current_is_valid

    @property
    def messages_changed(self) -> bool:
        return (
            self.stored_errors != self.current_errors
            or self.stored_warnings != self.current_warnings
        )


def _cursor_rows(cursor) -> list[dict[str, Any]]:
    columns = [str(column[0]) for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _load_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _load_json_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def list_ai_generation_sessions(
    con: duckdb.DuckDBPyConnection,
    *,
    query: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    normalized_query = str(query or "").strip().casefold()
    like_query = f"%{normalized_query}%"
    rows = _cursor_rows(
        con.execute(
            """
            SELECT g.generation_id, g.topic_id, g.content_pack_id,
                   g.ai_provider, g.parse_status, g.schema_version,
                   g.created_at, p.version AS pack_version,
                   t.title AS topic_title,
                   d.draft_id, d.title AS draft_title,
                   d.current_revision
            FROM generation_sessions g
            JOIN content_packs p ON p.content_pack_id = g.content_pack_id
            JOIN topics t ON t.topic_id = g.topic_id
            LEFT JOIN drafts d ON d.generation_id = g.generation_id
            WHERE (
                ? = ''
                OR LOWER(COALESCE(t.title, '')) LIKE ?
                OR LOWER(COALESCE(g.ai_provider, '')) LIKE ?
                OR LOWER(COALESCE(d.title, '')) LIKE ?
                OR LOWER(COALESCE(g.generation_id, '')) LIKE ?
            )
            ORDER BY g.created_at DESC, g.generation_id DESC
            LIMIT ?
            """,
            [
                normalized_query,
                like_query,
                like_query,
                like_query,
                like_query,
                max(1, min(int(limit), 500)),
            ],
        )
    )
    return rows


def get_ai_generation_session(
    con: duckdb.DuckDBPyConnection,
    generation_id: str,
) -> dict[str, Any] | None:
    rows = _cursor_rows(
        con.execute(
            """
            SELECT g.generation_id, g.topic_id, g.content_pack_id,
                   g.ai_provider, g.prompt_text, g.raw_response,
                   g.parsed_json, g.parse_status,
                   g.validation_errors_json, g.validation_warnings_json,
                   g.schema_version, g.created_at,
                   p.version AS pack_version, p.references_json,
                   t.title AS topic_title,
                   d.draft_id, d.title AS draft_title,
                   d.current_revision,
                   (SELECT COUNT(*) FROM fact_check_items f
                    WHERE f.draft_id = d.draft_id) AS fact_check_count
            FROM generation_sessions g
            JOIN content_packs p ON p.content_pack_id = g.content_pack_id
            JOIN topics t ON t.topic_id = g.topic_id
            LEFT JOIN drafts d ON d.generation_id = g.generation_id
            WHERE g.generation_id = ?
            LIMIT 1
            """,
            [generation_id],
        )
    )
    if not rows:
        return None
    item = rows[0]
    item["parsed_data"] = _load_json_dict(item.get("parsed_json"))
    item["validation_errors"] = [
        str(value) for value in _load_json_list(item.get("validation_errors_json"))
    ]
    item["validation_warnings"] = [
        str(value) for value in _load_json_list(item.get("validation_warnings_json"))
    ]
    item["references"] = [
        value
        for value in _load_json_list(item.get("references_json"))
        if isinstance(value, dict)
    ]
    return item


def revalidate_ai_generation_session(
    con: duckdb.DuckDBPyConnection,
    generation_id: str,
) -> GenerationRevalidation:
    session = get_ai_generation_session(con, generation_id)
    if session is None:
        raise ValueError("AI 생성 기록을 찾을 수 없습니다.")

    current_result: ParseResult = parse_ai_result(
        str(session.get("raw_response") or "")
    )
    current_result = validate_ai_result_against_references(
        current_result,
        session.get("references") or [],
    )
    current_schema = ""
    if current_result.data is not None:
        current_schema = str(current_result.data.get("schema_version") or "")

    return GenerationRevalidation(
        generation_id=str(session["generation_id"]),
        stored_is_valid=str(session.get("parse_status") or "") == "valid"
        and not bool(session.get("validation_errors")),
        current_is_valid=current_result.is_valid,
        stored_errors=tuple(str(value) for value in session.get("validation_errors") or []),
        stored_warnings=tuple(
            str(value) for value in session.get("validation_warnings") or []
        ),
        current_errors=tuple(current_result.errors),
        current_warnings=tuple(current_result.warnings),
        stored_schema_version=str(session.get("schema_version") or ""),
        current_schema_version=current_schema,
        current_data=current_result.data,
    )


def provider_for_ai_import(ai_provider: str) -> str:
    normalized = str(ai_provider or "").strip().casefold()
    if "chatgpt" in normalized or "openai" in normalized:
        return "ChatGPT"
    if "gemini" in normalized or "google" in normalized:
        return "Gemini"
    return "기타"
