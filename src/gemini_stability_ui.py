"""설정 화면에 Gemini 글감 분석 안정성과 처리량 추천을 표시합니다."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.services.gemini_stability_service import (
    GENERATION_TOKEN_WARNING,
    GeminiRunWindow,
    get_gemini_stability_recommendation,
)
from src.services.gemini_usage_log_service import (
    get_gemini_usage_log_summary,
)


def _percent(value: float | None) -> str:
    return "기록 없음" if value is None else f"{value * 100:.1f}%"


def _duration(value_ms: int) -> str:
    if value_ms <= 0:
        return "기록 없음"
    seconds = value_ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}초"
    minutes, remaining = divmod(int(round(seconds)), 60)
    return f"{minutes}분 {remaining}초"


def _window_row(label: str, window: GeminiRunWindow) -> dict[str, object]:
    return {
        "기간": str(label),
        "완료 실행": f"{window.run_count:,}회",
        "요청 글감": f"{window.requested_clusters:,}개",
        "저장 글감": f"{window.generated_clusters:,}개",
        "저장률": _percent(window.save_rate),
        "부분 성공": f"{window.partial_runs:,}회",
        "실패": f"{window.failed_runs:,}회",
        "API 시도": f"{window.request_count:,}회",
        "재시도": f"{window.retry_count:,}회",
        "평균 소요": _duration(window.average_duration_ms),
    }


def render_gemini_stability_panel(
    con,
    *,
    app_id: str,
    current_items_per_request: int,
    current_thinking_level: str,
    st_module=st,
) -> None:
    recommendation = get_gemini_stability_recommendation(
        con,
        app_id=app_id,
        current_items_per_request=current_items_per_request,
        current_thinking_level=current_thinking_level,
    )

    calls = recommendation.calls

    with st_module.expander("Gemini 글감 생성 안정성·설정 추천", expanded=False):
        st_module.caption(
            "최근 Gemini 글감 분석 실행과 API 호출 기록을 읽어 요청당 처리량을 보수적으로 추천합니다. "
            "모델·처리량·사고 수준은 자동으로 변경하지 않습니다."
        )

        metric_columns = st_module.columns(5)
        metric_columns[0].metric(
            "처리량 추천",
            recommendation.recommendation_label,
            help=(
                f"현재 요청당 {recommendation.current_items_per_request}개를 기준으로 유지하거나, "
                "필요할 때만 현재값보다 낮은 처리량을 추천합니다. "
                "저장소 기본값과 허용 상한은 30개입니다."
            ),
            border=True,
        )
        metric_columns[1].metric(
            "최근 최대 30회 저장률",
            _percent(recommendation.recent_30.save_rate),
            help="요청한 글감 중 프로필과 방향이 실제 저장된 글감의 비율입니다.",
            border=True,
        )
        metric_columns[2].metric(
            "부분 성공·실패",
            f"{recommendation.recent_30.partial_runs + recommendation.recent_30.failed_runs}회",
            help="최근 최대 30회 중 일부만 저장됐거나 저장 결과가 없었던 실행 수입니다.",
            border=True,
        )
        metric_columns[3].metric(
            "응답 검증 실패",
            f"{calls.validation_failure_count}회",
            help="JSON은 반환됐지만 유효한 글감 분석 결과로 저장하지 못한 API 시도 수입니다.",
            border=True,
        )
        metric_columns[4].metric(
            "토큰 한도 종료",
            f"{calls.max_tokens_count}회",
            help="Gemini 응답의 finish reason이 MAX_TOKENS로 기록된 API 시도 수입니다.",
            border=True,
        )

        if recommendation.evaluation_status == "표본 부족":
            st_module.info(
                f"표본이 더 쌓일 때까지 현재 요청당 {recommendation.current_items_per_request}개 "
                "설정을 유지하는 편이 안전합니다."
            )
        elif recommendation.recommended_items_per_request < recommendation.current_items_per_request:
            st_module.warning(
                f"현재 요청당 {recommendation.current_items_per_request}개에서 "
                f"{recommendation.recommended_items_per_request}개로 낮추는 것을 권장합니다."
            )
        elif recommendation.evaluation_status == "유지·추가 점검":
            st_module.warning(
                f"현재 요청당 {recommendation.current_items_per_request}개 설정은 유지하되, "
                "처리량 외 원인을 추가 점검하는 것을 권장합니다."
            )
        elif recommendation.evaluation_status == "유지·관찰":
            st_module.info(
                f"현재 요청당 {recommendation.current_items_per_request}개 설정을 유지하면서 "
                "안정성 신호를 더 관찰합니다."
            )
        else:
            st_module.success(
                f"현재 요청당 {recommendation.current_items_per_request}개 설정을 유지해도 되는 "
                "기록 상태입니다."
            )

        st_module.markdown("**판단 근거**")
        for reason in recommendation.reasons:
            st_module.markdown(f"- {reason}")

        windows = pd.DataFrame(
            [
                _window_row("최근 최대 10회", recommendation.recent_10),
                _window_row("최근 최대 30회", recommendation.recent_30),
            ]
        )
        st_module.dataframe(windows, hide_index=True, width="stretch")

        call_columns = st_module.columns(4)
        call_columns[0].metric(
            "분석 API 시도",
            f"{calls.attempt_count:,}회",
            help="최근 최대 30회 실행 구간의 글감 분석 API 시도입니다. 재시도도 각각 포함합니다.",
            border=True,
        )
        call_columns[1].metric(
            "평균 생성 토큰",
            f"{calls.average_generation_tokens:,}",
            help="출력 토큰과 사고 토큰을 합친 기록의 평균입니다.",
            border=True,
        )
        call_columns[2].metric(
            "최대 생성 토큰",
            f"{calls.maximum_generation_tokens:,}",
            help=f"{GENERATION_TOKEN_WARNING:,} 이상이면 JSON 잘림 가능성을 주의합니다.",
            border=True,
        )
        call_columns[3].metric(
            "한도 근접",
            f"{calls.near_limit_count:,}회",
            help=f"생성 토큰이 {GENERATION_TOKEN_WARNING:,} 이상이었던 API 시도 수입니다.",
            border=True,
        )

        st_module.markdown("**오류·재시도 세부 현황 (최근 최대 30회 실행 구간)**")

        retry_summary_columns = st_module.columns(4)
        retry_summary_columns[0].metric(
            "Rate limit 영향 요청",
            f"{calls.rate_limit_affected_request_count:,}건",
            help="HTTP 429나 재시도 대기가 발생했던 독립 요청 묶음 수입니다.",
            border=True,
        )
        retry_summary_columns[1].metric(
            "재시도 후 복구",
            f"{calls.retry_recovered_request_count:,}건",
            help="Rate limit 대기 후 최종 정상 완료된 요청 묶음 수입니다.",
            border=True,
        )
        retry_summary_columns[2].metric(
            "Rate limit 최종 실패",
            f"{calls.rate_limited_final_request_count:,}건",
            help="대기 상한(30초) 초과로 최종 실패한 요청 묶음 수입니다.",
            border=True,
        )
        retry_summary_columns[3].metric(
            "재시도 누적 대기",
            f"{calls.retry_wait_total_seconds:.1f}초",
            help=(
                f"호출 내부 재시도 대기시간의 총합입니다. "
                f"평균 {calls.retry_wait_average_seconds:.1f}초, "
                f"최대 {calls.retry_wait_max_seconds:.1f}초"
            ),
            border=True,
        )

        detail_rows = [
            {
                "분류": "재시도 대기 기록",
                "요청·시도 건수": f"{calls.retrying_attempt_count:,}회",
                "설명": "429 등으로 호출 내부에서 재시도 대기 중 기록된 시도 행입니다.",
            },
            {
                "분류": "일반 Rate limit",
                "요청·시도 건수": f"{calls.rate_limit_attempt_count:,}회",
                "설명": "초당/분당 요청 제한(HTTP 429 등)으로 대기 또는 실패한 시도 수입니다.",
            },
            {
                "분류": "일일 Quota",
                "요청·시도 건수": f"{calls.quota_exhausted_count:,}회",
                "설명": "일일 쿼터 소진(daily_quota_exhausted)으로 중단된 시도 수입니다.",
            },
            {
                "분류": "Timeout",
                "요청·시도 건수": f"{calls.timeout_count:,}회",
                "설명": "요청 제한시간 내 응답하지 못해 연결 종료된 시도 수입니다.",
            },
            {
                "분류": "네트워크 오류",
                "요청·시도 건수": f"{calls.network_error_count:,}회",
                "설명": "소켓/연결 오류 등 네트워크 통신 실패 수입니다.",
            },
            {
                "분류": "서버 오류",
                "요청·시도 건수": f"{calls.server_error_count:,}회",
                "설명": "Gemini API 5xx 서버 장애 수입니다.",
            },
            {
                "분류": "응답 검증 실패",
                "요청·시도 건수": f"{calls.validation_failure_count:,}회",
                "설명": "HTTP 200 수신 후 JSON 검증 또는 핵심 정보 저장에 실패한 수입니다.",
            },
            {
                "분류": "MAX_TOKENS",
                "요청·시도 건수": f"{calls.max_tokens_count:,}회",
                "설명": "생성 토큰 한도 초과(MAX_TOKENS)로 응답이 절단된 수입니다.",
            },
            {
                "분류": "잘못된 요청",
                "요청·시도 건수": f"{calls.invalid_request_count:,}회",
                "설명": "HTTP 400 등 요청 파라미터 규격 오류 수입니다.",
            },
            {
                "분류": "기타",
                "요청·시도 건수": f"{calls.other_failure_count:,}회",
                "설명": "기타 분류되지 않은 실패 시도 수입니다.",
            },
        ]
        details_df = pd.DataFrame(detail_rows)
        st_module.dataframe(details_df, hide_index=True, width="stretch")

        st_module.caption(
            "※ 재시도 행 수는 실제 실패 요청 수와 다르며, 재시도 후 성공은 정상 복구로 별도 집계합니다. "
            "실행 간 지속 cooldown 상태는 현재 구현에 없습니다. "
            "rate limit 발생을 요청당 글감 수 문제로 단정하지 않습니다."
        )

        thinking_summary = ", ".join(
            f"{level} {count:,}회"
            for level, count in calls.thinking_level_counts
        ) or "기록 없음"
        finish_summary = ", ".join(
            f"{reason} {count:,}회"
            for reason, count in calls.finish_reason_counts
        ) or "기록 없음"
        st_module.caption(
            "실제 요청 글감 수 기록 "
            f"{calls.recorded_requested_item_count:,}회 · "
            f"평균 {calls.average_requested_item_count:.1f}개 · "
            f"최대 {calls.maximum_requested_item_count:,}개"
        )
        st_module.caption(
            f"사고 수준별 호출: {thinking_summary} · 종료 사유별 호출: {finish_summary} · "
            f"종료 사유 미기록 {calls.missing_finish_reason_count:,}회"
        )

        st_module.caption(
            f"사고 수준 추천: {recommendation.thinking_recommendation} · "
            "현재 사고 수준은 자동 변경하지 않습니다. 현재 기록만으로 처리량 효과와 사고 수준 효과를 "
            "분리하기 어려워, 문제가 있으면 처리량을 먼저 조정합니다. "
            "이 진단은 `trend_topic_angle_batch_v1` 글감 분석만 포함하고 Gemini 직접 초안 생성은 제외합니다."
        )


def _usage_time(value: object) -> str:
    formatter = getattr(value, "strftime", None)
    if callable(formatter):
        return formatter("%Y-%m-%d %H:%M:%S")
    return str(value or "-")


def render_gemini_usage_log_panel(
    con,
    *,
    app_id: str,
    st_module=st,
) -> None:
    summary = get_gemini_usage_log_summary(
        con,
        app_id=app_id,
        period_days=30,
        limit=500,
    )
    with st_module.expander(
        "Gemini API 모델·기능별 사용 로그",
        expanded=False,
    ):
        st_module.caption(
            "최근 30일 동안 이 프로그램이 `gemini_api_calls`에 기록한 실제 외부 호출을 "
            "모델과 기능별로 보여줍니다. 3.6 Flash 자동 분석과 3.5 Flash-Lite 기본 군집화를 "
            "따로 확인할 수 있습니다. Google AI Studio의 공식 남은 RPM·RPD·결제 사용량을 "
            "조회하는 화면은 아닙니다."
        )

        metric_columns = st_module.columns(5)
        metric_columns[0].metric(
            "최근 30일 API 호출",
            f"{summary.attempt_count:,}회",
            help="캐시 적중을 제외하고 실제 외부 Gemini API 호출로 기록된 행 수입니다.",
            border=True,
        )
        metric_columns[1].metric(
            "3.6 Flash 호출",
            f"{summary.flash_36_attempt_count:,}회",
            help="모델명이 gemini-3.6-flash로 시작하는 실제 호출 수입니다.",
            border=True,
        )
        metric_columns[2].metric(
            "자동 분석 호출",
            f"{summary.auto_analysis_attempt_count:,}회",
            help="trend_topic_angle_batch_v1 글감 분석 호출 수입니다.",
            border=True,
        )
        metric_columns[3].metric(
            "AI 군집화 호출",
            f"{summary.cluster_review_attempt_count:,}회",
            help="trend_cluster_grouping_v3 2단계 군집화와 과거 군집 호출 수입니다.",
            border=True,
        )
        metric_columns[4].metric(
            "기록 토큰 합계",
            f"{summary.total_tokens:,}",
            help="각 호출의 total_tokens를 우선 사용하고 없으면 입력·출력·사고 토큰을 합산합니다.",
            border=True,
        )

        if not summary.rows:
            st_module.info(
                "최근 30일 실제 Gemini API 호출 기록이 없습니다. 순위 재계산이나 자동 글감 분석을 "
                "실행한 뒤 다시 확인하세요."
            )
            return

        grouped_rows = [
            {
                "모델": row["model_name"],
                "기능": row["feature_label"],
                "기능 ID": row["feature_id"],
                "호출": int(row["attempt_count"]),
                "성공": int(row["successful_count"]),
                "실패": int(row["failed_count"]),
                "재시도 중": int(row["retrying_count"]),
                "요청 항목": int(row["requested_item_count"]),
                "입력 토큰": int(row["input_tokens"]),
                "출력 토큰": int(row["output_tokens"]),
                "사고 토큰": int(row["thought_tokens"]),
                "전체 토큰": int(row["total_tokens"]),
                "최근 호출": _usage_time(row["latest_created_at"]),
            }
            for row in summary.grouped_rows
        ]
        st_module.dataframe(
            pd.DataFrame(grouped_rows),
            hide_index=True,
            width="stretch",
        )
        st_module.caption(
            "요청 항목은 자동 분석에서는 글감 수, 기본 군집화에서는 제목 후보 수입니다. "
            "API 키 없음으로 AI 군집화를 건너뛴 경우에는 외부 호출 기록이 생기지 않습니다."
        )

        failure_rows = [
            row
            for row in summary.rows
            if str(row.get("status") or "").strip().casefold()
            not in {"success", "success_after_retry", "retrying"}
        ]
        with st_module.expander(
            f"실패 호출 상세 ({len(failure_rows):,}회)",
            expanded=False,
        ):
            st_module.caption(
                "최근 30일 조회 범위에서 최종 실패로 기록된 호출만 표시합니다. "
                "재시도 중 기록과 재시도 후 성공은 실패에서 제외합니다."
            )
            if not failure_rows:
                st_module.success("최근 30일 최종 실패 호출 기록이 없습니다.")
            else:
                failure_detail_rows = [
                    {
                        "시각": _usage_time(row.get("created_at")),
                        "모델": str(row.get("model_name") or "모델 미기록"),
                        "기능": str(row.get("feature_id") or "기능 미기록"),
                        "상태": str(row.get("status") or "-"),
                        "HTTP": row.get("http_status"),
                        "오류": str(row.get("error_type") or ""),
                        "요청 항목": int(row.get("requested_item_count") or 0),
                        "입력 토큰": int(row.get("input_tokens") or 0),
                        "출력 토큰": int(row.get("output_tokens") or 0),
                        "사고 토큰": int(row.get("thought_tokens") or 0),
                        "전체 토큰": int(row.get("total_tokens") or 0),
                    }
                    for row in failure_rows
                ]
                st_module.dataframe(
                    pd.DataFrame(failure_detail_rows),
                    hide_index=True,
                    width="stretch",
                )

        with st_module.expander("최근 호출 상세", expanded=False):
            detail_rows = [
                {
                    "시각": _usage_time(row.get("created_at")),
                    "모델": str(row.get("model_name") or "모델 미기록"),
                    "기능": str(row.get("feature_id") or "기능 미기록"),
                    "상태": str(row.get("status") or "-"),
                    "요청 항목": int(row.get("requested_item_count") or 0),
                    "입력 토큰": int(row.get("input_tokens") or 0),
                    "출력 토큰": int(row.get("output_tokens") or 0),
                    "사고 토큰": int(row.get("thought_tokens") or 0),
                    "전체 토큰": int(row.get("total_tokens") or 0),
                    "HTTP": row.get("http_status"),
                    "오류": str(row.get("error_type") or ""),
                }
                for row in summary.rows[:50]
            ]
            st_module.dataframe(
                pd.DataFrame(detail_rows),
                hide_index=True,
                width="stretch",
            )
