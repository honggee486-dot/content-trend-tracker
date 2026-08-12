from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Mapping

import pandas as pd
import streamlit as st

from src.services.publish_performance_service import (
    MIN_PROFILE_SAMPLE,
    STANDARD_OBSERVATION_WINDOWS,
    build_publish_performance_comparison,
    ensure_publish_performance_schema,
    list_publish_performance_snapshots,
    save_publish_performance_snapshot,
)


def _format_datetime(value: object) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else "-"


def _format_percent(value: object) -> str:
    return "-" if value is None else f"{float(value) * 100:.1f}%"


def _combine_datetime(date_value: date, time_value: time) -> datetime:
    return datetime.combine(date_value, time_value).replace(microsecond=0)


def render_publish_performance_panel(
    con,
    *,
    selected_record: Mapping[str, Any],
    st_module=st,
) -> None:
    ensure_publish_performance_schema(con)
    publish_id = str(selected_record.get("publish_id") or "").strip()
    if not publish_id:
        return

    st_module.divider()
    st_module.subheader("발행 성과 기록·비교")
    st_module.caption(
        "플랫폼 통계 화면에서 직접 확인한 수치만 입력합니다. "
        "7일·30일·90일처럼 같은 관찰 구간끼리 비교하며 자동 수집이나 추천 규칙 자동 변경은 수행하지 않습니다."
    )

    published_at = selected_record.get("published_at") or selected_record.get("created_at")
    if not isinstance(published_at, datetime):
        published_at = datetime.now().replace(microsecond=0)
    default_observed_at = datetime.now().replace(microsecond=0)

    with st_module.expander("선택 글 성과 입력", expanded=False):
        st_module.info(
            f"선택 발행 기록: {selected_record.get('draft_title') or selected_record.get('topic_title') or publish_id}"
        )
        with st_module.form(f"publish_performance_form_{publish_id}"):
            form_columns = st_module.columns(3)
            window_days = form_columns[0].selectbox(
                "관찰 구간",
                STANDARD_OBSERVATION_WINDOWS,
                index=0,
                format_func=lambda value: f"발행 후 {value}일",
            )
            observed_date = form_columns[1].date_input(
                "성과 확인 날짜",
                value=default_observed_at.date(),
            )
            observed_time = form_columns[2].time_input(
                "성과 확인 시간",
                value=default_observed_at.time(),
            )

            count_columns = st_module.columns(3)
            views = count_columns[0].number_input(
                "조회수",
                min_value=0,
                step=1,
                value=0,
            )
            search_visits = count_columns[1].number_input(
                "검색 유입",
                min_value=0,
                step=1,
                value=0,
                help="플랫폼에서 검색 유입 수치를 제공할 때만 입력합니다.",
            )
            likes = count_columns[2].number_input(
                "좋아요",
                min_value=0,
                step=1,
                value=0,
            )

            reaction_columns = st_module.columns(2)
            comments = reaction_columns[0].number_input(
                "댓글",
                min_value=0,
                step=1,
                value=0,
            )
            shares = reaction_columns[1].number_input(
                "공유",
                min_value=0,
                step=1,
                value=0,
            )
            memo = st_module.text_area(
                "확인 메모",
                placeholder="예: 티스토리 통계 화면에서 발행 후 7일 기준으로 확인",
                height=80,
            )
            submitted = st_module.form_submit_button(
                "성과 스냅샷 저장",
                type="primary",
                use_container_width=True,
            )

        if submitted:
            observed_at = _combine_datetime(observed_date, observed_time)
            expected_at = published_at.replace(microsecond=0)
            elapsed_days = max(0, (observed_at - expected_at).days)
            try:
                save_publish_performance_snapshot(
                    con,
                    publish_id=publish_id,
                    observation_window_days=int(window_days),
                    observed_at=observed_at,
                    views=int(views),
                    search_visits=int(search_visits),
                    likes=int(likes),
                    comments=int(comments),
                    shares=int(shares),
                    memo=memo,
                )
            except ValueError as exc:
                st_module.error(str(exc))
            else:
                if abs(elapsed_days - int(window_days)) > 3:
                    st_module.warning(
                        f"발행 후 실제 {elapsed_days}일 시점의 값으로 저장했습니다. "
                        f"선택한 {int(window_days)}일 구간과 차이가 크므로 비교할 때 참고하세요."
                    )
                else:
                    st_module.success("발행 성과 스냅샷을 추가했습니다.")
                st_module.rerun()

    snapshots = list_publish_performance_snapshots(
        con,
        publish_id=publish_id,
        limit=100,
    )
    with st_module.expander(f"선택 글 성과 이력 {len(snapshots):,}건", expanded=False):
        if not snapshots:
            st_module.caption("아직 저장된 성과 스냅샷이 없습니다.")
        else:
            st_module.dataframe(
                pd.DataFrame(
                    [
                        {
                            "관찰 구간": f"{int(item['observation_window_days'])}일",
                            "확인 시각": _format_datetime(item.get("observed_at")),
                            "조회수": int(item.get("views") or 0),
                            "검색 유입": int(item.get("search_visits") or 0),
                            "좋아요": int(item.get("likes") or 0),
                            "댓글": int(item.get("comments") or 0),
                            "공유": int(item.get("shares") or 0),
                            "검색 비중": _format_percent(item.get("search_share")),
                            "반응률": _format_percent(item.get("engagement_rate")),
                            "메모": str(item.get("memo") or ""),
                        }
                        for item in snapshots
                    ]
                ),
                hide_index=True,
                width="stretch",
            )

    st_module.markdown("**발행처별 동일 구간 비교**")
    compare_window = st_module.selectbox(
        "비교할 관찰 구간",
        STANDARD_OBSERVATION_WINDOWS,
        index=0,
        format_func=lambda value: f"발행 후 {value}일",
        key="publish_performance_compare_window",
    )
    comparison = build_publish_performance_comparison(
        con,
        observation_window_days=int(compare_window),
        minimum_profile_sample=MIN_PROFILE_SAMPLE,
    )
    if comparison.severity == "success":
        st_module.success(comparison.summary)
    else:
        st_module.info(comparison.summary)

    metric_columns = st_module.columns(4)
    metric_columns[0].metric("판단", comparison.status, border=True)
    metric_columns[1].metric(
        "비교 가능",
        "예" if comparison.comparison_ready else "아니요",
        help=f"서로 다른 발행처 2곳 이상이 각각 최소 {MIN_PROFILE_SAMPLE}건을 충족해야 합니다.",
        border=True,
    )
    metric_columns[2].metric(
        "평균 조회 우위",
        comparison.view_leader or "표본 없음",
        border=True,
    )
    metric_columns[3].metric(
        "평균 반응률 우위",
        comparison.engagement_leader or "표본 없음",
        border=True,
    )

    if comparison.profile_rows:
        st_module.dataframe(
            pd.DataFrame(
                [
                    {
                        "발행처": row["profile_name"],
                        "플랫폼": row["platform"],
                        "측정 글": int(row["measured_posts"]),
                        "최소 표본": "충족" if row["sample_sufficient"] else "미충족",
                        "평균 조회수": float(row["average_views"]),
                        "중앙 조회수": float(row["median_views"]),
                        "평균 검색 비중": _format_percent(row.get("average_search_share")),
                        "평균 반응률": _format_percent(row.get("average_engagement_rate")),
                    }
                    for row in comparison.profile_rows
                ]
            ),
            hide_index=True,
            width="stretch",
        )

    st_module.markdown("**다음 판단**")
    st_module.markdown(comparison.next_step)
    st_module.caption(
        "성과 수치는 플랫폼마다 집계 방식이 다를 수 있습니다. "
        "이 화면은 참고 비교만 제공하며 글감 배정·추천 발행처 규칙을 자동으로 변경하지 않습니다."
    )
