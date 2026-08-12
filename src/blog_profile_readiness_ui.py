from __future__ import annotations

from html import escape
from typing import Iterable, Mapping

import streamlit as st

from src.blog_platform_presentation import (
    BLOG_PLATFORM_ORDER,
    BLOG_PLATFORM_PRESENTATION,
)
from src.services.blog_profile_readiness_service import (
    BlogProfileReadiness,
    BlogProfileReadinessSummary,
    INVALID_STATUS,
    MISSING_STATUS,
    evaluate_blog_profile_readiness,
    summarize_blog_profile_readiness,
)


_STATUS_LABELS = {
    "ready": "연결 완료",
    MISSING_STATUS: "연결 필요",
    INVALID_STATUS: "주소 확인 필요",
}
_STATUS_COLORS = {
    "ready": ("#15803D", "rgba(21, 128, 61, 0.10)"),
    MISSING_STATUS: ("#D97706", "rgba(217, 119, 6, 0.10)"),
    INVALID_STATUS: ("#DC2626", "rgba(220, 38, 38, 0.10)"),
}


def _status_badge(item: BlogProfileReadiness) -> str:
    color, background = _STATUS_COLORS[item.status]
    return (
        f'<span style="font-size:.72rem;color:{color};background:{background};'
        'padding:.14rem .42rem;border-radius:999px;font-weight:850;">'
        f'{escape(_STATUS_LABELS[item.status])}</span>'
    )


def render_profile_readiness_status(
    *,
    profile: Mapping[str, object],
    st_module=st,
) -> BlogProfileReadiness:
    item = evaluate_blog_profile_readiness(profile)
    presentation = BLOG_PLATFORM_PRESENTATION.get(item.platform)
    accent = presentation.accent if presentation else "#64748B"
    st_module.markdown(
        (
            f'<div style="border-left:4px solid {accent};padding:.42rem .65rem;'
            'margin:.2rem 0 .75rem 0;background:rgba(128,128,128,.05);'
            'border-radius:0 8px 8px 0;">'
            '<div style="display:flex;align-items:center;gap:.45rem;flex-wrap:wrap;">'
            f'<strong>{escape(item.profile_name)}</strong>{_status_badge(item)}'
            '</div>'
            f'<div style="font-size:.76rem;opacity:.78;margin-top:.16rem;">'
            f'{escape(item.message)}</div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )
    return item


def render_blog_profile_readiness_summary(
    *,
    profiles: Iterable[Mapping[str, object]],
    st_module=st,
) -> BlogProfileReadinessSummary:
    summary = summarize_blog_profile_readiness(profiles)
    st_module.markdown("##### 연결 준비도")
    st_module.caption(
        "실제 브라우저 검증 전에 5개 프로필의 글쓰기 연결 주소가 준비됐는지 확인합니다. "
        "외부 접속이나 로그인은 수행하지 않고 저장된 주소 형식만 읽습니다."
    )

    overall, blogger, naver, tistory = st_module.columns(4)
    overall.metric(
        "전체 연결",
        f"{summary.ready_count}/{summary.total_count}",
        border=True,
    )
    platform_columns = {
        "blogger": blogger,
        "naver_blog": naver,
        "tistory": tistory,
    }
    for platform in BLOG_PLATFORM_ORDER:
        ready_count, total_count = summary.platform_counts(platform)
        presentation = BLOG_PLATFORM_PRESENTATION[platform]
        platform_columns[platform].metric(
            f"{presentation.emoji} {presentation.short_label}",
            f"{ready_count}/{total_count}",
            border=True,
        )

    if summary.is_fully_ready:
        st_module.success(
            "고정 블로그 프로필 5개가 모두 연결 준비 완료 상태입니다. "
            "이제 발행 보조에서 실제 편집기 이동을 순서대로 확인하세요."
        )
    else:
        st_module.warning(
            f"연결 완료 {summary.ready_count}개 · 미설정 {summary.missing_count}개 · "
            f"주소 확인 필요 {summary.invalid_count}개입니다. "
            "아래 미완료 프로필부터 연결 정보를 저장하세요."
        )
        for item in summary.items:
            if item.is_ready:
                continue
            presentation = BLOG_PLATFORM_PRESENTATION.get(item.platform)
            emoji = presentation.emoji if presentation else "⚪"
            st_module.markdown(
                f"- {emoji} **{item.profile_name}** · "
                f"`{_STATUS_LABELS[item.status]}` — {item.recommended_action}"
            )

    with st_module.expander("프로필별 연결 상태 전체 보기", expanded=False):
        for item in summary.items:
            presentation = BLOG_PLATFORM_PRESENTATION.get(item.platform)
            accent = presentation.accent if presentation else "#64748B"
            st_module.markdown(
                (
                    f'<div style="border-left:4px solid {accent};padding:.5rem .65rem;'
                    'margin:.35rem 0;background:rgba(128,128,128,.04);'
                    'border-radius:0 8px 8px 0;">'
                    '<div style="display:flex;align-items:center;gap:.45rem;flex-wrap:wrap;">'
                    f'<strong>{escape(item.profile_name)}</strong>{_status_badge(item)}'
                    '</div>'
                    f'<div style="font-size:.76rem;opacity:.78;margin-top:.16rem;">'
                    f'{escape(item.message)}</div>'
                    '</div>'
                ),
                unsafe_allow_html=True,
            )

    return summary
