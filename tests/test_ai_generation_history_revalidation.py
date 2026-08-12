from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pytest

from src.database import connect_database, init_database
from src.services.ai_generation_history_service import (
    get_ai_generation_session,
    list_ai_generation_sessions,
    provider_for_ai_import,
    revalidate_ai_generation_session,
)


def _valid_raw_response() -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "title": "기록 재검사 초안",
            "summary": "요약",
            "category": "정보",
            "tags": ["기록"],
            "body_markdown": "현재 규칙으로 다시 검사할 본문입니다.",
            "fact_checks": [],
            "sources": [
                {
                    "id": "S1",
                    "title": "공식 자료",
                    "publisher": "공식 기관",
                    "url": "https://example.com/source",
                    "published_at": "2026-07-31",
                }
            ],
            "image_prompts": [],
        },
        ensure_ascii=False,
    )


def _insert_generation(
    con,
    *,
    generation_id: str = "gen_history",
    raw_response: str | None = None,
    ai_provider: str = "Gemini API (gemini-test)",
    parse_status: str = "valid",
    validation_errors: list[str] | None = None,
) -> None:
    now = datetime(2026, 7, 31, 14, 0, 0)
    raw = _valid_raw_response() if raw_response is None else raw_response
    parsed_json = raw if raw.startswith("{") else "{}"
    con.execute(
        """
        INSERT INTO topics(
            topic_id, title, normalized_title, summary, category, status,
            priority, is_interested, memo, source_count,
            first_seen_at, last_seen_at, created_at, updated_at, archived_at
        ) VALUES (
            'topic_generation_history', 'AI 생성 기록 주제', 'ai 생성 기록 주제',
            '', '정보', 'draft_complete', 2, TRUE, '', 0,
            ?, ?, ?, ?, NULL
        )
        """,
        [now, now, now, now],
    )
    references = [
        {
            "id": "S1",
            "reference_kind": "factual_reference",
            "title": "공식 자료",
            "publisher": "공식 기관",
            "url": "https://example.com/source",
            "published_at": "2026-07-31",
        }
    ]
    con.execute(
        """
        INSERT INTO content_packs(
            content_pack_id, topic_id, version, audience, purpose, angle,
            category, target_length, title_rules_json, outline_json,
            forbidden_expressions_json, fact_check_items_json,
            references_json, pack_markdown, prompt_text, created_at
        ) VALUES (
            'pack_generation_history', 'topic_generation_history', 2,
            '일반 독자', '정보 제공', '현재 규칙 재검사', '정보', 2500,
            '[]', '[]', '[]', '[]', ?, '자료팩', '요청서', ?
        )
        """,
        [json.dumps(references, ensure_ascii=False), now],
    )
    con.execute(
        """
        INSERT INTO generation_sessions(
            generation_id, topic_id, content_pack_id, ai_provider,
            prompt_text, raw_response, parsed_json, parse_status,
            validation_errors_json, validation_warnings_json,
            schema_version, created_at
        ) VALUES (?, 'topic_generation_history', 'pack_generation_history', ?,
                  '요청서', ?, ?, ?, ?, '[]', '1.0', ?)
        """,
        [
            generation_id,
            ai_provider,
            raw,
            parsed_json,
            parse_status,
            json.dumps(validation_errors or [], ensure_ascii=False),
            now,
        ],
    )
    con.execute(
        """
        INSERT INTO drafts(
            draft_id, topic_id, generation_id, title, summary, category,
            tags_json, body_markdown, body_html, sources_json,
            image_prompts_json, current_revision, created_at, updated_at
        ) VALUES (
            'draft_generation_history', 'topic_generation_history', ?,
            '기록 재검사 초안', '요약', '정보', '["기록"]',
            '본문', '<p>본문</p>', '[]', '[]', 3, ?, ?
        )
        """,
        [generation_id, now, now],
    )


def test_list_and_get_ai_generation_history(tmp_path: Path) -> None:
    db_path = tmp_path / "generation-history.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _insert_generation(con)
        rows = list_ai_generation_sessions(con)
        session = get_ai_generation_session(con, "gen_history")

    assert len(rows) == 1
    assert rows[0]["pack_version"] == 2
    assert rows[0]["draft_title"] == "기록 재검사 초안"
    assert session is not None
    assert session["parsed_data"]["schema_version"] == "1.0"
    assert session["references"][0]["id"] == "S1"
    assert session["current_revision"] == 3


def test_revalidation_passes_and_does_not_write(tmp_path: Path) -> None:
    db_path = tmp_path / "generation-revalidation.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _insert_generation(con)
        before = {
            table: int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("generation_sessions", "content_packs", "drafts", "topics")
        }
        result = revalidate_ai_generation_session(con, "gen_history")
        after = {
            table: int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("generation_sessions", "content_packs", "drafts", "topics")
        }

    assert result.stored_is_valid is True
    assert result.current_is_valid is True
    assert result.status_changed is False
    assert result.current_schema_version == "1.0"
    assert before == after


def test_revalidation_detects_stored_and_current_status_difference(tmp_path: Path) -> None:
    db_path = tmp_path / "generation-invalid-revalidation.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _insert_generation(con, raw_response="현재는 JSON이 아닌 원문")
        result = revalidate_ai_generation_session(con, "gen_history")

    assert result.stored_is_valid is True
    assert result.current_is_valid is False
    assert result.status_changed is True
    assert result.current_errors


def test_generation_history_search_and_provider_mapping(tmp_path: Path) -> None:
    db_path = tmp_path / "generation-search.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _insert_generation(con)
        matching = list_ai_generation_sessions(con, query="Gemini")
        missing = list_ai_generation_sessions(con, query="존재하지 않는 검색어")

    assert len(matching) == 1
    assert missing == []
    assert provider_for_ai_import("Gemini API (model)") == "Gemini"
    assert provider_for_ai_import("ChatGPT") == "ChatGPT"
    assert provider_for_ai_import("Local model") == "기타"


def test_revalidation_rejects_missing_generation(tmp_path: Path) -> None:
    db_path = tmp_path / "generation-missing.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        with pytest.raises(ValueError, match="찾을 수 없습니다"):
            revalidate_ai_generation_session(con, "missing_generation")


def test_generation_history_panel_is_collapsed_and_attached() -> None:
    root = Path(__file__).resolve().parents[1]
    panel_source = (root / "src" / "ai_generation_history_ui.py").read_text(
        encoding="utf-8"
    )
    content_pack_ui_source = (root / "src" / "content_pack_history_ui.py").read_text(
        encoding="utf-8"
    )

    assert 'with st_module.expander("AI 생성 결과 기록·현재 규칙 재검사", expanded=False)' in panel_source
    assert "revalidate_ai_generation_session" in panel_source
    assert "이 원문을 AI 결과 입력란에서 다시 열기" in panel_source
    assert "prepare_workflow_navigation_state" in panel_source
    assert '"AI 결과 가져오기"' in panel_source
    assert 'st_module.session_state["page"] = "AI 결과 가져오기"' not in panel_source
    assert "render_ai_generation_history_panel" in content_pack_ui_source
