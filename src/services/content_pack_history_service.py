"""자료팩 버전 조회, 비교와 안전한 입력값 재사용 준비를 제공합니다."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from typing import Any

import duckdb

from src.services.content_pack_service import get_content_pack


SETTING_FIELD_LABELS = {
    "audience": "독자 대상",
    "purpose": "글 목적",
    "angle": "글의 관점",
    "category": "카테고리",
    "target_length": "목표 분량",
    "title_rules": "제목 규칙",
    "outline": "본문 구성",
    "forbidden_expressions": "금지 표현",
    "fact_check_items": "사실 확인 목록",
}


@dataclass(frozen=True)
class ContentPackComparison:
    older_pack_id: str
    newer_pack_id: str
    older_version: int
    newer_version: int
    changed_fields: tuple[str, ...]
    added_references: tuple[str, ...]
    removed_references: tuple[str, ...]
    added_lines: int
    removed_lines: int
    diff_text: str
    diff_truncated: bool

    @property
    def has_changes(self) -> bool:
        return bool(
            self.changed_fields
            or self.added_references
            or self.removed_references
            or self.added_lines
            or self.removed_lines
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


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in _load_json_list(value)]


def _reference_key(reference: dict[str, Any]) -> str:
    kind = str(reference.get("reference_kind") or "unknown")
    identifier = str(
        reference.get("source_item_id")
        or reference.get("topic_reference_id")
        or reference.get("url")
        or reference.get("title")
        or reference.get("id")
        or ""
    ).strip()
    return f"{kind}:{identifier}"


def _reference_label(reference: dict[str, Any]) -> str:
    kind = str(reference.get("reference_kind_label") or "참고 자료")
    title = str(reference.get("title") or "제목 없음")
    publisher = str(reference.get("publisher") or "출처 미입력")
    return f"[{kind}] {title} · {publisher}"


def _hydrate_pack(pack: dict[str, Any]) -> dict[str, Any]:
    hydrated = dict(pack)
    hydrated["title_rules"] = _string_list(pack.get("title_rules_json"))
    hydrated["outline"] = _string_list(pack.get("outline_json"))
    hydrated["forbidden_expressions"] = _string_list(
        pack.get("forbidden_expressions_json")
    )
    hydrated["fact_check_items"] = _string_list(pack.get("fact_check_items_json"))
    hydrated["references"] = [
        item
        for item in _load_json_list(pack.get("references_json"))
        if isinstance(item, dict)
    ]
    return hydrated


def list_content_pack_topics(
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    return _cursor_rows(
        con.execute(
            """
            SELECT p.topic_id, t.title AS topic_title,
                   COUNT(*) AS version_count,
                   MAX(p.version) AS latest_version,
                   MAX(p.created_at) AS latest_created_at
            FROM content_packs p
            JOIN topics t ON t.topic_id = p.topic_id
            GROUP BY p.topic_id, t.title
            ORDER BY latest_created_at DESC, t.title
            LIMIT ?
            """,
            [max(1, min(int(limit), 1000))],
        )
    )


def list_content_pack_versions(
    con: duckdb.DuckDBPyConnection,
    topic_id: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = _cursor_rows(
        con.execute(
            """
            SELECT p.*,
                   (
                       SELECT COUNT(*)
                       FROM generation_sessions g
                       WHERE g.content_pack_id = p.content_pack_id
                   ) AS generation_count,
                   (
                       SELECT COUNT(*)
                       FROM drafts d
                       JOIN generation_sessions g
                         ON g.generation_id = d.generation_id
                       WHERE g.content_pack_id = p.content_pack_id
                   ) AS draft_count
            FROM content_packs p
            WHERE p.topic_id = ?
            ORDER BY p.version DESC, p.created_at DESC
            LIMIT ?
            """,
            [topic_id, max(1, min(int(limit), 500))],
        )
    )
    return [_hydrate_pack(row) for row in rows]


def get_content_pack_snapshot(
    con: duckdb.DuckDBPyConnection,
    content_pack_id: str,
) -> dict[str, Any] | None:
    pack = get_content_pack(con, content_pack_id)
    return _hydrate_pack(pack) if pack is not None else None


def _comparable_settings(pack: dict[str, Any]) -> dict[str, Any]:
    return {
        "audience": str(pack.get("audience") or "").strip(),
        "purpose": str(pack.get("purpose") or "").strip(),
        "angle": str(pack.get("angle") or "").strip(),
        "category": str(pack.get("category") or "").strip(),
        "target_length": int(pack.get("target_length") or 0),
        "title_rules": tuple(str(item) for item in pack.get("title_rules") or []),
        "outline": tuple(str(item) for item in pack.get("outline") or []),
        "forbidden_expressions": tuple(
            str(item) for item in pack.get("forbidden_expressions") or []
        ),
        "fact_check_items": tuple(
            str(item) for item in pack.get("fact_check_items") or []
        ),
    }


def compare_content_packs(
    con: duckdb.DuckDBPyConnection,
    *,
    older_pack_id: str,
    newer_pack_id: str,
    max_diff_lines: int = 500,
) -> ContentPackComparison:
    older = get_content_pack_snapshot(con, older_pack_id)
    newer = get_content_pack_snapshot(con, newer_pack_id)
    if older is None or newer is None:
        raise ValueError("비교할 자료팩을 찾을 수 없습니다.")
    if str(older.get("topic_id")) != str(newer.get("topic_id")):
        raise ValueError("같은 주제의 자료팩만 비교할 수 있습니다.")

    older_settings = _comparable_settings(older)
    newer_settings = _comparable_settings(newer)
    changed_fields = tuple(
        SETTING_FIELD_LABELS[field]
        for field in SETTING_FIELD_LABELS
        if older_settings[field] != newer_settings[field]
    )

    older_references = {
        _reference_key(reference): reference
        for reference in older.get("references") or []
    }
    newer_references = {
        _reference_key(reference): reference
        for reference in newer.get("references") or []
    }
    added_references = tuple(
        _reference_label(newer_references[key])
        for key in sorted(newer_references.keys() - older_references.keys())
    )
    removed_references = tuple(
        _reference_label(older_references[key])
        for key in sorted(older_references.keys() - newer_references.keys())
    )

    older_prompt = str(older.get("prompt_text") or "")
    newer_prompt = str(newer.get("prompt_text") or "")
    raw_diff = list(
        difflib.unified_diff(
            older_prompt.splitlines(),
            newer_prompt.splitlines(),
            fromfile=f"자료팩 v{int(older['version'])}",
            tofile=f"자료팩 v{int(newer['version'])}",
            lineterm="",
        )
    )
    added_lines = sum(
        1 for line in raw_diff if line.startswith("+") and not line.startswith("+++")
    )
    removed_lines = sum(
        1 for line in raw_diff if line.startswith("-") and not line.startswith("---")
    )
    bounded = max(40, min(int(max_diff_lines), 3000))
    truncated = len(raw_diff) > bounded
    shown = raw_diff[:bounded]
    if truncated:
        shown.append(f"... 차이 {len(raw_diff) - bounded:,}줄은 화면에서 생략했습니다.")

    return ContentPackComparison(
        older_pack_id=str(older["content_pack_id"]),
        newer_pack_id=str(newer["content_pack_id"]),
        older_version=int(older["version"]),
        newer_version=int(newer["version"]),
        changed_fields=changed_fields,
        added_references=added_references,
        removed_references=removed_references,
        added_lines=added_lines,
        removed_lines=removed_lines,
        diff_text="\n".join(shown) if shown else "두 자료팩의 AI 요청서가 같습니다.",
        diff_truncated=truncated,
    )


def build_content_pack_reuse_payload(
    con: duckdb.DuckDBPyConnection,
    content_pack_id: str,
) -> dict[str, Any]:
    pack = get_content_pack_snapshot(con, content_pack_id)
    if pack is None:
        raise ValueError("재사용할 자료팩을 찾을 수 없습니다.")
    topic_id = str(pack.get("topic_id") or "")
    topic_row = con.execute(
        "SELECT title FROM topics WHERE topic_id = ?",
        [topic_id],
    ).fetchone()
    if topic_row is None:
        raise ValueError("자료팩과 연결된 주제를 찾을 수 없습니다.")

    linked_source_ids = {
        str(row[0])
        for row in con.execute(
            "SELECT source_item_id FROM topic_source_links WHERE topic_id = ?",
            [topic_id],
        ).fetchall()
    }
    active_reference_ids = {
        str(row[0])
        for row in con.execute(
            """
            SELECT reference_id
            FROM topic_references
            WHERE topic_id = ? AND archived_at IS NULL
            """,
            [topic_id],
        ).fetchall()
    }

    requested_source_ids: list[str] = []
    requested_reference_ids: list[str] = []
    for reference in pack.get("references") or []:
        if reference.get("reference_kind") == "trend_signal":
            value = str(reference.get("source_item_id") or "").strip()
            if value and value not in requested_source_ids:
                requested_source_ids.append(value)
        elif reference.get("reference_kind") == "factual_reference":
            value = str(reference.get("topic_reference_id") or "").strip()
            if value and value not in requested_reference_ids:
                requested_reference_ids.append(value)

    selected_source_ids = [
        value for value in requested_source_ids if value in linked_source_ids
    ]
    selected_reference_ids = [
        value for value in requested_reference_ids if value in active_reference_ids
    ]
    missing_source_ids = [
        value for value in requested_source_ids if value not in linked_source_ids
    ]
    missing_reference_ids = [
        value for value in requested_reference_ids if value not in active_reference_ids
    ]

    return {
        "content_pack_id": str(pack["content_pack_id"]),
        "topic_id": topic_id,
        "topic_title": str(topic_row[0] or ""),
        "version": int(pack["version"]),
        "defaults": {
            "source": "reused",
            "source_cluster_id": "",
            "audience": str(pack.get("audience") or ""),
            "purpose": str(pack.get("purpose") or ""),
            "angle": str(pack.get("angle") or ""),
            "category": str(pack.get("category") or ""),
            "target_length": int(pack.get("target_length") or 2500),
            "title_rules": list(pack.get("title_rules") or []),
            "outline": list(pack.get("outline") or []),
            "forbidden_expressions": list(
                pack.get("forbidden_expressions") or []
            ),
            "fact_check_items": list(pack.get("fact_check_items") or []),
            "timeliness": {},
            "evidence_plan": {},
            "primary_direction_reason": "",
        },
        "selected_source_item_ids": selected_source_ids,
        "selected_reference_ids": selected_reference_ids,
        "missing_source_item_ids": missing_source_ids,
        "missing_reference_ids": missing_reference_ids,
        "evidence_applied": False,
    }
