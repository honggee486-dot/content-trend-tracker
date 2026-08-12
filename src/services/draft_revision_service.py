"""초안 리비전 목록, 현재본 비교와 안전 복원을 제공합니다."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

import duckdb

from src.services.draft_service import get_draft, markdown_to_html


@dataclass(frozen=True)
class DraftRevisionComparison:
    revision_id: str
    revision_number: int
    current_revision: int
    title_changed: bool
    summary_changed: bool
    category_changed: bool
    tags_changed: bool
    body_changed: bool
    added_lines: int
    removed_lines: int
    diff_text: str
    diff_truncated: bool

    @property
    def has_changes(self) -> bool:
        return any(
            (
                self.title_changed,
                self.summary_changed,
                self.category_changed,
                self.tags_changed,
                self.body_changed,
            )
        )


def _load_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _revision_row_to_dict(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    result = dict(zip(columns, row))
    result["tags"] = _load_tags(result.get("tags_json"))
    return result


def list_draft_revisions(
    con: duckdb.DuckDBPyConnection,
    draft_id: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT revision_id, draft_id, revision_number, title, summary, category,
               tags_json, body_markdown, change_note, created_at
        FROM draft_revisions
        WHERE draft_id = ?
        ORDER BY revision_number DESC, created_at DESC
        LIMIT ?
        """,
        [draft_id, max(1, min(int(limit), 500))],
    ).fetchall()
    columns = [str(item[0]) for item in con.description]
    return [_revision_row_to_dict(columns, row) for row in rows]


def get_draft_revision(
    con: duckdb.DuckDBPyConnection,
    *,
    draft_id: str,
    revision_id: str,
) -> dict[str, Any] | None:
    row = con.execute(
        """
        SELECT revision_id, draft_id, revision_number, title, summary, category,
               tags_json, body_markdown, change_note, created_at
        FROM draft_revisions
        WHERE draft_id = ? AND revision_id = ?
        """,
        [draft_id, revision_id],
    ).fetchone()
    if row is None:
        return None
    columns = [str(item[0]) for item in con.description]
    return _revision_row_to_dict(columns, row)


def _get_revision_by_number(
    con: duckdb.DuckDBPyConnection,
    *,
    draft_id: str,
    revision_number: int,
) -> dict[str, Any] | None:
    row = con.execute(
        """
        SELECT revision_id, draft_id, revision_number, title, summary, category,
               tags_json, body_markdown, change_note, created_at
        FROM draft_revisions
        WHERE draft_id = ? AND revision_number = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [draft_id, int(revision_number)],
    ).fetchone()
    if row is None:
        return None
    columns = [str(item[0]) for item in con.description]
    return _revision_row_to_dict(columns, row)


def _draft_state(value: dict[str, Any]) -> tuple[str, str, str, tuple[str, ...], str]:
    return (
        str(value.get("title") or "").strip(),
        str(value.get("summary") or "").strip(),
        str(value.get("category") or "").strip(),
        tuple(str(item) for item in value.get("tags", [])),
        str(value.get("body_markdown") or "").strip(),
    )


def compare_draft_to_revision(
    con: duckdb.DuckDBPyConnection,
    *,
    draft_id: str,
    revision_id: str,
    max_diff_lines: int = 400,
) -> DraftRevisionComparison:
    draft = get_draft(con, draft_id)
    if draft is None:
        raise ValueError("초안을 찾을 수 없습니다.")
    revision = get_draft_revision(
        con,
        draft_id=draft_id,
        revision_id=revision_id,
    )
    if revision is None:
        raise ValueError("선택한 초안 버전을 찾을 수 없습니다.")

    revision_body = str(revision.get("body_markdown") or "").strip()
    current_body = str(draft.get("body_markdown") or "").strip()
    raw_diff = list(
        difflib.unified_diff(
            revision_body.splitlines(),
            current_body.splitlines(),
            fromfile=f"과거 v{int(revision['revision_number'])}",
            tofile=f"현재 v{int(draft['current_revision'])}",
            lineterm="",
        )
    )
    added_lines = sum(
        1 for line in raw_diff if line.startswith("+") and not line.startswith("+++")
    )
    removed_lines = sum(
        1 for line in raw_diff if line.startswith("-") and not line.startswith("---")
    )
    bounded = max(20, min(int(max_diff_lines), 2000))
    truncated = len(raw_diff) > bounded
    shown = raw_diff[:bounded]
    if truncated:
        shown.append(f"... 차이 {len(raw_diff) - bounded:,}줄은 화면에서 생략했습니다.")

    return DraftRevisionComparison(
        revision_id=str(revision["revision_id"]),
        revision_number=int(revision["revision_number"]),
        current_revision=int(draft["current_revision"]),
        title_changed=str(revision.get("title") or "").strip()
        != str(draft.get("title") or "").strip(),
        summary_changed=str(revision.get("summary") or "").strip()
        != str(draft.get("summary") or "").strip(),
        category_changed=str(revision.get("category") or "").strip()
        != str(draft.get("category") or "").strip(),
        tags_changed=tuple(revision.get("tags") or []) != tuple(draft.get("tags") or []),
        body_changed=revision_body != current_body,
        added_lines=added_lines,
        removed_lines=removed_lines,
        diff_text="\n".join(shown) if shown else "본문 차이가 없습니다.",
        diff_truncated=truncated,
    )


def _insert_revision_snapshot(
    con: duckdb.DuckDBPyConnection,
    *,
    draft_id: str,
    revision_number: int,
    state: dict[str, Any],
    change_note: str,
    created_at: datetime,
) -> str:
    revision_id = f"rev_{uuid4().hex}"
    con.execute(
        """
        INSERT INTO draft_revisions(
            revision_id, draft_id, revision_number, title, summary, category,
            tags_json, body_markdown, change_note, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            revision_id,
            draft_id,
            revision_number,
            str(state.get("title") or "").strip(),
            str(state.get("summary") or "").strip(),
            str(state.get("category") or "").strip(),
            json.dumps(list(state.get("tags") or []), ensure_ascii=False),
            str(state.get("body_markdown") or "").strip(),
            change_note.strip(),
            created_at,
        ],
    )
    return revision_id


def restore_draft_revision(
    con: duckdb.DuckDBPyConnection,
    *,
    draft_id: str,
    revision_id: str,
    change_note: str = "",
) -> int:
    """선택 버전을 새 리비전으로 복원하고 모든 기존 버전을 보존합니다."""
    draft = get_draft(con, draft_id)
    if draft is None:
        raise ValueError("초안을 찾을 수 없습니다.")
    revision = get_draft_revision(
        con,
        draft_id=draft_id,
        revision_id=revision_id,
    )
    if revision is None:
        raise ValueError("선택한 초안 버전을 찾을 수 없습니다.")

    restored_title = str(revision.get("title") or "").strip()
    restored_body = str(revision.get("body_markdown") or "").strip()
    if not restored_title or not restored_body:
        raise ValueError("제목이나 본문이 비어 있는 버전은 복원할 수 없습니다.")
    if _draft_state(draft) == _draft_state(revision):
        raise ValueError("선택한 버전과 현재 편집본의 내용이 같습니다.")

    max_revision = int(
        con.execute(
            "SELECT COALESCE(MAX(revision_number), 0) FROM draft_revisions WHERE draft_id = ?",
            [draft_id],
        ).fetchone()[0]
    )
    current_revision = _get_revision_by_number(
        con,
        draft_id=draft_id,
        revision_number=int(draft.get("current_revision") or 0),
    )

    now = datetime.now()
    con.execute("BEGIN TRANSACTION")
    try:
        if current_revision is None or _draft_state(draft) != _draft_state(current_revision):
            max_revision += 1
            _insert_revision_snapshot(
                con,
                draft_id=draft_id,
                revision_number=max_revision,
                state=draft,
                change_note="복원 전 현재 편집본 자동 보존",
                created_at=now,
            )

        restored_revision = max_revision + 1
        selected_number = int(revision["revision_number"])
        note = change_note.strip() or f"v{selected_number}에서 복원"
        _insert_revision_snapshot(
            con,
            draft_id=draft_id,
            revision_number=restored_revision,
            state=revision,
            change_note=note,
            created_at=now,
        )
        con.execute(
            """
            UPDATE drafts
            SET title = ?, summary = ?, category = ?, tags_json = ?,
                body_markdown = ?, body_html = ?, current_revision = ?, updated_at = ?
            WHERE draft_id = ?
            """,
            [
                restored_title,
                str(revision.get("summary") or "").strip(),
                str(revision.get("category") or "").strip(),
                json.dumps(list(revision.get("tags") or []), ensure_ascii=False),
                restored_body,
                markdown_to_html(restored_body),
                restored_revision,
                now,
                draft_id,
            ],
        )
        con.execute(
            "UPDATE topics SET status = 'editing', updated_at = ? WHERE topic_id = ?",
            [now, str(draft["topic_id"])],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return restored_revision
