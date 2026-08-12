from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.database import connect_database, init_database
from src.services.draft_revision_service import (
    compare_draft_to_revision,
    get_draft_revision,
    list_draft_revisions,
    restore_draft_revision,
)
from src.services.draft_service import get_draft


def _insert_draft_history(con, *, current_matches_v2: bool) -> None:
    now = datetime(2026, 7, 31, 11, 20, 0)
    con.execute(
        """
        INSERT INTO topics(
            topic_id, title, normalized_title, summary, category, status,
            priority, is_interested, memo, source_count,
            first_seen_at, last_seen_at, created_at, updated_at, archived_at
        ) VALUES (
            'topic_revision', '리비전 주제', '리비전 주제', '', '',
            'publish_ready', 2, TRUE, '', 0, ?, ?, ?, ?, NULL
        )
        """,
        [now, now, now, now],
    )
    current_title = "두 번째 제목" if current_matches_v2 else "저장하지 않은 현재 제목"
    current_body = "두 번째 본문\n추가 줄" if current_matches_v2 else "임시 현재 본문\n아직 리비전 아님"
    con.execute(
        """
        INSERT INTO drafts(
            draft_id, topic_id, generation_id, title, summary, category,
            tags_json, body_markdown, body_html, sources_json,
            image_prompts_json, current_revision, created_at, updated_at
        ) VALUES (
            'draft_revision', 'topic_revision', NULL, ?, '현재 요약', '현재 분류',
            '["현재", "태그"]', ?, '', '[]', '[]', 2, ?, ?
        )
        """,
        [current_title, current_body, now, now],
    )
    con.executemany(
        """
        INSERT INTO draft_revisions(
            revision_id, draft_id, revision_number, title, summary, category,
            tags_json, body_markdown, change_note, created_at
        ) VALUES (?, 'draft_revision', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "rev_one",
                1,
                "첫 번째 제목",
                "첫 번째 요약",
                "초기 분류",
                json.dumps(["초기"], ensure_ascii=False),
                "첫 번째 본문\n과거 줄",
                "최초 생성",
                now - timedelta(hours=2),
            ),
            (
                "rev_two",
                2,
                "두 번째 제목",
                "현재 요약",
                "현재 분류",
                json.dumps(["현재", "태그"], ensure_ascii=False),
                "두 번째 본문\n추가 줄",
                "사용자 수정",
                now - timedelta(hours=1),
            ),
        ],
    )


def test_revision_list_and_comparison(tmp_path: Path) -> None:
    db_path = tmp_path / "draft-revision-compare.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _insert_draft_history(con, current_matches_v2=True)

        revisions = list_draft_revisions(con, "draft_revision")
        assert [item["revision_number"] for item in revisions] == [2, 1]
        assert revisions[1]["tags"] == ["초기"]

        comparison = compare_draft_to_revision(
            con,
            draft_id="draft_revision",
            revision_id="rev_one",
        )

    assert comparison.has_changes is True
    assert comparison.title_changed is True
    assert comparison.summary_changed is True
    assert comparison.category_changed is True
    assert comparison.tags_changed is True
    assert comparison.body_changed is True
    assert comparison.added_lines == 2
    assert comparison.removed_lines == 2
    assert "--- 과거 v1" in comparison.diff_text
    assert "+++ 현재 v2" in comparison.diff_text


def test_restore_preserves_unversioned_current_edit_and_all_history(tmp_path: Path) -> None:
    db_path = tmp_path / "draft-revision-restore.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _insert_draft_history(con, current_matches_v2=False)
        before_count = con.execute(
            "SELECT COUNT(*) FROM draft_revisions WHERE draft_id = 'draft_revision'"
        ).fetchone()[0]

        restored_number = restore_draft_revision(
            con,
            draft_id="draft_revision",
            revision_id="rev_one",
            change_note="초기 구성으로 안전 복원",
        )

        draft = get_draft(con, "draft_revision")
        revisions = list_draft_revisions(con, "draft_revision")
        safety = next(item for item in revisions if item["revision_number"] == 3)
        restored = next(item for item in revisions if item["revision_number"] == 4)
        original = get_draft_revision(
            con,
            draft_id="draft_revision",
            revision_id="rev_one",
        )
        topic_status = con.execute(
            "SELECT status FROM topics WHERE topic_id = 'topic_revision'"
        ).fetchone()[0]
        after_count = con.execute(
            "SELECT COUNT(*) FROM draft_revisions WHERE draft_id = 'draft_revision'"
        ).fetchone()[0]

    assert restored_number == 4
    assert draft is not None
    assert draft["current_revision"] == 4
    assert draft["title"] == "첫 번째 제목"
    assert draft["body_markdown"] == "첫 번째 본문\n과거 줄"
    assert "첫 번째 본문" in draft["body_html"]
    assert safety["title"] == "저장하지 않은 현재 제목"
    assert safety["body_markdown"] == "임시 현재 본문\n아직 리비전 아님"
    assert safety["change_note"] == "복원 전 현재 편집본 자동 보존"
    assert restored["change_note"] == "초기 구성으로 안전 복원"
    assert original is not None
    assert original["revision_number"] == 1
    assert topic_status == "editing"
    assert before_count == 2
    assert after_count == 4


def test_restore_without_unversioned_edit_adds_only_restored_revision(tmp_path: Path) -> None:
    db_path = tmp_path / "draft-revision-no-safety-copy.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _insert_draft_history(con, current_matches_v2=True)

        restored_number = restore_draft_revision(
            con,
            draft_id="draft_revision",
            revision_id="rev_one",
        )
        revisions = list_draft_revisions(con, "draft_revision")

    assert restored_number == 3
    assert [item["revision_number"] for item in revisions] == [3, 2, 1]
    assert revisions[0]["change_note"] == "v1에서 복원"


def test_restore_rejects_same_content(tmp_path: Path) -> None:
    db_path = tmp_path / "draft-revision-same.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _insert_draft_history(con, current_matches_v2=True)

        with pytest.raises(ValueError, match="내용이 같습니다"):
            restore_draft_revision(
                con,
                draft_id="draft_revision",
                revision_id="rev_two",
            )

        count = con.execute(
            "SELECT COUNT(*) FROM draft_revisions WHERE draft_id = 'draft_revision'"
        ).fetchone()[0]

    assert count == 2


def test_editor_installs_collapsed_revision_panel() -> None:
    root = Path(__file__).resolve().parents[1]
    ui_source = (root / "src" / "ui.py").read_text(encoding="utf-8")
    panel_source = (root / "src" / "draft_revision_ui.py").read_text(encoding="utf-8")

    assert "def _install_draft_revision_ui" in ui_source
    assert 'caller_globals["render_editor"] = wrapped' in ui_source
    assert "render_draft_revision_panel" in ui_source
    assert 'with st_module.expander("초안 버전 기록·비교·복원", expanded=False)' in panel_source
    assert "복원 전 현재 편집본 자동 보존" in (
        root / "src" / "services" / "draft_revision_service.py"
    ).read_text(encoding="utf-8")
