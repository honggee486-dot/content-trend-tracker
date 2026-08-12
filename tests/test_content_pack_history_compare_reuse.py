from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.database import connect_database, init_database
from src.services.content_pack_history_service import (
    build_content_pack_reuse_payload,
    compare_content_packs,
    list_content_pack_topics,
    list_content_pack_versions,
)


def _insert_topic(con, *, topic_id: str, title: str, now: datetime) -> None:
    con.execute(
        """
        INSERT INTO topics(
            topic_id, title, normalized_title, summary, category, status,
            priority, is_interested, memo, source_count,
            first_seen_at, last_seen_at, created_at, updated_at, archived_at
        ) VALUES (?, ?, ?, '', '', 'ai_ready', 2, TRUE, '', 0, ?, ?, ?, ?, NULL)
        """,
        [topic_id, title, title, now, now, now, now],
    )


def _trend_reference(source_id: str, title: str) -> dict:
    return {
        "id": f"S-{source_id}",
        "reference_kind": "trend_signal",
        "reference_kind_label": "트렌드 신호",
        "source_item_id": source_id,
        "topic_reference_id": None,
        "title": title,
        "publisher": "NAVER",
        "url": f"https://example.com/{source_id}",
    }


def _factual_reference(reference_id: str, title: str) -> dict:
    return {
        "id": f"S-{reference_id}",
        "reference_kind": "factual_reference",
        "reference_kind_label": "사실 참고 자료",
        "source_item_id": None,
        "topic_reference_id": reference_id,
        "title": title,
        "publisher": "공식 기관",
        "url": f"https://example.com/{reference_id}",
    }


def _insert_pack(
    con,
    *,
    pack_id: str,
    topic_id: str,
    version: int,
    audience: str,
    target_length: int,
    references: list[dict],
    prompt_text: str,
    created_at: datetime,
) -> None:
    con.execute(
        """
        INSERT INTO content_packs(
            content_pack_id, topic_id, version, audience, purpose, angle,
            category, target_length, title_rules_json, outline_json,
            forbidden_expressions_json, fact_check_items_json,
            references_json, pack_markdown, prompt_text, created_at
        ) VALUES (?, ?, ?, ?, '정보 제공', '핵심 비교', '생활', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            pack_id,
            topic_id,
            version,
            audience,
            target_length,
            json.dumps(["과장 금지"], ensure_ascii=False),
            json.dumps(["도입", "핵심"], ensure_ascii=False),
            json.dumps(["무조건"], ensure_ascii=False),
            json.dumps(["수치 확인"], ensure_ascii=False),
            json.dumps(references, ensure_ascii=False),
            f"자료팩 v{version}",
            prompt_text,
            created_at,
        ],
    )


def _prepare_history(con) -> None:
    now = datetime(2026, 7, 31, 13, 10, 0)
    _insert_topic(con, topic_id="topic_pack", title="자료팩 비교 주제", now=now)
    con.executemany(
        """
        INSERT INTO topic_source_links(
            topic_id, source_item_id, match_type, match_score, linked_at
        ) VALUES ('topic_pack', ?, 'manual', 1.0, ?)
        """,
        [("source_old", now), ("source_new", now)],
    )
    con.executemany(
        """
        INSERT INTO topic_references(
            reference_id, topic_id, reference_type, title, publisher,
            url, normalized_url, published_at, memo,
            created_at, updated_at, archived_at
        ) VALUES (?, 'topic_pack', 'official', ?, '공식 기관', ?, ?,
                  '2026-07-31', '', ?, ?, ?)
        """,
        [
            (
                "reference_active",
                "활성 공식 자료",
                "https://example.com/reference_active",
                "https://example.com/reference_active",
                now,
                now,
                None,
            ),
            (
                "reference_archived",
                "보관 공식 자료",
                "https://example.com/reference_archived",
                "https://example.com/reference_archived",
                now,
                now,
                now,
            ),
        ],
    )
    _insert_pack(
        con,
        pack_id="pack_one",
        topic_id="topic_pack",
        version=1,
        audience="초보 독자",
        target_length=2000,
        references=[
            _trend_reference("source_old", "과거 트렌드"),
            _factual_reference("reference_active", "활성 공식 자료"),
        ],
        prompt_text="공통 줄\n과거 설명\n마무리",
        created_at=now - timedelta(hours=1),
    )
    _insert_pack(
        con,
        pack_id="pack_two",
        topic_id="topic_pack",
        version=2,
        audience="일반 독자",
        target_length=2600,
        references=[
            _trend_reference("source_new", "새 트렌드"),
            _factual_reference("reference_archived", "보관 공식 자료"),
            _trend_reference("source_missing", "연결 해제 신호"),
        ],
        prompt_text="공통 줄\n새 설명\n추가 근거\n마무리",
        created_at=now,
    )


def test_lists_versions_and_compares_settings_references_and_prompt(tmp_path: Path) -> None:
    db_path = tmp_path / "content-pack-history.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _prepare_history(con)
        topics = list_content_pack_topics(con)
        versions = list_content_pack_versions(con, "topic_pack")
        comparison = compare_content_packs(
            con,
            older_pack_id="pack_one",
            newer_pack_id="pack_two",
        )

    assert topics[0]["topic_id"] == "topic_pack"
    assert topics[0]["version_count"] == 2
    assert [item["version"] for item in versions] == [2, 1]
    assert "독자 대상" in comparison.changed_fields
    assert "목표 분량" in comparison.changed_fields
    assert any("새 트렌드" in item for item in comparison.added_references)
    assert any("과거 트렌드" in item for item in comparison.removed_references)
    assert comparison.added_lines == 2
    assert comparison.removed_lines == 1
    assert "--- 자료팩 v1" in comparison.diff_text
    assert "+++ 자료팩 v2" in comparison.diff_text


def test_reuse_payload_keeps_only_currently_available_evidence_without_writes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "content-pack-reuse.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _prepare_history(con)
        before_pack_count = con.execute("SELECT COUNT(*) FROM content_packs").fetchone()[0]
        before_preference_count = con.execute(
            "SELECT COUNT(*) FROM topic_content_preferences"
        ).fetchone()[0]

        payload = build_content_pack_reuse_payload(con, "pack_two")

        after_pack_count = con.execute("SELECT COUNT(*) FROM content_packs").fetchone()[0]
        after_preference_count = con.execute(
            "SELECT COUNT(*) FROM topic_content_preferences"
        ).fetchone()[0]

    assert payload["topic_id"] == "topic_pack"
    assert payload["version"] == 2
    assert payload["defaults"]["source"] == "reused"
    assert payload["defaults"]["audience"] == "일반 독자"
    assert payload["selected_source_item_ids"] == ["source_new"]
    assert payload["selected_reference_ids"] == []
    assert payload["missing_source_item_ids"] == ["source_missing"]
    assert payload["missing_reference_ids"] == ["reference_archived"]
    assert before_pack_count == after_pack_count == 2
    assert before_preference_count == after_preference_count == 0


def test_comparison_rejects_different_topics(tmp_path: Path) -> None:
    db_path = tmp_path / "content-pack-topic-guard.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 31, 13, 20, 0)
    with connect_database(db_path) as con:
        _prepare_history(con)
        _insert_topic(con, topic_id="topic_other", title="다른 주제", now=now)
        _insert_pack(
            con,
            pack_id="pack_other",
            topic_id="topic_other",
            version=1,
            audience="다른 독자",
            target_length=1800,
            references=[],
            prompt_text="다른 요청서",
            created_at=now,
        )

        with pytest.raises(ValueError, match="같은 주제"):
            compare_content_packs(
                con,
                older_pack_id="pack_one",
                newer_pack_id="pack_other",
            )


def test_same_pack_comparison_has_no_changes(tmp_path: Path) -> None:
    db_path = tmp_path / "content-pack-same.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _prepare_history(con)
        comparison = compare_content_packs(
            con,
            older_pack_id="pack_one",
            newer_pack_id="pack_one",
        )

    assert comparison.has_changes is False
    assert comparison.diff_text == "두 자료팩의 AI 요청서가 같습니다."


def test_content_pack_history_panel_is_collapsed_and_attached() -> None:
    root = Path(__file__).resolve().parents[1]
    panel_source = (root / "src" / "content_pack_history_ui.py").read_text(
        encoding="utf-8"
    )
    ui_source = (root / "src" / "ui.py").read_text(encoding="utf-8")

    assert 'with st_module.expander("자료팩 버전 기록·비교·입력값 재사용", expanded=False)' in panel_source
    assert "저장 버튼을 누르기 전까지 새 자료팩은 생성되지 않습니다" in panel_source
    assert "REUSE_PAYLOAD_KEY" in panel_source
    assert "def _install_content_pack_history_ui" in ui_source
    assert 'caller_globals["render_content_pack"] = wrapped' in ui_source
    assert "tracked_save_content_pack" in ui_source
    assert "render_content_pack_history_panel" in ui_source
