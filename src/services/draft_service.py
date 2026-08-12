from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import duckdb

try:
    import markdown as markdown_lib
except ImportError:  # pragma: no cover
    markdown_lib = None

from src.services.ai_result_parser import ParseResult
from src.services.content_pack_service import get_content_pack

FACT_CHECK_STATUS_OPTIONS = ["needs_verification", "verified", "needs_revision"]
FACT_CHECK_STATUS_LABELS = {
    "needs_verification": "미확인",
    "verified": "확인 완료",
    "needs_revision": "수정 필요",
}


def markdown_to_html(markdown_text: str) -> str:
    if markdown_lib is None:
        escaped = html.escape(markdown_text).replace("\n", "<br>\n")
        return f"<div>{escaped}</div>"
    return markdown_lib.markdown(
        markdown_text,
        extensions=["extra", "sane_lists", "tables", "fenced_code"],
    )


def _load_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _canonical_sources(pack: dict[str, Any], output_sources: list[Any]) -> list[dict[str, Any]]:
    references = _load_json_list(pack.get("references_json"))
    allowed = {
        str(item.get("id") or "").strip(): item
        for item in references
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    canonical: list[dict[str, Any]] = []
    for output in output_sources:
        if not isinstance(output, dict):
            continue
        source_id = str(output.get("id") or "").strip()
        reference = allowed.get(source_id)
        if reference is None:
            continue
        canonical.append(
            {
                "id": source_id,
                "title": reference.get("title") or "",
                "publisher": reference.get("publisher") or "",
                "url": reference.get("url") or "",
                "published_at": reference.get("published_at") or "",
            }
        )
    return canonical


def save_generation_and_draft(
    con: duckdb.DuckDBPyConnection,
    *,
    content_pack_id: str,
    ai_provider: str,
    raw_response: str,
    result: ParseResult,
) -> tuple[str, str]:
    pack = get_content_pack(con, content_pack_id)
    if pack is None:
        raise ValueError("자료팩을 찾을 수 없습니다.")
    if not result.is_valid or result.data is None:
        raise ValueError("형식 검사를 통과한 결과만 초안으로 저장할 수 있습니다.")

    existing = con.execute(
        """
        SELECT g.generation_id, d.draft_id
        FROM generation_sessions g
        JOIN drafts d ON d.generation_id = g.generation_id
        WHERE g.content_pack_id = ?
          AND g.ai_provider = ?
          AND g.raw_response = ?
        ORDER BY g.created_at DESC
        LIMIT 1
        """,
        [content_pack_id, ai_provider, raw_response],
    ).fetchone()
    if existing is not None:
        return str(existing[0]), str(existing[1])

    now = datetime.now()
    generation_id = f"gen_{uuid4().hex}"
    data = result.data
    con.execute(
        """
        INSERT INTO generation_sessions(
            generation_id, topic_id, content_pack_id, ai_provider, prompt_text,
            raw_response, parsed_json, parse_status, validation_errors_json,
            validation_warnings_json, schema_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'valid', ?, ?, ?, ?)
        """,
        [
            generation_id,
            pack["topic_id"],
            content_pack_id,
            ai_provider,
            pack["prompt_text"],
            raw_response,
            json.dumps(data, ensure_ascii=False),
            json.dumps(result.errors, ensure_ascii=False),
            json.dumps(result.warnings, ensure_ascii=False),
            data.get("schema_version"),
            now,
        ],
    )

    draft_id = f"draft_{uuid4().hex}"
    body_markdown = str(data.get("body_markdown") or "")
    canonical_sources = _canonical_sources(pack, data.get("sources") or [])
    con.execute(
        """
        INSERT INTO drafts(
            draft_id, topic_id, generation_id, title, summary, category,
            tags_json, body_markdown, body_html, sources_json,
            image_prompts_json, current_revision, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        [
            draft_id,
            pack["topic_id"],
            generation_id,
            str(data.get("title") or "").strip(),
            str(data.get("summary") or "").strip(),
            str(data.get("category") or "").strip(),
            json.dumps(data.get("tags") or [], ensure_ascii=False),
            body_markdown,
            markdown_to_html(body_markdown),
            json.dumps(canonical_sources, ensure_ascii=False),
            json.dumps(data.get("image_prompts") or [], ensure_ascii=False),
            now,
            now,
        ],
    )
    _insert_revision(
        con,
        draft_id=draft_id,
        revision_number=1,
        title=str(data.get("title") or "").strip(),
        summary=str(data.get("summary") or "").strip(),
        category=str(data.get("category") or "").strip(),
        tags=data.get("tags") or [],
        body_markdown=body_markdown,
        change_note="AI 결과에서 최초 초안 생성",
        created_at=now,
    )

    for item in data.get("fact_checks") or []:
        if not isinstance(item, dict):
            continue
        con.execute(
            """
            INSERT INTO fact_check_items(
                fact_check_id, draft_id, claim_text, check_status, reason,
                evidence, source_ids_json, source_url, checked_at
            ) VALUES (?, ?, ?, 'needs_verification', ?, '', ?, NULL, NULL)
            """,
            [
                f"fact_{uuid4().hex}",
                draft_id,
                str(item.get("claim") or "").strip(),
                str(item.get("reason") or "").strip(),
                json.dumps(item.get("source_ids") or [], ensure_ascii=False),
            ],
        )

    con.execute(
        "UPDATE topics SET status = 'draft_complete', updated_at = ? WHERE topic_id = ?",
        [now, pack["topic_id"]],
    )
    return generation_id, draft_id


def _insert_revision(
    con: duckdb.DuckDBPyConnection,
    *,
    draft_id: str,
    revision_number: int,
    title: str,
    summary: str,
    category: str,
    tags: list[str],
    body_markdown: str,
    change_note: str,
    created_at: datetime,
) -> None:
    con.execute(
        """
        INSERT INTO draft_revisions(
            revision_id, draft_id, revision_number, title, summary, category,
            tags_json, body_markdown, change_note, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            f"rev_{uuid4().hex}",
            draft_id,
            revision_number,
            title,
            summary,
            category,
            json.dumps(tags, ensure_ascii=False),
            body_markdown,
            change_note,
            created_at,
        ],
    )


def list_drafts(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT d.draft_id, d.topic_id, d.title, d.category, d.current_revision,
               d.updated_at, t.status, t.title AS topic_title
        FROM drafts d
        JOIN topics t ON t.topic_id = d.topic_id
        ORDER BY d.updated_at DESC
        """
    ).fetchall()
    columns = [item[0] for item in con.description]
    return [dict(zip(columns, row)) for row in rows]


def get_draft(con: duckdb.DuckDBPyConnection, draft_id: str) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM drafts WHERE draft_id = ?", [draft_id]).fetchone()
    if row is None:
        return None
    columns = [item[0] for item in con.description]
    draft = dict(zip(columns, row))
    for key in ["tags_json", "sources_json", "image_prompts_json"]:
        draft[key.removesuffix("_json")] = _load_json_list(draft.get(key))
    return draft


def update_draft(
    con: duckdb.DuckDBPyConnection,
    *,
    draft_id: str,
    title: str,
    summary: str,
    category: str,
    tags: list[str],
    body_markdown: str,
    create_revision: bool,
    change_note: str,
    topic_status: str = "editing",
) -> int:
    draft = get_draft(con, draft_id)
    if draft is None:
        raise ValueError("초안을 찾을 수 없습니다.")
    clean_title = title.strip()
    clean_body = body_markdown.strip()
    if not clean_title or not clean_body:
        raise ValueError("제목과 본문은 비워둘 수 없습니다.")

    revision = int(draft["current_revision"])
    now = datetime.now()
    if create_revision:
        revision += 1
        _insert_revision(
            con,
            draft_id=draft_id,
            revision_number=revision,
            title=clean_title,
            summary=summary.strip(),
            category=category.strip(),
            tags=tags,
            body_markdown=clean_body,
            change_note=change_note.strip() or "사용자 수정",
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
            clean_title,
            summary.strip(),
            category.strip(),
            json.dumps(tags, ensure_ascii=False),
            clean_body,
            markdown_to_html(clean_body),
            revision,
            now,
            draft_id,
        ],
    )
    con.execute(
        "UPDATE topics SET status = ?, updated_at = ? WHERE topic_id = ?",
        [topic_status, now, draft["topic_id"]],
    )
    return revision


def get_fact_checks(con: duckdb.DuckDBPyConnection, draft_id: str) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT fact_check_id, claim_text, check_status, reason, evidence,
               source_ids_json, source_url, checked_at
        FROM fact_check_items
        WHERE draft_id = ?
        ORDER BY checked_at NULLS FIRST, claim_text
        """,
        [draft_id],
    ).fetchall()
    columns = [item[0] for item in con.description]
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(zip(columns, row))
        item["source_ids"] = [str(value) for value in _load_json_list(item.get("source_ids_json"))]
        item["status_label"] = FACT_CHECK_STATUS_LABELS.get(
            str(item.get("check_status") or ""),
            str(item.get("check_status") or "미확인"),
        )
        result.append(item)
    return result


def get_fact_check_summary(con: duckdb.DuckDBPyConnection, draft_id: str) -> dict[str, int]:
    rows = con.execute(
        """
        SELECT check_status, COUNT(*)
        FROM fact_check_items
        WHERE draft_id = ?
        GROUP BY check_status
        """,
        [draft_id],
    ).fetchall()
    counts = {str(status): int(count) for status, count in rows}
    total = sum(counts.values())
    verified = counts.get("verified", 0)
    return {
        "total": total,
        "verified": verified,
        "needs_verification": counts.get("needs_verification", 0),
        "needs_revision": counts.get("needs_revision", 0),
        "unresolved": total - verified,
    }


def _valid_optional_url(value: str) -> bool:
    if not value:
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def update_fact_check(
    con: duckdb.DuckDBPyConnection,
    *,
    fact_check_id: str,
    check_status: str,
    evidence: str,
    source_url: str,
) -> None:
    if check_status not in FACT_CHECK_STATUS_OPTIONS:
        raise ValueError("지원하지 않는 사실 확인 상태입니다.")
    clean_evidence = evidence.strip()
    clean_url = source_url.strip()
    if not _valid_optional_url(clean_url):
        raise ValueError("근거 URL은 http 또는 https 주소여야 합니다.")
    if check_status == "verified" and not (clean_evidence or clean_url):
        raise ValueError("확인 완료로 저장하려면 확인 메모나 근거 URL을 입력하세요.")

    row = con.execute(
        """
        SELECT f.draft_id, d.topic_id
        FROM fact_check_items f
        JOIN drafts d ON d.draft_id = f.draft_id
        WHERE f.fact_check_id = ?
        """,
        [fact_check_id],
    ).fetchone()
    if row is None:
        raise ValueError("사실 확인 항목을 찾을 수 없습니다.")

    draft_id, topic_id = row
    checked_at = None if check_status == "needs_verification" else datetime.now()
    con.execute(
        """
        UPDATE fact_check_items
        SET check_status = ?, evidence = ?, source_url = ?, checked_at = ?
        WHERE fact_check_id = ?
        """,
        [check_status, clean_evidence, clean_url or None, checked_at, fact_check_id],
    )

    summary = get_fact_check_summary(con, draft_id)
    current_status_row = con.execute(
        "SELECT status FROM topics WHERE topic_id = ?",
        [topic_id],
    ).fetchone()
    current_status = str(current_status_row[0]) if current_status_row else ""
    if current_status != "published":
        next_status = "publish_ready" if summary["total"] > 0 and summary["unresolved"] == 0 else "editing"
        con.execute(
            "UPDATE topics SET status = ?, updated_at = ? WHERE topic_id = ?",
            [next_status, datetime.now(), topic_id],
        )


def build_full_copy_text(draft: dict[str, Any]) -> str:
    tags = " ".join(f"#{tag.lstrip('#')}" for tag in draft.get("tags", []))
    parts = [draft.get("title", ""), "", draft.get("body_markdown", "")]
    if tags:
        parts.extend(["", tags])
    return "\n".join(parts).strip()
