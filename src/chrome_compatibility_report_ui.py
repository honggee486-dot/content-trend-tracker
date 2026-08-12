from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

import pandas as pd
import streamlit as st

from src.services.chrome_compatibility_report_service import (
    CompatibilityReportReview,
    review_chrome_compatibility_report,
)


def _field_rows(review: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for field in review.get("fields") or []:
        rows.append(
            {
                "항목": str(field.get("label") or field.get("field_name") or ""),
                "상태": str(field.get("status") or ""),
                "선택자": str(field.get("selector") or "-") or "-",
                "iframe 위치": str(field.get("frame_path") or "-") or "-",
                "태그": str(field.get("tag_name") or "-") or "-",
                "contenteditable": "예" if field.get("contenteditable") else "아니요",
            }
        )
    return rows


def _candidate_rows(review: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in review.get("candidate_controls") or []:
        rows.append(
            {
                "iframe 위치": str(candidate.get("frame_path") or "top"),
                "태그·타입": " · ".join(
                    value
                    for value in (
                        str(candidate.get("tag_name") or ""),
                        str(candidate.get("input_type") or ""),
                    )
                    if value
                )
                or "-",
                "id": str(candidate.get("element_id") or "-") or "-",
                "name": str(candidate.get("name") or "-") or "-",
                "role": str(candidate.get("role") or "-") or "-",
                "placeholder": str(candidate.get("placeholder") or "-") or "-",
                "aria-label": str(candidate.get("aria_label") or "-") or "-",
                "data-placeholder": str(candidate.get("data_placeholder") or "-") or "-",
                "class": " ".join(candidate.get("class_names") or []) or "-",
                "contenteditable": "예" if candidate.get("contenteditable") else "아니요",
            }
        )
    return rows


def _render_review(review: Mapping[str, Any], *, st_module=st) -> None:
    severity = str(review.get("severity") or "info")
    summary = str(review.get("summary") or "")
    if severity == "success":
        st_module.success(summary)
    elif severity == "warning":
        st_module.warning(summary)
    else:
        st_module.info(summary)

    metric_columns = st_module.columns(5)
    metric_columns[0].metric("판단", str(review.get("status") or "-"), border=True)
    metric_columns[1].metric("호스트", str(review.get("hostname") or "-"), border=True)
    metric_columns[2].metric(
        "예상·감지",
        f"{review.get('expected_platform') or '-'} · {review.get('detected_adapter') or '-'}",
        border=True,
    )
    metric_columns[3].metric(
        "보고 동작",
        "진단" if review.get("action") == "diagnose" else "입력",
        border=True,
    )
    metric_columns[4].metric(
        "문서·차단 iframe",
        f"{int(review.get('accessible_documents') or 0)}개 · {int(review.get('blocked_iframe_count') or 0)}개",
        border=True,
    )

    rows = _field_rows(review)
    if rows:
        st_module.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    candidate_rows = _candidate_rows(review)
    if candidate_rows:
        total_count = int(review.get("candidate_control_count") or len(candidate_rows))
        truncated = bool(review.get("candidate_controls_truncated"))
        with st_module.expander(
            f"개인정보 제외 구조 후보 {len(candidate_rows)}개 보기",
            expanded=False,
        ):
            st_module.caption(
                f"전체 감지 {total_count}개"
                + (" · 최대 40개만 보고서에 포함" if truncated else "")
                + " · 현재 값과 화면 본문은 포함하지 않음"
            )
            st_module.dataframe(
                pd.DataFrame(candidate_rows), hide_index=True, width="stretch"
            )

    reasons = [str(item) for item in review.get("reasons") or [] if str(item).strip()]
    if reasons:
        st_module.markdown("**판단 근거**")
        for reason in reasons:
            st_module.markdown(f"- {reason}")

    st_module.markdown("**다음 한 작업**")
    st_module.markdown(str(review.get("next_step") or "보고서를 다시 확인합니다."))


def render_chrome_compatibility_report_review(
    *,
    expected_platform: str,
    key_scope: str,
    st_module=st,
) -> None:
    state_key = f"chrome_compatibility_review_{key_scope}"
    input_key = f"chrome_compatibility_report_{key_scope}"

    with st_module.expander("Chrome 호환성 보고서 검사", expanded=False):
        st_module.caption(
            "확장 프로그램의 `호환성 보고서 복사` 결과만 붙여넣으세요. "
            "보고서는 DB에 저장하지 않으며 JSON 구조와 개인정보 제외 계약을 먼저 검사합니다."
        )
        report_text = st_module.text_area(
            "호환성 보고서 JSON",
            key=input_key,
            height=220,
            placeholder=(
                "티스토리·네이버·Blogger 편집기에서 입력칸 진단 또는 입력 후 "
                "복사한 JSON을 붙여넣으세요."
            ),
        )
        button_columns = st_module.columns(2)
        analyze = button_columns[0].button(
            "보고서 검사",
            type="primary",
            key=f"chrome_compatibility_analyze_{key_scope}",
            use_container_width=True,
        )
        clear = button_columns[1].button(
            "검사 결과 지우기",
            key=f"chrome_compatibility_clear_{key_scope}",
            use_container_width=True,
        )

        if clear:
            st_module.session_state.pop(state_key, None)

        if analyze:
            try:
                review: CompatibilityReportReview = review_chrome_compatibility_report(
                    report_text,
                    expected_platform=expected_platform,
                )
            except ValueError as exc:
                st_module.session_state.pop(state_key, None)
                st_module.error(str(exc))
            else:
                st_module.session_state[state_key] = asdict(review)

        saved_review = st_module.session_state.get(state_key)
        if isinstance(saved_review, Mapping):
            _render_review(saved_review, st_module=st_module)

        st_module.caption(
            "검사 결과는 현재 Streamlit 세션에만 남고 원본 초안·블로그 프로필·발행 기록은 변경하지 않습니다."
        )
