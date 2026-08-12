"""글 편집 화면에 사실 확인 변경 이력과 안전 되돌리기를 표시합니다."""

from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from src.services.draft_service import FACT_CHECK_STATUS_LABELS, get_fact_checks
from src.services.fact_check_history_service import (
    list_fact_check_history,
    reconcile_fact_check_history,
    revert_fact_check_history,
)


_FLASH_KEY = "fact_check_history_flash"


def _format_time(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else "기록 없음"


def _status_label(value: object) -> str:
    status = str(value or "needs_verification")
    return FACT_CHECK_STATUS_LABELS.get(status, status or "미확인")


def _change_summary(previous: dict, current: dict) -> list[str]:
    labels: list[str] = []
    if str(previous.get("check_status") or "") != str(current.get("check_status") or ""):
        labels.append("상태")
    if str(previous.get("evidence") or "") != str(current.get("evidence") or ""):
        labels.append("확인 메모")
    if str(previous.get("source_url") or "") != str(current.get("source_url") or ""):
        labels.append("근거 URL")
    return labels


def render_fact_check_history_panel(
    con,
    draft_id: str,
    *,
    st_module=st,
) -> None:
    checks = get_fact_checks(con, draft_id)
    if not checks:
        return

    reconciliation = reconcile_fact_check_history(con, draft_id)
    history = list_fact_check_history(
        con,
        draft_id=draft_id,
        include_baseline=False,
        limit=300,
    )

    flash = st_module.session_state.pop(_FLASH_KEY, None)
    if flash:
        st_module.success(str(flash))

    st_module.divider()
    with st_module.expander("사실 확인 변경 이력·안전 되돌리기", expanded=False):
        st_module.caption(
            "사실 확인 상태·메모·근거 URL의 수정 전후를 보존합니다. "
            "과거 상태로 되돌려도 기존 이력은 삭제하지 않고 새 되돌리기 이력을 추가합니다."
        )
        if reconciliation["baselines"]:
            st_module.info(
                f"기존 사실 확인 항목 {reconciliation['baselines']:,}개의 현재 상태를 "
                "최초 기준점으로 보존했습니다. 기능 도입 이전 변경 내용은 역산하지 않습니다."
            )
        elif reconciliation["updates"]:
            st_module.success(
                f"이번 화면 갱신에서 변경 {reconciliation['updates']:,}건을 감지해 이력에 저장했습니다."
            )

        changed_item_ids = {str(item["fact_check_id"]) for item in history}
        metric_columns = st_module.columns(3)
        metric_columns[0].metric("사실 확인 항목", f"{len(checks):,}개")
        metric_columns[1].metric("변경 이력", f"{len(history):,}건")
        metric_columns[2].metric("변경된 항목", f"{len(changed_item_ids):,}개")

        if not history:
            st_module.info(
                "아직 상태·메모·근거 URL 변경 이력이 없습니다. "
                "현재 상태는 기준점으로 보존됐으며 이후 변경부터 전후 값을 확인할 수 있습니다."
            )
            return

        check_map = {str(item["fact_check_id"]): item for item in checks}
        selectable_ids = [
            fact_check_id
            for fact_check_id in check_map
            if fact_check_id in changed_item_ids
        ]
        selected_fact_check_id = st_module.selectbox(
            "이력을 확인할 주장",
            selectable_ids,
            format_func=lambda value: str(
                check_map.get(str(value), {}).get("claim_text") or value
            ),
            key=f"fact_check_history_item_{draft_id}",
        )
        selected_history = [
            item
            for item in history
            if str(item["fact_check_id"]) == selected_fact_check_id
        ]
        history_ids = [str(item["history_id"]) for item in selected_history]
        history_map = {str(item["history_id"]): item for item in selected_history}
        selected_history_id = st_module.selectbox(
            "비교할 변경",
            history_ids,
            format_func=lambda value: (
                f"{_format_time(history_map[str(value)].get('changed_at'))} · "
                f"{history_map[str(value)].get('action_label') or '변경'} · "
                f"{history_map[str(value)].get('change_note') or '사유 없음'}"
            ),
            key=f"fact_check_history_change_{draft_id}_{selected_fact_check_id}",
        )
        selected = history_map[selected_history_id]
        previous = dict(selected.get("previous_values") or {})
        current = dict(selected.get("new_values") or {})
        changed_labels = _change_summary(previous, current)

        claim = str(selected.get("claim_text") or current.get("claim_text") or "확인할 주장")
        st_module.markdown(f"**{escape(claim)}**", unsafe_allow_html=True)
        st_module.caption(
            f"변경 항목: {', '.join(changed_labels) if changed_labels else '표시값 차이 없음'} · "
            f"기록 시각 {_format_time(selected.get('changed_at'))}"
        )

        comparison_frame = pd.DataFrame(
            [
                {
                    "항목": "확인 상태",
                    "변경 전": _status_label(previous.get("check_status")),
                    "변경 후": _status_label(current.get("check_status")),
                },
                {
                    "항목": "확인 메모",
                    "변경 전": str(previous.get("evidence") or ""),
                    "변경 후": str(current.get("evidence") or ""),
                },
                {
                    "항목": "근거 URL",
                    "변경 전": str(previous.get("source_url") or ""),
                    "변경 후": str(current.get("source_url") or ""),
                },
            ]
        )
        st_module.dataframe(
            comparison_frame,
            hide_index=True,
            width="stretch",
        )

        with st_module.expander("선택한 주장의 전체 변경 목록", expanded=False):
            history_frame = pd.DataFrame(
                [
                    {
                        "변경 시각": item.get("changed_at"),
                        "구분": item.get("action_label"),
                        "변경 전 상태": _status_label(
                            (item.get("previous_values") or {}).get("check_status")
                        ),
                        "변경 후 상태": _status_label(
                            (item.get("new_values") or {}).get("check_status")
                        ),
                        "사유": item.get("change_note"),
                    }
                    for item in selected_history
                ]
            )
            st_module.dataframe(history_frame, hide_index=True, width="stretch")

        st_module.warning(
            "되돌리면 이 변경의 직전 상태를 현재 사실 확인 항목에 반영합니다. "
            "초안 본문은 바꾸지 않으므로 주장과 본문이 일치하는지 다시 확인하세요."
        )
        revert_note = st_module.text_input(
            "되돌리기 사유",
            value=f"{_format_time(selected.get('changed_at'))} 변경 전 상태로 되돌림",
            key=f"fact_check_history_revert_note_{selected_history_id}",
        )
        confirmed = st_module.checkbox(
            "선택한 변경의 직전 상태로 되돌리는 것을 확인했습니다.",
            key=f"fact_check_history_revert_confirm_{selected_history_id}",
        )
        confirmation_text = st_module.text_input(
            "확인을 위해 `되돌리기`를 입력하세요.",
            key=f"fact_check_history_revert_text_{selected_history_id}",
        )
        revert_enabled = bool(
            revert_note.strip()
            and confirmed
            and confirmation_text.strip() == "되돌리기"
        )
        if st_module.button(
            "선택 변경 안전 되돌리기",
            key=f"fact_check_history_revert_button_{selected_history_id}",
            type="primary",
            disabled=not revert_enabled,
            width="stretch",
        ):
            try:
                changed = revert_fact_check_history(
                    con,
                    history_id=selected_history_id,
                    change_note=revert_note,
                )
                if changed:
                    st_module.session_state[_FLASH_KEY] = (
                        "선택한 변경의 직전 상태로 되돌리고 새 이력을 저장했습니다."
                    )
                    st_module.rerun()
                else:
                    st_module.info("현재 상태가 이미 선택한 변경 전 상태와 같습니다.")
            except ValueError as exc:
                st_module.error(str(exc))
            except Exception as exc:
                st_module.error(f"사실 확인 이력을 되돌리지 못했습니다: {exc}")
