"""검색어별 실제 요청과 원문 발견 성과를 설정 화면에 표시합니다."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.services.portal_request_history_service import list_recent_portal_requests
from src.services.query_discovery_diagnostics_service import (
    QUERY_PERIOD_OPTIONS,
    QUERY_SOURCE_LABELS,
    QUERY_SOURCE_TYPE_LABELS,
    get_query_discovery_diagnostics,
)
from src.source_freshness_ui import render_source_freshness_diagnostics
from src.trend_feedback_diagnostics_ui import render_trend_feedback_diagnostics


def _format_time(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else "기록 없음"


def _format_rank(value) -> str:
    if value is None:
        return "기록 없음"
    return f"{float(value):.1f}".rstrip("0").rstrip(".")


def _format_ratio(value, suffix: str = "") -> str:
    if value is None:
        return "계산 불가"
    return f"{float(value):.2f}".rstrip("0").rstrip(".") + suffix


def _format_duration_ms(value) -> str:
    if value is None:
        return "기록 없음"
    milliseconds = max(0, int(round(float(value))))
    if milliseconds < 1000:
        return f"{milliseconds:,}ms"
    return f"{milliseconds / 1000:.1f}초"


def render_query_discovery_diagnostics(con, *, st_module=st) -> None:
    render_source_freshness_diagnostics(con, st_module=st_module)
    render_trend_feedback_diagnostics(con, st_module=st_module)
    st_module.subheader("검색어 성과 진단")
    filter_columns = st_module.columns(2)
    selected_days = filter_columns[0].selectbox(
        "집계 기간",
        options=list(QUERY_PERIOD_OPTIONS),
        format_func=lambda value: f"최근 {int(value)}일",
        key="query_discovery_diagnostics_days",
    )
    selected_source = filter_columns[1].selectbox(
        "포털 출처",
        options=["", *QUERY_SOURCE_LABELS],
        format_func=lambda value: "전체" if not value else QUERY_SOURCE_LABELS[value],
        key="query_discovery_diagnostics_source",
    )
    diagnostics = get_query_discovery_diagnostics(
        con,
        days=int(selected_days),
        source_name=selected_source,
        limit=100,
    )

    request_count = int(diagnostics["request_count"])
    if request_count:
        st_module.caption("0.10.66 이후 실제 NAVER·Daum 검색 요청 결과")
        with st_module.container(horizontal=True):
            st_module.metric(
                "논리 검색 요청",
                f"{request_count:,}회",
                f"실제 시도 {int(diagnostics['attempt_count']):,}회",
                help="검색어·세부 출처·페이지 단위 요청 수입니다. 재시도는 실제 시도에 별도로 포함됩니다.",
                border=True,
            )
            st_module.metric(
                "성공 요청",
                f"{int(diagnostics['successful_request_count']):,}회",
                f"재시도 {int(diagnostics['request_retry_count']):,}회",
                help="최종적으로 정상 응답을 받은 논리 요청과 재시도 횟수입니다.",
                border=True,
            )
            st_module.metric(
                "결과 0건",
                f"{int(diagnostics['zero_result_count']):,}회",
                f"성공 중 {float(diagnostics['zero_result_rate_percent']):.1f}%",
                help="정상 응답이었지만 프로그램에서 사용할 수 있는 원문이 없었던 요청입니다.",
                border=True,
            )
            st_module.metric(
                "요청 오류",
                f"{int(diagnostics['failed_request_count']):,}회",
                f"전체의 {float(diagnostics['request_error_rate_percent']):.1f}%",
                help="재시도 후에도 최종 실패한 논리 요청입니다. HTTP·DNS·시간 초과 등은 요청 원장에서 확인합니다.",
                border=True,
            )
            st_module.metric(
                "성공당 결과",
                _format_ratio(diagnostics["average_results_per_success"], "건"),
                f"총 {int(diagnostics['request_result_count']):,}건",
                help="성공 요청 한 번당 adapter가 정상 원문 신호로 변환한 평균 결과 수입니다.",
                border=True,
            )
            st_module.metric(
                "신규 1건당 요청",
                _format_ratio(diagnostics["requests_per_new_item"], "회"),
                f"신규 {int(diagnostics['request_new_count']):,}건",
                help="논리 검색 요청 수를 실제 신규 저장 원문 수로 나눈 값입니다. 신규가 없으면 계산할 수 없습니다.",
                border=True,
            )
        st_module.caption(
            f"마지막 요청: {_format_time(diagnostics['last_request_at'])} · "
            f"평균 소요 {_format_duration_ms(diagnostics['average_request_duration_ms'])} · "
            f"기존 원문 갱신 {int(diagnostics['request_updated_count']):,}건"
        )

        with st_module.expander("검색어별 실제 요청 성과", expanded=False):
            request_frame = pd.DataFrame(
                [
                    {
                        "포털": QUERY_SOURCE_LABELS.get(
                            str(row.get("source_name") or ""),
                            str(row.get("source_name") or ""),
                        ),
                        "세부 출처": QUERY_SOURCE_TYPE_LABELS.get(
                            str(row.get("source_type") or ""),
                            str(row.get("source_type") or ""),
                        ),
                        "검색어": str(row.get("discovery_query") or ""),
                        "요청": int(row.get("request_count") or 0),
                        "실제 시도": int(row.get("attempt_count") or 0),
                        "재시도": int(row.get("retry_count") or 0),
                        "성공": int(row.get("successful_request_count") or 0),
                        "오류": int(row.get("failed_request_count") or 0),
                        "오류율": f"{float(row.get('error_rate_percent') or 0):.1f}%",
                        "0건": int(row.get("zero_result_count") or 0),
                        "0건 비율": f"{float(row.get('zero_result_rate_percent') or 0):.1f}%",
                        "결과": int(row.get("result_count") or 0),
                        "신규": int(row.get("new_count") or 0),
                        "갱신": int(row.get("updated_count") or 0),
                        "신규 1건당 요청": _format_ratio(row.get("requests_per_new_item")),
                        "평균 소요": _format_duration_ms(row.get("average_duration_ms")),
                        "마지막 요청": _format_time(row.get("last_request_at")),
                    }
                    for row in diagnostics["request_rows"]
                ]
            )
            st_module.dataframe(
                request_frame,
                hide_index=True,
                width="stretch",
                height=420,
            )
            st_module.caption(
                "재시도는 하나의 논리 요청 안에서 실제 API를 다시 호출한 횟수입니다. "
                "한 수집 실행의 인증·DNS 같은 치명적 오류로 시작조차 못 한 후속 계획은 실제 요청 원장에 포함하지 않습니다."
            )

        recent_requests = list_recent_portal_requests(
            con,
            days=int(selected_days),
            source_name=selected_source,
            limit=200,
        )
        with st_module.expander("최근 검색 요청 원장", expanded=False):
            recent_frame = pd.DataFrame(
                [
                    {
                        "시각": _format_time(row.get("finished_at")),
                        "포털": QUERY_SOURCE_LABELS.get(
                            str(row.get("source_name") or ""),
                            str(row.get("source_name") or ""),
                        ),
                        "세부 출처": QUERY_SOURCE_TYPE_LABELS.get(
                            str(row.get("source_type") or ""),
                            str(row.get("source_type") or ""),
                        ),
                        "검색어": str(row.get("discovery_query") or ""),
                        "페이지": int(row.get("request_page") or 1),
                        "상태": "성공" if str(row.get("status")) == "success" else "오류",
                        "요청 크기": int(row.get("requested_result_count") or 0),
                        "실제 시도": int(row.get("attempt_count") or 0),
                        "재시도": int(row.get("retry_count") or 0),
                        "결과": int(row.get("result_count") or 0),
                        "신규": int(row.get("newly_saved_count") or 0),
                        "갱신": int(row.get("updated_count") or 0),
                        "형식·중복 제외": int(row.get("skipped_count") or 0),
                        "HTTP": row.get("http_status") if row.get("http_status") is not None else "-",
                        "오류 유형": str(row.get("error_type") or ""),
                        "오류 요약": str(row.get("error_message") or "")[:300],
                        "소요": _format_duration_ms(row.get("duration_ms")),
                    }
                    for row in recent_requests
                ]
            )
            st_module.dataframe(
                recent_frame,
                hide_index=True,
                width="stretch",
                height=420,
            )
            st_module.caption(
                "재시도 후 성공한 요청도 원인 확인을 위해 마지막 재시도 오류의 HTTP 상태·유형·요약을 보존할 수 있습니다."
            )
    else:
        st_module.caption(
            "아직 0.10.66 이후 실제 검색 요청 기록이 없습니다. 다음 NAVER·Daum 수집부터 "
            "성공·결과 0건·오류·재시도와 신규 저장 효율을 확인할 수 있습니다."
        )

    if int(diagnostics["discovery_count"]) == 0:
        st_module.caption(
            f"최근 {int(diagnostics['days'])}일 동안 현재 조건에 맞는 검색어 발견 기록이 없습니다."
        )
        return

    st_module.caption("실제로 원문을 발견해 저장한 결과")
    with st_module.container(horizontal=True):
        st_module.metric(
            "사용된 검색어",
            f"{int(diagnostics['query_count']):,}개",
            help="선택한 기간에 원문을 한 건 이상 발견한 서로 다른 검색어 수입니다.",
            border=True,
        )
        st_module.metric(
            "발견 기록",
            f"{int(diagnostics['discovery_count']):,}건",
            help="검색어·출처·원문·수집 실행 단위로 원장에 저장된 발견 기록 수입니다.",
            border=True,
        )
        st_module.metric(
            "신규 원문",
            f"{int(diagnostics['new_count']):,}건",
            f"신규율 {float(diagnostics['new_rate_percent']):.1f}%",
            help="해당 수집 배치에서 처음 저장된 원문으로 기록된 발견 수입니다.",
            border=True,
        )
        st_module.metric(
            "고유 원문",
            f"{int(diagnostics['unique_item_count']):,}개",
            f"중복 발견률 {float(diagnostics['duplicate_rate_percent']):.1f}%",
            help="같은 원문이 여러 실행이나 검색어에서 다시 발견된 기록을 하나로 합친 수입니다.",
            border=True,
        )
        st_module.metric(
            "검색 결과 순위",
            f"평균 {_format_rank(diagnostics['average_rank'])}",
            f"최고 {_format_rank(diagnostics['best_rank'])}",
            help="순위가 기록된 검색 결과의 평균 위치와 가장 높은 위치입니다. 숫자가 작을수록 상단입니다.",
            border=True,
        )

    st_module.caption(
        f"마지막 유효 발견: {_format_time(diagnostics['last_discovered_at'])} · "
        f"재발견 {int(diagnostics['repeat_count']):,}건 · "
        f"동일 원문 중복 발견 {int(diagnostics['duplicate_discovery_count']):,}건"
    )

    with st_module.expander("검색어별 발견 성과 상세", expanded=False):
        frame = pd.DataFrame(
            [
                {
                    "포털": QUERY_SOURCE_LABELS.get(
                        str(row.get("source_name") or ""),
                        str(row.get("source_name") or ""),
                    ),
                    "세부 출처": QUERY_SOURCE_TYPE_LABELS.get(
                        str(row.get("source_type") or ""),
                        str(row.get("source_type") or ""),
                    ),
                    "검색어": str(row.get("discovery_query") or ""),
                    "발견": int(row.get("discovery_count") or 0),
                    "신규": int(row.get("new_count") or 0),
                    "재발견": int(row.get("repeat_count") or 0),
                    "신규율": f"{float(row.get('new_rate_percent') or 0):.1f}%",
                    "고유 원문": int(row.get("unique_item_count") or 0),
                    "중복 발견": int(row.get("duplicate_discovery_count") or 0),
                    "평균 순위": _format_rank(row.get("average_rank")),
                    "최고 순위": _format_rank(row.get("best_rank")),
                    "마지막 발견": _format_time(row.get("last_discovered_at")),
                }
                for row in diagnostics["rows"]
            ]
        )
        st_module.dataframe(frame, hide_index=True, width="stretch", height=420)
        st_module.caption(
            "0.10.66 이전에는 결과 0건이었던 검색 요청과 API 오류를 요청별로 남기지 않았습니다. "
            "따라서 실제 요청 효율은 0.10.66 이후 원장 구간을 기준으로 판단하세요."
        )
