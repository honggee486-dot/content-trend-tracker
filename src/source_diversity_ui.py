"""설정 화면에 최근 수집 출처 다양성과 군집 사례 진단을 표시합니다."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import src.cluster_case_diagnostic_ui as cluster_case_diagnostic_ui
from src.services.cluster_case_candidate_expansion_service import (
    analyze_cluster_cases_with_expanded_candidates,
)
from src.services.source_analysis_limit_service import (
    analyze_source_analysis_limits,
)
from src.services.source_diversity_service import (
    LOOKBACK_OPTIONS,
    SourceDiversityIssue,
    analyze_source_diversity,
)



def _percent(value: float) -> str:
    return f"{max(0.0, float(value or 0.0)) * 100:.1f}%"



def _render_issue(issue: SourceDiversityIssue, *, st_module=st) -> None:
    text = f"{issue.message}  \n권고: {issue.recommendation}"
    if issue.severity == "error":
        st_module.error(text)
    elif issue.severity == "warning":
        st_module.warning(text)
    elif issue.severity == "success":
        st_module.success(text)
    else:
        st_module.info(text)



def _render_cluster_case_panel(con, *, st_module=st) -> None:
    """사례 패널이 실행될 때만 확장 후보 분석 함수를 임시 연결합니다."""
    original = cluster_case_diagnostic_ui.analyze_cluster_cases
    cluster_case_diagnostic_ui.analyze_cluster_cases = (
        analyze_cluster_cases_with_expanded_candidates
    )
    try:
        cluster_case_diagnostic_ui.render_cluster_case_diagnostic_panel(
            con,
            st_module=st_module,
        )
    finally:
        cluster_case_diagnostic_ui.analyze_cluster_cases = original



def render_source_diversity_panel(con, *, st_module=st) -> None:
    with st_module.expander("수집 출처 다양성 진단", expanded=False):
        st_module.caption(
            "최근 수집 원문과 현재 순위 군집을 읽기 전용으로 비교합니다. "
            "수집 설정·분석 상한·군집 기준은 자동으로 변경하지 않습니다."
        )
        lookback_hours = st_module.selectbox(
            "진단 기간",
            list(LOOKBACK_OPTIONS),
            index=1,
            format_func=lambda value: LOOKBACK_OPTIONS[int(value)],
            key="source_diversity_lookback_hours",
        )
        report = analyze_source_diversity(
            con,
            lookback_hours=int(lookback_hours),
        )
        limit_report = analyze_source_analysis_limits(
            con,
            lookback_hours=int(lookback_hours),
            now=report.generated_at,
        )
        limit_excluded = min(
            report.unclustered_item_count,
            limit_report.estimated_excluded_count,
        )
        other_unclustered = max(
            0,
            report.unclustered_item_count - limit_excluded,
        )

        metric_columns = st_module.columns(4)
        metric_columns[0].metric(
            "최근 수집 원문",
            f"{report.collected_item_count:,}개",
            help="게시·관측·최근 포착 시각이 선택 기간 안에 있는 원문입니다.",
            border=True,
        )
        metric_columns[1].metric(
            "현재 군집",
            f"{report.cluster_count:,}개",
            help="선택 기간의 원문이 하나 이상 연결된 현재 순위 군집입니다.",
            border=True,
        )
        metric_columns[2].metric(
            "다중 출처 비율",
            _percent(report.multi_source_ratio),
            help=(
                f"서로 다른 source_type이 2개 이상 포함된 군집 "
                f"{report.multi_source_cluster_count:,}개입니다."
            ),
            border=True,
        )
        metric_columns[3].metric(
            "현재 진단",
            report.status_label,
            help=(
                "군집 20개 미만은 표본 부족, 다중 출처 비율 5% 미만은 매우 낮음, "
                "15% 미만은 개선 필요, 30% 미만은 관찰로 표시하는 운영 기준입니다."
            ),
            border=True,
        )

        detail_columns = st_module.columns(4)
        detail_columns[0].metric(
            "군집 연결 원문",
            f"{report.clustered_item_count:,}개",
            border=True,
        )
        detail_columns[1].metric(
            "원문 연결률",
            _percent(report.cluster_coverage),
            help="최근 수집 원문 중 현재 순위 군집에 연결된 원문의 비율입니다.",
            border=True,
        )
        detail_columns[2].metric(
            "단일 출처 군집",
            f"{report.single_source_cluster_count:,}개",
            border=True,
        )
        detail_columns[3].metric(
            "3개 이상 출처",
            f"{report.three_plus_source_cluster_count:,}개",
            border=True,
        )

        unclustered_columns = st_module.columns(3)
        unclustered_columns[0].metric(
            "현재 군집 밖 원문",
            f"{report.unclustered_item_count:,}개",
            help="최근 수집됐지만 현재 순위 군집 연결표에 없는 원문입니다.",
            border=True,
        )
        unclustered_columns[1].metric(
            "입력 상한 초과 추정",
            f"{limit_excluded:,}개",
            help=(
                "최근 원문 수가 현재 출처 그룹별 분석 입력 상한을 넘은 양의 합계입니다. "
                "실제 필터 통과 순서까지 재현한 정확한 제외 수가 아닌 보수적 추정치입니다."
            ),
            border=True,
        )
        unclustered_columns[2].metric(
            "상한 외 미연결",
            f"{other_unclustered:,}개",
            help=(
                "현재 군집 밖 원문에서 입력 상한 초과 추정치를 뺀 값입니다. "
                "품질 필터·시간 범위·후보 탐색·정규화 문제를 추가 확인할 대상입니다."
            ),
            border=True,
        )

        st_module.markdown("#### 진단 결과와 개선 순서")
        for issue in report.issues:
            _render_issue(issue, st_module=st_module)

        st_module.markdown("#### 분석 입력 상한 영향")
        limit_frame = pd.DataFrame(
            [
                {
                    "출처 그룹": row.source_label,
                    "최근 원문": int(row.collected_item_count),
                    "현재 입력 상한": int(row.configured_limit),
                    "상한 초과 추정": int(row.estimated_excluded_count),
                }
                for row in limit_report.rows
            ]
        )
        st_module.dataframe(limit_frame, hide_index=True, width="stretch")
        st_module.caption(
            "상한 초과 추정은 최근 원문 수와 현재 설정값의 차이입니다. 실제 군집 입력은 "
            "품질 필터와 정렬을 거치므로 정확한 실패 건수로 단정하지 않습니다. 상한을 자동으로 늘리지도 않습니다."
        )

        st_module.markdown("#### 출처별 연결 상태")
        source_frame = pd.DataFrame(
            [
                {
                    "출처": row.source_label,
                    "최근 원문": int(row.collected_item_count),
                    "군집 연결 원문": int(row.clustered_item_count),
                    "원문 연결률": _percent(row.cluster_coverage),
                    "포함 군집": int(row.cluster_count),
                    "다중 출처 군집": int(row.multi_source_cluster_count),
                    "교차 연결률": _percent(row.cross_source_rate),
                    "군집 점유율": _percent(row.cluster_share),
                }
                for row in report.source_rows
            ]
        )
        st_module.dataframe(
            source_frame,
            hide_index=True,
            width="stretch",
        )
        st_module.caption(
            "원문 연결률은 수집된 원문이 현재 순위 군집에 포함된 비율이며, 교차 연결률은 "
            "해당 출처가 포함된 군집 중 다른 출처와 함께 묶인 비율입니다. 분석 입력 상한과 "
            "품질 필터 때문에 원문 연결률이 100%가 아닌 것은 정상일 수 있습니다."
        )

        st_module.markdown("#### 자주 함께 묶이는 출처 조합")
        if report.pair_rows:
            pair_frame = pd.DataFrame(
                [
                    {
                        "출처 조합": row.pair_label,
                        "함께 묶인 군집": int(row.cluster_count),
                        "다중 출처 군집 내 비율": _percent(row.multi_source_share),
                    }
                    for row in report.pair_rows[:10]
                ]
            )
            st_module.dataframe(pair_frame, hide_index=True, width="stretch")
        else:
            st_module.info("선택 기간에는 서로 다른 출처가 함께 묶인 군집이 없습니다.")

        st_module.caption(
            f"진단 시각: {report.generated_at:%Y-%m-%d %H:%M:%S} · "
            f"범위: {report.lookback_label} · 군집 밖 최근 원문 {report.unclustered_item_count:,}개"
        )

    try:
        _render_cluster_case_panel(con, st_module=st_module)
    except Exception as exc:
        st_module.caption(f"군집 실패·단일 출처 사례를 불러오지 못했습니다: {exc}")
