from __future__ import annotations

from typing import Any, Mapping

import duckdb
import streamlit as st

from src.blogger_preflight_ui import render_blogger_preflight
from src.services.blogger_draft_service import (
    DEFAULT_CLIENT_SECRET_PATH,
    DEFAULT_TOKEN_PATH,
    authorize_blogger_account,
    disconnect_blogger_account,
    ensure_blogger_draft_schema,
    get_blogger_connection_status,
    get_blogger_profile_binding,
    list_blogger_blogs,
    list_blogger_draft_uploads,
    save_blogger_profile_binding,
    upload_blogger_draft,
)


def render_blogger_draft_upload(
    con: duckdb.DuckDBPyConnection,
    *,
    draft: Mapping[str, Any],
    profile: Mapping[str, Any],
    package: Any,
    st_module=st,
) -> None:
    if str(profile.get("platform") or "") != "blogger":
        return

    ensure_blogger_draft_schema(con)
    draft_id = str(draft.get("draft_id") or "").strip()
    profile_id = str(profile.get("blog_profile_id") or "").strip()
    scope = f"{draft_id}_{profile_id}"
    blogs_key = f"blogger_api_blogs_{profile_id}"

    st_module.subheader("Blogger 공식 API 비공개 초안")
    st_module.caption(
        "사용자가 버튼을 눌렀을 때만 Blogger API의 초안 생성 기능을 호출합니다. "
        "게시 전환 API는 사용하지 않으며 최종 공개는 Blogger 화면에서 직접 수행합니다."
    )

    preflight = render_blogger_preflight(st_module=st_module)
    status = get_blogger_connection_status()
    status_columns = st_module.columns(3)
    status_columns[0].metric(
        "API 의존성",
        "준비됨" if status.dependency_ready else "설치 필요",
        border=True,
    )
    status_columns[1].metric(
        "OAuth 클라이언트",
        "준비됨" if status.client_secret_ready else "파일 필요",
        border=True,
    )
    status_columns[2].metric(
        "Google 계정",
        "연결됨" if status.token_ready else "연결 필요",
        border=True,
    )
    st_module.info(status.message)

    with st_module.expander("Blogger API 최초 설정", expanded=False):
        st_module.markdown(
            "1. Google Cloud에서 Blogger API를 활성화합니다.\n"
            "2. OAuth 동의 화면을 구성합니다.\n"
            "3. 애플리케이션 유형이 `데스크톱 앱`인 OAuth 클라이언트를 만듭니다.\n"
            "4. 다운로드한 JSON을 아래 경로에 `blogger_oauth_client.json` 이름으로 둡니다.\n"
            f"5. `{DEFAULT_CLIENT_SECRET_PATH}`\n"
            "6. 아래 `Google 계정 연결`을 누르고 일반 브라우저에서 직접 승인합니다."
        )
        st_module.caption(
            f"승인 토큰은 `{DEFAULT_TOKEN_PATH}`에만 저장되며 Git과 안전 ZIP에서 제외됩니다."
        )

    connect_columns = st_module.columns(3)
    if connect_columns[0].button(
        "Google 계정 연결" if not status.token_ready else "Google 계정 다시 연결",
        key=f"blogger_connect_{scope}",
        disabled=not preflight.ready_for_authorization,
        use_container_width=True,
    ):
        try:
            authorize_blogger_account()
        except Exception as exc:  # external OAuth boundary
            st_module.error(f"Blogger 계정 연결에 실패했습니다: {exc}")
        else:
            st_module.success("Blogger 계정 연결 토큰을 로컬에 저장했습니다.")
            st_module.session_state.pop(blogs_key, None)
            st_module.rerun()

    if connect_columns[1].button(
        "Blogger 블로그 목록 새로고침",
        key=f"blogger_refresh_blogs_{scope}",
        disabled=not preflight.ready_for_api,
        use_container_width=True,
    ):
        try:
            st_module.session_state[blogs_key] = list_blogger_blogs()
        except Exception as exc:  # external API boundary
            st_module.error(f"Blogger 블로그 목록을 가져오지 못했습니다: {exc}")
        else:
            st_module.success("현재 Google 계정의 Blogger 블로그 목록을 불러왔습니다.")

    if connect_columns[2].button(
        "로컬 계정 연결 해제",
        key=f"blogger_disconnect_{scope}",
        disabled=not status.token_ready,
        use_container_width=True,
    ):
        if disconnect_blogger_account():
            st_module.session_state.pop(blogs_key, None)
            st_module.success("로컬 OAuth 토큰을 삭제했습니다. Google 계정의 권한 철회는 별도로 수행하세요.")
            st_module.rerun()

    binding = get_blogger_profile_binding(con, blog_profile_id=profile_id)
    blogs = st_module.session_state.get(blogs_key)
    if isinstance(blogs, list) and blogs:
        options = [str(item.get("id") or "") for item in blogs if item.get("id")]
        labels = {
            str(item.get("id") or ""): (
                f"{item.get('name') or item.get('id')} · {item.get('url') or '주소 없음'}"
            )
            for item in blogs
            if item.get("id")
        }
        current_id = str((binding or {}).get("blogger_blog_id") or "")
        default_index = options.index(current_id) if current_id in options else 0
        selected_blog_id = st_module.selectbox(
            "연결할 Blogger 블로그",
            options,
            index=default_index,
            format_func=lambda value: labels.get(value, value),
            key=f"blogger_blog_select_{scope}",
        )
        selected = next(item for item in blogs if str(item.get("id")) == selected_blog_id)
        if st_module.button(
            "이 Blogger 블로그를 발행 프로필에 연결",
            key=f"blogger_save_binding_{scope}",
            use_container_width=True,
        ):
            save_blogger_profile_binding(
                con,
                blog_profile_id=profile_id,
                blogger_blog_id=selected_blog_id,
                blogger_blog_name=str(selected.get("name") or selected_blog_id),
                blogger_blog_url=str(selected.get("url") or ""),
            )
            st_module.success("Blogger 발행 프로필 연결을 저장했습니다.")
            st_module.rerun()
    elif binding:
        st_module.info(
            "연결된 Blogger 블로그: "
            f"{binding.get('blogger_blog_name') or binding.get('blogger_blog_id')}"
        )
    else:
        st_module.caption("계정 연결 후 블로그 목록을 불러와 이 발행 프로필에 연결하세요.")

    binding = get_blogger_profile_binding(con, blog_profile_id=profile_id)
    if binding and preflight.ready_for_api:
        st_module.markdown("**비공개 초안 전송**")
        st_module.caption(
            "현재 SEO 제목·플랫폼용 본문·출력 태그를 Blogger 초안으로 보냅니다. "
            "동일한 내용은 기존 전송 기록을 재사용해 중복 초안을 만들지 않습니다."
        )
        confirmed = st_module.checkbox(
            "공개 게시가 아니라 비공개 초안만 생성됨을 확인했습니다.",
            key=f"blogger_draft_confirm_{scope}",
        )
        if st_module.button(
            "Blogger 비공개 초안 전송",
            type="primary",
            key=f"blogger_upload_draft_{scope}",
            disabled=not confirmed,
            use_container_width=True,
        ):
            try:
                result = upload_blogger_draft(
                    con,
                    draft=draft,
                    profile=profile,
                    package=package,
                    blogger_blog_id=str(binding["blogger_blog_id"]),
                )
            except Exception as exc:  # external API boundary
                st_module.error(f"Blogger 비공개 초안 전송에 실패했습니다: {exc}")
            else:
                if result.reused:
                    st_module.info(
                        "동일한 내용으로 이미 만든 Blogger 초안 기록을 재사용했습니다. "
                        f"게시물 ID: {result.blogger_post_id}"
                    )
                else:
                    st_module.success(
                        "Blogger에 비공개 초안을 만들었습니다. "
                        f"게시물 ID: {result.blogger_post_id}"
                    )

    uploads = list_blogger_draft_uploads(
        con,
        draft_id=draft_id,
        blog_profile_id=profile_id,
    )
    if uploads:
        with st_module.expander("Blogger 비공개 초안 전송 기록", expanded=False):
            st_module.dataframe(
                [
                    {
                        "전송 시각": item.get("updated_at"),
                        "제목": item.get("title_snapshot"),
                        "Blogger 게시물 ID": item.get("blogger_post_id"),
                        "상태": item.get("status"),
                    }
                    for item in uploads
                ],
                hide_index=True,
                width="stretch",
            )

    st_module.warning(
        "이 기능은 Blogger 비공개 초안 생성만 지원합니다. 공개 게시·예약 게시·자동 로그인·쿠키 저장은 수행하지 않습니다."
    )
