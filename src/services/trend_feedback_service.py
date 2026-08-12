from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any
from uuid import uuid4

import duckdb

from src.services.trend_normalization import identity_tokens, normalize_title, normalize_url

FEEDBACK_LABELS = {
    "good": "좋은 글감",
    "ambiguous": "애매한 글감",
    "useless": "쓸모없는 글감",
    "false_merge": "잘못 묶인 주제",
}
FEEDBACK_TYPES = tuple(FEEDBACK_LABELS)
REJECTED_FEEDBACK_TYPES = {"useless", "false_merge"}
_DIAGNOSTIC_GENERIC_TERMS = {
    "기능", "공개", "출시", "발표", "변경", "정리", "방법", "후기", "비교",
    "일정", "정보", "내용", "관련", "문제", "오류", "해결", "사용", "업데이트",
    "정답", "뉴스", "안내", "update", "release", "review", "comparison", "guide",
}


def _item_title(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    return str(metadata.get("item_title") or item.get("raw_title") or "").strip()


def _item_evidence_key(item: dict[str, Any]) -> str:
    normalized = str(item.get("normalized_url") or "").strip()
    if not normalized:
        normalized = normalize_url(str(item.get("source_url") or ""))
    if normalized:
        return f"url:{normalized}"
    source_item_id = str(item.get("source_item_id") or "").strip()
    if source_item_id:
        return f"id:{source_item_id}"
    return "title:" + normalize_title(_item_title(item))


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value in {None, ""}:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def build_cluster_diagnostics(
    cluster: dict[str, Any],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """현재 군집이 왜 글감으로 보이는지 화면용 근거를 결정론적으로 요약합니다."""
    unique_items: dict[str, dict[str, Any]] = {}
    for item in items:
        unique_items.setdefault(_item_evidence_key(item), item)

    unique_evidence = list(unique_items.values())
    source_types = {
        str(item.get("source_type") or "").strip()
        for item in unique_evidence
        if str(item.get("source_type") or "").strip()
    }
    publishers = {
        str(item.get("source_name") or "").strip().casefold()
        for item in unique_evidence
        if str(item.get("source_name") or "").strip()
    }
    normalized_titles = {
        normalize_title(_item_title(item))
        for item in unique_evidence
        if normalize_title(_item_title(item))
    }

    token_support: Counter[str] = Counter()
    for item in unique_evidence:
        token_support.update(identity_tokens(_item_title(item)))
    repeated_terms = [
        term
        for term, count in sorted(
            token_support.items(),
            key=lambda pair: (-pair[1], -len(pair[0]), pair[0]),
        )
        if count >= 2 and term not in _DIAGNOSTIC_GENERIC_TERMS
    ][:6]

    timestamps = [
        parsed
        for item in unique_evidence
        for parsed in [
            _coerce_datetime(item.get("published_at"))
            or _coerce_datetime(item.get("observed_at"))
        ]
        if parsed is not None
    ]
    oldest_at = min(timestamps) if timestamps else None
    latest_at = max(timestamps) if timestamps else None

    unique_count = len(unique_evidence)
    duplicate_count = max(0, len(items) - unique_count)
    publisher_count = len(publishers)
    source_type_count = len(source_types)

    if repeated_terms and unique_count >= 2:
        binding_reason = (
            f"중복을 제외한 원문 {unique_count}건에서 "
            f"{', '.join(repeated_terms[:3])} 표현이 반복되어 같은 글감으로 묶였습니다."
        )
    elif duplicate_count > 0 and unique_count >= 1:
        binding_reason = (
            f"동일 URL·복제 원문 {duplicate_count}건을 제외하고 실제 근거 {unique_count}건을 남겼습니다."
        )
    elif unique_count == 1:
        binding_reason = "독립 원문이 1건뿐이라 제목과 원문을 직접 확인해야 합니다."
    else:
        binding_reason = "표시할 수 있는 독립 원문 근거가 없습니다."

    warnings: list[str] = []
    if unique_count <= 1:
        warnings.append("독립 원문이 1건 이하입니다.")
    if publisher_count <= 1 and unique_count >= 2:
        warnings.append("여러 문서가 있어도 발행처가 한 곳에 치우쳐 있습니다.")
    if not repeated_terms and unique_count >= 2:
        warnings.append("여러 원문에서 반복되는 구체 대상이 뚜렷하지 않습니다.")
    if duplicate_count >= max(2, unique_count):
        warnings.append("중복·복제 원문의 비중이 높습니다.")

    return {
        "cluster_id": str(cluster.get("cluster_id") or ""),
        "canonical_title": str(cluster.get("canonical_title") or ""),
        "raw_item_count": len(items),
        "unique_evidence_count": unique_count,
        "duplicate_count": duplicate_count,
        "unique_title_count": len(normalized_titles),
        "source_type_count": source_type_count,
        "publisher_count": publisher_count,
        "repeated_terms": repeated_terms,
        "oldest_at": oldest_at,
        "latest_at": latest_at,
        "binding_reason": binding_reason,
        "warnings": warnings,
    }


def save_trend_feedback(
    con: duckdb.DuckDBPyConnection,
    *,
    cluster_id: str,
    canonical_title: str,
    feedback_type: str,
    note: str = "",
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    feedback_type = str(feedback_type or "").strip()
    if feedback_type not in FEEDBACK_LABELS:
        raise ValueError("지원하지 않는 글감 평가입니다.")
    cluster_id = str(cluster_id or "").strip()
    if not cluster_id:
        raise ValueError("평가할 글감 ID가 없습니다.")

    diagnostics = diagnostics or {}
    now = datetime.now()
    existing = con.execute(
        "SELECT feedback_id, created_at FROM trend_feedback WHERE cluster_id = ?",
        [cluster_id],
    ).fetchone()
    feedback_id = str(existing[0]) if existing else "feedback_" + uuid4().hex
    created_at = existing[1] if existing else now
    con.execute(
        """
        INSERT INTO trend_feedback(
            feedback_id, cluster_id, canonical_title, feedback_type, note,
            item_count, unique_evidence_count, source_type_count, publisher_count,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cluster_id) DO UPDATE SET
            canonical_title = EXCLUDED.canonical_title,
            feedback_type = EXCLUDED.feedback_type,
            note = EXCLUDED.note,
            item_count = EXCLUDED.item_count,
            unique_evidence_count = EXCLUDED.unique_evidence_count,
            source_type_count = EXCLUDED.source_type_count,
            publisher_count = EXCLUDED.publisher_count,
            updated_at = EXCLUDED.updated_at
        """,
        [
            feedback_id,
            cluster_id,
            str(canonical_title or "").strip(),
            feedback_type,
            str(note or "").strip(),
            int(diagnostics.get("raw_item_count") or 0),
            int(diagnostics.get("unique_evidence_count") or 0),
            int(diagnostics.get("source_type_count") or 0),
            int(diagnostics.get("publisher_count") or 0),
            created_at,
            now,
        ],
    )
    return get_trend_feedback(con, cluster_id) or {}


def clear_trend_feedback(con: duckdb.DuckDBPyConnection, cluster_id: str) -> bool:
    before = int(
        con.execute(
            "SELECT COUNT(*) FROM trend_feedback WHERE cluster_id = ?",
            [str(cluster_id or "")],
        ).fetchone()[0]
        or 0
    )
    con.execute(
        "DELETE FROM trend_feedback WHERE cluster_id = ?",
        [str(cluster_id or "")],
    )
    return before > 0


def get_trend_feedback(
    con: duckdb.DuckDBPyConnection,
    cluster_id: str,
) -> dict[str, Any] | None:
    row = con.execute(
        """
        SELECT feedback_id, cluster_id, canonical_title, feedback_type, note,
               item_count, unique_evidence_count, source_type_count, publisher_count,
               created_at, updated_at
        FROM trend_feedback
        WHERE cluster_id = ?
        """,
        [str(cluster_id or "")],
    ).fetchone()
    if row is None:
        return None
    columns = [column[0] for column in con.description]
    return dict(zip(columns, row))


def list_trend_feedback_map(
    con: duckdb.DuckDBPyConnection,
    cluster_ids: list[str],
) -> dict[str, dict[str, Any]]:
    normalized_ids = list(dict.fromkeys(str(value) for value in cluster_ids if str(value)))
    if not normalized_ids:
        return {}
    placeholders = ", ".join("?" for _ in normalized_ids)
    rows = con.execute(
        f"""
        SELECT cluster_id, feedback_type, note, updated_at
        FROM trend_feedback
        WHERE cluster_id IN ({placeholders})
        """,
        normalized_ids,
    ).fetchall()
    return {
        str(cluster_id): {
            "feedback_type": str(feedback_type),
            "note": str(note or ""),
            "updated_at": updated_at,
        }
        for cluster_id, feedback_type, note, updated_at in rows
    }


def get_trend_feedback_summary(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    summary = {feedback_type: 0 for feedback_type in FEEDBACK_TYPES}
    rows = con.execute(
        "SELECT feedback_type, COUNT(*) FROM trend_feedback GROUP BY feedback_type"
    ).fetchall()
    for feedback_type, count in rows:
        if str(feedback_type) in summary:
            summary[str(feedback_type)] = int(count or 0)
    summary["total"] = sum(summary.values())
    return summary
