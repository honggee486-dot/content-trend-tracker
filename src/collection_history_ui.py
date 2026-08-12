"""최근 수집 실행 이력과 Gemini 사용량을 설정 화면에 표시합니다."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import PROJECT_ROOT, get_gemini_config
from src.query_discovery_diagnostics_ui import render_query_discovery_diagnostics
from src.services.collection_history_service import (
    RUN_STATUS_LABELS,
    RUN_TYPE_LABELS,
    SOURCE_LABELS,
    get_collection_history_summary,
    list_collection_run_sources,
    list_recent_collection_runs,
)
from src.services.collection_history_view_service import (
    GEMINI_STATE_LABELS,
    RUN_DISPLAY_STATUS_LABELS,
    RUN_DISPLAY_STATUS_REVIEW,
    annotate_collection_run_display_statuses,
    filter_collection_runs,
    inspect_collection_history_lock_state,
    list_collection_run_source_map,
    topic_angle_run_summary,
)
from src.services.gemini_model_service import (
    MODEL_PURPOSE_AUTO,
    MODEL_PURPOSE_MANUAL,
    get_selected_gemini_model,
    model_info_map,
    model_rate_limit_reference,
)
from src.services.gemini_usage_service import (
    get_daily_gemini_usage,
    list_recent_gemini_usage,
    model_token_limits,
)
from src.services.topic_angle_history_service import get_topic_angle_history_summary


GENERATION_TOKEN_WARNING_THRESHOLD = 65_000


def _format_time(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else "기록 없음"


def _format_duration(duration_ms) -> str:
    if duration_ms is None:
        return "-"
    milliseconds = max(0, int(duration_ms or 0))
    if milliseconds < 1000:
        return f"{milliseconds}ms"
    seconds = milliseconds / 1000
    if seconds < 60:
        return f"{seconds:.1f}초"
    return f"{int(seconds // 60)}분 {int(seconds % 60)}초"


def _format_elapsed(value) -> str:
    if value is None:
        return "기록 없음"
    seconds = max(0, int(value.total_seconds()))
    if seconds < 60:
        return f"{seconds}초 전"
    if seconds < 3600:
        return f"{seconds // 60}분 전"
    if seconds < 86400:
        return f"{seconds // 3600}시간 전"
    return f"{seconds // 86400}일 전"


def _format_source_result(row: dict[str, object]) -> str:
    newly_saved = int(row.get("newly_saved_count") or 0)
    updated = int(row.get("updated_count") or 0)
    skipped = int(row.get("skipped_count") or 0)
    if str(row.get("source_name") or "") == "topic_angles":
        return (
            f"글감 {updated:,}개 · 방향 {newly_saved:,}개 · "
            f"미처리 {skipped:,}개"
        )
    return f"신규 {newly_saved:,}개 · 갱신 {updated:,}개 · 생략 {skipped:,}개"


def _format_character_counts(row: dict[str, object], prefix: str) -> str:
    total = row.get(f"{prefix}_char_count")
    if total is None:
        return "이전 기록"
    non_whitespace = int(row.get(f"{prefix}_non_whitespace_char_count") or 0)
    hangul = int(row.get(f"{prefix}_hangul_char_count") or 0)
    return f"전체 {int(total):,} · 공백 제외 {non_whitespace:,} · 한글 {hangul:,}"


def _format_token_count(value: object) -> str:
    if value is None:
        return "이전 기록"
    return f"{int(value):,}"


def _format_generation_token_usage(
    row: dict[str, object],
    output_limit: int | None,
) -> tuple[str, bool]:
    output_tokens = row.get("output_tokens")
    thought_tokens = row.get("thought_tokens")
    if output_tokens is None and thought_tokens is None:
        return "이전 기록", False

    generation_tokens = int(output_tokens or 0) + int(thought_tokens or 0)
    near_limit = generation_tokens >= GENERATION_TOKEN_WARNING_THRESHOLD
    if output_limit is None:
        label = f"{generation_tokens:,}"
    else:
        percent = generation_tokens / max(1, int(output_limit)) * 100
        label = f"{generation_tokens:,}/{int(output_limit):,} · {percent:.1f}%"
    if near_limit:
        label += " · 한도 근접"
    return label, near_limit


def _feature_label(value: object) -> str:
    return {
        "blog_draft_generation_v1": "초안 생성",
        "trend_topic_angle_batch_v1": "글감 분석 일괄 생성",
    }.get(str(value or ""), str(value or ""))


def _render_gemini_usage(con) -> None:
    base_config = get_gemini_config()
    auto_model = get_selected_gemini_model(
        con,
        MODEL_PURPOSE_AUTO,
        base_config=base_config,
    )
    manual_model = get_selected_gemini_model(
        con,
        MODEL_PURPOSE_MANUAL,
        base_config=base_config,
    )
    selected_models = list(dict.fromkeys((auto_model, manual_model)))
    info_map = model_info_map(con, base_config=base_config)

    st.subheader("오늘 Gemini API 사용량")
    for model_name in selected_models:
        roles = []
        if model_name == auto_model:
            roles.append("자동·예약 분석")
        if model_name == manual_model:
            roles.append("수동 초안")
        rate_limit = model_rate_limit_reference(model_name)
        reference_limit = (
            int(rate_limit["rpd"])
            if rate_limit is not None
            else int(base_config.daily_request_reference_limit)
        )
        usage = get_daily_gemini_usage(
            con,
            app_id=base_config.app_id,
            reference_limit=reference_limit,
            model_name=model_name,
        )
        model_info = info_map.get(model_name)
        input_limit = model_info.input_token_limit if model_info is not None else None
        output_limit = model_info.output_token_limit if model_info is not None else None
        if input_limit is None or output_limit is None:
            known_input, known_output = model_token_limits(model_name)
            input_limit = input_limit or known_input
            output_limit = output_limit or known_output

        input_limit_text = f"{input_limit:,}토큰" if input_limit is not None else "확인 불가"
        output_limit_text = f"{output_limit:,}토큰" if output_limit is not None else "확인 불가"
        role_text = " · ".join(roles)
        st.markdown(f"#### {model_name} · {role_text}")
        with st.container(horizontal=True):
            st.metric(
                f"API 요청 · 참고 RPD {int(usage['reference_limit']):,}회",
                f"{int(usage['request_count']):,}회",
                help=(
                    "오늘 실제로 이 모델의 Gemini 서버에 전송한 API 호출 횟수입니다. "
                    "캐시 재사용은 제외하고 재시도 호출은 각각 포함합니다. "
                    "표시한 RPD는 프로그램 참고값이며 Google AI Studio 프로젝트 한도가 우선합니다."
                ),
                border=True,
            )
            st.metric(
                f"1회 최대 입력 · 한도 {input_limit_text}",
                f"{int(usage['max_input_tokens']):,}토큰",
                help=(
                    "오늘 이 모델 호출 중 입력 토큰이 가장 많았던 1회의 값입니다. "
                    f"오늘 누적 입력은 {int(usage['input_tokens']):,}토큰입니다."
                ),
                border=True,
            )
            st.metric(
                f"1회 최대 생성 · 출력 한도 참고 {output_limit_text}",
                f"{int(usage['max_generation_tokens']):,}토큰",
                help=(
                    "오늘 한 번의 호출에서 사용한 출력 토큰과 사고 토큰의 합계 중 최댓값입니다. "
                    f"오늘 1회 최대 최종 출력은 {int(usage['max_output_tokens']):,}토큰이고, "
                    f"오늘 누적 출력은 {int(usage['output_tokens']):,}토큰입니다."
                ),
                border=True,
            )
            st.metric(
                "사고 토큰",
                f"{int(usage['thought_tokens']):,}토큰",
                help="오늘 이 모델 호출에서 API가 별도로 알려준 사고 토큰 누적값입니다.",
                border=True,
            )
            st.metric(
                "전체 토큰",
                f"{int(usage['total_tokens']):,}토큰",
                help="오늘 이 모델의 실제 API 호출 전체 토큰 누적값입니다.",
                border=True,
            )

        rate_text = (
            f"참고 RPM {rate_limit['rpm']:,} · TPM {rate_limit['tpm']:,} · RPD {rate_limit['rpd']:,}"
            if rate_limit is not None
            else "모델별 RPM·TPM·RPD 참고값 없음"
        )
        st.caption(
            f"{rate_text} · 오늘 보낸 텍스트 {int(usage['request_char_count']):,}자 · "
            f"받은 텍스트 {int(usage['response_char_count']):,}자"
        )

    st.caption(
        "모델별 참고 한도와 프로그램 기록을 비교한 값입니다. 같은 API 키를 다른 프로그램에서 사용한 양과 "
        "Google 측 집계 지연은 포함되지 않으므로 실제 한도는 Google AI Studio에서 확인하세요."
    )

    recent_calls = list_recent_gemini_usage(
        con,
        app_id=base_config.app_id,
        limit=20,
    )
    if not recent_calls:
        st.caption("아직 저장된 Gemini API 호출 기록이 없습니다.")
        return

    with st.expander("최근 Gemini 호출 20건의 글자·토큰 상세", expanded=False):
        rows = []
        near_limit_count = 0
        for item in recent_calls:
            model_name = str(item.get("model_name") or "")
            model_info = info_map.get(model_name)
            output_limit = model_info.output_token_limit if model_info is not None else None
            if output_limit is None:
                _, output_limit = model_token_limits(model_name)
            generation_label, near_limit = _format_generation_token_usage(
                item,
                output_limit,
            )
            if near_limit:
                near_limit_count += 1
            rows.append(
                {
                    "시각": _format_time(item.get("created_at")),
                    "기능": _feature_label(item.get("feature_id")),
                    "모델": model_name,
                    "상태": item.get("status"),
                    "실제 요청 수": (
                        "기록 없음"
                        if item.get("requested_item_count") is None
                        else int(item["requested_item_count"])
                    ),
                    "설정 상한": (
                        "기록 없음"
                        if item.get("configured_items_per_request") is None
                        else int(item["configured_items_per_request"])
                    ),
                    "사고 수준": str(item.get("thinking_level") or "기록 없음"),
                    "제한 시간": (
                        "기록 없음"
                        if item.get("request_timeout_seconds") is None
                        else f"{int(item['request_timeout_seconds']):,}초"
                    ),
                    "보낸 글자": _format_character_counts(item, "request"),
                    "입력 토큰": _format_token_count(item.get("input_tokens")),
                    "받은 글자": _format_character_counts(item, "response"),
                    "출력 토큰": _format_token_count(item.get("output_tokens")),
                    "사고 토큰": _format_token_count(item.get("thought_tokens")),
                    "생성 토큰(출력+사고)": generation_label,
                    "전체 토큰": _format_token_count(item.get("total_tokens")),
                    "종료 사유": str(item.get("finish_reason") or "기록 없음"),
                    "종료 메시지": str(item.get("finish_message") or "")[:300],
                    "오류 요약": str(item.get("error_message") or "")[:300],
                    "소요 시간": _format_duration(item.get("duration_ms")),
                    "캐시": "예" if item.get("cache_hit") else "아니요",
                }
            )
        if near_limit_count:
            st.warning(
                f"최근 호출 중 생성 토큰(출력+사고)이 {GENERATION_TOKEN_WARNING_THRESHOLD:,}토큰 이상인 "
                f"호출이 {near_limit_count:,}건 있습니다. 응답 JSON이 잘리거나 필수 항목이 누락될 수 있으므로 "
                "같은 행의 상태와 오류 요약을 함께 확인하세요."
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(
            "생성 토큰은 출력 토큰과 사고 토큰의 합계입니다. "
            "0.10.49 이전 호출은 글자 수와 사고 토큰이 ‘이전 기록’ 또는 빈 값으로 표시될 수 있습니다."
        )


def _render_topic_angle_save_diagnostics(con) -> None:
    diagnostics = get_topic_angle_history_summary(con, limit=10)
    st.subheader("Gemini 글감 저장 진단")
    if int(diagnostics["history_count"]) == 0:
        st.caption("아직 비교할 Gemini 글감 분석 실행 이력이 없습니다.")
        return

    requested = int(diagnostics["last_requested_clusters"])
    saved = int(diagnostics["last_saved_clusters"])
    missing = int(diagnostics["last_missing_clusters"])
    save_rate = diagnostics["save_rate_percent"]
    save_rate_text = "기록 없음" if save_rate is None else f"{float(save_rate):.1f}%"
    status_label = {
        "success": "성공",
        "partial_success": "부분 성공",
        "failure": "실패",
        "skipped": "변경 없음",
    }.get(str(diagnostics["last_status"]), str(diagnostics["last_status"] or "기록 없음"))

    with st.container(horizontal=True):
        st.metric(
            "최근 요청 대비 저장",
            f"{saved:,}/{requested:,}개",
            status_label,
            help="가장 최근 Gemini 글감 분석에서 요청한 글감 수와 실제 저장된 글감 수입니다.",
            border=True,
        )
        st.metric(
            "최근 미처리",
            f"{missing:,}개",
            help="최근 요청에서 결과가 저장되지 않아 다음 실행 대상으로 남은 글감 수입니다.",
            border=True,
        )
        st.metric(
            "최근 10회 저장률",
            save_rate_text,
            help="최근 최대 10회 실행의 요청 글감 합계 대비 저장 글감 합계입니다.",
            border=True,
        )
        st.metric(
            "최근 10회 부분·실패",
            f"{int(diagnostics['problem_run_count']):,}회",
            help="최근 최대 10회 중 일부 누락 또는 전체 실패로 기록된 실행 수입니다.",
            border=True,
        )

    st.caption(
        f"최근 실행 {_format_time(diagnostics['last_started_at'])} · "
        f"방향 {int(diagnostics['last_generated_angles']):,}개 · "
        f"API 시도 {int(diagnostics['last_request_count']):,}회 · "
        f"재시도 {int(diagnostics['last_retry_count']):,}회 · "
        f"소요 {_format_duration(diagnostics['last_duration_ms'])}"
    )


def render_collection_history(con) -> None:
    _render_gemini_usage(con)
    _render_topic_angle_save_diagnostics(con)
    render_query_discovery_diagnostics(con, st_module=st)
    st.subheader("최근 수집 실행 이력")
    summary = get_collection_history_summary(con)
    recent_runs = list_recent_collection_runs(con, limit=30)
    lock_state = inspect_collection_history_lock_state(PROJECT_ROOT)
    recent_runs = annotate_collection_run_display_statuses(
        recent_runs,
        lock_state=lock_state,
    )

    with st.container(horizontal=True):
        st.metric(
            "마지막 자동·백그라운드 실행",
            _format_time(summary["last_background_at"]),
            RUN_STATUS_LABELS.get(summary["last_background_status"], "기록 없음"),
            help=(
                "Windows 작업 스케줄러나 run_trend_refresh.bat로 실행된 최근 백그라운드 수집의 "
                "시작 시각과 최종 상태입니다."
            ),
            border=True,
        )
        st.metric(
            "마지막 전체 성공",
            _format_time(summary["last_success_at"]),
            help="모든 필수 출처 수집이 전체 성공으로 끝난 가장 최근 실행 시각입니다.",
            border=True,
        )
        st.metric(
            "마지막 성공 후 경과",
            _format_elapsed(summary["elapsed_since_success"]),
            help="마지막 전체 성공 이후 현재까지 지난 시간입니다.",
            border=True,
        )
        st.metric(
            "연속 전체 성공",
            f"{int(summary['consecutive_success_count']):,}회",
            help="가장 최근 실행부터 거꾸로 세었을 때 중단 없이 이어진 전체 성공 횟수입니다.",
            border=True,
        )
        st.metric(
            "최근 24시간 부분·실패",
            f"{int(summary['recent_problem_count']):,}회",
            help="최근 24시간에 부분 성공 또는 실패로 기록된 수집 실행 횟수입니다.",
            border=True,
        )

    st.caption(
        "BAT 파일을 Windows 작업 스케줄러가 실행했는지 사용자가 직접 실행했는지는 "
        "신뢰할 수 있는 구분 신호가 없어 모두 ‘예약·백그라운드 수집’으로 표시합니다. "
        "Gemini 주제 방향은 선택적 후처리이므로 실패해도 출처 수집의 전체 성공 판정은 유지합니다."
    )
    review_count = sum(
        1 for row in recent_runs if row.get("display_status") == RUN_DISPLAY_STATUS_REVIEW
    )
    if review_count:
        st.warning(
            f"6시간 이상 '실행 중'으로 남았고 현재 수집·군집 heartbeat 잠금이 확인되지 않는 "
            f"이력이 {review_count:,}건 있습니다. DB 기록은 자동 변경하지 않고 ‘상태 확인 필요’로만 표시합니다."
        )
    if not recent_runs:
        st.info("아직 저장된 수집 실행 이력이 없습니다.")
        return

    with st.expander("최근 실행 30건과 출처별 상세 보기", expanded=False):
        run_ids = [str(row["run_id"]) for row in recent_runs]
        source_map = list_collection_run_source_map(con, run_ids)
        filter_cols = st.columns(3)
        selected_run_type = filter_cols[0].selectbox(
            "실행 유형",
            options=["", *RUN_TYPE_LABELS],
            format_func=lambda value: "전체" if not value else RUN_TYPE_LABELS[value],
            key="collection_history_run_type_filter",
        )
        selected_status = filter_cols[1].selectbox(
            "전체 상태",
            options=["", *RUN_DISPLAY_STATUS_LABELS],
            format_func=lambda value: (
                "전체" if not value else RUN_DISPLAY_STATUS_LABELS[value]
            ),
            key="collection_history_status_filter",
        )
        selected_gemini_state = filter_cols[2].selectbox(
            "Gemini 결과",
            options=list(GEMINI_STATE_LABELS),
            format_func=lambda value: GEMINI_STATE_LABELS[value],
            key="collection_history_gemini_filter",
        )
        filtered_runs = filter_collection_runs(
            recent_runs,
            source_map,
            run_type=selected_run_type,
            run_status=selected_status,
            gemini_state=selected_gemini_state,
        )
        st.caption(
            f"최근 {len(recent_runs):,}건 중 현재 조건에 맞는 실행 {len(filtered_runs):,}건입니다. "
            "Gemini 결과는 출처 수집의 전체 성공 여부와 별도로 표시합니다."
        )
        if not filtered_runs:
            st.info("현재 필터 조건에 맞는 실행 이력이 없습니다.")
            return

        frame = pd.DataFrame(
            [
                {
                    "실행 유형": RUN_TYPE_LABELS.get(row["run_type"], row["run_type"]),
                    "시작 시각": _format_time(row["started_at"]),
                    "상태": RUN_DISPLAY_STATUS_LABELS.get(
                        row.get("display_status") or row["status"],
                        row.get("display_status") or row["status"],
                    ),
                    "Gemini 저장": topic_angle_run_summary(
                        source_map.get(str(row["run_id"]), ())
                    )["label"],
                    "소요 시간": _format_duration(row["duration_ms"]),
                    "요청": int(row["request_count"] or 0),
                    "재시도": int(row["retry_count"] or 0),
                    "신규 저장": int(row["newly_saved_count"] or 0),
                    "출처 성공/실패": (
                        f"{int(row['succeeded_source_count'] or 0)}/"
                        f"{int(row['failed_source_count'] or 0)}"
                    ),
                }
                for row in filtered_runs
            ]
        )
        st.dataframe(frame, hide_index=True, height=360)

        run_by_id = {str(row["run_id"]): row for row in filtered_runs}
        selected_key = "collection_history_selected_run"
        if st.session_state.get(selected_key) not in run_by_id:
            st.session_state[selected_key] = next(iter(run_by_id))
        selected_run_id = st.selectbox(
            "출처별 상세를 확인할 실행",
            options=list(run_by_id),
            format_func=lambda run_id: (
                f"{_format_time(run_by_id[run_id]['started_at'])} · "
                f"{RUN_TYPE_LABELS.get(run_by_id[run_id]['run_type'], run_by_id[run_id]['run_type'])} · "
                f"{RUN_DISPLAY_STATUS_LABELS.get(run_by_id[run_id].get('display_status') or run_by_id[run_id]['status'], run_by_id[run_id].get('display_status') or run_by_id[run_id]['status'])} · "
                f"{topic_angle_run_summary(source_map.get(run_id, ()))['label']}"
            ),
            key=selected_key,
        )
        source_rows = source_map.get(selected_run_id)
        if source_rows is None:
            source_rows = list_collection_run_sources(con, selected_run_id)
        selected_run = run_by_id[selected_run_id]
        if selected_run.get("summary"):
            st.caption(str(selected_run["summary"]))
        if selected_run.get("error_message"):
            st.warning(str(selected_run["error_message"]))
        if not source_rows:
            st.info("이 실행에는 출처별 상세 기록이 없습니다.")
            return

        source_status_labels = {
            "success": "성공",
            "partial_success": "부분 성공",
            "skipped": "변경 없음",
            "failure": "실패",
        }
        source_frame = pd.DataFrame(
            [
                {
                    "출처": SOURCE_LABELS.get(row["source_name"], row["source_name"]),
                    "상태": source_status_labels.get(row["status"], row["status"]),
                    "소요 시간": _format_duration(row["duration_ms"]),
                    "요청": int(row["request_count"] or 0),
                    "재시도": int(row["retry_count"] or 0),
                    "처리 결과": _format_source_result(row),
                    "오류 요약": str(row["error_message"] or ""),
                }
                for row in source_rows
            ]
        )
        st.dataframe(source_frame, hide_index=True)
