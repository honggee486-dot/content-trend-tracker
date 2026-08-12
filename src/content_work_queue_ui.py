"""오늘의 트렌드 첫 화면에 다음 콘텐츠 작업을 표시합니다."""

from __future__ import annotations

from html import escape
from typing import Any, Callable

import pandas as pd
import streamlit as st

from src.services.content_work_queue_service import get_content_work_queue

NavigateCallback = Callable[..., None]

_SOURCE_LABELS = {
    "youtube": "YouTube",
    "naver_news": "NAVER 뉴스",
    "naver_blog": "NAVER 블로그",
    "daum_web": "Daum 웹문서",
    "daum_cafe": "Daum 카페",
    "google_trends": "Google Trends",
    "wikipedia": "위키백과",
}


def _format_time(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value is not None else "기록 없음"


def _cursor_rows(cursor) -> list[dict[str, Any]]:
    columns = [str(column[0]) for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _load_topic_evidence(con, topic_id: str, *, limit: int = 8) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit), 20))
    trend_rows = _cursor_rows(
        con.execute(
            """
            SELECT s.source_type, s.source_name, s.raw_title, s.source_url,
                   s.published_at, s.observed_at, s.signal_value,
                   s.observation_count, l.match_type, l.match_score
            FROM topic_source_links l
            JOIN source_items s ON s.source_item_id = l.source_item_id
            WHERE l.topic_id = ?
            ORDER BY COALESCE(
                         s.published_at,
                         s.observed_at,
                         s.last_imported_at,
                         s.imported_at
                     ) DESC,
                     l.linked_at DESC
            LIMIT ?
            """,
            [topic_id, bounded_limit],
        )
    )
    reference_rows = _cursor_rows(
        con.execute(
            """
            SELECT reference_type, title, publisher, url, published_at, memo
            FROM topic_references
            WHERE topic_id = ? AND archived_at IS NULL
            ORDER BY updated_at DESC, created_at DESC
            """,
            [topic_id],
        )
    )
    total_row = con.execute(
        "SELECT COUNT(*) FROM topic_source_links WHERE topic_id = ?",
        [topic_id],
    ).fetchone()
    return {
        "trend_total": int(total_row[0] or 0) if total_row else 0,
        "trend_rows": trend_rows,
        "reference_rows": reference_rows,
    }


def _format_signal_value(value: object) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,.1f}" if number % 1 else f"{int(number):,}"


def _render_request_ready_evidence(
    con,
    row: dict[str, Any],
    *,
    st_module=st,
) -> None:
    if str(row.get("stage") or "") != "request_ready":
        return
    topic_id = str(row.get("topic_id") or "").strip()
    if not topic_id:
        return

    evidence = _load_topic_evidence(con, topic_id)
    trend_total = int(evidence["trend_total"])
    trend_rows = list(evidence["trend_rows"])
    reference_rows = list(evidence["reference_rows"])
    label = (
        f"수집 근거 보기 · 트렌드 신호 {trend_total:,}개 · "
        f"사실 참고 자료 {len(reference_rows):,}개"
    )
    with st_module.expander(label, expanded=False):
        st_module.caption(
            "AI 요청서에 넣을 근거를 빠르게 확인하는 읽기 전용 영역입니다. "
            "트렌드 신호는 최근 연결 항목 최대 8개만 표시하며 원본 데이터는 변경하지 않습니다."
        )
        if trend_rows:
            st_module.markdown("**연결된 트렌드 신호**")
            trend_frame = pd.DataFrame(
                [
                    {
                        "출처": _SOURCE_LABELS.get(
                            str(item.get("source_type") or ""),
                            str(item.get("source_name") or item.get("source_type") or "출처"),
                        ),
                        "제목": str(item.get("raw_title") or ""),
                        "게시·관측 시각": _format_time(
                            item.get("published_at") or item.get("observed_at")
                        ),
                        "신호값": _format_signal_value(item.get("signal_value")),
                        "포착 횟수": int(item.get("observation_count") or 0),
                        "URL": str(item.get("source_url") or ""),
                    }
                    for item in trend_rows
                ]
            )
            st_module.dataframe(trend_frame, hide_index=True, width="stretch")
            omitted = max(0, trend_total - len(trend_rows))
            if omitted:
                st_module.caption(
                    f"최근 {len(trend_rows):,}개만 표시했습니다. 나머지 {omitted:,}개는 주제·트렌드 화면에서 확인하세요."
                )
        else:
            st_module.info("연결된 트렌드 신호 상세가 없습니다.")

        if reference_rows:
            st_module.markdown("**사용자가 추가한 사실 참고 자료**")
            reference_frame = pd.DataFrame(
                [
                    {
                        "종류": str(item.get("reference_type") or "참고 자료"),
                        "제목": str(item.get("title") or ""),
                        "발행처": str(item.get("publisher") or ""),
                        "게시일": str(item.get("published_at") or ""),
                        "메모": str(item.get("memo") or ""),
                        "URL": str(item.get("url") or ""),
                    }
                    for item in reference_rows
                ]
            )
            st_module.dataframe(reference_frame, hide_index=True, width="stretch")
        else:
            st_module.caption("사용자가 별도로 추가한 사실 참고 자료는 없습니다.")


def _render_queue_row(
    con,
    row: dict[str, Any],
    *,
    row_index: int,
    st_module=st,
    navigate: NavigateCallback | None = None,
    primary: bool = False,
) -> None:
    stale_text = (
        f" · {int(row.get('age_days') or 0)}일 방치"
        if row.get("is_stale")
        else ""
    )
    title = str(
        row.get("draft_title")
        or row.get("topic_title")
        or "제목 없는 작업"
    )
    with st_module.container(border=True):
        columns = st_module.columns(
            [1.0, 3.8, 1.4],
            gap="medium",
            vertical_alignment="center",
        )
        columns[0].markdown(
            f"**{escape(str(row.get('stage_label') or '작업'))}**"
            f"<br><small>{escape(_format_time(row.get('last_activity_at')) + stale_text)}</small>",
            unsafe_allow_html=True,
        )
        columns[1].markdown(f"**{escape(title)}**")
        columns[1].caption(str(row.get("reason") or "다음 작업을 확인하세요."))
        clicked = columns[2].button(
            str(row.get("action_label") or "이어서 하기"),
            key=(
                f"content_work_queue_{row_index}_{row.get('topic_id')}_"
                f"{row.get('stage')}"
            ),
            type="primary" if primary else "secondary",
            width="stretch",
        )
        if clicked and navigate is not None:
            target_page = str(row.get("target_page") or "오늘의 트렌드")
            action_state = dict(row.get("action_state") or {})
            navigate(target_page, **action_state)
        _render_request_ready_evidence(
            con,
            dict(row),
            st_module=st_module,
        )


def render_content_work_queue(
    con,
    *,
    st_module=st,
    navigate: NavigateCallback | None = None,
    limit: int = 8,
) -> None:
    queue = get_content_work_queue(con, limit=limit)
    stage_counts = queue["stage_counts"]

    st_module.subheader("콘텐츠 작업 대기열")
    st_module.caption(
        "저장된 주제마다 현재 가장 앞선 제작 단계 하나만 표시합니다. "
        "발행 완료·보류 작업은 제외하고, 버튼을 누르면 이어서 처리할 화면으로 이동합니다."
    )

    with st_module.container(horizontal=True):
        st_module.metric(
            "할 작업",
            f"{int(queue['total_count']):,}개",
            help="발행 완료와 보류를 제외하고 다음 행동이 남아 있는 저장 작업 수입니다.",
            border=True,
        )
        st_module.metric(
            "자료 보완",
            f"{int(stage_counts['needs_research']):,}개",
            help="연결 근거가 없거나 시점 의존 주제에 필요한 사실 참고 자료가 부족한 작업입니다.",
            border=True,
        )
        st_module.metric(
            "AI 요청·결과",
            f"{int(stage_counts['request_ready']) + int(stage_counts['awaiting_ai_result']):,}개",
            help="AI 요청서를 만들 수 있거나, 이미 만든 요청서의 결과를 가져와야 하는 작업입니다.",
            border=True,
        )
        st_module.metric(
            "편집·사실 확인",
            f"{int(stage_counts['draft_editing']) + int(stage_counts['fact_check']):,}개",
            help="초안 수정, 미확인 주장 확인 또는 시점 의존 정보 재확인이 필요한 작업입니다.",
            border=True,
        )
        st_module.metric(
            "발행 준비",
            f"{int(stage_counts['publish_ready']):,}개",
            help="등록된 사실 확인 항목을 모두 확인해 발행 보조로 이동할 수 있는 작업입니다.",
            border=True,
        )
        st_module.metric(
            f"{int(queue['abandoned_days'])}일 이상",
            f"{int(queue['stale_count']):,}개",
            help="마지막 제작 활동 이후 7일 이상 지난 작업입니다. 각 단계 안에서 먼저 표시합니다.",
            border=True,
        )

    rows = [dict(row) for row in queue["rows"]]
    if not rows:
        st_module.success(
            "현재 이어서 처리할 저장 작업이 없습니다. 아래 트렌드 후보에서 새 글감을 선택하세요."
        )
        return

    indexed_rows = list(enumerate(rows))
    request_ready_rows = [
        (index, row)
        for index, row in indexed_rows
        if str(row.get("stage") or "") == "request_ready"
    ]
    visible_rows = [
        (index, row)
        for index, row in indexed_rows
        if str(row.get("stage") or "") != "request_ready"
    ]

    if request_ready_rows:
        total_request_ready = int(stage_counts.get("request_ready") or 0)
        label = f"AI 요청서 준비 {total_request_ready:,}개 보기"
        if total_request_ready != len(request_ready_rows):
            label += f" · 현재 목록 {len(request_ready_rows):,}개"
        with st_module.expander(label, expanded=False):
            st_module.caption(
                "첫 화면을 간결하게 유지하기 위해 AI 요청서 준비 작업은 기본 접힘입니다. "
                "필요한 주제를 펼쳐 이어서 처리하고, 각 항목의 수집 근거도 안에서 확인할 수 있습니다."
            )
            for position, (row_index, row) in enumerate(request_ready_rows):
                _render_queue_row(
                    con,
                    row,
                    row_index=row_index,
                    st_module=st_module,
                    navigate=navigate,
                    primary=position == 0 and not visible_rows,
                )

    for position, (row_index, row) in enumerate(visible_rows):
        _render_queue_row(
            con,
            row,
            row_index=row_index,
            st_module=st_module,
            navigate=navigate,
            primary=position == 0,
        )

    truncated = int(queue.get("truncated_count") or 0)
    if truncated:
        st_module.caption(
            f"우선순위 상위 {len(rows):,}개를 표시했습니다. 나머지 {truncated:,}개는 각 제작 화면에서 확인하세요."
        )
