from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.content_work_queue_ui import _load_topic_evidence
from src.database import connect_database, init_database
from src.services.fact_check_readiness_service import get_fact_check_readiness
from src.services.publish_service import (
    archive_publish_record,
    get_publish_record,
    list_publish_record_history,
    list_publish_records,
    mark_published,
    restore_publish_record,
    update_publish_record,
)


def _insert_topic_and_draft(con, *, now: datetime) -> None:
    con.execute(
        """
        INSERT INTO topics(
            topic_id, title, normalized_title, summary, category, status,
            priority, is_interested, memo, source_count,
            first_seen_at, last_seen_at, created_at, updated_at, archived_at
        ) VALUES (
            'topic_history', '발행 기록 주제', '발행 기록 주제', '', '',
            'publish_ready', 2, TRUE, '', 0, ?, ?, ?, ?, NULL
        )
        """,
        [now, now, now, now],
    )
    con.execute(
        """
        INSERT INTO drafts(
            draft_id, topic_id, generation_id, title, summary, category,
            tags_json, body_markdown, body_html, sources_json,
            image_prompts_json, current_revision, created_at, updated_at
        ) VALUES (
            'draft_history', 'topic_history', NULL, '발행 기록 초안', '', '',
            '[]', '본문', '', '[]', '[]', 1, ?, ?
        )
        """,
        [now, now],
    )
    con.execute(
        """
        INSERT INTO fact_check_items(
            fact_check_id, draft_id, claim_text, check_status, reason,
            evidence, source_ids_json, source_url, checked_at
        ) VALUES (
            'fact_history', 'draft_history', '확인된 주장', 'verified', '',
            '확인 완료', '[]', 'https://example.com/source', ?
        )
        """,
        [now],
    )


def test_request_ready_evidence_loads_trends_and_references(tmp_path: Path) -> None:
    db_path = tmp_path / "queue-evidence.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 31, 10, 45, 0)
    with connect_database(db_path) as con:
        con.execute(
            """
            INSERT INTO topics(
                topic_id, title, normalized_title, summary, category, status,
                priority, is_interested, memo, source_count,
                first_seen_at, last_seen_at, created_at, updated_at, archived_at
            ) VALUES (
                'topic_evidence', '근거 확인 주제', '근거 확인 주제', '', '',
                'ai_ready', 2, TRUE, '', 1, ?, ?, ?, ?, NULL
            )
            """,
            [now, now, now, now],
        )
        con.execute(
            """
            INSERT INTO source_items(
                source_item_id, source_type, external_id, raw_title,
                normalized_title, source_url, normalized_url, source_name,
                published_at, observed_at, signal_value, metadata_json,
                first_imported_at, previous_imported_at, last_imported_at,
                observation_count, imported_at
            ) VALUES (
                'source_evidence', 'naver_news', 'external_evidence',
                '수집된 뉴스 제목', '수집된 뉴스 제목',
                'https://example.com/news', 'https://example.com/news', 'NAVER',
                ?, ?, 123, '{}', ?, NULL, ?, 3, ?
            )
            """,
            [now, now, now, now, now],
        )
        con.execute(
            """
            INSERT INTO topic_source_links(
                topic_id, source_item_id, match_type, match_score, linked_at
            ) VALUES ('topic_evidence', 'source_evidence', 'manual', 1.0, ?)
            """,
            [now],
        )
        con.execute(
            """
            INSERT INTO topic_references(
                reference_id, topic_id, reference_type, title, publisher,
                url, normalized_url, published_at, memo,
                created_at, updated_at, archived_at
            ) VALUES (
                'reference_evidence', 'topic_evidence', 'official',
                '공식 참고 자료', '공식 기관', 'https://example.com/official',
                'https://example.com/official', '2026-07-31', '확인 메모',
                ?, ?, NULL
            )
            """,
            [now, now],
        )

        evidence = _load_topic_evidence(con, "topic_evidence")

    assert evidence["trend_total"] == 1
    assert evidence["trend_rows"][0]["raw_title"] == "수집된 뉴스 제목"
    assert evidence["trend_rows"][0]["observation_count"] == 3
    assert evidence["reference_rows"][0]["title"] == "공식 참고 자료"


def test_request_ready_evidence_ui_is_collapsed_by_default() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "src" / "content_work_queue_ui.py"
    ).read_text(encoding="utf-8")

    assert "수집 근거 보기" in source
    assert "with st_module.expander(label, expanded=False)" in source
    assert "최근 연결 항목 최대 8개" in source


def test_publish_record_correction_archive_restore_and_history(tmp_path: Path) -> None:
    db_path = tmp_path / "publish-history.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 31, 10, 50, 0)
    corrected_at = now - timedelta(hours=1)

    with connect_database(db_path) as con:
        _insert_topic_and_draft(con, now=now)
        publish_id = mark_published(
            con,
            draft_id="draft_history",
            platform="네이버 블로그",
            write_url="https://blog.example.com/write",
            published_url="https://blog.example.com/wrong",
            memo="처음 기록",
        )

        changed = update_publish_record(
            con,
            publish_id=publish_id,
            platform="네이버 블로그",
            write_url="https://blog.example.com/write",
            published_url="https://blog.example.com/correct",
            memo="URL 정정",
            published_at=corrected_at,
            change_note="발행 URL 오입력 정정",
        )
        assert changed is True
        corrected = get_publish_record(con, publish_id)
        assert corrected is not None
        assert corrected["published_url"] == "https://blog.example.com/correct"
        assert corrected["published_at"] == corrected_at

        history = list_publish_record_history(con, publish_id)
        assert history[0]["action"] == "corrected"
        assert history[0]["previous_values"]["published_url"].endswith("/wrong")
        assert history[0]["new_values"]["published_url"].endswith("/correct")

        archived = archive_publish_record(
            con,
            publish_id=publish_id,
            change_note="테스트 기록 보관",
        )
        assert archived is True
        assert list_publish_records(con) == []
        archived_rows = list_publish_records(con, include_archived=True)
        assert len(archived_rows) == 1
        assert archived_rows[0]["archived_at"] is not None
        topic_status = con.execute(
            "SELECT status FROM topics WHERE topic_id = 'topic_history'"
        ).fetchone()[0]
        assert topic_status == "publish_ready"

        readiness = get_fact_check_readiness(con, now=now)
        draft_row = next(
            row for row in readiness["rows"] if row["draft_id"] == "draft_history"
        )
        assert draft_row["readiness_state"] == "ready"
        assert draft_row["publish_count"] == 0

        restored = restore_publish_record(
            con,
            publish_id=publish_id,
            change_note="정상 기록으로 복원",
        )
        assert restored is True
        assert len(list_publish_records(con)) == 1
        topic_status = con.execute(
            "SELECT status FROM topics WHERE topic_id = 'topic_history'"
        ).fetchone()[0]
        assert topic_status == "published"

        final_history = list_publish_record_history(con, publish_id)
        assert [item["action"] for item in final_history] == [
            "restored",
            "archived",
            "corrected",
        ]
        record_count = con.execute(
            "SELECT COUNT(*) FROM publish_records WHERE publish_id = ?",
            [publish_id],
        ).fetchone()[0]
        assert record_count == 1


def test_publish_screen_installs_history_wrapper() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "ui.py").read_text(
        encoding="utf-8"
    )

    assert "def _install_publish_history_ui" in source
    assert 'caller_globals["render_publish"] = wrapped' in source
    assert "render_publish_history_panel" in source
