from __future__ import annotations

from typing import Mapping

import streamlit as st

from src.blog_platform_presentation import get_blog_platform_presentation
from src.blog_profile_readiness_ui import render_profile_readiness_status
from src.publish_flow_ui import render_publish_step_header
from src.services.blog_editor_navigation_service import (
    BlogEditorNavigationTarget,
    resolve_blog_editor_navigation,
)
from src.services.browser_service import open_in_regular_chrome


def render_blog_editor_navigation(
    *,
    profile: Mapping[str, object],
    st_module=st,
) -> BlogEditorNavigationTarget:
    target = resolve_blog_editor_navigation(profile)
    profile_id = str(profile.get("blog_profile_id") or target.platform)
    presentation = get_blog_platform_presentation(target.platform)

    render_publish_step_header(
        number=3,
        title="글쓰기 편집기 열기",
        requirement="필수",
        detail=(
            f"{target.platform_label} 홈이 아니라 새 글 편집기를 엽니다. "
            "로그인 화면이 나오면 로그인 후 같은 버튼을 다시 누르세요."
        ),
        profile=profile,
        st_module=st_module,
    )
    readiness = render_profile_readiness_status(
        profile=profile,
        st_module=st_module,
    )
    if not readiness.is_ready:
        st_module.warning(readiness.recommended_action)
        st_module.code(
            "설정 → 발행 채널 → 해당 프로필 → 연결 정보 저장",
            language=None,
        )
        return target

    button_key = f"open_blog_editor_{profile_id}"
    st_module.markdown(
        (
            "<style>"
            f".st-key-{button_key} button {{"
            f"background:{presentation.accent} !important;"
            "border-color:transparent !important;color:white !important;"
            "font-weight:800 !important;}"
            "</style>"
        ),
        unsafe_allow_html=True,
    )
    if st_module.button(
        f"필수 · {target.action_label}",
        type="primary",
        width="stretch",
        key=button_key,
        help=target.action_help,
    ):
        try:
            st_module.success(open_in_regular_chrome(target.write_url))
        except Exception as exc:
            st_module.error(str(exc))

    with st_module.expander(
        f"선택 · {target.platform_label} 주소 확인과 로그인 문제 해결",
        expanded=False,
    ):
        st_module.caption("실제로 열릴 새 글 편집기 주소")
        st_module.code(target.write_url, language=None)
        st_module.markdown(
            f"""- 로그인 화면이 나오면 로그인한 뒤 `필수 · {target.action_label}`을 다시 누릅니다.
- 홈이나 글 목록이 열리면 설정에 저장한 새 글 편집기 주소를 다시 확인합니다.
- 편집기가 열렸다면 이 화면으로 돌아와 4단계를 진행합니다."""
        )
        if target.login_url:
            if st_module.button(
                "선택 · 로그인 화면만 열기",
                type="secondary",
                width="stretch",
                key=f"open_blog_login_{profile_id}",
                help=(
                    "글쓰기 바로 열기가 로그인 문제로 진행되지 않을 때만 사용합니다. "
                    "로그인 완료 후 필수 글쓰기 버튼을 다시 누르세요."
                ),
            ):
                try:
                    st_module.success(open_in_regular_chrome(target.login_url))
                except Exception as exc:
                    st_module.error(str(exc))

    return target
