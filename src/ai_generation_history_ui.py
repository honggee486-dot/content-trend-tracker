"""AI 요청서 화면에 저장된 AI 생성 기록과 현재 규칙 재검사를 표시합니다."""

from __future__ import annotations

import streamlit as st

from src.services.ai_generation_history_service import (
    get_ai_generation_session,
    list_ai_generation_sessions,
    provider_for_ai_import,
    revalidate_ai_generation_session,
)
from src.services.workflow_navigation_service import (
    prepare_workflow_navigation_state,
)


def _format_created_at(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else "기록 없음"


def _session_label(item: dict) -> str:
    draft_text = str(item.get("draft_title") or "연결 초안 없음")
    return (
        f"{_format_created_at(item.get('created_at'))} · "
        f"{item.get('topic_title') or '주제 없음'} · "
        f"자료팩 v{int(item.get('pack_version') or 0)} · "
        f"{item.get('ai_provider') or 'AI 미상'} · {draft_text}"
    )


def _render_messages(st_module, title: str, messages: tuple[str, ...] | list[str]) -> None:
    st_module.markdown(f"**{title}**")
    if messages:
        for message in messages:
            st_module.markdown(f"- {message}")
    else:
        st_module.caption("없음")


def render_ai_generation_history_panel(con, *, st_module=st) -> None:
    st_module.divider()
    with st_module.expander("AI 생성 결과 기록·현재 규칙 재검사", expanded=False):
        st_module.caption(
            "저장된 AI 원문과 검사 결과를 수정하지 않고 조회합니다. 현재 파서와 출처 규칙으로 "
            "다시 검사해 저장 당시 결과와 달라졌는지 확인할 수 있습니다."
        )
        query = st_module.text_input(
            "생성 기록 검색",
            placeholder="주제, 초안 제목, AI 제공자 또는 기록 ID",
            key="ai_generation_history_query",
        )
        sessions = list_ai_generation_sessions(con, query=query, limit=100)
        selection_key = "ai_generation_history_selected"
        if not sessions:
            st_module.session_state.pop(selection_key, None)
            st_module.info("조건에 맞는 저장된 AI 생성 기록이 없습니다.")
            return

        session_map = {str(item["generation_id"]): item for item in sessions}
        session_ids = list(session_map)
        if st_module.session_state.get(selection_key) not in session_ids:
            st_module.session_state.pop(selection_key, None)
        selected_id = st_module.selectbox(
            "확인할 생성 기록",
            session_ids,
            format_func=lambda value: _session_label(session_map[str(value)]),
            key=selection_key,
        )
        session = get_ai_generation_session(con, selected_id)
        if session is None:
            st_module.error("선택한 AI 생성 기록을 불러오지 못했습니다.")
            return

        metric_cols = st_module.columns(4)
        metric_cols[0].metric("AI 제공자", str(session.get("ai_provider") or "미상"))
        metric_cols[1].metric("자료팩", f"v{int(session.get('pack_version') or 0)}")
        metric_cols[2].metric(
            "저장 스키마",
            str(session.get("schema_version") or "기록 없음"),
        )
        metric_cols[3].metric(
            "연결 초안",
            f"v{int(session.get('current_revision') or 0)}"
            if session.get("draft_id")
            else "없음",
        )
        st_module.caption(
            f"주제: {session.get('topic_title') or '-'} · "
            f"생성 시각: {_format_created_at(session.get('created_at'))} · "
            f"사실 확인 항목: {int(session.get('fact_check_count') or 0):,}개"
        )

        try:
            revalidation = revalidate_ai_generation_session(con, selected_id)
        except ValueError as exc:
            st_module.error(str(exc))
            return

        status_cols = st_module.columns(2)
        stored_label = "통과" if revalidation.stored_is_valid else "오류"
        current_label = "통과" if revalidation.current_is_valid else "오류"
        status_cols[0].metric("저장 당시 검사", stored_label)
        status_cols[1].metric("현재 규칙 재검사", current_label)

        if revalidation.status_changed:
            st_module.warning(
                "저장 당시와 현재 재검사 통과 여부가 다릅니다. 파서·출처 검사 규칙 변경이나 "
                "저장 당시 기록 상태를 확인하세요. 기존 생성 기록과 초안은 변경하지 않았습니다."
            )
        elif revalidation.messages_changed:
            st_module.info(
                "통과 여부는 같지만 경고 또는 오류 문구가 달라졌습니다. 현재 규칙의 상세 내용을 확인하세요."
            )
        elif revalidation.current_is_valid:
            st_module.success("현재 형식·출처 검사 규칙으로도 통과했습니다.")
        else:
            st_module.error("현재 형식·출처 검사 규칙을 통과하지 못했습니다.")

        validation_tab, raw_tab, parsed_tab = st_module.tabs(
            ["검사 결과", "AI 원문", "저장된 파싱 JSON"]
        )
        with validation_tab:
            message_cols = st_module.columns(2)
            with message_cols[0]:
                _render_messages(
                    st_module,
                    "저장 당시 오류",
                    revalidation.stored_errors,
                )
                _render_messages(
                    st_module,
                    "저장 당시 경고",
                    revalidation.stored_warnings,
                )
            with message_cols[1]:
                _render_messages(
                    st_module,
                    "현재 재검사 오류",
                    revalidation.current_errors,
                )
                _render_messages(
                    st_module,
                    "현재 재검사 경고",
                    revalidation.current_warnings,
                )
            st_module.caption(
                f"저장 스키마 {revalidation.stored_schema_version or '-'} · "
                f"현재 해석 스키마 {revalidation.current_schema_version or '-'}"
            )
        with raw_tab:
            raw_response = str(session.get("raw_response") or "")
            st_module.text_area(
                "저장된 AI 원문",
                value=raw_response,
                height=360,
                disabled=True,
                key=f"ai_generation_history_raw_{selected_id}",
            )
            st_module.caption(f"원문 길이: {len(raw_response):,}자")
        with parsed_tab:
            parsed_data = session.get("parsed_data")
            if isinstance(parsed_data, dict):
                st_module.json(parsed_data)
            else:
                st_module.code(
                    str(session.get("parsed_json") or "파싱 JSON 기록 없음"),
                    language="json",
                )

        action_cols = st_module.columns(2)
        if action_cols[0].button(
            "이 원문을 AI 결과 입력란에서 다시 열기",
            key=f"ai_generation_history_reopen_{selected_id}",
            type="primary",
            width="stretch",
        ):
            pack_id = str(session.get("content_pack_id") or "")
            prepare_workflow_navigation_state(
                st_module.session_state,
                "AI 결과 가져오기",
                {"prefill_content_pack_id": pack_id},
            )
            st_module.session_state["ai_import_provider"] = provider_for_ai_import(
                str(session.get("ai_provider") or "")
            )
            st_module.session_state[f"ai_import_raw_{pack_id}"] = str(
                session.get("raw_response") or ""
            )
            st_module.rerun()

        draft_id = str(session.get("draft_id") or "")
        if action_cols[1].button(
            "연결된 초안 편집으로 이동",
            key=f"ai_generation_history_draft_{selected_id}",
            disabled=not bool(draft_id),
            width="stretch",
        ):
            prepare_workflow_navigation_state(
                st_module.session_state,
                "글 편집",
                {"prefill_draft_id": draft_id},
            )
            st_module.rerun()

        st_module.warning(
            "다시 열기는 원문을 입력란에 복사할 뿐 DB를 수정하지 않습니다. 원문을 바꾸지 않고 "
            "다시 저장하면 기존 멱등성 규칙에 따라 이미 연결된 생성 기록과 초안이 재사용될 수 있습니다."
        )
