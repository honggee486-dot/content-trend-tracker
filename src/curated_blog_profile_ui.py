from __future__ import annotations

from html import escape

import duckdb
import streamlit as st

from src.blog_platform_presentation import (
    BLOG_PLATFORM_ORDER,
    BLOG_PLATFORM_PRESENTATION,
)
from src.blog_profile_readiness_ui import (
    render_blog_profile_readiness_summary,
    render_profile_readiness_status,
)
from src.services.blog_profile_service import (
    OUTPUT_FORMAT_LABELS,
    save_blog_profile,
)
from src.services.blog_editor_navigation_service import (
    BLOGGER_LOGIN_URL,
    NAVER_LOGIN_URL,
    TISTORY_LOGIN_URL,
    build_tistory_write_url,
    extract_tistory_blog_home_url,
    normalize_platform_editor_url,
)
from src.services.curated_blog_profile_service import (
    CuratedBlogProfileSyncResult,
)


def _render_platform_badge(platform: str, *, profile_name: str) -> None:
    presentation = BLOG_PLATFORM_PRESENTATION[platform]
    st.markdown(
        (
            '<div style="display:flex;align-items:center;gap:.6rem;margin:.15rem 0 .7rem 0;">'
            f'<span style="display:inline-flex;align-items:center;padding:.22rem .62rem;'
            f'border-radius:999px;background:{presentation.accent};color:white;'
            f'font-weight:750;font-size:.82rem;">{escape(presentation.label)}</span>'
            f'<strong style="font-size:1rem;">{escape(profile_name)}</strong>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )


def _current_editor_url(platform: str, value: object) -> str:
    try:
        return normalize_platform_editor_url(platform, value)
    except ValueError:
        return ""


def _render_profile_editor(
    con: duckdb.DuckDBPyConnection,
    profile: dict[str, object],
) -> None:
    profile_id = str(profile["blog_profile_id"])
    platform = str(profile.get("platform") or "")
    presentation = BLOG_PLATFORM_PRESENTATION[platform]
    default_mark = " · 기본 발행처" if bool(profile.get("is_default")) else ""
    with st.expander(
        f"{presentation.emoji} {profile.get('profile_name') or profile_id}{default_mark}",
        expanded=False,
    ):
        _render_platform_badge(
            platform,
            profile_name=str(profile.get("profile_name") or profile_id),
        )
        render_profile_readiness_status(profile=profile, st_module=st)
        st.caption(
            "로그인 주소는 자동 처리합니다. 아래 연결 주소와 기본 출력값만 확인하세요."
        )
        output_formats = list(OUTPUT_FORMAT_LABELS)
        current_output = str(profile.get("output_format") or "plain_text")
        current_output_index = (
            output_formats.index(current_output)
            if current_output in output_formats
            else 0
        )
        connection_value = ""
        with st.form(f"curated_blog_profile_{profile_id}"):
            profile_name = st.text_input(
                "프로필 이름",
                value=str(profile.get("profile_name") or ""),
                help="발행 보조 화면에서 구분할 이름입니다. 플랫폼이나 연결 방식은 바뀌지 않습니다.",
            )
            st.text_input(
                "플랫폼",
                value=str(presentation.label),
                disabled=True,
                help="고정 프로필의 플랫폼 종류는 변경할 수 없습니다.",
            )
            if platform == "tistory":
                connection_value = (
                    extract_tistory_blog_home_url(profile.get("write_url"))
                    or extract_tistory_blog_home_url(profile.get("login_url"))
                )
                connection_value = st.text_input(
                    "내 티스토리 블로그 주소",
                    value=connection_value,
                    placeholder="https://내블로그.tistory.com",
                    help=(
                        "티스토리 홈 주소 하나만 입력하세요. 저장하면 실제 새 글 주소인 "
                        "/manage/newpost를 자동으로 연결합니다."
                    ),
                )
                st.caption(
                    "예: `https://myblog.tistory.com` · 로그인 주소와 글쓰기 경로는 자동 처리합니다."
                )
            elif platform == "blogger":
                connection_value = st.text_input(
                    "Blogger 새 글 편집기 주소",
                    value=_current_editor_url(platform, profile.get("write_url")),
                    placeholder="https://www.blogger.com/blog/post/edit/...",
                    help=(
                        "이 프로필에 연결할 Blogger 블로그에서 ‘새 글’을 연 뒤 "
                        "주소창의 편집기 주소를 그대로 붙여넣으세요."
                    ),
                )
                st.caption(
                    "Blogger 3개는 서로 다른 블로그일 수 있으므로 각 프로필에 실제 새 글 편집기 주소를 저장합니다."
                )
            else:
                connection_value = st.text_input(
                    "네이버 새 글 편집기 주소",
                    value=_current_editor_url(platform, profile.get("write_url")),
                    placeholder="https://blog.naver.com/PostWriteForm.naver?...",
                    help=(
                        "네이버 블로그에서 ‘글쓰기’를 연 뒤 주소창의 편집기 주소를 "
                        "그대로 붙여넣으세요."
                    ),
                )
                st.caption(
                    "블로그 홈 주소가 아니라 제목과 본문 입력칸이 보이는 실제 글쓰기 화면 주소를 저장합니다."
                )

            output_format = st.selectbox(
                "기본 출력 형식",
                output_formats,
                index=current_output_index,
                format_func=OUTPUT_FORMAT_LABELS.get,
                help="이 플랫폼에 복사할 때 사용할 본문 형식입니다.",
            )
            default_category = st.text_input(
                "기본 카테고리 · 선택",
                value=str(profile.get("default_category") or ""),
                help="새 글을 준비할 때 먼저 제안할 카테고리입니다. 비워도 됩니다.",
            )
            default_tags = st.text_input(
                "기본 태그 · 쉼표로 구분",
                value=", ".join(profile.get("default_tags", [])),
                help="모든 글에 공통으로 제안할 태그입니다. 쉼표로 구분하세요.",
            )
            is_default = st.checkbox(
                "발행 보조에서 기본 선택",
                value=bool(profile.get("is_default")),
                help="발행 보조에 처음 들어왔을 때 이 프로필을 먼저 선택합니다.",
            )
            submitted = st.form_submit_button(
                "연결 정보 저장",
                type="primary",
                width="stretch",
                help="입력한 연결 주소와 기본값을 이 프로필에 저장합니다.",
            )
        if submitted:
            try:
                if platform == "tistory":
                    effective_login_url = TISTORY_LOGIN_URL
                    effective_write_url = build_tistory_write_url(connection_value)
                elif platform == "blogger":
                    effective_login_url = BLOGGER_LOGIN_URL
                    effective_write_url = normalize_platform_editor_url(
                        platform,
                        connection_value,
                    )
                else:
                    effective_login_url = NAVER_LOGIN_URL
                    effective_write_url = normalize_platform_editor_url(
                        platform,
                        connection_value,
                    )
                save_blog_profile(
                    con,
                    blog_profile_id=profile_id,
                    profile_name=profile_name,
                    platform=platform,
                    login_url=effective_login_url,
                    write_url=effective_write_url,
                    output_format=output_format,
                    default_category=default_category,
                    default_tags=default_tags,
                    is_default=is_default,
                )
                st.success("블로그 연결 정보를 저장했습니다.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def render_curated_blog_profile_settings(
    con: duckdb.DuckDBPyConnection,
    *,
    sync_result: CuratedBlogProfileSyncResult,
) -> None:
    st.markdown("#### 실제 블로그 프로필 연결")
    st.caption(
        "사용할 프로필만 남겼습니다. Blogger 3개, 네이버 블로그 1개, "
        "티스토리 1개만 연결·수정할 수 있습니다."
    )
    st.markdown(
        (
            '<div style="display:flex;flex-wrap:wrap;gap:.45rem;margin:.2rem 0 .8rem 0;">'
            '<span style="padding:.25rem .62rem;border-radius:999px;background:#4285F4;color:white;font-weight:750;">Blogger</span>'
            '<span style="padding:.25rem .62rem;border-radius:999px;background:#03C75A;color:white;font-weight:750;">네이버</span>'
            '<span style="padding:.25rem .62rem;border-radius:999px;background:#F97316;color:white;font-weight:750;">티스토리</span>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )
    render_blog_profile_readiness_summary(
        profiles=sync_result.profiles,
        st_module=st,
    )
    if sync_result.archived_profile_ids:
        st.info(
            f"불필요한 이전 프로필 {len(sync_result.archived_profile_ids)}개를 연결 목록에서 제거했습니다. "
            "과거 초안·발행 이력 보호를 위해 DB에는 비활성 상태로만 보관합니다."
        )
    if sync_result.migrated_naver_from_profile_id:
        st.info(
            "기존 네이버 프로필에 저장된 실제 연결 주소를 새 네이버 프로필로 이어받았습니다."
        )

    profiles_by_platform = {
        platform: [
            dict(profile)
            for profile in sync_result.profiles
            if str(profile.get("platform") or "") == platform
        ]
        for platform in BLOG_PLATFORM_ORDER
    }
    tabs = st.tabs(
        [BLOG_PLATFORM_PRESENTATION[platform].tab for platform in BLOG_PLATFORM_ORDER]
    )
    for tab, platform in zip(tabs, BLOG_PLATFORM_ORDER, strict=True):
        with tab:
            profiles = profiles_by_platform[platform]
            expected_count = 3 if platform == "blogger" else 1
            if len(profiles) != expected_count:
                st.warning(
                    f"{BLOG_PLATFORM_PRESENTATION[platform].label} 프로필 수를 확인하세요. "
                    f"현재 {len(profiles)}개 · 기대 {expected_count}개"
                )
            for profile in profiles:
                _render_profile_editor(con, profile)
