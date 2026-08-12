"""출처별 신선도와 백그라운드 수집 주기 상태를 설정 화면에 표시합니다."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.fact_check_readiness_ui import render_fact_check_readiness
from src.services.collection_history_service import SOURCE_LABELS
from src.services.source_freshness_service import get_source_freshness_diagnostics

STATE_LABELS = {
    "healthy": "정상",
    "warning": "주의",
    "stale": "지연",
    "failure": "실패",
    "no_history": "기록 없음",
    "overdue": "지연",
}
SOURCE_RESULT_LABELS = {
    "success": "성공",
    "partial_success": "부분 성공",
    "failure": "실패",
    "skipped": "생략",
}


def _format_time(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else "기록 없음"


def _format_minutes(value) -> str:
    if value is None:
        return "기록 없음"
    minutes = max(0, int(value))
    if minutes < 60:
        return f"{minutes}분 전"
    hours, remaining = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}시간 {remaining}분 전" if remaining else f"{hours}시간 전"
    days, remaining_hours = divmod(hours, 24)
    return (
        f"{days}일 {remaining_hours}시간 전"
        if remaining_hours
        else f"{days}일 전"
    )


def _format_interval(minutes: int) -> str:
    if minutes % 1_440 == 0:
        return f"{minutes // 1_440}일"
    if minutes % 60 == 0:
        return f"{minutes // 60}시간"
    if minutes > 60:
        hours, remaining = divmod(minutes, 60)
        return f"{hours}시간 {remaining}분"
    return f"{minutes}분"


def render_source_freshness_diagnostics(con, *, st_module=st) -> None:
    if hasattr(con, "execute"):
        render_fact_check_readiness(con, st_module=st_module)
    diagnostics = get_source_freshness_diagnostics(con)
    interval = int(diagnostics["interval_minutes"])
    stale_minutes = int(diagnostics["stale_minutes"])
    scheduler_state = str(diagnostics["scheduler_state"])

    st_module.subheader("출처 신선도·스케줄러 상태")
    with st_module.container(horizontal=True):
        st_module.metric(
            "저장된 수집 주기",
            _format_interval(interval),
            f"지연 기준 {_format_interval(stale_minutes)}",
            help=(
                "설정에 저장된 자동 수집 간격입니다. 마지막 백그라운드 실행 또는 출처 정상 수집이 "
                "한 주기를 넘으면 주의, 두 주기를 넘으면 지연으로 표시합니다."
            ),
            border=True,
        )
        st_module.metric(
            "스케줄러 상태",
            STATE_LABELS.get(scheduler_state, scheduler_state),
            SOURCE_RESULT_LABELS.get(
                str(diagnostics.get("latest_background_status") or ""),
                str(diagnostics.get("latest_background_status") or "기록 없음"),
            ),
            help=(
                "Windows 작업 스케줄러 자체를 직접 조회한 값이 아니라, 실제로 저장된 "
                "background_refresh 실행 시각을 기준으로 계산한 상태입니다."
            ),
            border=True,
        )
        st_module.metric(
            "마지막 백그라운드 실행",
            _format_minutes(diagnostics.get("background_elapsed_minutes")),
            _format_time(diagnostics.get("last_background_at")),
            help="예약·BAT 백그라운드 수집이 마지막으로 시작된 시각입니다.",
            border=True,
        )
        st_module.metric(
            "주의 출처",
            f"{int(diagnostics['attention_source_count']):,}개",
            f"지연 {int(diagnostics['stale_source_count']):,} · 실패 {int(diagnostics['failed_source_count']):,}",
            help="최근 부분 성공·실패 또는 설정 주기보다 오래 정상 수집되지 않은 출처 수입니다.",
            border=True,
        )
        st_module.metric(
            "이력 없는 출처",
            f"{int(diagnostics['no_history_source_count']):,}개",
            help="현재 보관 중인 수집 이력에서 한 번도 확인되지 않은 출처입니다.",
            border=True,
        )

    st_module.caption(
        f"다음 실행 예상 기준 {_format_time(diagnostics.get('next_expected_at'))} · "
        f"지연 판정 기준 {_format_time(diagnostics.get('stale_after_at'))} · "
        f"마지막 전체 성공 {_format_time(diagnostics.get('last_background_success_at'))}"
    )

    if scheduler_state == "overdue":
        st_module.warning(
            "저장된 수집 주기의 두 배가 지나도록 백그라운드 실행 기록이 없습니다. "
            "Windows 작업 스케줄러 상태, 작업 계정과 run_trend_refresh.bat 실행 결과를 확인하세요."
        )
    elif scheduler_state == "warning":
        st_module.warning(
            "백그라운드 실행이 한 주기를 넘겼거나 최근 실행이 부분 성공·실패였습니다. "
            "아래 출처별 상태와 최근 수집 이력을 함께 확인하세요."
        )

    background_error = str(diagnostics.get("background_error_message") or "").strip()
    if background_error:
        st_module.caption(f"최근 백그라운드 오류: {background_error[:500]}")

    source_rows = diagnostics["source_rows"]
    frame = pd.DataFrame(
        [
            {
                "출처": SOURCE_LABELS.get(
                    str(row.get("source_name") or ""),
                    str(row.get("source_name") or ""),
                ),
                "상태": STATE_LABELS.get(
                    str(row.get("state") or ""),
                    str(row.get("state") or ""),
                ),
                "최근 결과": SOURCE_RESULT_LABELS.get(
                    str(row.get("latest_status") or ""),
                    str(row.get("latest_status") or "기록 없음"),
                ),
                "마지막 시도": _format_time(row.get("latest_at")),
                "시도 후 경과": _format_minutes(row.get("latest_elapsed_minutes")),
                "마지막 정상": _format_time(row.get("last_healthy_at")),
                "정상 후 경과": _format_minutes(row.get("healthy_elapsed_minutes")),
                "마지막 신규": _format_time(row.get("last_new_at")),
                "연속 문제": int(row.get("consecutive_problem_count") or 0),
                "최근 신규": int(row.get("newly_saved_count") or 0),
                "최근 갱신": int(row.get("updated_count") or 0),
                "오류 요약": str(row.get("error_message") or "")[:300],
            }
            for row in source_rows
        ]
    )
    st_module.dataframe(frame, hide_index=True, width="stretch")
    st_module.caption(
        "출처 신선도는 수동 수집과 예약·백그라운드 수집의 출처별 이력을 함께 사용합니다. "
        "‘생략’은 비활성 설정이나 변경 없는 입력처럼 정상적인 경우도 있으므로 실패로 세지 않습니다. "
        "새 원문이 없다는 사실만으로 출처 장애로 판단하지 않습니다."
    )
