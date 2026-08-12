"""저장된 콘텐츠 제작 기록에서 다음 작업 한 건을 주제별로 계산합니다."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

import duckdb

from src.services.content_pack_service import assess_content_pack_readiness
from src.services.fact_check_readiness_service import get_fact_check_readiness

WORK_QUEUE_STAGE_LABELS = {
    "needs_research": "자료 보완",
    "request_ready": "AI 요청서 준비",
    "awaiting_ai_result": "AI 결과 대기",
    "draft_editing": "초안 편집",
    "fact_check": "사실 확인",
    "publish_ready": "발행 준비",
}

WORK_QUEUE_STAGE_ORDER = {
    "needs_research": 1,
    "request_ready": 2,
    "awaiting_ai_result": 3,
    "draft_editing": 4,
    "fact_check": 5,
    "publish_ready": 6,
}

ABANDONED_DAYS = 7
MAX_SOURCE_TOPICS = 500


def _cursor_rows(cursor) -> list[dict[str, Any]]:
    columns = [str(column[0]) for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _as_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _latest_datetime(*values: object) -> datetime | None:
    parsed = [value for value in values if isinstance(value, datetime)]
    return max(parsed) if parsed else None


def _queue_action(
    *,
    page: str,
    label: str,
    state_key: str | None = None,
    state_value: object = None,
) -> tuple[str, str, dict[str, object]]:
    state = {state_key: state_value} if state_key and state_value not in {None, ""} else {}
    return page, label, state


def _draft_queue_stage(
    readiness: Mapping[str, Any] | None,
    *,
    draft_id: str,
    draft_title: str,
) -> tuple[str, str, str, str, dict[str, object]] | None:
    page, action_label, action_state = _queue_action(
        page="글 편집",
        label="초안 편집하기",
        state_key="prefill_draft_id",
        state_value=draft_id,
    )
    if readiness is None:
        return (
            "draft_editing",
            "초안은 저장됐지만 사실 확인 준비도를 계산하지 못했습니다.",
            page,
            action_label,
            action_state,
        )

    state = str(readiness.get("readiness_state") or "")
    unresolved = int(readiness.get("unresolved_count") or 0)
    revisions = int(readiness.get("needs_revision_count") or 0)
    rechecks = int(readiness.get("recheck_due_count") or 0)

    if state == "published":
        return None
    if state == "needs_revision":
        return (
            "draft_editing",
            f"수정이 필요한 사실 확인 항목 {revisions:,}개가 남아 있습니다.",
            page,
            action_label,
            action_state,
        )
    if state in {"needs_verification", "recheck_due", "published_attention"}:
        page, action_label, action_state = _queue_action(
            page="글 편집",
            label="사실 확인하기",
            state_key="prefill_draft_id",
            state_value=draft_id,
        )
        if state == "recheck_due":
            reason = f"시점 의존 정보 {rechecks:,}개가 재확인 기준을 지났습니다."
        elif state == "published_attention":
            reason = f"이미 발행했지만 미확인·재확인 항목 {max(unresolved, rechecks):,}개가 남아 있습니다."
        else:
            reason = f"확인이 끝나지 않은 주장 {unresolved:,}개가 남아 있습니다."
        return "fact_check", reason, page, action_label, action_state
    if state == "no_checks":
        return (
            "draft_editing",
            "사실 확인 항목이 없어 숫자·정책·가격·일정을 수동 검토해야 합니다.",
            page,
            action_label,
            action_state,
        )
    if state == "ready":
        page, action_label, action_state = _queue_action(
            page="발행 보조",
            label="발행 준비하기",
            state_key="prefill_draft_id",
            state_value=draft_id,
        )
        return (
            "publish_ready",
            "등록된 사실 확인 항목을 모두 확인해 발행 보조로 이동할 수 있습니다.",
            page,
            action_label,
            action_state,
        )

    return (
        "draft_editing",
        f"초안 ‘{draft_title}’의 본문과 사실 확인 상태를 검토해야 합니다.",
        page,
        action_label,
        action_state,
    )


def build_content_work_queue(
    topic_rows: Iterable[Mapping[str, Any]],
    *,
    readiness_by_draft: Mapping[str, Mapping[str, Any]] | None = None,
    references_by_topic: Mapping[str, list[dict[str, Any]]] | None = None,
    now: datetime | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """주제별 최신 제작 단계 하나만 남겨 읽기 전용 작업 대기열을 만듭니다."""
    current = now or datetime.now()
    readiness_map = dict(readiness_by_draft or {})
    reference_map = dict(references_by_topic or {})
    queue_rows: list[dict[str, Any]] = []

    for source in topic_rows:
        row = dict(source)
        topic_id = str(row.get("topic_id") or "").strip()
        if not topic_id:
            continue

        topic_status = str(row.get("topic_status") or row.get("status") or "candidate")
        if topic_status == "on_hold":
            continue

        content_pack_id = str(row.get("content_pack_id") or "").strip()
        draft_id = str(row.get("draft_id") or "").strip()
        draft_pack_id = str(row.get("draft_content_pack_id") or "").strip()
        pack_created_at = _as_datetime(row.get("pack_created_at"))
        draft_updated_at = _as_datetime(row.get("draft_updated_at"))
        newer_pack_waiting = bool(
            content_pack_id
            and draft_id
            and content_pack_id != draft_pack_id
            and (
                draft_updated_at is None
                or (pack_created_at is not None and pack_created_at >= draft_updated_at)
            )
        )

        stage: str
        reason: str
        target_page: str
        action_label: str
        action_state: dict[str, object]

        if content_pack_id and (not draft_id or newer_pack_waiting):
            parse_status = str(row.get("generation_parse_status") or "").strip()
            version = int(row.get("pack_version") or 0)
            if parse_status and parse_status != "valid":
                reason = (
                    f"자료팩 v{version}의 최근 AI 결과 검사 상태가 ‘{parse_status}’입니다. "
                    "현재 결과를 다시 검사해야 합니다."
                )
            else:
                reason = f"자료팩 v{version}가 준비됐지만 이 자료팩으로 저장한 초안이 없습니다."
            stage = "awaiting_ai_result"
            target_page, action_label, action_state = _queue_action(
                page="AI 결과 가져오기",
                label="AI 결과 가져오기",
                state_key="prefill_content_pack_id",
                state_value=content_pack_id,
            )
        elif draft_id:
            draft_stage = _draft_queue_stage(
                readiness_map.get(draft_id),
                draft_id=draft_id,
                draft_title=str(row.get("draft_title") or row.get("topic_title") or "초안"),
            )
            if draft_stage is None:
                continue
            stage, reason, target_page, action_label, action_state = draft_stage
        else:
            references = reference_map.get(topic_id, [])
            readiness = assess_content_pack_readiness(
                {
                    "title": str(row.get("topic_title") or ""),
                    "summary": str(row.get("topic_summary") or ""),
                    "memo": str(row.get("topic_memo") or ""),
                },
                references,
            )
            source_count = int(row.get("source_count") or 0)
            if readiness["is_blocked"]:
                stage = "needs_research"
                reason = str(readiness["message"])
                target_page, action_label, action_state = _queue_action(
                    page="주제·트렌드",
                    label="사실 자료 보완하기",
                )
            elif topic_status == "researching" or (source_count <= 0 and not references):
                stage = "needs_research"
                reason = (
                    "연결된 트렌드 신호와 사실 참고 자료가 없습니다."
                    if source_count <= 0 and not references
                    else "자료 확인 상태인 주제입니다. 원문과 사실 참고 자료를 검토해야 합니다."
                )
                target_page, action_label, action_state = _queue_action(
                    page="주제·트렌드",
                    label="자료 보완하기",
                )
            else:
                stage = "request_ready"
                reason = (
                    f"트렌드 신호 {source_count:,}개와 사실 참고 자료 {len(references):,}개를 바탕으로 "
                    "AI 요청서를 만들 수 있습니다."
                )
                target_page, action_label, action_state = _queue_action(
                    page="AI 요청서",
                    label="AI 요청서 만들기",
                    state_key="prefill_topic_id",
                    state_value=topic_id,
                )

        last_activity_at = _latest_datetime(
            row.get("topic_updated_at"),
            row.get("pack_created_at"),
            row.get("generation_created_at"),
            row.get("draft_updated_at"),
            row.get("fact_checked_at"),
        )
        age_days = (
            max(0, (current - last_activity_at).days)
            if last_activity_at is not None
            else None
        )
        is_stale = bool(age_days is not None and age_days >= ABANDONED_DAYS)

        queue_rows.append(
            {
                **row,
                "stage": stage,
                "stage_label": WORK_QUEUE_STAGE_LABELS[stage],
                "reason": reason,
                "target_page": target_page,
                "action_label": action_label,
                "action_state": action_state,
                "last_activity_at": last_activity_at,
                "age_days": age_days,
                "is_stale": is_stale,
            }
        )

    queue_rows.sort(
        key=lambda item: (
            WORK_QUEUE_STAGE_ORDER.get(str(item.get("stage") or ""), 99),
            not bool(item.get("is_stale")),
            -int(item.get("topic_priority") or item.get("priority") or 0),
            item.get("last_activity_at") or datetime.min,
            str(item.get("topic_title") or ""),
        )
    )

    bounded_limit = max(1, min(int(limit), 100))
    stage_counts = Counter(str(row["stage"]) for row in queue_rows)
    return {
        "total_count": len(queue_rows),
        "stale_count": sum(1 for row in queue_rows if row["is_stale"]),
        "stage_counts": {
            stage: int(stage_counts.get(stage, 0))
            for stage in WORK_QUEUE_STAGE_LABELS
        },
        "abandoned_days": ABANDONED_DAYS,
        "rows": queue_rows[:bounded_limit],
        "truncated_count": max(0, len(queue_rows) - bounded_limit),
    }


def get_content_work_queue(
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    """기존 주제·자료팩·초안·사실 확인·발행 기록을 읽어 작업 대기열을 반환합니다."""
    topic_rows = _cursor_rows(
        con.execute(
            """
            WITH latest_pack AS (
                SELECT * EXCLUDE(row_number)
                FROM (
                    SELECT p.content_pack_id, p.topic_id, p.version AS pack_version,
                           p.created_at AS pack_created_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY p.topic_id
                               ORDER BY p.version DESC, p.created_at DESC
                           ) AS row_number
                    FROM content_packs p
                ) ranked
                WHERE row_number = 1
            ),
            latest_draft AS (
                SELECT * EXCLUDE(row_number)
                FROM (
                    SELECT d.draft_id, d.topic_id, d.generation_id,
                           d.title AS draft_title, d.current_revision,
                           d.created_at AS draft_created_at,
                           d.updated_at AS draft_updated_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY d.topic_id
                               ORDER BY d.updated_at DESC, d.created_at DESC
                           ) AS row_number
                    FROM drafts d
                ) ranked
                WHERE row_number = 1
            ),
            latest_generation AS (
                SELECT * EXCLUDE(row_number)
                FROM (
                    SELECT g.content_pack_id, g.generation_id,
                           g.parse_status AS generation_parse_status,
                           g.created_at AS generation_created_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY g.content_pack_id
                               ORDER BY g.created_at DESC
                           ) AS row_number
                    FROM generation_sessions g
                ) ranked
                WHERE row_number = 1
            ),
            latest_check AS (
                SELECT draft_id, MAX(checked_at) AS fact_checked_at
                FROM fact_check_items
                GROUP BY draft_id
            )
            SELECT t.topic_id, t.title AS topic_title, t.summary AS topic_summary,
                   t.memo AS topic_memo, t.status AS topic_status,
                   t.priority AS topic_priority, t.source_count,
                   t.updated_at AS topic_updated_at,
                   lp.content_pack_id, lp.pack_version, lp.pack_created_at,
                   lg.generation_id AS latest_generation_id,
                   lg.generation_parse_status, lg.generation_created_at,
                   ld.draft_id, ld.draft_title, ld.current_revision,
                   ld.draft_created_at, ld.draft_updated_at,
                   dg.content_pack_id AS draft_content_pack_id,
                   lc.fact_checked_at
            FROM topics t
            LEFT JOIN latest_pack lp ON lp.topic_id = t.topic_id
            LEFT JOIN latest_generation lg
              ON lg.content_pack_id = lp.content_pack_id
            LEFT JOIN latest_draft ld ON ld.topic_id = t.topic_id
            LEFT JOIN generation_sessions dg ON dg.generation_id = ld.generation_id
            LEFT JOIN latest_check lc ON lc.draft_id = ld.draft_id
            WHERE t.archived_at IS NULL
            ORDER BY t.priority DESC, t.updated_at ASC
            LIMIT ?
            """,
            [MAX_SOURCE_TOPICS],
        )
    )

    references_by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _cursor_rows(
        con.execute(
            """
            SELECT topic_id, reference_id, memo
            FROM topic_references
            WHERE archived_at IS NULL
            """
        )
    ):
        references_by_topic[str(row["topic_id"])].append(row)

    readiness = get_fact_check_readiness(con, limit=MAX_SOURCE_TOPICS, now=now)
    readiness_by_draft = {
        str(row.get("draft_id") or ""): row
        for row in readiness["rows"]
        if str(row.get("draft_id") or "")
    }
    return build_content_work_queue(
        topic_rows,
        readiness_by_draft=readiness_by_draft,
        references_by_topic=references_by_topic,
        now=now,
        limit=limit,
    )
