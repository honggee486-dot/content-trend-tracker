from __future__ import annotations

from datetime import datetime
import json
from typing import Any
from uuid import uuid4

import duckdb


PUBLISH_HISTORY_ACTION_LABELS = {
    "corrected": "정정",
    "archived": "보관",
    "restored": "복원",
}


def _cursor_rows(cursor) -> list[dict[str, Any]]:
    columns = [str(column[0]) for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def ensure_publish_record_management_schema(
    con: duckdb.DuckDBPyConnection,
) -> None:
    columns = {
        str(row[1])
        for row in con.execute("PRAGMA table_info('publish_records')").fetchall()
    }
    history_row = con.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = 'main'
          AND table_name = 'publish_record_history'
        """
    ).fetchone()
    history_exists = bool(history_row and int(history_row[0] or 0))
    required_columns = {"blog_profile_id", "updated_at", "archived_at"}
    if required_columns.issubset(columns) and history_exists:
        return

    if "blog_profile_id" not in columns:
        con.execute("ALTER TABLE publish_records ADD COLUMN blog_profile_id VARCHAR")
    if "updated_at" not in columns:
        con.execute("ALTER TABLE publish_records ADD COLUMN updated_at TIMESTAMP")
    if "archived_at" not in columns:
        con.execute("ALTER TABLE publish_records ADD COLUMN archived_at TIMESTAMP")
    con.execute(
        """
        UPDATE publish_records
        SET updated_at = COALESCE(updated_at, published_at, created_at)
        WHERE updated_at IS NULL
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS publish_record_history (
            history_id VARCHAR PRIMARY KEY,
            publish_id VARCHAR NOT NULL,
            action VARCHAR NOT NULL,
            previous_values_json VARCHAR NOT NULL,
            new_values_json VARCHAR NOT NULL,
            change_note VARCHAR NOT NULL,
            changed_at TIMESTAMP NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_publish_records_active
        ON publish_records(archived_at, published_at)
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_publish_record_history_publish
        ON publish_record_history(publish_id, changed_at)
        """
    )


def _record_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "publish_id",
        "draft_id",
        "platform",
        "publish_status",
        "write_url",
        "published_url",
        "memo",
        "blog_profile_id",
        "created_at",
        "published_at",
        "updated_at",
        "archived_at",
    )
    snapshot: dict[str, Any] = {}
    for key in keys:
        value = record.get(key)
        snapshot[key] = value.isoformat() if isinstance(value, datetime) else value
    return snapshot


def _write_publish_history(
    con: duckdb.DuckDBPyConnection,
    *,
    publish_id: str,
    action: str,
    previous: dict[str, Any],
    current: dict[str, Any],
    change_note: str,
    changed_at: datetime,
) -> None:
    con.execute(
        """
        INSERT INTO publish_record_history(
            history_id, publish_id, action, previous_values_json,
            new_values_json, change_note, changed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            f"pubhist_{uuid4().hex}",
            publish_id,
            action,
            json.dumps(_record_snapshot(previous), ensure_ascii=False, sort_keys=True),
            json.dumps(_record_snapshot(current), ensure_ascii=False, sort_keys=True),
            change_note.strip(),
            changed_at,
        ],
    )


def get_unresolved_fact_check_count(
    con: duckdb.DuckDBPyConnection,
    draft_id: str,
) -> int:
    row = con.execute(
        """
        SELECT COUNT(*)
        FROM fact_check_items
        WHERE draft_id = ? AND check_status <> 'verified'
        """,
        [draft_id],
    ).fetchone()
    return int(row[0]) if row else 0


def list_publish_records(
    con: duckdb.DuckDBPyConnection,
    *,
    include_archived: bool = False,
    query: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    ensure_publish_record_management_schema(con)
    normalized_query = str(query or "").strip().casefold()
    like_query = f"%{normalized_query}%"
    rows = con.execute(
        """
        SELECT pr.publish_id, pr.draft_id, pr.platform, pr.publish_status,
               pr.write_url, pr.published_url, pr.memo, pr.blog_profile_id,
               pr.created_at, pr.published_at, pr.updated_at, pr.archived_at,
               d.title AS draft_title, d.topic_id,
               t.title AS topic_title,
               bp.profile_name AS blog_profile_name
        FROM publish_records pr
        JOIN drafts d ON d.draft_id = pr.draft_id
        JOIN topics t ON t.topic_id = d.topic_id
        LEFT JOIN blog_profiles bp ON bp.blog_profile_id = pr.blog_profile_id
        WHERE (? OR pr.archived_at IS NULL)
          AND (
                ? = ''
                OR LOWER(COALESCE(t.title, '')) LIKE ?
                OR LOWER(COALESCE(d.title, '')) LIKE ?
                OR LOWER(COALESCE(pr.platform, '')) LIKE ?
                OR LOWER(COALESCE(pr.published_url, '')) LIKE ?
                OR LOWER(COALESCE(pr.memo, '')) LIKE ?
              )
        ORDER BY pr.archived_at NULLS FIRST,
                 pr.published_at DESC NULLS LAST,
                 pr.created_at DESC
        LIMIT ?
        """,
        [
            bool(include_archived),
            normalized_query,
            like_query,
            like_query,
            like_query,
            like_query,
            like_query,
            max(1, min(int(limit), 500)),
        ],
    ).fetchall()
    columns = [str(column[0]) for column in con.description]
    return [dict(zip(columns, row)) for row in rows]


def get_publish_record(
    con: duckdb.DuckDBPyConnection,
    publish_id: str,
) -> dict[str, Any] | None:
    ensure_publish_record_management_schema(con)
    rows = _cursor_rows(
        con.execute(
            """
            SELECT publish_id, draft_id, platform, publish_status, write_url,
                   published_url, memo, blog_profile_id, created_at,
                   published_at, updated_at, archived_at
            FROM publish_records
            WHERE publish_id = ?
            """,
            [publish_id],
        )
    )
    return rows[0] if rows else None


def list_publish_record_history(
    con: duckdb.DuckDBPyConnection,
    publish_id: str,
) -> list[dict[str, Any]]:
    ensure_publish_record_management_schema(con)
    rows = _cursor_rows(
        con.execute(
            """
            SELECT history_id, publish_id, action, previous_values_json,
                   new_values_json, change_note, changed_at
            FROM publish_record_history
            WHERE publish_id = ?
            ORDER BY changed_at DESC, history_id DESC
            """,
            [publish_id],
        )
    )
    for row in rows:
        for source_key, target_key in (
            ("previous_values_json", "previous_values"),
            ("new_values_json", "new_values"),
        ):
            try:
                parsed = json.loads(str(row.pop(source_key) or "{}"))
            except json.JSONDecodeError:
                parsed = {}
            row[target_key] = parsed if isinstance(parsed, dict) else {}
        row["action_label"] = PUBLISH_HISTORY_ACTION_LABELS.get(
            str(row.get("action") or ""),
            str(row.get("action") or "변경"),
        )
    return rows


def _refresh_topic_publication_status(
    con: duckdb.DuckDBPyConnection,
    *,
    draft_id: str,
    now: datetime,
) -> None:
    topic_row = con.execute(
        "SELECT topic_id FROM drafts WHERE draft_id = ?",
        [draft_id],
    ).fetchone()
    if topic_row is None:
        return
    topic_id = str(topic_row[0])
    active_row = con.execute(
        """
        SELECT COUNT(*)
        FROM publish_records pr
        JOIN drafts d ON d.draft_id = pr.draft_id
        WHERE d.topic_id = ?
          AND pr.publish_status = 'published'
          AND pr.archived_at IS NULL
        """,
        [topic_id],
    ).fetchone()
    if active_row and int(active_row[0] or 0) > 0:
        status = "published"
    else:
        latest_draft = con.execute(
            """
            SELECT draft_id
            FROM drafts
            WHERE topic_id = ?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            [topic_id],
        ).fetchone()
        if latest_draft is None:
            status = "candidate"
        else:
            counts = con.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN check_status <> 'verified' THEN 1 ELSE 0 END) AS unresolved
                FROM fact_check_items
                WHERE draft_id = ?
                """,
                [str(latest_draft[0])],
            ).fetchone()
            total = int(counts[0] or 0) if counts else 0
            unresolved = int(counts[1] or 0) if counts else 0
            if unresolved:
                status = "editing"
            elif total:
                status = "publish_ready"
            else:
                status = "draft_complete"
    con.execute(
        "UPDATE topics SET status = ?, updated_at = ? WHERE topic_id = ?",
        [status, now, topic_id],
    )


def update_publish_record(
    con: duckdb.DuckDBPyConnection,
    *,
    publish_id: str,
    platform: str,
    write_url: str,
    published_url: str,
    memo: str,
    published_at: datetime,
    change_note: str,
) -> bool:
    ensure_publish_record_management_schema(con)
    note = str(change_note or "").strip()
    if not note:
        raise ValueError("정정 사유를 입력하세요.")
    normalized_platform = str(platform or "").strip()
    if not normalized_platform:
        raise ValueError("발행 플랫폼을 입력하세요.")
    if not isinstance(published_at, datetime):
        raise ValueError("올바른 발행 시각을 입력하세요.")

    previous = get_publish_record(con, publish_id)
    if previous is None:
        raise ValueError("발행 기록을 찾을 수 없습니다.")
    current = {
        **previous,
        "platform": normalized_platform,
        "write_url": str(write_url or "").strip(),
        "published_url": str(published_url or "").strip(),
        "memo": str(memo or "").strip(),
        "published_at": published_at,
    }
    comparable_keys = ("platform", "write_url", "published_url", "memo", "published_at")
    if all(previous.get(key) == current.get(key) for key in comparable_keys):
        return False

    now = datetime.now()
    current["updated_at"] = now
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            """
            UPDATE publish_records
            SET platform = ?, write_url = ?, published_url = ?, memo = ?,
                published_at = ?, updated_at = ?
            WHERE publish_id = ?
            """,
            [
                current["platform"],
                current["write_url"],
                current["published_url"],
                current["memo"],
                current["published_at"],
                now,
                publish_id,
            ],
        )
        _write_publish_history(
            con,
            publish_id=publish_id,
            action="corrected",
            previous=previous,
            current=current,
            change_note=note,
            changed_at=now,
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return True


def archive_publish_record(
    con: duckdb.DuckDBPyConnection,
    *,
    publish_id: str,
    change_note: str,
) -> bool:
    ensure_publish_record_management_schema(con)
    note = str(change_note or "").strip()
    if not note:
        raise ValueError("보관 사유를 입력하세요.")
    previous = get_publish_record(con, publish_id)
    if previous is None:
        raise ValueError("발행 기록을 찾을 수 없습니다.")
    if previous.get("archived_at") is not None:
        return False

    now = datetime.now()
    current = {**previous, "archived_at": now, "updated_at": now}
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            """
            UPDATE publish_records
            SET archived_at = ?, updated_at = ?
            WHERE publish_id = ? AND archived_at IS NULL
            """,
            [now, now, publish_id],
        )
        _write_publish_history(
            con,
            publish_id=publish_id,
            action="archived",
            previous=previous,
            current=current,
            change_note=note,
            changed_at=now,
        )
        _refresh_topic_publication_status(
            con,
            draft_id=str(previous["draft_id"]),
            now=now,
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return True


def restore_publish_record(
    con: duckdb.DuckDBPyConnection,
    *,
    publish_id: str,
    change_note: str,
) -> bool:
    ensure_publish_record_management_schema(con)
    note = str(change_note or "").strip()
    if not note:
        raise ValueError("복원 사유를 입력하세요.")
    previous = get_publish_record(con, publish_id)
    if previous is None:
        raise ValueError("발행 기록을 찾을 수 없습니다.")
    if previous.get("archived_at") is None:
        return False

    now = datetime.now()
    current = {**previous, "archived_at": None, "updated_at": now}
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            """
            UPDATE publish_records
            SET archived_at = NULL, updated_at = ?
            WHERE publish_id = ? AND archived_at IS NOT NULL
            """,
            [now, publish_id],
        )
        _write_publish_history(
            con,
            publish_id=publish_id,
            action="restored",
            previous=previous,
            current=current,
            change_note=note,
            changed_at=now,
        )
        _refresh_topic_publication_status(
            con,
            draft_id=str(previous["draft_id"]),
            now=now,
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return True


def mark_published(
    con: duckdb.DuckDBPyConnection,
    *,
    draft_id: str,
    platform: str,
    write_url: str,
    published_url: str,
    memo: str = "",
    allow_unverified: bool = False,
    blog_profile_id: str | None = None,
) -> str:
    ensure_publish_record_management_schema(con)
    row = con.execute(
        "SELECT topic_id FROM drafts WHERE draft_id = ?",
        [draft_id],
    ).fetchone()
    if row is None:
        raise ValueError("초안을 찾을 수 없습니다.")

    unresolved = get_unresolved_fact_check_count(con, draft_id)
    if unresolved and not allow_unverified:
        raise ValueError(
            f"미확인 또는 수정 필요 사실 확인 항목이 {unresolved}개 남아 있습니다."
        )

    normalized_platform = str(platform or "").strip()
    if not normalized_platform:
        raise ValueError("발행 플랫폼을 입력하세요.")
    normalized_published_url = published_url.strip()
    existing = con.execute(
        """
        SELECT publish_id
        FROM publish_records
        WHERE draft_id = ?
          AND platform = ?
          AND COALESCE(blog_profile_id, '') = COALESCE(?, '')
          AND publish_status = 'published'
          AND archived_at IS NULL
          AND COALESCE(published_url, '') = ?
        ORDER BY published_at DESC NULLS LAST, created_at DESC
        LIMIT 1
        """,
        [draft_id, normalized_platform, blog_profile_id, normalized_published_url],
    ).fetchone()
    if existing is not None:
        return str(existing[0])

    now = datetime.now()
    publish_id = f"pub_{uuid4().hex}"
    con.execute(
        """
        INSERT INTO publish_records(
            publish_id, draft_id, platform, publish_status, write_url,
            published_url, memo, created_at, published_at, blog_profile_id,
            updated_at, archived_at
        ) VALUES (?, ?, ?, 'published', ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        [
            publish_id,
            draft_id,
            normalized_platform,
            write_url,
            normalized_published_url,
            memo.strip(),
            now,
            now,
            blog_profile_id,
            now,
        ],
    )
    con.execute(
        "UPDATE topics SET status = 'published', updated_at = ? WHERE topic_id = ?",
        [now, row[0]],
    )
    return publish_id
