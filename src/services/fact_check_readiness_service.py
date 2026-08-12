"""초안별 사실 확인 준비도를 기존 기록에서 읽기 전용으로 계산합니다."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterable

import duckdb

from src.services.publish_service import ensure_publish_record_management_schema

FACT_CHECK_READINESS_LABELS = {
    "needs_revision": "수정 필요",
    "needs_verification": "확인 대기",
    "recheck_due": "재확인 필요",
    "no_checks": "확인 항목 없음",
    "ready": "발행 준비",
    "published_attention": "발행 후 확인 필요",
    "published": "발행 완료",
}

FAST_MOVING_TERMS = (
    "환율",
    "주가",
    "시세",
    "가격",
    "날씨",
    "기온",
    "강수",
    "예보",
    "순위",
    "경기 결과",
    "경기 일정",
    "스코어",
    "승률",
    "라인업",
    "부상 명단",
    "재고 현황",
    "당첨 결과",
    "실시간",
    "오늘",
)
SLOW_MOVING_TERMS = (
    "현재 직책",
    "현직",
    "현행 정책",
    "현재 정책",
    "신청 마감",
    "접수 마감",
    "지원금",
    "금리",
    "요금",
    "최신",
    "현재",
)
FAST_RECHECK_HOURS = 24
SLOW_RECHECK_HOURS = 24 * 7
ABANDONED_DAYS = 7

_STATE_PRIORITY = {
    "needs_revision": 0,
    "needs_verification": 1,
    "recheck_due": 2,
    "published_attention": 3,
    "no_checks": 4,
    "ready": 5,
    "published": 6,
}


def _cursor_rows(cursor) -> list[dict[str, Any]]:
    columns = [str(column[0]) for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _matched_terms(text: str) -> tuple[list[str], int | None]:
    folded = str(text or "").casefold()
    fast = [term for term in FAST_MOVING_TERMS if term.casefold() in folded]
    slow = [term for term in SLOW_MOVING_TERMS if term.casefold() in folded]
    matched = list(dict.fromkeys([*fast, *slow]))
    if fast:
        return matched, FAST_RECHECK_HOURS
    if slow:
        return matched, SLOW_RECHECK_HOURS
    return [], None


def _is_recheck_due(
    *,
    checked_at: object,
    recheck_hours: int | None,
    now: datetime,
) -> bool:
    if recheck_hours is None:
        return False
    if not isinstance(checked_at, datetime):
        return True
    return checked_at < now - timedelta(hours=recheck_hours)


def build_fact_check_readiness(
    draft_rows: Iterable[dict[str, Any]],
    fact_check_rows: Iterable[dict[str, Any]],
    publish_rows: Iterable[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """초안·사실 확인·발행 행을 준비도 표와 요약 수치로 변환합니다."""
    current = now or datetime.now()
    checks_by_draft: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in fact_check_rows:
        draft_id = str(row.get("draft_id") or "").strip()
        if draft_id:
            checks_by_draft[draft_id].append(dict(row))

    publishes_by_draft: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in publish_rows:
        draft_id = str(row.get("draft_id") or "").strip()
        if draft_id:
            publishes_by_draft[draft_id].append(dict(row))

    output_rows: list[dict[str, Any]] = []
    for raw_draft in draft_rows:
        draft = dict(raw_draft)
        draft_id = str(draft.get("draft_id") or "").strip()
        checks = checks_by_draft.get(draft_id, [])
        publishes = publishes_by_draft.get(draft_id, [])

        status_counts = defaultdict(int)
        verified_without_url_count = 0
        verified_without_evidence_count = 0
        time_sensitive_count = 0
        recheck_due_count = 0
        matched_terms: list[str] = []

        draft_text = " ".join(
            str(draft.get(key) or "")
            for key in ("title", "summary", "body_markdown")
        )
        draft_terms, _draft_recheck_hours = _matched_terms(draft_text)

        for check in checks:
            check_status = str(check.get("check_status") or "needs_verification")
            status_counts[check_status] += 1
            if check_status == "verified":
                if not str(check.get("source_url") or "").strip():
                    verified_without_url_count += 1
                if not (
                    str(check.get("source_url") or "").strip()
                    or str(check.get("evidence") or "").strip()
                ):
                    verified_without_evidence_count += 1

            check_text = " ".join(
                str(check.get(key) or "")
                for key in ("claim_text", "reason")
            )
            check_terms, recheck_hours = _matched_terms(check_text)
            if check_terms:
                time_sensitive_count += 1
                matched_terms.extend(check_terms)
                if check_status == "verified" and _is_recheck_due(
                    checked_at=check.get("checked_at"),
                    recheck_hours=recheck_hours,
                    now=current,
                ):
                    recheck_due_count += 1

        total_count = len(checks)
        verified_count = int(status_counts["verified"])
        needs_verification_count = int(status_counts["needs_verification"])
        needs_revision_count = int(status_counts["needs_revision"])
        unknown_status_count = max(
            0,
            total_count
            - verified_count
            - needs_verification_count
            - needs_revision_count,
        )
        unresolved_count = total_count - verified_count
        has_time_sensitive_gap = total_count == 0 and bool(draft_terms)

        published_count = len(publishes)
        last_published_at = max(
            (
                row.get("published_at") or row.get("created_at")
                for row in publishes
                if isinstance(row.get("published_at") or row.get("created_at"), datetime)
            ),
            default=None,
        )

        if published_count and (unresolved_count > 0 or recheck_due_count > 0):
            readiness_state = "published_attention"
            next_action = "발행된 글의 미확인·재확인 항목을 검토"
        elif published_count:
            readiness_state = "published"
            next_action = "발행 기록 확인"
        elif needs_revision_count > 0:
            readiness_state = "needs_revision"
            next_action = "본문과 주장 수정 후 다시 확인"
        elif needs_verification_count > 0 or unknown_status_count > 0:
            readiness_state = "needs_verification"
            next_action = "미확인 주장과 근거를 확인"
        elif recheck_due_count > 0:
            readiness_state = "recheck_due"
            next_action = "최신 공식 자료로 시점 의존 정보를 재확인"
        elif total_count == 0:
            readiness_state = "no_checks"
            next_action = (
                "시점 의존 표현에 대한 확인 항목을 직접 추가 검토"
                if has_time_sensitive_gap
                else "숫자·정책·가격 등 확인할 주장이 없는지 수동 검토"
            )
        else:
            readiness_state = "ready"
            next_action = "발행 보조에서 최종 검토"

        updated_at = draft.get("updated_at")
        unresolved_age_days = None
        if unresolved_count > 0 and isinstance(updated_at, datetime):
            unresolved_age_days = max(0, (current - updated_at).days)
        is_abandoned = bool(
            unresolved_count > 0
            and unresolved_age_days is not None
            and unresolved_age_days >= ABANDONED_DAYS
        )

        output_rows.append(
            {
                **draft,
                "readiness_state": readiness_state,
                "readiness_label": FACT_CHECK_READINESS_LABELS[readiness_state],
                "next_action": next_action,
                "fact_check_count": total_count,
                "verified_count": verified_count,
                "needs_verification_count": needs_verification_count,
                "needs_revision_count": needs_revision_count,
                "unknown_status_count": unknown_status_count,
                "unresolved_count": unresolved_count,
                "verified_without_url_count": verified_without_url_count,
                "verified_without_evidence_count": verified_without_evidence_count,
                "time_sensitive_count": time_sensitive_count,
                "recheck_due_count": recheck_due_count,
                "matched_time_sensitive_terms": list(dict.fromkeys(matched_terms)),
                "draft_time_sensitive_terms": draft_terms,
                "has_time_sensitive_gap": has_time_sensitive_gap,
                "publish_count": published_count,
                "last_published_at": last_published_at,
                "unresolved_age_days": unresolved_age_days,
                "is_abandoned": is_abandoned,
            }
        )

    output_rows.sort(
        key=lambda row: (
            _STATE_PRIORITY.get(str(row.get("readiness_state") or ""), 99),
            not bool(row.get("is_abandoned")),
            -(row.get("updated_at").timestamp() if isinstance(row.get("updated_at"), datetime) else 0),
        )
    )

    return {
        "draft_count": len(output_rows),
        "needs_revision_count": sum(
            1 for row in output_rows if row["readiness_state"] == "needs_revision"
        ),
        "needs_verification_count": sum(
            1 for row in output_rows if row["readiness_state"] == "needs_verification"
        ),
        "recheck_due_draft_count": sum(
            1 for row in output_rows if row["readiness_state"] == "recheck_due"
        ),
        "ready_count": sum(1 for row in output_rows if row["readiness_state"] == "ready"),
        "no_checks_count": sum(
            1 for row in output_rows if row["readiness_state"] == "no_checks"
        ),
        "published_attention_count": sum(
            1 for row in output_rows if row["readiness_state"] == "published_attention"
        ),
        "abandoned_count": sum(1 for row in output_rows if row["is_abandoned"]),
        "verified_without_url_count": sum(
            int(row["verified_without_url_count"]) for row in output_rows
        ),
        "time_sensitive_gap_count": sum(
            1 for row in output_rows if row["has_time_sensitive_gap"]
        ),
        "rows": output_rows,
        "fast_recheck_hours": FAST_RECHECK_HOURS,
        "slow_recheck_hours": SLOW_RECHECK_HOURS,
        "abandoned_days": ABANDONED_DAYS,
    }


def get_fact_check_readiness(
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int = 200,
    now: datetime | None = None,
) -> dict[str, Any]:
    """기존 초안·사실 확인·발행 기록만 조회해 준비도를 반환합니다."""
    ensure_publish_record_management_schema(con)
    bounded_limit = max(1, min(int(limit), 500))
    draft_rows = _cursor_rows(
        con.execute(
            """
            SELECT d.draft_id, d.topic_id, d.title, d.summary, d.body_markdown,
                   d.current_revision, d.updated_at, d.created_at,
                   t.title AS topic_title, t.status AS topic_status
            FROM drafts d
            JOIN topics t ON t.topic_id = d.topic_id
            ORDER BY d.updated_at DESC
            LIMIT ?
            """,
            [bounded_limit],
        )
    )
    draft_ids = [str(row["draft_id"]) for row in draft_rows]
    if not draft_ids:
        return build_fact_check_readiness([], [], [], now=now)

    placeholders = ", ".join("?" for _ in draft_ids)
    fact_check_rows = _cursor_rows(
        con.execute(
            f"""
            SELECT draft_id, fact_check_id, claim_text, check_status, reason,
                   evidence, source_url, checked_at
            FROM fact_check_items
            WHERE draft_id IN ({placeholders})
            """,
            draft_ids,
        )
    )
    publish_rows = _cursor_rows(
        con.execute(
            f"""
            SELECT draft_id, publish_id, publish_status, created_at, published_at
            FROM publish_records
            WHERE draft_id IN ({placeholders})
              AND publish_status = 'published'
              AND archived_at IS NULL
            """,
            draft_ids,
        )
    )
    return build_fact_check_readiness(
        draft_rows,
        fact_check_rows,
        publish_rows,
        now=now,
    )
