"""글 편집 화면에 초안 버전 비교와 안전 복원을 표시합니다."""

from __future__ import annotations

from html import escape

import streamlit as st

from src.fact_check_history_ui import render_fact_check_history_panel
from src.services.draft_revision_service import (
    compare_draft_to_revision,
    get_draft_revision,
    list_draft_revisions,
    restore_draft_revision,
)
from src.services.draft_service import get_draft


_FLASH_KEY = "draft_revision_restore_flash"


def _format_created_at(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else "기록 없음"


def _field_change_labels(comparison) -> list[str]:
    labels: list[str] = []
    for changed, label in (
        (comparison.title_changed, "제목"),
        (comparison.summary_changed, "요약"),
        (comparison.category_changed, "카테고리"),
        (comparison.tags_changed, "태그"),
        (comparison.body_changed, "본문"),
    ):
        if changed:
            labels.append(label)
    return labels


def render_draft_revision_panel(
    con,
    draft_id: str,
    *,
    st_module=st,
) -> None:
    draft = get_draft(con, draft_id)
    if draft is None:
        return
    revisions = list_draft_revisions(con, draft_id, limit=100)

    flash = st_module.session_state.pop(_FLASH_KEY, None)
    if flash:
        st_module.success(str(flash))

    if revisions:
        st_module.divider()
        with st_module.expander("초안 버전 기록·비교·복원", expanded=False):
            st_module.caption(
                "과거 버전과 현재 편집본을 비교합니다. 복원해도 기존 버전은 삭제하지 않으며, "
                "선택한 내용을 새 버전으로 저장합니다."
            )
            metric_columns = st_module.columns(3)
            metric_columns[0].metric("현재 버전", f"v{int(draft['current_revision'])}")
            metric_columns[1].metric("저장된 버전", f"{len(revisions):,}개")
            metric_columns[2].metric(
                "최근 저장",
                _format_created_at(revisions[0].get("created_at")),
            )

            revision_ids = [str(item["revision_id"]) for item in revisions]
            revision_map = {str(item["revision_id"]): item for item in revisions}
            selected_id = st_module.selectbox(
                "비교할 과거 버전",
                revision_ids,
                format_func=lambda value: (
                    f"v{int(revision_map[str(value)]['revision_number'])} · "
                    f"{_format_created_at(revision_map[str(value)].get('created_at'))} · "
                    f"{str(revision_map[str(value)].get('change_note') or '수정 메모 없음')}"
                ),
                key=f"draft_revision_selected_{draft_id}",
            )
            selected = get_draft_revision(
                con,
                draft_id=draft_id,
                revision_id=selected_id,
            )
            if selected is None:
                st_module.error("선택한 버전을 불러오지 못했습니다.")
            else:
                comparison = compare_draft_to_revision(
                    con,
                    draft_id=draft_id,
                    revision_id=selected_id,
                )
                changed_labels = _field_change_labels(comparison)
                if changed_labels:
                    st_module.info(
                        f"현재 편집본과 다른 항목: {', '.join(changed_labels)} · "
                        f"본문 추가 {comparison.added_lines:,}줄 / 삭제 {comparison.removed_lines:,}줄"
                    )
                else:
                    st_module.success("선택한 버전과 현재 편집본의 내용이 같습니다.")

                metadata_columns = st_module.columns(2)
                metadata_columns[0].markdown(
                    f"**선택 버전 제목**<br>{escape(str(selected.get('title') or ''))}",
                    unsafe_allow_html=True,
                )
                metadata_columns[0].caption(
                    f"카테고리: {str(selected.get('category') or '없음')} · "
                    f"태그: {', '.join(selected.get('tags') or []) or '없음'}"
                )
                metadata_columns[1].markdown(
                    f"**현재 제목**<br>{escape(str(draft.get('title') or ''))}",
                    unsafe_allow_html=True,
                )
                metadata_columns[1].caption(
                    f"카테고리: {str(draft.get('category') or '없음')} · "
                    f"태그: {', '.join(draft.get('tags') or []) or '없음'}"
                )

                if comparison.summary_changed:
                    summary_columns = st_module.columns(2)
                    summary_columns[0].text_area(
                        "선택 버전 요약",
                        value=str(selected.get("summary") or ""),
                        height=110,
                        disabled=True,
                        key=f"draft_revision_old_summary_{draft_id}_{selected_id}",
                    )
                    summary_columns[1].text_area(
                        "현재 요약",
                        value=str(draft.get("summary") or ""),
                        height=110,
                        disabled=True,
                        key=f"draft_revision_current_summary_{draft_id}_{selected_id}",
                    )

                st_module.markdown("**본문 차이**")
                st_module.code(comparison.diff_text, language="diff")
                if comparison.diff_truncated:
                    st_module.caption("화면 성능을 위해 긴 차이는 일부만 표시했습니다.")

                st_module.warning(
                    "복원하면 선택 버전의 제목·요약·카테고리·태그·본문을 현재 초안에 반영합니다. "
                    "출처·이미지 지시·사실 확인 기록은 삭제하지 않으며, 복원 후 사실 확인 상태를 다시 검토하세요."
                )
                restore_note = st_module.text_input(
                    "복원 메모",
                    value=f"v{comparison.revision_number}에서 복원",
                    key=f"draft_revision_restore_note_{draft_id}_{selected_id}",
                )
                confirmed = st_module.checkbox(
                    "선택 버전을 새 버전으로 복원하는 것을 확인했습니다.",
                    key=f"draft_revision_restore_confirm_{draft_id}_{selected_id}",
                )
                confirmation_text = st_module.text_input(
                    "확인을 위해 `복원`을 입력하세요.",
                    key=f"draft_revision_restore_text_{draft_id}_{selected_id}",
                )
                restore_enabled = bool(
                    comparison.has_changes
                    and confirmed
                    and confirmation_text.strip() == "복원"
                )
                if st_module.button(
                    "선택 버전 안전 복원",
                    key=f"draft_revision_restore_button_{draft_id}_{selected_id}",
                    type="primary",
                    disabled=not restore_enabled,
                    width="stretch",
                ):
                    try:
                        restored_revision = restore_draft_revision(
                            con,
                            draft_id=draft_id,
                            revision_id=selected_id,
                            change_note=restore_note,
                        )
                        st_module.session_state[_FLASH_KEY] = (
                            f"v{comparison.revision_number} 내용을 새 v{restored_revision}로 복원했습니다."
                        )
                        st_module.rerun()
                    except ValueError as exc:
                        st_module.error(str(exc))
                    except Exception as exc:
                        st_module.error(f"초안 버전을 복원하지 못했습니다: {exc}")

    try:
        render_fact_check_history_panel(
            con,
            draft_id,
            st_module=st_module,
        )
    except Exception as exc:
        st_module.caption(f"사실 확인 변경 이력을 불러오지 못했습니다: {exc}")
