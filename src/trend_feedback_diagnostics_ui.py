"""사용자 글감 평가와 근거 품질 관계를 설정 화면에 표시합니다."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.services.query_discovery_diagnostics_service import (
    QUERY_SOURCE_LABELS,
    QUERY_SOURCE_TYPE_LABELS,
)
from src.services.score_adjustment_preview_service import get_score_adjustment_preview
from src.services.trend_feedback_diagnostics_service import (
    get_trend_feedback_diagnostics,
)
from src.services.trend_feedback_service import FEEDBACK_LABELS


_RECOMMENDATION_LABELS = {
    "recommended": "추천",
    "review": "검토",
    "hold": "보류",
}


def _format_time(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else "기록 없음"


def _format_average(value: object) -> str:
    return f"{float(value or 0):.1f}"


def _format_signed(value: object) -> str:
    return f"{float(value or 0):+.1f}점"


def render_trend_feedback_diagnostics(con, *, st_module=st) -> None:
    diagnostics = get_trend_feedback_diagnostics(con)
    st_module.subheader("사용자 글감 평가 진단")

    total_count = int(diagnostics["total_count"])
    if total_count == 0:
        st_module.caption(
            "아직 분석할 사용자 글감 평가가 없습니다. 오늘의 트렌드에서 글감을 평가하면 "
            "근거 품질·출처·검색어와의 관계를 이곳에서 확인할 수 있습니다."
        )
        return

    with st_module.container(horizontal=True):
        st_module.metric(
            "누적 평가",
            f"{total_count:,}건",
            help="군집별로 현재 저장된 최신 사용자 평가 수입니다. 평가를 바꾸면 이전 값 대신 최신 값이 집계됩니다.",
            border=True,
        )
        st_module.metric(
            "좋은 글감",
            f"{int(diagnostics['good_count']):,}건",
            f"{float(diagnostics['good_rate_percent']):.1f}%",
            help="사용자가 ‘좋은 글감’으로 평가한 군집 수와 전체 평가 대비 비율입니다.",
            border=True,
        )
        st_module.metric(
            "애매한 글감",
            f"{int(diagnostics['ambiguous_count']):,}건",
            f"{float(diagnostics['ambiguous_rate_percent']):.1f}%",
            help="사용자가 추가 검토가 필요하다고 본 ‘애매한 글감’ 수와 비율입니다.",
            border=True,
        )
        st_module.metric(
            "제외 판단",
            f"{int(diagnostics['rejected_count']):,}건",
            f"{float(diagnostics['rejected_rate_percent']):.1f}%",
            help="‘쓸모없는 글감’과 ‘잘못 묶인 주제’를 합친 수입니다. 이 화면은 진단만 하며 자동 삭제하지 않습니다.",
            border=True,
        )
        st_module.metric(
            "독립 근거 평균",
            f"{_format_average(diagnostics['average_unique_evidence_count'])}건",
            f"1건 이하 {int(diagnostics['low_evidence_count']):,}건",
            help="평가를 저장한 시점에 중복 URL·복제 원문을 제외하고 남은 독립 근거 수의 평균입니다.",
            border=True,
        )
        st_module.metric(
            "발행처 평균",
            f"{_format_average(diagnostics['average_publisher_count'])}곳",
            f"1곳 이하 {int(diagnostics['single_publisher_count']):,}건",
            help="평가 당시 독립 원문을 제공한 서로 다른 발행처 수의 평균입니다.",
            border=True,
        )

    st_module.caption(
        f"마지막 평가 갱신: {_format_time(diagnostics['latest_updated_at'])} · "
        f"평가 당시 원문 평균 {_format_average(diagnostics['average_item_count'])}건 · "
        f"출처 유형 평균 {_format_average(diagnostics['average_source_type_count'])}개"
    )

    with st_module.expander("평가 유형별 근거 품질 비교", expanded=False):
        type_frame = pd.DataFrame(
            [
                {
                    "평가": FEEDBACK_LABELS.get(
                        str(row.get("feedback_type") or ""),
                        str(row.get("feedback_type") or ""),
                    ),
                    "평가 수": int(row.get("evaluated_count") or 0),
                    "원문 평균": _format_average(row.get("average_item_count")),
                    "독립 근거 평균": _format_average(
                        row.get("average_unique_evidence_count")
                    ),
                    "출처 유형 평균": _format_average(
                        row.get("average_source_type_count")
                    ),
                    "발행처 평균": _format_average(row.get("average_publisher_count")),
                    "독립 근거 1건 이하": (
                        f"{int(row.get('low_evidence_count') or 0):,}건 · "
                        f"{float(row.get('low_evidence_rate_percent') or 0):.1f}%"
                    ),
                    "발행처 1곳 이하": (
                        f"{int(row.get('single_publisher_count') or 0):,}건 · "
                        f"{float(row.get('single_publisher_rate_percent') or 0):.1f}%"
                    ),
                }
                for row in diagnostics["type_rows"]
            ]
        )
        st_module.dataframe(type_frame, hide_index=True, width="stretch")
        st_module.caption(
            "근거 개수는 평가를 저장한 당시의 스냅샷입니다. 표본이 적을 때는 비율보다 개별 글감을 함께 확인하세요."
        )

    with st_module.expander("현재 연결 출처별 평가 관계", expanded=False):
        source_rows = diagnostics["source_type_rows"]
        if not source_rows:
            st_module.caption("평가된 글감과 현재 연결된 원문 출처를 찾지 못했습니다.")
        else:
            source_frame = pd.DataFrame(
                [
                    {
                        "출처 유형": QUERY_SOURCE_TYPE_LABELS.get(
                            str(row.get("source_type") or ""),
                            str(row.get("source_type") or ""),
                        ),
                        "평가 글감": int(row.get("evaluated_count") or 0),
                        "좋은 글감": int(row.get("good_count") or 0),
                        "좋은 비율": f"{float(row.get('good_rate_percent') or 0):.1f}%",
                        "애매": int(row.get("ambiguous_count") or 0),
                        "쓸모없음": int(row.get("useless_count") or 0),
                        "잘못 묶임": int(row.get("false_merge_count") or 0),
                        "제외 비율": f"{float(row.get('rejected_rate_percent') or 0):.1f}%",
                    }
                    for row in source_rows
                ]
            )
            st_module.dataframe(source_frame, hide_index=True, width="stretch")
        st_module.caption(
            "출처 관계는 현재 군집에 연결된 원문을 기준으로 합니다. 특정 출처가 평가 결과의 원인이라는 뜻은 아닙니다."
        )

    with st_module.expander("검색어별 사용자 평가 관계", expanded=False):
        query_rows = diagnostics["query_rows"]
        if not query_rows:
            st_module.caption("평가된 글감과 연결할 수 있는 검색어 발견 기록이 없습니다.")
        else:
            query_frame = pd.DataFrame(
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
                        "평가 글감": int(row.get("evaluated_count") or 0),
                        "좋은 글감": int(row.get("good_count") or 0),
                        "좋은 비율": f"{float(row.get('good_rate_percent') or 0):.1f}%",
                        "애매": int(row.get("ambiguous_count") or 0),
                        "쓸모없음": int(row.get("useless_count") or 0),
                        "잘못 묶임": int(row.get("false_merge_count") or 0),
                        "제외 비율": f"{float(row.get('rejected_rate_percent') or 0):.1f}%",
                    }
                    for row in query_rows
                ]
            )
            st_module.dataframe(
                query_frame,
                hide_index=True,
                width="stretch",
                height=420,
            )
        st_module.caption(
            "같은 글감이 여러 검색어와 연결될 수 있습니다. 평가 표본이 충분히 쌓이기 전에는 검색어를 자동 감점하거나 제거하지 않습니다."
        )

    with st_module.expander("최근 사용자 평가 상세", expanded=False):
        recent_frame = pd.DataFrame(
            [
                {
                    "갱신 시각": _format_time(row.get("updated_at")),
                    "평가": FEEDBACK_LABELS.get(
                        str(row.get("feedback_type") or ""),
                        str(row.get("feedback_type") or ""),
                    ),
                    "글감": str(row.get("canonical_title") or ""),
                    "원문": int(row.get("item_count") or 0),
                    "독립 근거": int(row.get("unique_evidence_count") or 0),
                    "근거 유지율": f"{float(row.get('evidence_retention_percent') or 0):.1f}%",
                    "출처 유형": int(row.get("source_type_count") or 0),
                    "발행처": int(row.get("publisher_count") or 0),
                    "메모": str(row.get("note") or "")[:300],
                }
                for row in diagnostics["recent_rows"]
            ]
        )
        st_module.dataframe(
            recent_frame,
            hide_index=True,
            width="stretch",
            height=420,
        )

    preview = get_score_adjustment_preview(con)
    with st_module.expander("글감 기회 점수 보정 미리보기", expanded=False):
        preview_rows = preview["rows"]
        st_module.caption(
            f"전체 평가 {int(preview['total_feedback_count']):,}건 중 현재 순위와 연결된 평가 "
            f"{int(preview['current_feedback_count']):,}건 · 숫자 미리보기 가능 "
            f"{int(preview['eligible_count']):,}건"
        )
        if not preview_rows:
            st_module.caption(
                "현재 순위에 남아 있는 평가 글감이 없어 보정 미리보기를 만들지 못했습니다. "
                "다음 순위 재계산 뒤에도 같은 글감이 유지되는지 확인하세요."
            )
        else:
            preview_frame = pd.DataFrame(
                [
                    {
                        "평가 갱신": _format_time(row.get("updated_at")),
                        "평가": str(row.get("feedback_label") or ""),
                        "글감": str(row.get("canonical_title") or ""),
                        "현재 판정": _RECOMMENDATION_LABELS.get(
                            str(row.get("recommendation_status") or ""),
                            str(row.get("recommendation_status") or ""),
                        ),
                        "트렌드": f"{float(row.get('trend_score') or 0):.1f}",
                        "글감 기회 원점수": f"{float(row.get('original_opportunity_score') or 0):.1f}",
                        "조정 방향": (
                            f"{row.get('direction')} {_format_signed(row.get('suggested_adjustment'))}"
                        ),
                        "예상 조정점수": (
                            f"{float(row['preview_opportunity_score']):.1f}"
                            if row.get("preview_opportunity_score") is not None
                            else "표본 부족"
                        ),
                        "표본 상태": str(row.get("sample_status") or ""),
                        "표본 근거": str(row.get("sample_reason") or "충족"),
                        "조정 근거": " · ".join(row.get("adjustment_reasons") or ()),
                    }
                    for row in preview_rows
                ]
            )
            st_module.dataframe(
                preview_frame,
                hide_index=True,
                width="stretch",
                height=460,
            )
        st_module.caption(
            f"전체 평가 {int(preview['minimum_total_feedback'])}건 이상이고 같은 평가 유형이 "
            f"{int(preview['minimum_feedback_type_count'])}건 이상일 때만 숫자를 표시합니다. "
            f"조정 폭은 최대 ±{float(preview['maximum_adjustment']):.0f}점이며, "
            "사용자 평가 방향을 근거 품질 보정이 뒤집지 않게 제한합니다."
        )
        st_module.caption(
            "보정 대상은 글감 기회 점수뿐입니다. 급상승 트렌드 점수, 추천 상태, 군집과 DB 값은 변경하지 않습니다. "
            "근거 개수는 평가 당시 스냅샷이므로 실제 적용 기능을 만들기 전에는 현재 원문을 다시 확인해야 합니다."
        )

    st_module.caption(
        "이 진단은 사용자 평가 패턴을 보여주기만 합니다. 트렌드 점수, 글감 기회 점수, 추천 상태, "
        "군집, 검색어와 원문을 자동 변경하지 않습니다."
    )
