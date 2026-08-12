from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import duckdb
import streamlit as st

from src.services.blog_channel_strategy_service import (
    BlogChannelRecommendation,
    get_draft_blog_assignment,
    install_default_blog_channels,
    list_managed_blog_channel_strategies,
    recommend_blog_channel,
    save_draft_blog_assignment,
)


MANAGED_CHANNEL_COUNT = 4


@dataclass(frozen=True)
class PublishChannelState:
    recommendation: BlogChannelRecommendation | None
    assignment: dict[str, Any] | None
    selected_profile_id: str
    active_strategy_count: int
    managed_strategy_count: int
    strategy_by_profile_id: dict[str, dict[str, Any]]

    @property
    def is_ready(self) -> bool:
        return self.active_strategy_count >= MANAGED_CHANNEL_COUNT


def _profile_ids(profiles: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        str(profile.get("blog_profile_id") or "").strip()
        for profile in profiles
        if str(profile.get("blog_profile_id") or "").strip()
    ]


def _default_profile_id(profiles: Sequence[Mapping[str, Any]]) -> str:
    for profile in profiles:
        if bool(profile.get("is_default")):
            return str(profile.get("blog_profile_id") or "").strip()
    profile_ids = _profile_ids(profiles)
    return profile_ids[0] if profile_ids else ""


def build_publish_channel_state(
    con: duckdb.DuckDBPyConnection,
    *,
    draft: Mapping[str, Any],
    profiles: Sequence[Mapping[str, Any]],
) -> PublishChannelState:
    active_strategies = list_managed_blog_channel_strategies(
        con, active_only=True
    )
    managed_strategies = list_managed_blog_channel_strategies(
        con, active_only=False
    )
    recommendation = recommend_blog_channel(draft, active_strategies)
    draft_id = str(draft.get("draft_id") or "").strip()
    assignment = get_draft_blog_assignment(con, draft_id) if draft_id else None

    profile_ids = _profile_ids(profiles)
    valid_profile_ids = set(profile_ids)
    assigned_profile_id = str(
        (assignment or {}).get("selected_blog_profile_id") or ""
    ).strip()
    recommended_profile_id = str(
        recommendation.blog_profile_id if recommendation is not None else ""
    ).strip()

    selected_profile_id = ""
    for candidate in (
        assigned_profile_id,
        recommended_profile_id,
        _default_profile_id(profiles),
    ):
        if candidate and candidate in valid_profile_ids:
            selected_profile_id = candidate
            break

    strategy_by_profile_id = {
        str(strategy.get("blog_profile_id") or ""): dict(strategy)
        for strategy in active_strategies
        if str(strategy.get("blog_profile_id") or "").strip()
    }
    return PublishChannelState(
        recommendation=recommendation,
        assignment=assignment,
        selected_profile_id=selected_profile_id,
        active_strategy_count=len(active_strategies),
        managed_strategy_count=len(managed_strategies),
        strategy_by_profile_id=strategy_by_profile_id,
    )


def build_channel_strategy_rows(
    strategies: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for strategy in strategies:
        rows.append(
            {
                "채널": str(strategy.get("profile_name") or ""),
                "플랫폼": str(strategy.get("platform") or ""),
                "상태": "사용" if bool(strategy.get("is_active")) else "숨김",
                "목표 독자": str(strategy.get("target_audience") or ""),
                "문체": str(strategy.get("writing_tone") or ""),
                "목표 분량": int(strategy.get("target_length") or 0),
                "기본 이미지": int(strategy.get("default_image_count") or 0),
                "SEO": str(strategy.get("seo_strategy") or ""),
            }
        )
    return rows


def _profile_option_label(
    profile_id: str,
    profiles: Sequence[Mapping[str, Any]],
) -> str:
    for profile in profiles:
        if str(profile.get("blog_profile_id") or "") != profile_id:
            continue
        return (
            f"{profile.get('profile_name') or profile_id} · "
            f"{profile.get('platform_label') or profile.get('platform') or '플랫폼'}"
        )
    return profile_id


def _render_strategy_detail(strategy: Mapping[str, Any], *, st_module=st) -> None:
    detail_columns = st_module.columns(4)
    detail_columns[0].metric(
        "목표 분량",
        f"{int(strategy.get('target_length') or 0):,}자",
        border=True,
    )
    detail_columns[1].metric(
        "기본 이미지",
        f"{int(strategy.get('default_image_count') or 0)}개",
        border=True,
    )
    detail_columns[2].metric(
        "기본 카테고리",
        str(strategy.get("default_category") or "미지정"),
        border=True,
    )
    detail_columns[3].metric(
        "배정 규칙",
        "글감당 1개 채널",
        border=True,
    )
    st_module.caption(
        f"목표 독자: {strategy.get('target_audience') or '-'}"
    )
    st_module.caption(
        f"문체: {strategy.get('writing_tone') or '-'}"
    )
    st_module.caption(
        f"SEO 방향: {strategy.get('seo_strategy') or '-'}"
    )
    with st_module.expander("채널별 상세 작성 기준", expanded=False):
        allowed = [
            str(item)
            for item in strategy.get("allowed_categories") or []
            if str(item).strip()
        ]
        excluded = [
            str(item)
            for item in strategy.get("excluded_categories") or []
            if str(item).strip()
        ]
        title_rules = [
            str(item)
            for item in strategy.get("title_rules") or []
            if str(item).strip()
        ]
        st_module.markdown(
            "**주요 분야:** " + (" · ".join(allowed) if allowed else "미지정")
        )
        st_module.markdown(
            "**제외 분야:** " + (" · ".join(excluded) if excluded else "미지정")
        )
        if title_rules:
            st_module.markdown("**제목 원칙**")
            for item in title_rules:
                st_module.markdown(f"- {item}")


def _strategy_summary_rows(
    strategies: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    platform_labels = {
        "blogger": "Google Blogger",
        "naver_blog": "네이버 블로그",
    }
    rows: list[dict[str, str]] = []
    for item in build_channel_strategy_rows(strategies):
        platform = str(item.get("플랫폼") or "")
        rows.append(
            {
                "채널": str(item.get("채널") or ""),
                "플랫폼": platform_labels.get(platform, platform or "사용자 지정"),
                "상태": str(item.get("상태") or ""),
                "목표 분량": f"{int(item.get('목표 분량') or 0):,}자",
                "기본 이미지": f"{int(item.get('기본 이미지') or 0)}개",
            }
        )
    return rows


def render_blog_channel_strategy_settings(
    con: duckdb.DuckDBPyConnection,
    *,
    st_module=st,
) -> list[dict[str, Any]]:
    st_module.markdown("#### 기본 발행 채널")
    st_module.caption(
        "기본 추천 채널은 Blogger 3개(생활자료·IT 사용법·요즘 화제)와 "
        "네이버 1개(국내 장소·서비스·경험)입니다. 실제 연결 목록에는 이 4개와 "
        "티스토리 1개만 표시하며 이전 프로필은 이력 보호를 위해 비활성 보관합니다."
    )

    all_strategies = list_managed_blog_channel_strategies(
        con, active_only=False
    )
    active_strategies = [
        strategy for strategy in all_strategies if bool(strategy.get("is_active"))
    ]
    if len(all_strategies) < MANAGED_CHANNEL_COUNT:
        st_module.warning(
            f"4개 기본 채널 중 {len(all_strategies)}개만 준비되어 있습니다."
        )
        if st_module.button(
            "4개 기본 발행 채널 준비",
            type="primary",
            key="install_default_blog_channels",
        ):
            result = install_default_blog_channels(con)
            st_module.success(
                "새 기본 채널 전략을 추가했습니다. "
                f"새 프로필 {len(result['created_profile_ids'])}개 · "
                f"새 전략 {len(result['created_strategy_codes'])}개"
            )
            st_module.rerun()
    elif len(active_strategies) < MANAGED_CHANNEL_COUNT:
        st_module.warning(
            "4개 전략은 준비됐지만 일부 프로필이 비활성 상태입니다. "
            "고정 프로필 동기화를 다시 실행하면 추천 대상에 복원됩니다."
        )
    else:
        st_module.success(
            "Blogger 3개·네이버 1개 기본 채널을 초안별 추천에 사용할 수 있습니다."
        )

    if all_strategies:
        show_rules = st_module.toggle(
            f"기본 채널 운영 규칙 {len(all_strategies)}개 보기",
            value=False,
            key="show_managed_blog_channel_rules",
            help=(
                "글감 추천·작성 기준을 확인합니다. 실제 로그인·글쓰기 주소와 "
                "출력 형식은 아래 블로그 프로필에서 설정합니다."
            ),
        )
        if show_rules:
            st_module.caption(
                "이 내용은 글감 추천·작성 기준입니다. 실제 로그인·글쓰기 주소와 "
                "출력 형식은 아래 블로그 프로필에서 설정합니다."
            )
            st_module.dataframe(
                _strategy_summary_rows(all_strategies),
                hide_index=True,
                width="stretch",
            )
            detail_tabs = st_module.tabs(
                [str(item.get("profile_name") or "발행 채널") for item in all_strategies]
            )
            for detail_tab, strategy in zip(
                detail_tabs,
                all_strategies,
                strict=True,
            ):
                with detail_tab:
                    _render_strategy_detail(strategy, st_module=st_module)

    st_module.divider()
    return all_strategies


def render_publish_channel_assignment(
    con: duckdb.DuckDBPyConnection,
    *,
    draft: Mapping[str, Any],
    profiles: Sequence[Mapping[str, Any]],
    st_module=st,
) -> str | None:
    state = build_publish_channel_state(con, draft=draft, profiles=profiles)
    draft_id = str(draft.get("draft_id") or "").strip()

    if state.managed_strategy_count < MANAGED_CHANNEL_COUNT:
        st_module.info(
            "4개 기본 발행 채널을 먼저 준비해야 글감별 추천과 배정을 사용할 수 있습니다."
        )
        if st_module.button(
            "4개 기본 발행 채널 준비",
            type="primary",
            key=f"publish_install_channels_{draft_id}",
        ):
            install_default_blog_channels(con)
            st_module.rerun()
        return None

    if state.active_strategy_count < MANAGED_CHANNEL_COUNT:
        st_module.warning(
            "일부 기본 발행 채널이 숨김 상태라 현재 활성 채널만 추천 후보로 사용합니다."
        )

    recommendation = state.recommendation
    if recommendation is not None:
        st_module.success(f"추천 발행처: {recommendation.profile_name}")
        st_module.caption(recommendation.reason)
    else:
        st_module.info(
            "활성화된 기본 채널 전략이 없어 기존 기본 프로필을 우선 표시합니다."
        )

    assignment = state.assignment
    if assignment is not None:
        source_label = (
            "사용자 변경"
            if str(assignment.get("selection_source") or "") == "user_override"
            else "추천 그대로"
        )
        st_module.info(
            f"저장된 배정: {assignment.get('selected_profile_name') or '-'} · {source_label}"
        )

    profile_ids = _profile_ids(profiles)
    if not profile_ids:
        return None
    initial_profile_id = (
        state.selected_profile_id
        if state.selected_profile_id in profile_ids
        else profile_ids[0]
    )
    selected_profile_id = st_module.selectbox(
        "발행할 블로그",
        profile_ids,
        index=profile_ids.index(initial_profile_id),
        format_func=lambda profile_id: _profile_option_label(profile_id, profiles),
        key=f"publish_channel_profile_{draft_id}",
        help=(
            "추천을 그대로 사용하거나 다른 활성 프로필로 변경할 수 있습니다. "
            "선택만으로 과거 배정은 바뀌지 않으며 아래 저장 버튼을 눌러야 기록됩니다."
        ),
    )

    selected_strategy = state.strategy_by_profile_id.get(selected_profile_id)
    if selected_strategy is not None:
        _render_strategy_detail(selected_strategy, st_module=st_module)
    else:
        st_module.caption(
            "사용자 지정 프로필입니다. 4개 기본 채널 전략과 별도로 선택할 수 있습니다."
        )

    if recommendation is not None and st_module.button(
        "이 발행처로 배정 저장",
        type="secondary",
        key=f"save_publish_channel_assignment_{draft_id}",
    ):
        try:
            save_draft_blog_assignment(
                con,
                draft_id=draft_id,
                recommendation=recommendation,
                selected_blog_profile_id=selected_profile_id,
            )
        except ValueError as exc:
            st_module.error(str(exc))
        else:
            st_module.success("초안의 추천·선택 발행처를 저장했습니다.")
            st_module.rerun()

    return selected_profile_id
