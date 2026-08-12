"""사용자 평가와 평가 당시 근거 품질로 글감 기회 점수 보정안을 미리 계산합니다.

이 모듈은 읽기 전용입니다. 실제 ``trend_clusters`` 점수나 추천 상태를 변경하지 않습니다.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

import duckdb

from src.services.trend_feedback_service import FEEDBACK_LABELS, FEEDBACK_TYPES

MIN_TOTAL_FEEDBACK = 20
MIN_FEEDBACK_TYPE_COUNT = 3
MAX_ABSOLUTE_ADJUSTMENT = 8.0

_FEEDBACK_BASE_ADJUSTMENTS = {
    "good": 4.0,
    "ambiguous": -1.0,
    "useless": -5.0,
    "false_merge": -7.0,
}


def _bounded_float(value: object, *, minimum: float = 0.0, maximum: float = 100.0) -> float:
    try:
        parsed = float(value or 0.0)
    except (TypeError, ValueError, OverflowError):
        parsed = 0.0
    return max(minimum, min(maximum, parsed))


def _bounded_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _cursor_rows(cursor) -> list[dict[str, Any]]:
    columns = [str(column[0]) for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _evidence_adjustment(row: dict[str, Any]) -> tuple[float, list[str]]:
    """평가 저장 당시 근거 스냅샷에 작은 범위의 가감점을 부여합니다."""
    item_count = _bounded_int(row.get("item_count"))
    unique_count = _bounded_int(row.get("unique_evidence_count"))
    source_type_count = _bounded_int(row.get("source_type_count"))
    publisher_count = _bounded_int(row.get("publisher_count"))

    adjustment = 0.0
    reasons: list[str] = []

    if unique_count >= 4:
        adjustment += 1.5
        reasons.append(f"독립 근거 {unique_count}건 +1.5")
    elif unique_count >= 3:
        adjustment += 1.0
        reasons.append(f"독립 근거 {unique_count}건 +1.0")
    elif unique_count <= 1:
        adjustment -= 1.5
        reasons.append(f"독립 근거 {unique_count}건 -1.5")

    if source_type_count >= 3:
        adjustment += 1.0
        reasons.append(f"출처 유형 {source_type_count}개 +1.0")
    elif source_type_count <= 1:
        adjustment -= 0.75
        reasons.append(f"출처 유형 {source_type_count}개 -0.75")

    if publisher_count >= 3:
        adjustment += 1.0
        reasons.append(f"발행처 {publisher_count}곳 +1.0")
    elif publisher_count <= 1:
        adjustment -= 1.5
        reasons.append(f"발행처 {publisher_count}곳 -1.5")

    if item_count >= 3:
        retention = unique_count / max(1, item_count)
        if retention >= 0.8:
            adjustment += 0.5
            reasons.append(f"근거 유지율 {retention * 100:.0f}% +0.5")
        elif retention <= 0.4:
            adjustment -= 1.0
            reasons.append(f"근거 유지율 {retention * 100:.0f}% -1.0")

    return adjustment, reasons


def _constrain_adjustment(feedback_type: str, value: float) -> float:
    """근거 품질 보정이 사용자 평가 방향을 뒤집지 않게 제한합니다."""
    if feedback_type == "good":
        value = max(0.0, value)
    elif feedback_type == "ambiguous":
        value = max(-3.0, min(1.0, value))
    elif feedback_type in {"useless", "false_merge"}:
        value = min(0.0, value)
    return round(
        max(-MAX_ABSOLUTE_ADJUSTMENT, min(MAX_ABSOLUTE_ADJUSTMENT, value)),
        1,
    )


def build_score_adjustment_preview(
    rows: Iterable[dict[str, Any]],
    *,
    total_feedback_count: int,
    feedback_type_counts: dict[str, int] | Counter[str],
) -> list[dict[str, Any]]:
    """현재 글감 기회 점수와 평가 스냅샷으로 읽기 전용 보정안을 만듭니다."""
    total_count = _bounded_int(total_feedback_count)
    normalized_type_counts = {
        feedback_type: _bounded_int(feedback_type_counts.get(feedback_type, 0))
        for feedback_type in FEEDBACK_TYPES
    }
    output: list[dict[str, Any]] = []

    for source_row in rows:
        row = dict(source_row)
        feedback_type = str(row.get("feedback_type") or "").strip()
        if feedback_type not in _FEEDBACK_BASE_ADJUSTMENTS:
            continue

        original_score = _bounded_float(row.get("opportunity_score"))
        base_adjustment = _FEEDBACK_BASE_ADJUSTMENTS[feedback_type]
        evidence_adjustment, evidence_reasons = _evidence_adjustment(row)
        suggested_adjustment = _constrain_adjustment(
            feedback_type,
            base_adjustment + evidence_adjustment,
        )

        type_count = normalized_type_counts.get(feedback_type, 0)
        total_sample_ready = total_count >= MIN_TOTAL_FEEDBACK
        type_sample_ready = type_count >= MIN_FEEDBACK_TYPE_COUNT
        is_eligible = total_sample_ready and type_sample_ready
        preview_score = (
            round(_bounded_float(original_score + suggested_adjustment), 1)
            if is_eligible
            else None
        )

        sample_reasons: list[str] = []
        if not total_sample_ready:
            sample_reasons.append(
                f"전체 평가 {MIN_TOTAL_FEEDBACK}건 필요(현재 {total_count}건)"
            )
        if not type_sample_ready:
            sample_reasons.append(
                f"같은 평가 {MIN_FEEDBACK_TYPE_COUNT}건 필요(현재 {type_count}건)"
            )

        direction = "유지"
        if suggested_adjustment > 0:
            direction = "가점"
        elif suggested_adjustment < 0:
            direction = "감점"

        reasons = [
            f"사용자 평가 ‘{FEEDBACK_LABELS[feedback_type]}’ {base_adjustment:+.1f}",
            *evidence_reasons,
        ]
        if abs(base_adjustment + evidence_adjustment) > MAX_ABSOLUTE_ADJUSTMENT:
            reasons.append(f"최대 조정 폭 ±{MAX_ABSOLUTE_ADJUSTMENT:.0f}점 적용")

        output.append(
            {
                **row,
                "feedback_label": FEEDBACK_LABELS[feedback_type],
                "original_opportunity_score": round(original_score, 1),
                "base_adjustment": base_adjustment,
                "evidence_adjustment": round(evidence_adjustment, 2),
                "suggested_adjustment": suggested_adjustment,
                "direction": direction,
                "preview_opportunity_score": preview_score,
                "is_eligible": is_eligible,
                "sample_status": "미리보기 가능" if is_eligible else "표본 부족",
                "sample_reason": " · ".join(sample_reasons),
                "feedback_type_sample_count": type_count,
                "adjustment_reasons": reasons,
            }
        )

    # 안정 정렬: 최신 평가 → 조정 폭 큰 순 → 숫자 미리보기 가능 항목 순.
    output.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    output.sort(
        key=lambda row: abs(float(row.get("suggested_adjustment") or 0.0)),
        reverse=True,
    )
    output.sort(key=lambda row: not bool(row.get("is_eligible")))
    return output


def get_score_adjustment_preview(
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """현재 순위에 남아 있는 평가 글감의 보정 미리보기를 조회합니다."""
    bounded_limit = max(1, min(int(limit), 200))
    count_rows = con.execute(
        "SELECT feedback_type, COUNT(*) FROM trend_feedback GROUP BY feedback_type"
    ).fetchall()
    type_counts = Counter(
        {
            str(feedback_type): int(count or 0)
            for feedback_type, count in count_rows
            if str(feedback_type) in FEEDBACK_TYPES
        }
    )
    total_feedback_count = sum(type_counts.values())
    current_feedback_count = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM trend_feedback f
            JOIN trend_clusters tc ON tc.cluster_id = f.cluster_id
            """
        ).fetchone()[0]
        or 0
    )

    rows = _cursor_rows(
        con.execute(
            """
            SELECT f.cluster_id, f.canonical_title, f.feedback_type, f.note,
                   f.item_count, f.unique_evidence_count, f.source_type_count,
                   f.publisher_count, f.updated_at,
                   tc.trend_score, tc.opportunity_score, tc.quality_score,
                   tc.fact_risk_score, tc.recommendation_status
            FROM trend_feedback f
            JOIN trend_clusters tc ON tc.cluster_id = f.cluster_id
            ORDER BY f.updated_at DESC, f.canonical_title
            LIMIT ?
            """,
            [bounded_limit],
        )
    )
    preview_rows = build_score_adjustment_preview(
        rows,
        total_feedback_count=total_feedback_count,
        feedback_type_counts=type_counts,
    )

    return {
        "total_feedback_count": total_feedback_count,
        "feedback_type_counts": {
            feedback_type: int(type_counts.get(feedback_type, 0))
            for feedback_type in FEEDBACK_TYPES
        },
        "current_feedback_count": current_feedback_count,
        "orphaned_feedback_count": max(0, total_feedback_count - current_feedback_count),
        "eligible_count": sum(1 for row in preview_rows if row["is_eligible"]),
        "minimum_total_feedback": MIN_TOTAL_FEEDBACK,
        "minimum_feedback_type_count": MIN_FEEDBACK_TYPE_COUNT,
        "maximum_adjustment": MAX_ABSOLUTE_ADJUSTMENT,
        "rows": preview_rows,
    }
