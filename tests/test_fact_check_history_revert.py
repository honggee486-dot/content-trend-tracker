from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.database import connect_database, init_database
from src.services.draft_service import get_fact_checks, update_fact_check
from src.services.fact_check_history_service import (
    list_fact_check_history,
    reconcile_fact_check_history,
    revert_fact_check_history,
)


def _insert_fact_check(con) -> None:
    now = datetime(2026, 7, 31, 12, 20, 0)
    con.execute(
        """
        INSERT INTO topics(
            topic_id, title, normalized_title, summary, category, status,
            priority, is_interested, memo, source_count,
            first_seen_at, last_seen_at, created_at, updated_at, archived_at
        ) VALUES (
            'topic_fact_history', '사실 확인 이력 주제', '사실 확인 이력 주제',
            '', '', 'editing', 2, TRUE, '', 0, ?, ?, ?, ?, NULL
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
            'draft_fact_history', 'topic_fact_history', NULL,
            '사실 확인 이력 초안', '', '', '[]', '본문', '<p>본문</p>',
            '[]', '[]', 1, ?, ?
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
            'fact_history', 'draft_fact_history', '확인이 필요한 주장',
            'needs_verification', '최신 자료 확인 필요', '', '[]', NULL, NULL
        )
        """
    )


def test_reconcile_records_baseline_change_and_avoids_duplicates(tmp_path: Path) -> None:
    db_path = tmp_path / "fact-check-history.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _insert_fact_check(con)

        first = reconcile_fact_check_history(con, "draft_fact_history")
        assert first == {"baselines": 1, "updates": 0}
        baseline = list_fact_check_history(
            con,
            draft_id="draft_fact_history",
            include_baseline=True,
        )
        assert len(baseline) == 1
        assert baseline[0]["action"] == "baseline"

        update_fact_check(
            con,
            fact_check_id="fact_history",
            check_status="verified",
            evidence="공식 발표에서 확인",
            source_url="https://example.com/official",
        )
        detected = reconcile_fact_check_history(con, "draft_fact_history")
        assert detected == {"baselines": 0, "updates": 1}

        history = list_fact_check_history(
            con,
            draft_id="draft_fact_history",
        )
        assert len(history) == 1
        assert history[0]["action"] == "updated"
        assert history[0]["previous_values"]["check_status"] == "needs_verification"
        assert history[0]["new_values"]["check_status"] == "verified"
        assert history[0]["new_values"]["evidence"] == "공식 발표에서 확인"
        assert history[0]["new_values"]["source_url"] == "https://example.com/official"

        duplicate_check = reconcile_fact_check_history(con, "draft_fact_history")
        assert duplicate_check == {"baselines": 0, "updates": 0}
        count = con.execute(
            "SELECT COUNT(*) FROM fact_check_history"
        ).fetchone()[0]
        topic_status = con.execute(
            "SELECT status FROM topics WHERE topic_id = 'topic_fact_history'"
        ).fetchone()[0]

    assert count == 2
    assert topic_status == "publish_ready"


def test_revert_restores_previous_state_and_writes_new_history(tmp_path: Path) -> None:
    db_path = tmp_path / "fact-check-revert.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _insert_fact_check(con)
        reconcile_fact_check_history(con, "draft_fact_history")
        update_fact_check(
            con,
            fact_check_id="fact_history",
            check_status="verified",
            evidence="확인 완료 메모",
            source_url="https://example.com/evidence",
        )
        reconcile_fact_check_history(con, "draft_fact_history")
        changed_history = list_fact_check_history(
            con,
            draft_id="draft_fact_history",
        )[0]

        changed = revert_fact_check_history(
            con,
            history_id=str(changed_history["history_id"]),
            change_note="검토 전 상태로 되돌림",
        )
        assert changed is True

        checks = get_fact_checks(con, "draft_fact_history")
        history = list_fact_check_history(
            con,
            draft_id="draft_fact_history",
        )
        topic_status = con.execute(
            "SELECT status FROM topics WHERE topic_id = 'topic_fact_history'"
        ).fetchone()[0]
        item_count = con.execute(
            "SELECT COUNT(*) FROM fact_check_items WHERE fact_check_id = 'fact_history'"
        ).fetchone()[0]

    assert len(checks) == 1
    assert checks[0]["check_status"] == "needs_verification"
    assert checks[0]["evidence"] == ""
    assert checks[0]["source_url"] in {None, ""}
    assert [item["action"] for item in history] == ["reverted", "updated"]
    assert history[0]["previous_values"]["check_status"] == "verified"
    assert history[0]["new_values"]["check_status"] == "needs_verification"
    assert history[0]["change_note"] == "검토 전 상태로 되돌림"
    assert topic_status == "editing"
    assert item_count == 1


def test_revert_keeps_already_published_topic_status(tmp_path: Path) -> None:
    db_path = tmp_path / "fact-check-published-revert.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _insert_fact_check(con)
        reconcile_fact_check_history(con, "draft_fact_history")
        update_fact_check(
            con,
            fact_check_id="fact_history",
            check_status="verified",
            evidence="발행 전 확인 완료",
            source_url="https://example.com/published-source",
        )
        reconcile_fact_check_history(con, "draft_fact_history")
        changed_history = list_fact_check_history(
            con,
            draft_id="draft_fact_history",
        )[0]
        con.execute(
            "UPDATE topics SET status = 'published' WHERE topic_id = 'topic_fact_history'"
        )

        revert_fact_check_history(
            con,
            history_id=str(changed_history["history_id"]),
            change_note="발행 후 재검토를 위해 되돌림",
        )
        topic_status = con.execute(
            "SELECT status FROM topics WHERE topic_id = 'topic_fact_history'"
        ).fetchone()[0]
        current_status = con.execute(
            "SELECT check_status FROM fact_check_items WHERE fact_check_id = 'fact_history'"
        ).fetchone()[0]

    assert topic_status == "published"
    assert current_status == "needs_verification"


def test_revert_rejects_baseline_and_requires_reason(tmp_path: Path) -> None:
    db_path = tmp_path / "fact-check-revert-guard.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        _insert_fact_check(con)
        reconcile_fact_check_history(con, "draft_fact_history")
        baseline = list_fact_check_history(
            con,
            draft_id="draft_fact_history",
            include_baseline=True,
        )[0]

        with pytest.raises(ValueError, match="사유"):
            revert_fact_check_history(
                con,
                history_id=str(baseline["history_id"]),
                change_note="",
            )
        with pytest.raises(ValueError, match="기준 상태"):
            revert_fact_check_history(
                con,
                history_id=str(baseline["history_id"]),
                change_note="기준점 복원 시도",
            )


def test_fact_check_history_panel_is_collapsed_and_attached() -> None:
    root = Path(__file__).resolve().parents[1]
    panel_source = (root / "src" / "fact_check_history_ui.py").read_text(
        encoding="utf-8"
    )
    revision_ui_source = (root / "src" / "draft_revision_ui.py").read_text(
        encoding="utf-8"
    )

    assert 'with st_module.expander("사실 확인 변경 이력·안전 되돌리기", expanded=False)' in panel_source
    assert "확인을 위해 `되돌리기`를 입력하세요." in panel_source
    assert "revert_fact_check_history" in panel_source
    assert "render_fact_check_history_panel" in revision_ui_source
    assert "사실 확인 변경 이력을 불러오지 못했습니다" in revision_ui_source
