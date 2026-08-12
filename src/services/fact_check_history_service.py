"""사실 확인 항목의 변경 전후를 보존하고 과거 상태로 안전하게 되돌립니다."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import duckdb


FACT_CHECK_HISTORY_ACTION_LABELS = {
    "baseline": "기준 상태",
    "updated": "수정",
    "reverted": "되돌리기",
}
_VALID_STATUSES = {"needs_verification", "verified", "needs_revision"}
_SNAPSHOT_KEYS = (
    "fact_check_id",
    "draft_id",
    "claim_text",
    "check_status",
    "reason",
    "evidence",
    "source_ids_json",
    "source_url",
    "checked_at",
)


def _cursor_rows(cursor) -> list[dict[str, Any]]:
    columns = [str(column[0]) for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def ensure_fact_check_history_schema(con: duckdb.DuckDBPyConnection) -> None:
    history_row = con.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = 'main'
          AND table_name = 'fact_check_history'
        """
    ).fetchone()
    if history_row and int(history_row[0] or 0) > 0:
        return
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS fact_check_history (
            history_id VARCHAR PRIMARY KEY,
            fact_check_id VARCHAR NOT NULL,
            draft_id VARCHAR NOT NULL,
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
        CREATE INDEX IF NOT EXISTS idx_fact_check_history_item
        ON fact_check_history(fact_check_id, changed_at)
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fact_check_history_draft
        ON fact_check_history(draft_id, changed_at)
        """
    )


def _serialize_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _snapshot(record: dict[str, Any]) -> dict[str, Any]:
    return {key: _serialize_value(record.get(key)) for key in _SNAPSHOT_KEYS}


def _normalized_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    snapshot = _snapshot(record)
    snapshot["source_ids_json"] = str(snapshot.get("source_ids_json") or "[]")
    snapshot["source_url"] = str(snapshot.get("source_url") or "")
    snapshot["evidence"] = str(snapshot.get("evidence") or "")
    snapshot["reason"] = str(snapshot.get("reason") or "")
    return snapshot


def _parse_snapshot(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_history(
    con: duckdb.DuckDBPyConnection,
    *,
    fact_check_id: str,
    draft_id: str,
    action: str,
    previous: dict[str, Any],
    current: dict[str, Any],
    change_note: str,
    changed_at: datetime,
) -> str:
    history_id = f"facthist_{uuid4().hex}"
    con.execute(
        """
        INSERT INTO fact_check_history(
            history_id, fact_check_id, draft_id, action,
            previous_values_json, new_values_json, change_note, changed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            history_id,
            fact_check_id,
            draft_id,
            action,
            json.dumps(_normalized_snapshot(previous), ensure_ascii=False, sort_keys=True),
            json.dumps(_normalized_snapshot(current), ensure_ascii=False, sort_keys=True),
            str(change_note or "").strip(),
            changed_at,
        ],
    )
    return history_id


def _current_fact_checks(
    con: duckdb.DuckDBPyConnection,
    draft_id: str,
) -> list[dict[str, Any]]:
    return _cursor_rows(
        con.execute(
            """
            SELECT fact_check_id, draft_id, claim_text, check_status, reason,
                   evidence, source_ids_json, source_url, checked_at
            FROM fact_check_items
            WHERE draft_id = ?
            ORDER BY claim_text, fact_check_id
            """,
            [draft_id],
        )
    )


def reconcile_fact_check_history(
    con: duckdb.DuckDBPyConnection,
    draft_id: str,
) -> dict[str, int]:
    """현재 상태를 기준으로 최초 스냅샷과 감지된 변경 이력을 보완합니다."""
    ensure_fact_check_history_schema(con)
    baseline_count = 0
    update_count = 0
    now = datetime.now()
    for current in _current_fact_checks(con, draft_id):
        latest = con.execute(
            """
            SELECT new_values_json
            FROM fact_check_history
            WHERE fact_check_id = ?
            ORDER BY changed_at DESC, history_id DESC
            LIMIT 1
            """,
            [str(current["fact_check_id"])],
        ).fetchone()
        if latest is None:
            _write_history(
                con,
                fact_check_id=str(current["fact_check_id"]),
                draft_id=draft_id,
                action="baseline",
                previous=current,
                current=current,
                change_note="기능 도입 시점의 기준 상태 자동 보존",
                changed_at=now,
            )
            baseline_count += 1
            continue
        previous_snapshot = _parse_snapshot(latest[0])
        if _normalized_snapshot(previous_snapshot) == _normalized_snapshot(current):
            continue
        _write_history(
            con,
            fact_check_id=str(current["fact_check_id"]),
            draft_id=draft_id,
            action="updated",
            previous=previous_snapshot,
            current=current,
            change_note="사실 확인 상태·메모·URL 변경 감지",
            changed_at=now,
        )
        update_count += 1
    return {"baselines": baseline_count, "updates": update_count}


def list_fact_check_history(
    con: duckdb.DuckDBPyConnection,
    *,
    draft_id: str,
    fact_check_id: str = "",
    include_baseline: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    ensure_fact_check_history_schema(con)
    rows = _cursor_rows(
        con.execute(
            """
            SELECT h.history_id, h.fact_check_id, h.draft_id, h.action,
                   h.previous_values_json, h.new_values_json,
                   h.change_note, h.changed_at,
                   f.claim_text
            FROM fact_check_history h
            LEFT JOIN fact_check_items f ON f.fact_check_id = h.fact_check_id
            WHERE h.draft_id = ?
              AND (? = '' OR h.fact_check_id = ?)
              AND (? OR h.action <> 'baseline')
            ORDER BY h.changed_at DESC, h.history_id DESC
            LIMIT ?
            """,
            [
                draft_id,
                str(fact_check_id or "").strip(),
                str(fact_check_id or "").strip(),
                bool(include_baseline),
                max(1, min(int(limit), 1000)),
            ],
        )
    )
    for row in rows:
        row["previous_values"] = _parse_snapshot(row.pop("previous_values_json", "{}"))
        row["new_values"] = _parse_snapshot(row.pop("new_values_json", "{}"))
        row["action_label"] = FACT_CHECK_HISTORY_ACTION_LABELS.get(
            str(row.get("action") or ""),
            str(row.get("action") or "변경"),
        )
    return rows


def _valid_optional_url(value: str) -> bool:
    if not value:
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _refresh_topic_status(
    con: duckdb.DuckDBPyConnection,
    *,
    draft_id: str,
    now: datetime,
) -> None:
    row = con.execute(
        """
        SELECT d.topic_id, t.status
        FROM drafts d
        JOIN topics t ON t.topic_id = d.topic_id
        WHERE d.draft_id = ?
        """,
        [draft_id],
    ).fetchone()
    if row is None or str(row[1] or "") == "published":
        return
    counts = con.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN check_status <> 'verified' THEN 1 ELSE 0 END) AS unresolved
        FROM fact_check_items
        WHERE draft_id = ?
        """,
        [draft_id],
    ).fetchone()
    total = int(counts[0] or 0) if counts else 0
    unresolved = int(counts[1] or 0) if counts else 0
    next_status = "publish_ready" if total > 0 and unresolved == 0 else "editing"
    con.execute(
        "UPDATE topics SET status = ?, updated_at = ? WHERE topic_id = ?",
        [next_status, now, str(row[0])],
    )


def revert_fact_check_history(
    con: duckdb.DuckDBPyConnection,
    *,
    history_id: str,
    change_note: str,
) -> bool:
    """선택한 변경의 직전 상태를 현재 항목에 반영하고 새 이력을 남깁니다."""
    ensure_fact_check_history_schema(con)
    note = str(change_note or "").strip()
    if not note:
        raise ValueError("되돌리기 사유를 입력하세요.")
    row = con.execute(
        """
        SELECT fact_check_id, draft_id, action, previous_values_json
        FROM fact_check_history
        WHERE history_id = ?
        """,
        [history_id],
    ).fetchone()
    if row is None:
        raise ValueError("선택한 사실 확인 변경 이력을 찾을 수 없습니다.")
    if str(row[2] or "") == "baseline":
        raise ValueError("기준 상태 행은 되돌리기 대상으로 사용할 수 없습니다.")

    fact_check_id = str(row[0])
    draft_id = str(row[1])
    target = _parse_snapshot(row[3])
    current_rows = _cursor_rows(
        con.execute(
            """
            SELECT fact_check_id, draft_id, claim_text, check_status, reason,
                   evidence, source_ids_json, source_url, checked_at
            FROM fact_check_items
            WHERE fact_check_id = ?
            """,
            [fact_check_id],
        )
    )
    if not current_rows:
        raise ValueError("되돌릴 사실 확인 항목을 찾을 수 없습니다.")
    current = current_rows[0]
    if _normalized_snapshot(current) == _normalized_snapshot(target):
        return False

    status = str(target.get("check_status") or "needs_verification")
    if status not in _VALID_STATUSES:
        raise ValueError("과거 이력의 사실 확인 상태가 올바르지 않습니다.")
    evidence = str(target.get("evidence") or "")
    source_url = str(target.get("source_url") or "")
    if not _valid_optional_url(source_url):
        raise ValueError("과거 이력의 근거 URL이 올바르지 않습니다.")
    if status == "verified" and not (evidence.strip() or source_url.strip()):
        raise ValueError("확인 완료 상태로 되돌리려면 과거 메모나 근거 URL이 필요합니다.")

    checked_at_raw = target.get("checked_at")
    checked_at = None
    if checked_at_raw:
        try:
            checked_at = datetime.fromisoformat(str(checked_at_raw))
        except ValueError:
            checked_at = None
    now = datetime.now()
    target_current = {
        **current,
        "check_status": status,
        "evidence": evidence,
        "source_url": source_url,
        "checked_at": checked_at,
    }

    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            """
            UPDATE fact_check_items
            SET check_status = ?, evidence = ?, source_url = ?, checked_at = ?
            WHERE fact_check_id = ?
            """,
            [status, evidence, source_url or None, checked_at, fact_check_id],
        )
        _write_history(
            con,
            fact_check_id=fact_check_id,
            draft_id=draft_id,
            action="reverted",
            previous=current,
            current=target_current,
            change_note=note,
            changed_at=now,
        )
        _refresh_topic_status(con, draft_id=draft_id, now=now)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return True
