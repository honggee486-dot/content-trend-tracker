from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlsplit, urlunsplit
from uuid import uuid4

import duckdb

REFERENCE_TYPE_LABELS = {
    "official_agency": "공식 기관",
    "company_official": "기업 공식 발표",
    "public_data": "공공데이터",
    "news": "뉴스 기사",
    "user_reference": "사용자 직접 자료",
}
REFERENCE_TYPE_OPTIONS = list(REFERENCE_TYPE_LABELS)


def _normalize_url(value: str) -> str:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def _validate_reference(*, title: str, url: str, reference_type: str) -> tuple[str, str, str]:
    clean_title = str(title or "").strip()
    clean_url = str(url or "").strip()
    clean_type = str(reference_type or "").strip()

    if not clean_title:
        raise ValueError("참고 자료 제목을 입력하세요.")
    if clean_type not in REFERENCE_TYPE_LABELS:
        raise ValueError("지원하지 않는 참고 자료 유형입니다.")

    parsed = urlparse(clean_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("참고 자료 URL은 http 또는 https 주소로 입력하세요.")

    return clean_title, clean_url, clean_type


def add_topic_reference(
    con: duckdb.DuckDBPyConnection,
    *,
    topic_id: str,
    reference_type: str,
    title: str,
    publisher: str = "",
    url: str,
    published_at: str = "",
    memo: str = "",
) -> tuple[str, bool]:
    clean_title, clean_url, clean_type = _validate_reference(
        title=title,
        url=url,
        reference_type=reference_type,
    )
    topic_exists = con.execute(
        "SELECT 1 FROM topics WHERE topic_id = ? AND archived_at IS NULL",
        [topic_id],
    ).fetchone()
    if not topic_exists:
        raise ValueError("참고 자료를 연결할 주제를 찾을 수 없습니다.")

    normalized_url = _normalize_url(clean_url)
    existing = con.execute(
        """
        SELECT reference_id
        FROM topic_references
        WHERE topic_id = ? AND normalized_url = ?
        LIMIT 1
        """,
        [topic_id, normalized_url],
    ).fetchone()
    now = datetime.now()
    if existing:
        reference_id = str(existing[0])
        con.execute(
            """
            UPDATE topic_references
            SET reference_type = ?, title = ?, publisher = ?, url = ?,
                published_at = ?, memo = ?, updated_at = ?, archived_at = NULL
            WHERE reference_id = ?
            """,
            [
                clean_type,
                clean_title,
                str(publisher or "").strip(),
                clean_url,
                str(published_at or "").strip(),
                str(memo or "").strip(),
                now,
                reference_id,
            ],
        )
        return reference_id, False

    reference_id = f"ref_{uuid4().hex}"
    con.execute(
        """
        INSERT INTO topic_references(
            reference_id, topic_id, reference_type, title, publisher,
            url, normalized_url, published_at, memo, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            reference_id,
            topic_id,
            clean_type,
            clean_title,
            str(publisher or "").strip(),
            clean_url,
            normalized_url,
            str(published_at or "").strip(),
            str(memo or "").strip(),
            now,
            now,
        ],
    )
    return reference_id, True


def list_topic_references(
    con: duckdb.DuckDBPyConnection,
    topic_id: str,
    *,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    archived_clause = "" if include_archived else "AND archived_at IS NULL"
    rows = con.execute(
        f"""
        SELECT reference_id, topic_id, reference_type, title, publisher,
               url, published_at, memo, created_at, updated_at, archived_at
        FROM topic_references
        WHERE topic_id = ? {archived_clause}
        ORDER BY archived_at NULLS FIRST, updated_at DESC, created_at DESC
        """,
        [topic_id],
    ).fetchall()
    columns = [item[0] for item in con.description]
    result = []
    for row in rows:
        item = dict(zip(columns, row))
        item["reference_type_label"] = REFERENCE_TYPE_LABELS.get(
            str(item.get("reference_type") or ""),
            str(item.get("reference_type") or "참고 자료"),
        )
        result.append(item)
    return result


def update_topic_reference(
    con: duckdb.DuckDBPyConnection,
    *,
    reference_id: str,
    reference_type: str,
    title: str,
    publisher: str = "",
    url: str,
    published_at: str = "",
    memo: str = "",
) -> None:
    clean_title, clean_url, clean_type = _validate_reference(
        title=title,
        url=url,
        reference_type=reference_type,
    )
    current = con.execute(
        "SELECT topic_id FROM topic_references WHERE reference_id = ? AND archived_at IS NULL",
        [reference_id],
    ).fetchone()
    if not current:
        raise ValueError("수정할 참고 자료를 찾을 수 없습니다.")

    normalized_url = _normalize_url(clean_url)
    duplicate = con.execute(
        """
        SELECT 1 FROM topic_references
        WHERE topic_id = ? AND normalized_url = ? AND reference_id <> ?
          AND archived_at IS NULL
        """,
        [str(current[0]), normalized_url, reference_id],
    ).fetchone()
    if duplicate:
        raise ValueError("같은 주제에 동일한 URL의 참고 자료가 이미 있습니다.")

    con.execute(
        """
        UPDATE topic_references
        SET reference_type = ?, title = ?, publisher = ?, url = ?,
            normalized_url = ?, published_at = ?, memo = ?, updated_at = ?
        WHERE reference_id = ?
        """,
        [
            clean_type,
            clean_title,
            str(publisher or "").strip(),
            clean_url,
            normalized_url,
            str(published_at or "").strip(),
            str(memo or "").strip(),
            datetime.now(),
            reference_id,
        ],
    )


def archive_topic_reference(
    con: duckdb.DuckDBPyConnection,
    reference_id: str,
) -> None:
    result = con.execute(
        """
        UPDATE topic_references
        SET archived_at = ?, updated_at = ?
        WHERE reference_id = ? AND archived_at IS NULL
        RETURNING reference_id
        """,
        [datetime.now(), datetime.now(), reference_id],
    ).fetchone()
    if not result:
        raise ValueError("보관할 참고 자료를 찾을 수 없습니다.")
