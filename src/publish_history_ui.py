"""발행 보조 화면에 발행 기록 조회·정정·보관 UI를 표시합니다."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import pandas as pd
import streamlit as st

from src.publish_performance_ui import render_publish_performance_panel
from src.services.publish_service import (
    archive_publish_record,
    ensure_publish_record_management_schema,
    list_publish_record_history,
    list_publish_records,
    restore_publish_record,
    update_publish_record,
)


def _format_datetime(value: object) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else "-"


def _combine_datetime(date_value: date, time_value: time) -> datetime:
    return datetime.combine(date_value, time_value).replace(microsecond=0)


def _record_label(record: dict[str, Any]) -> str:
    status = "보관" if record.get("archived_at") is not None else "활성"
    title = str(record.get("draft_title") or record.get("topic_title") or "제목 없음")
    platform = str(record.get("platform") or "플랫폼 없음")
    return f"[{status}] {title} · {platform} · {_format_datetime(record.get('published_at'))}"


def render_publish_history_panel(con, *, st_module=st) -> None:
    ensure_publish_record_management_schema(con)

    st_module.divider()
    st_module.subheader("발행 기록 조회·정정·보관")
    st_module.caption(
        "발행 기록을 검색하고 잘못 입력한 URL·메모·발행 시각을 정정할 수 있습니다. "
        "기록은 삭제하지 않고 보관하며, 정정·보관·복원 전후 값과 사유를 변경 이력에 남깁니다."
    )

    filter_columns = st_module.columns([3, 1])
    query = filter_columns[0].text_input(
        "발행 기록 검색",
        placeholder="글 제목, 플랫폼, 발행 URL 또는 메모",
        key="publish_history_query",
    )
    include_archived = filter_columns[1].checkbox(
        "보관 기록 포함",
        value=False,
        key="publish_history_include_archived",
    )

    records = list_publish_records(
        con,
        include_archived=include_archived,
        query=query,
        limit=200,
    )
    all_matching = list_publish_records(
        con,
        include_archived=True,
        query=query,
        limit=500,
    )
    active_count = sum(record.get("archived_at") is None for record in all_matching)
    archived_count = sum(record.get("archived_at") is not None for record in all_matching)

    metric_columns = st_module.columns(3)
    metric_columns[0].metric("검색 결과", f"{len(records):,}개", border=True)
    metric_columns[1].metric("활성 기록", f"{active_count:,}개", border=True)
    metric_columns[2].metric("보관 기록", f"{archived_count:,}개", border=True)

    if not records:
        st_module.info("조건에 맞는 발행 기록이 없습니다.")
        return

    summary_frame = pd.DataFrame(
        [
            {
                "상태": "보관" if record.get("archived_at") is not None else "활성",
                "주제": str(record.get("topic_title") or ""),
                "초안": str(record.get("draft_title") or ""),
                "플랫폼": str(record.get("platform") or ""),
                "발행 시각": _format_datetime(record.get("published_at")),
                "발행 URL": str(record.get("published_url") or ""),
                "수정 시각": _format_datetime(record.get("updated_at")),
            }
            for record in records
        ]
    )
    with st_module.expander("발행 기록 목록", expanded=False):
        st_module.dataframe(summary_frame, hide_index=True, width="stretch")

    record_map = {str(record["publish_id"]): record for record in records}
    publish_ids = list(record_map)
    selected_id = st_module.selectbox(
        "관리할 발행 기록",
        publish_ids,
        format_func=lambda publish_id: _record_label(record_map[publish_id]),
        key="publish_history_selected_id",
    )
    selected = record_map[selected_id]
    published_at = selected.get("published_at") or selected.get("created_at") or datetime.now()
    if not isinstance(published_at, datetime):
        published_at = datetime.now()

    with st_module.expander("선택 기록 정정", expanded=False):
        st_module.caption(
            "정정 전 값은 변경 이력에 그대로 보존됩니다. 보관된 기록도 정정할 수 있습니다."
        )
        with st_module.form(f"publish_history_edit_{selected_id}"):
            platform = st_module.text_input(
                "플랫폼",
                value=str(selected.get("platform") or ""),
            )
            published_url = st_module.text_input(
                "발행된 글 URL",
                value=str(selected.get("published_url") or ""),
            )
            write_url = st_module.text_input(
                "당시 글쓰기 페이지 URL",
                value=str(selected.get("write_url") or ""),
            )
            memo = st_module.text_area(
                "발행 메모",
                value=str(selected.get("memo") or ""),
                height=100,
            )
            time_columns = st_module.columns(2)
            published_date = time_columns[0].date_input(
                "발행 날짜",
                value=published_at.date(),
            )
            published_time = time_columns[1].time_input(
                "발행 시간",
                value=published_at.time().replace(microsecond=0),
            )
            change_note = st_module.text_input(
                "정정 사유",
                placeholder="예: 발행 URL을 잘못 입력해 수정",
            )
            submitted = st_module.form_submit_button("정정 내용 저장", type="primary")
        if submitted:
            try:
                changed = update_publish_record(
                    con,
                    publish_id=selected_id,
                    platform=platform,
                    write_url=write_url,
                    published_url=published_url,
                    memo=memo,
                    published_at=_combine_datetime(published_date, published_time),
                    change_note=change_note,
                )
                if changed:
                    st_module.success("발행 기록을 정정하고 변경 이력을 저장했습니다.")
                    st_module.rerun()
                else:
                    st_module.info("변경된 내용이 없습니다.")
            except ValueError as exc:
                st_module.error(str(exc))

    is_archived = selected.get("archived_at") is not None
    action_label = "보관 기록 복원" if is_archived else "선택 기록 보관"
    with st_module.expander(action_label, expanded=False):
        if is_archived:
            st_module.info(
                "복원하면 이 기록을 다시 활성 발행 기록으로 계산하고 해당 주제를 발행 완료 상태로 되돌립니다."
            )
            restore_note = st_module.text_input(
                "복원 사유",
                placeholder="예: 보관 처리가 잘못되어 복원",
                key=f"publish_restore_note_{selected_id}",
            )
            if st_module.button(
                "발행 기록 복원",
                key=f"publish_restore_{selected_id}",
                type="primary",
            ):
                try:
                    if restore_publish_record(
                        con,
                        publish_id=selected_id,
                        change_note=restore_note,
                    ):
                        st_module.success("발행 기록을 복원했습니다.")
                        st_module.rerun()
                except ValueError as exc:
                    st_module.error(str(exc))
        else:
            st_module.warning(
                "보관하면 일반 발행 기록 목록과 발행 완료 판단에서 제외됩니다. 기록과 변경 이력은 삭제하지 않습니다."
            )
            archive_note = st_module.text_input(
                "보관 사유",
                placeholder="예: 테스트로 잘못 저장한 발행 기록",
                key=f"publish_archive_note_{selected_id}",
            )
            archive_confirmed = st_module.checkbox(
                "이 기록을 보관 목록으로 이동하는 것을 확인했습니다.",
                key=f"publish_archive_confirmed_{selected_id}",
            )
            if st_module.button(
                "발행 기록 보관",
                key=f"publish_archive_{selected_id}",
                disabled=not archive_confirmed,
            ):
                try:
                    if archive_publish_record(
                        con,
                        publish_id=selected_id,
                        change_note=archive_note,
                    ):
                        st_module.success("발행 기록을 보관했습니다.")
                        st_module.rerun()
                except ValueError as exc:
                    st_module.error(str(exc))

    history = list_publish_record_history(con, selected_id)
    with st_module.expander(f"변경 이력 {len(history):,}건", expanded=False):
        if not history:
            st_module.caption("아직 정정·보관·복원 이력이 없습니다.")
        else:
            history_frame = pd.DataFrame(
                [
                    {
                        "변경 시각": _format_datetime(item.get("changed_at")),
                        "작업": str(item.get("action_label") or "변경"),
                        "사유": str(item.get("change_note") or ""),
                        "이전 발행 URL": str(
                            (item.get("previous_values") or {}).get("published_url") or ""
                        ),
                        "변경 발행 URL": str(
                            (item.get("new_values") or {}).get("published_url") or ""
                        ),
                    }
                    for item in history
                ]
            )
            st_module.dataframe(history_frame, hide_index=True, width="stretch")

    render_publish_performance_panel(
        con,
        selected_record=selected,
        st_module=st_module,
    )
