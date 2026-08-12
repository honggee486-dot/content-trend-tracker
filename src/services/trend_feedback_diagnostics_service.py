"""사용자 글감 평가를 근거 품질·출처·검색어 기준으로 읽기 전용 집계합니다."""

from __future__ import annotations

from typing import Any

import duckdb

from src.services.trend_feedback_service import FEEDBACK_TYPES


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100, 1)


def _cursor_rows(cursor) -> list[dict[str, Any]]:
    columns = [str(column[0]) for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def get_trend_feedback_diagnostics(
    con: duckdb.DuckDBPyConnection,
    *,
    recent_limit: int = 100,
    query_limit: int = 50,
) -> dict[str, Any]:
    """현재 저장된 최신 군집 평가를 진단용으로 집계합니다.

    평가 당시 근거 개수는 ``trend_feedback``의 스냅샷을 사용합니다. 출처와 검색어
    관계는 현재 군집에 연결된 원문을 조회하므로 인과관계가 아니라 탐색 신호입니다.
    """
    bounded_recent_limit = max(1, min(int(recent_limit), 200))
    bounded_query_limit = max(1, min(int(query_limit), 200))

    summary_row = con.execute(
        """
        SELECT COUNT(*) AS total_count,
               COALESCE(SUM(CASE WHEN feedback_type = 'good' THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN feedback_type = 'ambiguous' THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN feedback_type = 'useless' THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN feedback_type = 'false_merge' THEN 1 ELSE 0 END), 0),
               AVG(item_count),
               AVG(unique_evidence_count),
               AVG(source_type_count),
               AVG(publisher_count),
               COALESCE(SUM(CASE WHEN unique_evidence_count <= 1 THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN publisher_count <= 1 THEN 1 ELSE 0 END), 0),
               MAX(updated_at)
        FROM trend_feedback
        """
    ).fetchone()

    total_count = int(summary_row[0] or 0)
    good_count = int(summary_row[1] or 0)
    ambiguous_count = int(summary_row[2] or 0)
    useless_count = int(summary_row[3] or 0)
    false_merge_count = int(summary_row[4] or 0)
    rejected_count = useless_count + false_merge_count

    type_rows = _cursor_rows(
        con.execute(
            """
            SELECT feedback_type,
                   COUNT(*) AS evaluated_count,
                   AVG(item_count) AS average_item_count,
                   AVG(unique_evidence_count) AS average_unique_evidence_count,
                   AVG(source_type_count) AS average_source_type_count,
                   AVG(publisher_count) AS average_publisher_count,
                   COALESCE(SUM(CASE WHEN unique_evidence_count <= 1 THEN 1 ELSE 0 END), 0)
                       AS low_evidence_count,
                   COALESCE(SUM(CASE WHEN publisher_count <= 1 THEN 1 ELSE 0 END), 0)
                       AS single_publisher_count
            FROM trend_feedback
            GROUP BY feedback_type
            ORDER BY CASE feedback_type
                WHEN 'good' THEN 1
                WHEN 'ambiguous' THEN 2
                WHEN 'useless' THEN 3
                WHEN 'false_merge' THEN 4
                ELSE 5
            END
            """
        )
    )
    for row in type_rows:
        evaluated_count = int(row.get("evaluated_count") or 0)
        row["low_evidence_rate_percent"] = _rate(
            int(row.get("low_evidence_count") or 0), evaluated_count
        )
        row["single_publisher_rate_percent"] = _rate(
            int(row.get("single_publisher_count") or 0), evaluated_count
        )

    source_type_rows = _cursor_rows(
        con.execute(
            """
            WITH linked_feedback AS (
                SELECT DISTINCT f.cluster_id, f.feedback_type, s.source_type
                FROM trend_feedback f
                JOIN trend_cluster_items ci ON ci.cluster_id = f.cluster_id
                JOIN source_items s ON s.source_item_id = ci.source_item_id
                WHERE COALESCE(TRIM(s.source_type), '') <> ''
            )
            SELECT source_type,
                   COUNT(*) AS evaluated_count,
                   COALESCE(SUM(CASE WHEN feedback_type = 'good' THEN 1 ELSE 0 END), 0)
                       AS good_count,
                   COALESCE(SUM(CASE WHEN feedback_type = 'ambiguous' THEN 1 ELSE 0 END), 0)
                       AS ambiguous_count,
                   COALESCE(SUM(CASE WHEN feedback_type = 'useless' THEN 1 ELSE 0 END), 0)
                       AS useless_count,
                   COALESCE(SUM(CASE WHEN feedback_type = 'false_merge' THEN 1 ELSE 0 END), 0)
                       AS false_merge_count
            FROM linked_feedback
            GROUP BY source_type
            ORDER BY evaluated_count DESC, good_count DESC, source_type
            """
        )
    )
    for row in source_type_rows:
        evaluated_count = int(row.get("evaluated_count") or 0)
        rejected = int(row.get("useless_count") or 0) + int(
            row.get("false_merge_count") or 0
        )
        row["good_rate_percent"] = _rate(
            int(row.get("good_count") or 0), evaluated_count
        )
        row["rejected_count"] = rejected
        row["rejected_rate_percent"] = _rate(rejected, evaluated_count)

    query_rows = _cursor_rows(
        con.execute(
            """
            WITH linked_feedback AS (
                SELECT DISTINCT f.cluster_id, f.feedback_type,
                       q.source_name, q.source_type, q.discovery_query
                FROM trend_feedback f
                JOIN trend_cluster_items ci ON ci.cluster_id = f.cluster_id
                JOIN collection_query_discoveries q
                  ON q.source_item_id = ci.source_item_id
                WHERE COALESCE(TRIM(q.discovery_query), '') <> ''
            )
            SELECT source_name, source_type, discovery_query,
                   COUNT(*) AS evaluated_count,
                   COALESCE(SUM(CASE WHEN feedback_type = 'good' THEN 1 ELSE 0 END), 0)
                       AS good_count,
                   COALESCE(SUM(CASE WHEN feedback_type = 'ambiguous' THEN 1 ELSE 0 END), 0)
                       AS ambiguous_count,
                   COALESCE(SUM(CASE WHEN feedback_type = 'useless' THEN 1 ELSE 0 END), 0)
                       AS useless_count,
                   COALESCE(SUM(CASE WHEN feedback_type = 'false_merge' THEN 1 ELSE 0 END), 0)
                       AS false_merge_count
            FROM linked_feedback
            GROUP BY source_name, source_type, discovery_query
            ORDER BY evaluated_count DESC, good_count DESC,
                     useless_count DESC, false_merge_count DESC,
                     source_name, source_type, discovery_query
            LIMIT ?
            """,
            [bounded_query_limit],
        )
    )
    for row in query_rows:
        evaluated_count = int(row.get("evaluated_count") or 0)
        rejected = int(row.get("useless_count") or 0) + int(
            row.get("false_merge_count") or 0
        )
        row["good_rate_percent"] = _rate(
            int(row.get("good_count") or 0), evaluated_count
        )
        row["rejected_count"] = rejected
        row["rejected_rate_percent"] = _rate(rejected, evaluated_count)

    recent_rows = _cursor_rows(
        con.execute(
            """
            SELECT cluster_id, canonical_title, feedback_type, note,
                   item_count, unique_evidence_count, source_type_count,
                   publisher_count, created_at, updated_at
            FROM trend_feedback
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ?
            """,
            [bounded_recent_limit],
        )
    )
    for row in recent_rows:
        item_count = int(row.get("item_count") or 0)
        unique_count = int(row.get("unique_evidence_count") or 0)
        row["evidence_retention_percent"] = _rate(unique_count, item_count)

    return {
        "total_count": total_count,
        "good_count": good_count,
        "ambiguous_count": ambiguous_count,
        "useless_count": useless_count,
        "false_merge_count": false_merge_count,
        "rejected_count": rejected_count,
        "good_rate_percent": _rate(good_count, total_count),
        "ambiguous_rate_percent": _rate(ambiguous_count, total_count),
        "rejected_rate_percent": _rate(rejected_count, total_count),
        "false_merge_rate_percent": _rate(false_merge_count, total_count),
        "average_item_count": float(summary_row[5] or 0.0),
        "average_unique_evidence_count": float(summary_row[6] or 0.0),
        "average_source_type_count": float(summary_row[7] or 0.0),
        "average_publisher_count": float(summary_row[8] or 0.0),
        "low_evidence_count": int(summary_row[9] or 0),
        "single_publisher_count": int(summary_row[10] or 0),
        "latest_updated_at": summary_row[11],
        "feedback_types": tuple(FEEDBACK_TYPES),
        "type_rows": type_rows,
        "source_type_rows": source_type_rows,
        "query_rows": query_rows,
        "recent_rows": recent_rows,
    }
