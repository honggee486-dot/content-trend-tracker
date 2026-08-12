"""AI 요청서 화면에 자료팩 버전 비교와 입력값 재사용을 표시합니다."""

from __future__ import annotations

from html import escape

import streamlit as st

from src.ai_generation_history_ui import render_ai_generation_history_panel
from src.services.content_pack_history_service import (
    build_content_pack_reuse_payload,
    compare_content_packs,
    list_content_pack_topics,
    list_content_pack_versions,
)
from src.services.workflow_navigation_service import (
    prepare_workflow_navigation_state,
)


REUSE_PAYLOAD_KEY = "content_pack_reuse_payload"
REUSE_FLASH_KEY = "content_pack_reuse_flash"


def _format_created_at(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else "기록 없음"


def _version_label(pack: dict) -> str:
    return (
        f"v{int(pack.get('version') or 0)} · "
        f"{_format_created_at(pack.get('created_at'))} · "
        f"AI 결과 {int(pack.get('generation_count') or 0):,}건 · "
        f"초안 {int(pack.get('draft_count') or 0):,}건"
    )


def render_active_content_pack_reuse_notice(*, st_module=st) -> None:
    flash = st_module.session_state.pop(REUSE_FLASH_KEY, None)
    if flash:
        st_module.success(str(flash))

    payload = st_module.session_state.get(REUSE_PAYLOAD_KEY)
    if not isinstance(payload, dict):
        return

    topic_title = str(payload.get("topic_title") or "주제")
    version = int(payload.get("version") or 0)
    st_module.info(
        f"`{topic_title}`의 자료팩 v{version} 작성 설정을 입력값으로 불러왔습니다. "
        "기존 자료팩은 변경하지 않으며, 아래 내용을 검토한 뒤 요청서를 저장해야 새 버전이 생성됩니다."
    )
    missing_sources = len(payload.get("missing_source_item_ids") or [])
    missing_references = len(payload.get("missing_reference_ids") or [])
    if missing_sources or missing_references:
        st_module.warning(
            "과거 자료팩에 있던 근거 중 현재 연결에서 사용할 수 없는 항목은 자동으로 제외했습니다. "
            f"트렌드 신호 {missing_sources:,}개 · 사실 참고 자료 {missing_references:,}개"
        )
    if st_module.button(
        "과거 자료팩 입력값 사용 취소",
        key="content_pack_reuse_cancel",
        type="secondary",
    ):
        topic_id = str(payload.get("topic_id") or "")
        st_module.session_state.pop(REUSE_PAYLOAD_KEY, None)
        if topic_id:
            st_module.session_state.pop(f"selected_evidence_{topic_id}", None)
            st_module.session_state.pop(
                f"selected_factual_references_{topic_id}",
                None,
            )
        st_module.session_state[REUSE_FLASH_KEY] = "과거 자료팩 입력값 사용을 취소했습니다."
        st_module.rerun()


def render_content_pack_history_panel(con, *, st_module=st) -> None:
    st_module.divider()
    with st_module.expander("자료팩 버전 기록·비교·입력값 재사용", expanded=False):
        st_module.caption(
            "저장된 자료팩은 수정하거나 삭제하지 않습니다. 같은 주제의 버전을 비교하고, "
            "과거 설정을 새 요청서의 시작값으로만 불러올 수 있습니다."
        )
        topics = list_content_pack_topics(con)
        if not topics:
            st_module.info("아직 저장된 자료팩이 없습니다.")
            return

        topic_map = {str(item["topic_id"]): item for item in topics}
        topic_ids = list(topic_map)
        selected_topic_id = st_module.selectbox(
            "비교할 주제",
            topic_ids,
            format_func=lambda value: (
                f"{topic_map[str(value)]['topic_title']} · "
                f"자료팩 {int(topic_map[str(value)]['version_count']):,}개 · "
                f"최신 v{int(topic_map[str(value)]['latest_version'])}"
            ),
            key="content_pack_history_topic",
        )
        versions = list_content_pack_versions(con, selected_topic_id)
        if not versions:
            st_module.info("선택한 주제의 자료팩을 불러오지 못했습니다.")
            return

        version_map = {str(item["content_pack_id"]): item for item in versions}
        version_ids = list(version_map)
        metric_cols = st_module.columns(4)
        metric_cols[0].metric("저장된 버전", f"{len(versions):,}개")
        metric_cols[1].metric("최신 버전", f"v{int(versions[0]['version'])}")
        metric_cols[2].metric(
            "AI 결과 연결",
            f"{sum(int(item.get('generation_count') or 0) for item in versions):,}건",
        )
        metric_cols[3].metric(
            "초안 연결",
            f"{sum(int(item.get('draft_count') or 0) for item in versions):,}건",
        )

        if len(versions) >= 2:
            compare_cols = st_module.columns(2)
            older_id = compare_cols[0].selectbox(
                "기준 버전",
                version_ids,
                index=1,
                format_func=lambda value: _version_label(version_map[str(value)]),
                key=f"content_pack_compare_older_{selected_topic_id}",
            )
            newer_id = compare_cols[1].selectbox(
                "비교 버전",
                version_ids,
                index=0,
                format_func=lambda value: _version_label(version_map[str(value)]),
                key=f"content_pack_compare_newer_{selected_topic_id}",
            )
            try:
                comparison = compare_content_packs(
                    con,
                    older_pack_id=older_id,
                    newer_pack_id=newer_id,
                )
            except ValueError as exc:
                st_module.error(str(exc))
            else:
                if comparison.has_changes:
                    changed_text = ", ".join(comparison.changed_fields) or "작성 설정 동일"
                    st_module.info(
                        f"변경된 설정: {changed_text} · "
                        f"요청서 추가 {comparison.added_lines:,}줄 / 삭제 {comparison.removed_lines:,}줄"
                    )
                else:
                    st_module.success("선택한 두 자료팩의 설정과 AI 요청서가 같습니다.")

                reference_cols = st_module.columns(2)
                with reference_cols[0]:
                    st_module.markdown("**비교 버전에 추가된 근거**")
                    if comparison.added_references:
                        for item in comparison.added_references:
                            st_module.markdown(f"- {escape(item)}")
                    else:
                        st_module.caption("추가된 근거 없음")
                with reference_cols[1]:
                    st_module.markdown("**비교 버전에서 제외된 근거**")
                    if comparison.removed_references:
                        for item in comparison.removed_references:
                            st_module.markdown(f"- {escape(item)}")
                    else:
                        st_module.caption("제외된 근거 없음")

                st_module.markdown("**AI 요청서 줄 단위 차이**")
                st_module.code(comparison.diff_text, language="diff")
                if comparison.diff_truncated:
                    st_module.caption("화면 성능을 위해 긴 차이는 일부만 표시했습니다.")
        else:
            st_module.info("자료팩이 1개뿐이어서 버전 비교는 아직 할 수 없습니다.")

        st_module.markdown("---")
        reuse_id = st_module.selectbox(
            "입력값으로 불러올 자료팩",
            version_ids,
            format_func=lambda value: _version_label(version_map[str(value)]),
            key=f"content_pack_reuse_version_{selected_topic_id}",
        )
        selected_reuse = version_map[reuse_id]
        st_module.caption(
            f"독자 대상: {selected_reuse.get('audience') or '-'} · "
            f"글 목적: {selected_reuse.get('purpose') or '-'} · "
            f"목표 분량: {int(selected_reuse.get('target_length') or 0):,}자"
        )
        st_module.warning(
            "불러오기는 기존 자료팩을 복원하거나 덮어쓰는 기능이 아닙니다. "
            "AI 요청서 입력란에 과거 값을 채우며, 저장 버튼을 누르기 전까지 새 자료팩은 생성되지 않습니다."
        )
        if st_module.button(
            "선택 자료팩 설정을 입력값으로 불러오기",
            key=f"content_pack_reuse_button_{selected_topic_id}_{reuse_id}",
            type="primary",
            width="stretch",
        ):
            try:
                payload = build_content_pack_reuse_payload(con, reuse_id)
            except ValueError as exc:
                st_module.error(str(exc))
            else:
                prepare_workflow_navigation_state(
                    st_module.session_state,
                    "AI 요청서",
                    {
                        "prefill_topic_id": str(payload["topic_id"]),
                        REUSE_PAYLOAD_KEY: payload,
                    },
                )
                st_module.session_state[REUSE_FLASH_KEY] = (
                    f"자료팩 v{int(payload['version'])}의 작성 설정을 입력값으로 불러왔습니다."
                )
                st_module.rerun()

    render_ai_generation_history_panel(con, st_module=st_module)
