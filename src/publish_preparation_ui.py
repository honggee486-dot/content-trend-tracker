from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import duckdb
import streamlit as st

from src.blog_editor_navigation_ui import render_blog_editor_navigation
from src.blog_platform_presentation import get_blog_platform_presentation
from src.publish_flow_ui import (
    render_publish_flow_overview,
    render_publish_platform_banner,
    render_publish_step_header,
)
from src.blogger_draft_ui import render_blogger_draft_upload
from src.chrome_compatibility_report_ui import (
    render_chrome_compatibility_report_review,
)
from src.services.chrome_extension_handoff_service import (
    build_chrome_extension_handoff,
)
from src.services.blog_channel_strategy_service import list_blog_channel_strategies
from src.services.publish_preparation_service import (
    PublishCopyPackage,
    build_default_publish_preparation,
    build_publish_copy_package,
    get_publish_preparation,
    save_publish_preparation,
)
from src.ui import render_copy_button


@dataclass(frozen=True)
class PublishPreparationState:
    strategy: dict[str, Any] | None
    saved: dict[str, Any] | None
    effective: dict[str, Any]
    package: PublishCopyPackage


def _strategy_for_profile(
    con: duckdb.DuckDBPyConnection,
    blog_profile_id: str,
) -> dict[str, Any] | None:
    for strategy in list_blog_channel_strategies(con, active_only=False):
        if str(strategy.get("blog_profile_id") or "") == blog_profile_id:
            return dict(strategy)
    return None


def build_publish_preparation_state(
    con: duckdb.DuckDBPyConnection,
    *,
    draft: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> PublishPreparationState:
    profile_id = str(profile.get("blog_profile_id") or "").strip()
    strategy = _strategy_for_profile(con, profile_id)
    defaults = build_default_publish_preparation(draft, profile, strategy)
    saved = get_publish_preparation(
        con,
        draft_id=str(draft.get("draft_id") or ""),
        blog_profile_id=profile_id,
    )
    effective = dict(defaults)
    if saved:
        effective.update(saved)
    package = build_publish_copy_package(
        draft=draft,
        profile=profile,
        strategy=strategy,
        preparation=effective,
    )
    return PublishPreparationState(
        strategy=strategy,
        saved=saved,
        effective=effective,
        package=package,
    )


def _split_keywords(value: str) -> list[str]:
    return [
        item.strip().lstrip("#")
        for item in str(value or "").replace("\n", ",").split(",")
        if item.strip().lstrip("#")
    ]


def render_publish_preparation(
    con: duckdb.DuckDBPyConnection,
    *,
    draft: Mapping[str, Any],
    profile: Mapping[str, Any],
    st_module=st,
) -> PublishCopyPackage:
    state = build_publish_preparation_state(con, draft=draft, profile=profile)
    draft_id = str(draft.get("draft_id") or "").strip()
    profile_id = str(profile.get("blog_profile_id") or "").strip()
    scope = f"{draft_id}_{profile_id}"
    policy = state.package.policy

    presentation = get_blog_platform_presentation(profile.get("platform"))
    render_publish_platform_banner(profile=profile, st_module=st_module)
    render_publish_flow_overview(profile=profile, st_module=st_module)
    render_publish_step_header(
        number=1,
        title="SEO·이미지 준비값 확인",
        requirement="조건부",
        detail=(
            "기본값을 그대로 사용할 수 있습니다. 제목·설명·키워드나 이미지 계획을 "
            "수정했을 때만 저장 버튼을 누르세요."
        ),
        profile=profile,
        st_module=st_module,
    )
    metric_columns = st_module.columns(4)
    metric_columns[0].metric("출력 정책", policy.label, border=True)
    metric_columns[1].metric(
        "SEO 제목 권장",
        f"{policy.seo_title_max_length}자 이내",
        border=True,
    )
    metric_columns[2].metric(
        "메타 설명 권장",
        f"{policy.meta_description_max_length}자 이내",
        border=True,
    )
    metric_columns[3].metric(
        "이미지 슬롯",
        "3개",
        border=True,
    )

    if state.saved:
        st_module.info(
            f"이 초안과 발행처에 저장된 준비 정보를 불러왔습니다. 최근 저장: {state.saved.get('updated_at') or '-'}"
        )
    else:
        st_module.info(
            "저장된 준비 정보가 없어 초안 제목·요약·태그와 채널 전략으로 기본값을 만들었습니다."
        )

    effective_slots = list(state.package.image_slots)
    with st_module.form(f"publish_preparation_form_{scope}"):
        seo_title = st_module.text_input(
            "SEO 제목",
            value=state.package.seo_title,
            help="플랫폼에 붙여넣을 최종 제목입니다. 원본 초안 제목은 바뀌지 않습니다.",
        )
        meta_description = st_module.text_area(
            "검색 설명·메타 설명",
            value=state.package.meta_description,
            height=90,
            help="검색 결과나 발행 설명에 사용할 짧은 요약입니다.",
        )
        focus_keywords_text = st_module.text_input(
            "핵심 키워드 · 쉼표로 구분",
            value=", ".join(state.package.focus_keywords),
            help="태그와 검색 설명에 활용할 핵심어입니다. 쉼표로 구분하세요.",
        )

        st_module.markdown("**이미지 3개 배치 계획**")
        image_slots: list[dict[str, Any]] = []
        for slot in effective_slots:
            number = int(slot.get("slot_number") or len(image_slots) + 1)
            role = str(slot.get("role") or f"이미지 {number}")
            position = str(slot.get("position") or "본문 중간")
            st_module.caption(f"이미지 {number} · {role} · 권장 위치: {position}")
            alt_column, note_column = st_module.columns(2)
            alt_text = alt_column.text_input(
                f"이미지 {number} 대체텍스트",
                value=str(slot.get("alt_text") or ""),
                key=f"publish_image_alt_{scope}_{number}",
                help="이미지를 볼 수 없을 때 대신 설명할 짧은 문장입니다.",
            )
            note = note_column.text_input(
                f"이미지 {number} 제작·선택 메모",
                value=str(slot.get("note") or ""),
                key=f"publish_image_note_{scope}_{number}",
                help="직접 이미지를 만들거나 고를 때 참고할 내부 메모입니다.",
            )
            image_slots.append(
                {
                    "slot_number": number,
                    "role": role,
                    "position": position,
                    "alt_text": alt_text,
                    "note": note,
                }
            )

        submitted = st_module.form_submit_button(
            "조건부 · 변경한 준비 내용 저장",
            type="secondary",
            help=(
                "기본값을 수정한 경우에만 누르세요. 변경하지 않았다면 2단계로 바로 진행해도 됩니다."
            ),
        )

    focus_keywords = _split_keywords(focus_keywords_text)
    live_preparation = {
        "seo_title": seo_title,
        "meta_description": meta_description,
        "focus_keywords": focus_keywords,
        "image_slots": image_slots,
    }
    package = build_publish_copy_package(
        draft=draft,
        profile=profile,
        strategy=state.strategy,
        preparation=live_preparation,
    )

    if submitted:
        try:
            save_publish_preparation(
                con,
                draft=draft,
                profile=profile,
                seo_title=seo_title,
                meta_description=meta_description,
                focus_keywords=focus_keywords,
                image_slots=image_slots,
            )
        except ValueError as exc:
            st_module.error(str(exc))
        else:
            st_module.success("이 초안과 발행처의 SEO·이미지 준비 정보를 저장했습니다.")
            st_module.rerun()

    with st_module.expander(
        "선택 · 세부 확인과 수동 복사 도구",
        expanded=False,
    ):
        st_module.caption(
            "아래 지표·미리보기·개별 복사 버튼은 기본 발행 순서에 필요하지 않습니다. "
            "Chrome 확장 입력이 어렵거나 일부 항목만 따로 복사할 때 사용하세요."
        )
        length_columns = st_module.columns(3)
        length_columns[0].metric(
            "현재 SEO 제목",
            f"{len(package.seo_title):,}자",
            delta=f"권장 {policy.seo_title_max_length}자",
            delta_color="off",
            border=True,
        )
        length_columns[1].metric(
            "현재 메타 설명",
            f"{len(package.meta_description):,}자",
            delta=f"권장 {policy.meta_description_max_length}자",
            delta_color="off",
            border=True,
        )
        length_columns[2].metric(
            "출력 태그",
            f"{len(package.output_tags):,}개",
            delta=f"권장 최대 {policy.recommended_tag_count}개",
            delta_color="off",
            border=True,
        )
        for warning in package.warnings:
            st_module.warning(warning)

        with st_module.expander("이미지 3개 배치 안내", expanded=False):
            st_module.code(package.image_guide_text, language=None)
        with st_module.expander("플랫폼용 본문 미리보기", expanded=False):
            st_module.code(
                package.output_body,
                language="markdown"
                if str(profile.get("output_format") or "") == "markdown"
                else None,
            )
        with st_module.expander("전체 발행 패키지 미리보기", expanded=False):
            st_module.code(package.full_output_text, language=None)

        copy_row_one = st_module.columns(3)
        with copy_row_one[0]:
            render_copy_button(
                "SEO 제목 복사",
                package.seo_title,
                key=f"publish_seo_title_{scope}",
            )
        with copy_row_one[1]:
            render_copy_button(
                "메타 설명 복사",
                package.meta_description,
                key=f"publish_meta_description_{scope}",
            )
        with copy_row_one[2]:
            render_copy_button(
                "핵심 키워드 복사",
                ", ".join(package.focus_keywords),
                key=f"publish_focus_keywords_{scope}",
            )

        copy_row_two = st_module.columns(3)
        with copy_row_two[0]:
            render_copy_button(
                "본문 복사",
                package.output_body,
                key=f"publish_body_{scope}",
            )
        with copy_row_two[1]:
            render_copy_button(
                "태그 복사",
                " ".join(f"#{tag}" for tag in package.output_tags),
                key=f"publish_tags_{scope}",
            )
        with copy_row_two[2]:
            render_copy_button(
                "전체 발행 패키지 복사",
                package.full_output_text,
                key=f"publish_package_{scope}",
            )

    try:
        handoff = build_chrome_extension_handoff(
            draft=draft,
            profile=profile,
            package=package,
        )
    except ValueError as exc:
        st_module.warning(f"Chrome 확장 전달 데이터를 만들 수 없습니다: {exc}")
    else:
        st_module.subheader("Chrome 편집기 입력 보조")
        render_publish_step_header(
            number=2,
            title="전달 데이터 복사",
            requirement="필수",
            detail="Chrome 확장이 사용할 10분 유효 데이터를 반드시 복사합니다.",
            profile=profile,
            st_module=st_module,
        )
        st_module.caption(
            f"{presentation.emoji} {presentation.short_label}용 제목·본문·태그·검색 설명을 복사합니다."
        )
        handoff_columns = st_module.columns(2)
        with handoff_columns[0]:
            render_copy_button(
                "필수 ② 전달 데이터 복사",
                handoff.serialized,
                key=f"chrome_extension_handoff_{scope}",
            )
        handoff_columns[1].metric(
            "전달 데이터 만료",
            handoff.expires_at,
            help="10분이 지나면 이 화면에서 전달 데이터를 다시 복사해야 합니다.",
            border=True,
        )

        render_blog_editor_navigation(
            profile=profile,
            st_module=st_module,
        )

        render_publish_step_header(
            number=4,
            title="확장에서 불러오기·진단·입력",
            requirement="필수",
            detail="글쓰기 탭에서 아래 세 버튼을 순서대로 실행합니다.",
            profile=profile,
            st_module=st_module,
        )
        st_module.markdown(
            """1. **클립보드에서 불러오기**
2. **입력칸 진단**
3. **현재 편집기에 입력**"""
        )
        with st_module.expander(
            "선택 · 처음 사용하는 경우 Chrome 확장 설치 방법",
            expanded=False,
        ):
            st_module.markdown(
                """1. Chrome 주소창에 `chrome://extensions`를 입력합니다.
2. 오른쪽 위 `개발자 모드`를 켭니다.
3. `압축해제된 확장 프로그램을 로드합니다`를 누릅니다.
4. 프로젝트 루트의 `chrome_extension` 폴더를 선택합니다.
5. 확장 아이콘을 도구 모음에 고정합니다."""
            )

        render_publish_step_header(
            number=5,
            title="입력 결과 확인 후 저장·발행",
            requirement="필수",
            detail="제목·본문·태그와 이미지 위치를 직접 확인한 뒤 저장 또는 발행합니다.",
            profile=profile,
            st_module=st_module,
        )
        st_module.warning(
            "확장은 게시·임시저장 버튼을 누르지 않습니다. 최종 저장과 발행은 반드시 사용자가 직접 수행합니다."
        )
        with st_module.expander(
            "선택 · 입력 오류가 있을 때만 호환성 보고서 검사",
            expanded=False,
        ):
            st_module.caption(
                "확장의 ‘호환성 보고서 복사’ 결과를 아래 검사기에 붙여넣으세요."
            )
            render_chrome_compatibility_report_review(
                expected_platform=str(profile.get("platform") or ""),
                key_scope=scope,
                st_module=st_module,
            )

    if str(profile.get("platform") or "") == "blogger":
        with st_module.expander(
            "선택 · Blogger API로 비공개 초안 만들기",
            expanded=False,
        ):
            st_module.caption(
                "수동 글쓰기 흐름과 별개의 선택 기능입니다. 공개 게시가 아니라 비공개 초안만 만듭니다."
            )
            render_blogger_draft_upload(
                con,
                draft=draft,
                profile=profile,
                package=package,
                st_module=st_module,
            )

    return package
