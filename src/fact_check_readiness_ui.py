"""초안별 사실 확인 준비도를 설정 진단 영역에 표시합니다."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.services.fact_check_readiness_service import get_fact_check_readiness


def _format_time(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else "기록 없음"


def _format_terms(values: object) -> str:
    terms = [str(value) for value in (values or []) if str(value).strip()]
    return " · ".join(terms[:6])


def render_fact_check_readiness(con, *, st_module=st) -> None:
    diagnostics = get_fact_check_readiness(con)
    st_module.subheader("사실 확인 준비도")

    draft_count = int(diagnostics["draft_count"])
    if draft_count == 0:
        st_module.caption(
            "아직 저장된 초안이 없습니다. AI 결과를 초안으로 저장하면 확인 대기·재확인·발행 준비 상태를 이곳에서 볼 수 있습니다."
        )
        return

    with st_module.container(horizontal=True):
        st_module.metric(
            "전체 초안",
            f"{draft_count:,}개",
            f"방치 주의 {int(diagnostics['abandoned_count']):,}개",
            help="현재 DB에 저장된 초안 수입니다. 미확인 항목이 7일 이상 남은 초안은 방치 주의로 표시합니다.",
            border=True,
        )
        st_module.metric(
            "확인 대기",
            f"{int(diagnostics['needs_verification_count']):,}개",
            help="현재 우선 상태가 확인 대기인 미발행 초안 수입니다. 발행 후 남은 항목은 별도 경고로 표시합니다.",
            border=True,
        )
        st_module.metric(
            "수정 필요",
            f"{int(diagnostics['needs_revision_count']):,}개",
            help="현재 우선 상태가 수정 필요인 미발행 초안 수입니다. 발행 후 남은 항목은 별도 경고로 표시합니다.",
            border=True,
        )
        st_module.metric(
            "재확인 필요",
            f"{int(diagnostics['recheck_due_draft_count']):,}개",
            help="현재 우선 상태가 재확인 필요인 미발행 초안 수입니다. 시점에 따라 달라지는 정보가 기준 시간을 넘긴 경우입니다.",
            border=True,
        )
        st_module.metric(
            "발행 준비",
            f"{int(diagnostics['ready_count']):,}개",
            help="등록된 사실 확인 항목이 모두 확인 완료이고 재확인 시점도 지나지 않은 미발행 초안 수입니다.",
            border=True,
        )
        st_module.metric(
            "확인 항목 없음",
            f"{int(diagnostics['no_checks_count']):,}개",
            f"시점 의존 표현 {int(diagnostics['time_sensitive_gap_count']):,}개",
            help="AI가 사실 확인 항목을 만들지 않은 초안입니다. 숫자·가격·정책·일정 같은 내용을 수동으로 검토해야 합니다.",
            border=True,
        )

    if int(diagnostics["published_attention_count"]) > 0:
        st_module.warning(
            f"이미 발행했지만 미확인·수정 필요·재확인 항목이 남은 초안이 "
            f"{int(diagnostics['published_attention_count']):,}개 있습니다. 발행된 글도 최신 근거와 함께 다시 확인하세요."
        )
    if int(diagnostics["verified_without_url_count"]) > 0:
        st_module.caption(
            f"확인 완료로 저장했지만 근거 URL이 비어 있는 항목이 "
            f"{int(diagnostics['verified_without_url_count']):,}개 있습니다. 확인 메모만으로 충분한 경우도 있으므로 자동 실패로 처리하지 않습니다."
        )

    frame = pd.DataFrame(
        [
            {
                "상태": str(row.get("readiness_label") or ""),
                "초안": str(row.get("title") or ""),
                "주제": str(row.get("topic_title") or ""),
                "버전": int(row.get("current_revision") or 0),
                "확인 항목": int(row.get("fact_check_count") or 0),
                "미확인": int(row.get("needs_verification_count") or 0)
                + int(row.get("unknown_status_count") or 0),
                "수정 필요": int(row.get("needs_revision_count") or 0),
                "확인 완료": int(row.get("verified_count") or 0),
                "재확인": int(row.get("recheck_due_count") or 0),
                "완료·URL 없음": int(row.get("verified_without_url_count") or 0),
                "시점 의존": _format_terms(
                    row.get("matched_time_sensitive_terms")
                    or row.get("draft_time_sensitive_terms")
                ),
                "방치": (
                    f"{int(row.get('unresolved_age_days') or 0)}일"
                    if row.get("is_abandoned")
                    else ""
                ),
                "최근 수정": _format_time(row.get("updated_at")),
                "마지막 발행": _format_time(row.get("last_published_at")),
                "다음 행동": str(row.get("next_action") or ""),
            }
            for row in diagnostics["rows"]
        ]
    )
    st_module.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        height=min(520, 80 + max(1, len(frame)) * 36),
    )
    st_module.caption(
        f"재확인 기준은 환율·주가·가격·날씨·스포츠 결과 같은 빠른 정보 {int(diagnostics['fast_recheck_hours'])}시간, "
        f"현재 직책·정책·마감 같은 비교적 느린 정보 {int(diagnostics['slow_recheck_hours']) // 24}일입니다. "
        "키워드 기반 보조 판정이므로 실제 공식 자료의 적용 시점과 변경 주기를 우선하세요."
    )
    st_module.caption(
        "이 화면은 기존 초안·사실 확인·발행 기록을 읽기만 합니다. 상태, 본문, 근거 URL, 주제와 발행 기록을 자동 변경하지 않습니다."
    )
