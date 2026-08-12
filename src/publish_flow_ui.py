from __future__ import annotations

from html import escape
from typing import Mapping

import streamlit as st

from src.blog_platform_presentation import get_blog_platform_presentation


_FLOW_STEPS = (
    ("1", "준비값 확인", "수정한 경우 저장", "조건부"),
    ("2", "전달 데이터 복사", "확장 입력 전에 반드시 실행", "필수"),
    ("3", "글쓰기 편집기 열기", "선택한 블로그의 새 글 화면 열기", "필수"),
    ("4", "확장에서 불러오기·진단·입력", "세 버튼을 순서대로 실행", "필수"),
    ("5", "직접 확인 후 저장·발행", "최종 저장과 발행은 사용자가 실행", "필수"),
)


def _requirement_badge(requirement: str) -> tuple[str, str]:
    if requirement == "필수":
        return "#DC2626", "rgba(220, 38, 38, 0.10)"
    return "#D97706", "rgba(217, 119, 6, 0.10)"


def render_publish_platform_banner(
    *,
    profile: Mapping[str, object],
    st_module=st,
) -> None:
    presentation = get_blog_platform_presentation(profile.get("platform"))
    profile_name = str(
        profile.get("profile_name")
        or profile.get("blog_profile_id")
        or presentation.short_label
    )
    st_module.markdown(
        (
            f'<div style="border:1px solid {presentation.accent};'
            f'border-left:7px solid {presentation.accent};'
            f'background:{presentation.soft_background};'
            'border-radius:12px;padding:.8rem 1rem;margin:.2rem 0 .8rem 0;">'
            f'<div style="font-size:.78rem;font-weight:800;color:{presentation.accent};">'
            f'{presentation.emoji} {escape(presentation.label)}</div>'
            f'<div style="font-size:1.08rem;font-weight:800;margin-top:.15rem;">'
            f'{escape(profile_name)}</div>'
            '<div style="font-size:.78rem;opacity:.76;margin-top:.15rem;">'
            '아래 단계와 필수 버튼은 선택한 발행처 색상으로 표시됩니다.</div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )


def render_publish_flow_overview(
    *,
    profile: Mapping[str, object],
    st_module=st,
) -> None:
    presentation = get_blog_platform_presentation(profile.get("platform"))
    rows = []
    for number, title, detail, requirement in _FLOW_STEPS:
        badge_color, badge_background = _requirement_badge(requirement)
        rows.append(
            (
                '<div style="display:flex;align-items:center;gap:.55rem;'
                'padding:.42rem .5rem;border-bottom:1px solid rgba(128,128,128,.14);">'
                f'<span style="display:inline-flex;align-items:center;justify-content:center;'
                f'width:1.55rem;height:1.55rem;border-radius:999px;background:{presentation.accent};'
                'color:white;font-size:.76rem;font-weight:850;">'
                f'{number}</span>'
                f'<strong style="min-width:10rem;">{escape(title)}</strong>'
                f'<span style="font-size:.74rem;color:{badge_color};background:{badge_background};'
                'padding:.14rem .42rem;border-radius:999px;font-weight:800;">'
                f'{requirement}</span>'
                f'<span style="font-size:.76rem;opacity:.72;">{escape(detail)}</span>'
                '</div>'
            )
        )
    st_module.markdown(
        (
            f'<div style="border:1px solid rgba(128,128,128,.22);'
            f'border-top:4px solid {presentation.accent};border-radius:12px;'
            'padding:.25rem .5rem;margin:0 0 1rem 0;">'
            '<div style="font-weight:850;padding:.45rem .5rem .35rem .5rem;">'
            '발행 순서</div>'
            + "".join(rows)
            + '</div>'
        ),
        unsafe_allow_html=True,
    )


def render_publish_step_header(
    *,
    number: int,
    title: str,
    requirement: str,
    detail: str,
    profile: Mapping[str, object],
    st_module=st,
) -> None:
    presentation = get_blog_platform_presentation(profile.get("platform"))
    badge_color, badge_background = _requirement_badge(requirement)
    st_module.markdown(
        (
            f'<div style="border-left:5px solid {presentation.accent};'
            f'background:{presentation.soft_background};border-radius:0 10px 10px 0;'
            'padding:.62rem .8rem;margin:1rem 0 .45rem 0;">'
            '<div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;">'
            f'<strong style="font-size:1rem;">{number}단계 · {escape(title)}</strong>'
            f'<span style="font-size:.72rem;color:{badge_color};background:{badge_background};'
            'padding:.14rem .42rem;border-radius:999px;font-weight:850;">'
            f'{escape(requirement)}</span>'
            '</div>'
            f'<div style="font-size:.77rem;opacity:.76;margin-top:.18rem;">{escape(detail)}</div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )
