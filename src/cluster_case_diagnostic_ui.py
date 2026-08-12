"""설정 화면에 군집 밖 원문과 단일 출처 군집 사례를 표시합니다."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.services.cluster_case_diagnostic_service import (
    ClusterCaseDiagnosticReport,
    SingleSourceClusterCase,
    UnclusteredItemCase,
    analyze_cluster_cases,
)
from src.services.source_diversity_service import LOOKBACK_OPTIONS, SOURCE_LABELS


_REPORT_KEY = "cluster_case_diagnostic_report"
_REPORT_LOOKBACK_KEY = "cluster_case_diagnostic_report_lookback"


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{max(0.0, float(value)) * 100:.1f}%"


def _time_text(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value is not None else "기록 없음"


def _gap_text(value: float | None) -> str:
    if value is None:
        return "-"
    if value < 24:
        return f"{value:.1f}시간"
    return f"{value / 24:.1f}일"


def _candidate_title(case) -> str:
    candidate = case.candidate
    return candidate.canonical_title if candidate is not None else "후보 없음"


def _candidate_sources(case) -> str:
    candidate = case.candidate
    return ", ".join(candidate.source_labels) if candidate is not None else "-"


def _shared_tokens(case) -> str:
    candidate = case.candidate
    return ", ".join(candidate.shared_tokens) if candidate and candidate.shared_tokens else "-"


def _filter_cases(cases, *, source_type: str, query: str):
    clean_query = str(query or "").strip().casefold()
    result = []
    for case in cases:
        if source_type and case.source_type != source_type:
            continue
        haystack = " ".join(
            [
                str(getattr(case, "raw_title", "") or ""),
                str(getattr(case, "canonical_title", "") or ""),
                str(getattr(case, "normalized_title", "") or ""),
                _candidate_title(case),
                str(case.reason_label),
            ]
        ).casefold()
        if clean_query and clean_query not in haystack:
            continue
        result.append(case)
    return result


def _source_options(report: ClusterCaseDiagnosticReport) -> list[str]:
    present = {
        case.source_type
        for case in (*report.unclustered_cases, *report.single_source_cases)
        if case.source_type
    }
    ordered = [source_type for source_type in SOURCE_LABELS if source_type in present]
    ordered.extend(sorted(present - set(ordered)))
    return ["", *ordered]


def _render_candidate_summary(case, *, st_module=st) -> None:
    candidate = case.candidate
    if candidate is None:
        st_module.info("다른 출처가 포함된 기존 군집에서 뚜렷한 유사 후보를 찾지 못했습니다.")
        return
    metric_columns = st_module.columns(3)
    metric_columns[0].metric("후보 유사도", _percent(candidate.similarity), border=True)
    metric_columns[1].metric("시간 차이", _gap_text(candidate.time_gap_hours), border=True)
    metric_columns[2].metric(
        "공통 핵심어",
        f"{len(candidate.shared_tokens):,}개",
        border=True,
    )
    st_module.markdown(f"**유사 후보 군집:** {candidate.canonical_title}")
    st_module.caption(
        f"군집 ID: {candidate.cluster_id} · 출처: {_candidate_sources(case)} · "
        f"공통 핵심어: {_shared_tokens(case)}"
    )


def _render_unclustered_detail(case: UnclusteredItemCase, *, st_module=st) -> None:
    st_module.markdown(f"### {case.raw_title or '제목 없음'}")
    st_module.caption(
        f"{case.source_label} · {_time_text(case.event_at)} · 신호값 {case.signal_value:g} · "
        f"원문 ID {case.source_item_id}"
    )
    st_module.markdown(f"**예상 원인:** {case.reason_label}")
    st_module.markdown("**현재 정규화 제목**")
    st_module.code(case.normalized_title or "정규화 결과 없음")
    _render_candidate_summary(case, st_module=st_module)
    if case.source_url:
        st_module.link_button("원문 열기", case.source_url, use_container_width=True)
    st_module.warning(
        "이 화면의 원인 분류는 다음 분석 대상을 좁히기 위한 추정입니다. "
        "원문을 군집에 연결하거나 군집 기준을 변경하지 않습니다."
    )


def _render_single_cluster_detail(
    case: SingleSourceClusterCase,
    *,
    st_module=st,
) -> None:
    st_module.markdown(f"### {case.canonical_title or '제목 없음'}")
    st_module.caption(
        f"{case.source_label} · 원문 {case.item_count:,}개 · "
        f"최근 신호 {_time_text(case.last_seen_at)} · 군집 ID {case.cluster_id}"
    )
    st_module.markdown(f"**예상 원인:** {case.reason_label}")
    st_module.markdown("**군집 원문 제목 예시**")
    for title in case.sample_titles:
        st_module.markdown(f"- {title or '제목 없음'}")
    _render_candidate_summary(case, st_module=st_module)
    st_module.warning(
        "후보 군집이 표시돼도 실제로 같은 사건이라는 의미는 아닙니다. "
        "반대 사례와 날짜·제품명·금액 충돌을 Agent 분석에서 함께 확인해야 합니다."
    )


def _render_unclustered_tab(
    report: ClusterCaseDiagnosticReport,
    *,
    source_type: str,
    query: str,
    st_module=st,
) -> None:
    cases = _filter_cases(
        report.unclustered_cases,
        source_type=source_type,
        query=query,
    )
    st_module.caption(
        f"선택 기간 군집 밖 원문 {report.unclustered_total:,}개 중 "
        f"최근 분석 대상 {len(report.unclustered_cases):,}개를 읽었고, "
        f"현재 필터에는 {len(cases):,}개가 표시됩니다."
    )
    if not cases:
        st_module.info("조건에 맞는 군집 밖 원문 사례가 없습니다.")
        return

    frame = pd.DataFrame(
        [
            {
                "출처": case.source_label,
                "원문 제목": case.raw_title,
                "관측 시각": _time_text(case.event_at),
                "유사 후보": _candidate_title(case),
                "후보 유사도": _percent(
                    case.candidate.similarity if case.candidate else None
                ),
                "공통 핵심어": _shared_tokens(case),
                "예상 원인": case.reason_label,
            }
            for case in cases
        ]
    )
    st_module.dataframe(frame, hide_index=True, width="stretch")

    case_map = {case.source_item_id: case for case in cases}
    selection_key = "cluster_case_unclustered_selected"
    if st_module.session_state.get(selection_key) not in case_map:
        st_module.session_state.pop(selection_key, None)
    selected_id = st_module.selectbox(
        "상세 확인할 군집 밖 원문",
        list(case_map),
        format_func=lambda value: (
            f"[{case_map[str(value)].source_label}] "
            f"{case_map[str(value)].raw_title}"
        ),
        key=selection_key,
    )
    _render_unclustered_detail(case_map[str(selected_id)], st_module=st_module)


def _render_single_source_tab(
    report: ClusterCaseDiagnosticReport,
    *,
    source_type: str,
    query: str,
    st_module=st,
) -> None:
    cases = _filter_cases(
        report.single_source_cases,
        source_type=source_type,
        query=query,
    )
    st_module.caption(
        f"선택 기간 단일 출처 군집 {report.single_source_cluster_total:,}개 중 "
        f"최근 분석 대상 {len(report.single_source_cases):,}개를 읽었고, "
        f"현재 필터에는 {len(cases):,}개가 표시됩니다."
    )
    if not cases:
        st_module.info("조건에 맞는 단일 출처 군집 사례가 없습니다.")
        return

    frame = pd.DataFrame(
        [
            {
                "출처": case.source_label,
                "군집 제목": case.canonical_title,
                "원문 수": int(case.item_count),
                "최근 신호": _time_text(case.last_seen_at),
                "유사 후보": _candidate_title(case),
                "후보 유사도": _percent(
                    case.candidate.similarity if case.candidate else None
                ),
                "공통 핵심어": _shared_tokens(case),
                "예상 원인": case.reason_label,
            }
            for case in cases
        ]
    )
    st_module.dataframe(frame, hide_index=True, width="stretch")

    case_map = {case.cluster_id: case for case in cases}
    selection_key = "cluster_case_single_selected"
    if st_module.session_state.get(selection_key) not in case_map:
        st_module.session_state.pop(selection_key, None)
    selected_id = st_module.selectbox(
        "상세 확인할 단일 출처 군집",
        list(case_map),
        format_func=lambda value: (
            f"[{case_map[str(value)].source_label}] "
            f"{case_map[str(value)].canonical_title}"
        ),
        key=selection_key,
    )
    _render_single_cluster_detail(case_map[str(selected_id)], st_module=st_module)


def render_cluster_case_diagnostic_panel(con, *, st_module=st) -> None:
    with st_module.expander(
        "군집 실패·단일 출처 사례 상세 보기",
        expanded=False,
    ):
        st_module.caption(
            "현재 군집 밖에 남은 원문과 단일 출처 군집을 읽기 전용으로 분석합니다. "
            "기존 군집화와 같은 제목 정규화·유사도 계산을 사용하지만 자동 병합은 하지 않습니다."
        )
        run_columns = st_module.columns([2.0, 1.0])
        lookback_hours = run_columns[0].selectbox(
            "사례 기간",
            list(LOOKBACK_OPTIONS),
            index=1,
            format_func=lambda value: LOOKBACK_OPTIONS[int(value)],
            key="cluster_case_lookback_hours",
        )
        run_clicked = run_columns[1].button(
            "사례 분석 실행",
            key="cluster_case_run",
            width="stretch",
        )
        if run_clicked:
            with st_module.spinner("군집 밖 원문과 단일 출처 군집의 유사 후보를 비교하고 있습니다..."):
                st_module.session_state[_REPORT_KEY] = analyze_cluster_cases(
                    con,
                    lookback_hours=int(lookback_hours),
                )
                st_module.session_state[_REPORT_LOOKBACK_KEY] = int(lookback_hours)

        report = st_module.session_state.get(_REPORT_KEY)
        report_lookback = st_module.session_state.get(_REPORT_LOOKBACK_KEY)
        if (
            not isinstance(report, ClusterCaseDiagnosticReport)
            or int(report_lookback or 0) != int(lookback_hours)
        ):
            st_module.info(
                "기간을 선택한 뒤 `사례 분석 실행`을 누르세요. "
                "설정 화면을 열기만 해서는 대용량 비교를 실행하지 않습니다."
            )
            return

        filter_columns = st_module.columns(2)
        source_options = _source_options(report)
        source_type = filter_columns[0].selectbox(
            "출처 필터",
            source_options,
            format_func=lambda value: "전체 출처"
            if not value
            else SOURCE_LABELS.get(str(value), str(value)),
            key="cluster_case_source_filter",
        )
        query = filter_columns[1].text_input(
            "제목·원인 검색",
            key="cluster_case_query",
            placeholder="예: Google Trends, 출시, 유사도",
        )

        metric_columns = st_module.columns(4)
        metric_columns[0].metric(
            "군집 밖 원문",
            f"{report.unclustered_total:,}개",
            border=True,
        )
        metric_columns[1].metric(
            "단일 출처 군집",
            f"{report.single_source_cluster_total:,}개",
            border=True,
        )
        near_unclustered = sum(
            1
            for case in report.unclustered_cases
            if case.candidate and case.candidate.similarity >= 0.60
        )
        metric_columns[2].metric(
            "유사 후보 60% 이상",
            f"{near_unclustered:,}개",
            help="현재 화면이 읽은 군집 밖 원문 중 다른 출처 군집 후보 유사도가 60% 이상인 건수입니다.",
            border=True,
        )
        google_cases = sum(
            1
            for case in (*report.unclustered_cases, *report.single_source_cases)
            if case.source_type == "google_trends"
        )
        metric_columns[3].metric(
            "Google Trends 사례",
            f"{google_cases:,}개",
            border=True,
        )

        unclustered_tab, single_tab = st_module.tabs(
            ["군집 밖 원문", "단일 출처 군집"]
        )
        with unclustered_tab:
            _render_unclustered_tab(
                report,
                source_type=str(source_type),
                query=query,
                st_module=st_module,
            )
        with single_tab:
            _render_single_source_tab(
                report,
                source_type=str(source_type),
                query=query,
                st_module=st_module,
            )

        st_module.caption(
            f"진단 시각: {report.generated_at:%Y-%m-%d %H:%M:%S} · "
            f"범위: {report.lookback_label} · 현재 병합 참고 기준 72%"
        )
        st_module.info(
            "이 사례 목록은 실제 DB를 읽기 전용으로 분석할 로컬 Agent의 표본입니다. "
            "진단만으로 군집 기준을 자동 변경하지 않습니다."
        )
