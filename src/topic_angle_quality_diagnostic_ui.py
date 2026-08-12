"""설정 화면에 주제 방향 v6 품질·운영 진단을 표시합니다."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.services.operation_diagnostic_report_service import (
    build_operation_diagnostic_report,
)
from src.services.topic_angle_quality_diagnostic_service import (
    TARGET_COMPLETED_REQUESTS,
    TARGET_REQUESTED_ITEMS,
    build_topic_angle_quality_diagnostic,
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


def _render_topic_angle_candidate_selection(topic: dict, *, st_module=st) -> None:
    selection = topic.get("candidate_selection") or {}
    st_module.markdown("**주제 방향 대상 선정 · 현재 요청 상한**")
    if not bool(selection.get("available")):
        missing_tables = ", ".join(selection.get("missing_tables") or [])
        error_type = str(selection.get("error_type") or "").strip()
        detail = missing_tables or error_type or "필수 진단 구조 없음"
        st_module.caption(f"대상 선정 흐름을 집계할 수 없습니다. · {detail}")
        return

    st_module.caption(
        f"전체 {int(selection.get('total_clusters') or 0):,}개 → "
        f"상태 통과 {int(selection.get('eligible_status_clusters') or 0):,}개 → "
        f"점수 통과 {int(selection.get('score_eligible_clusters') or 0):,}개 → "
        f"완료 제외 후 생성 필요 {int(selection.get('generation_needed_clusters') or 0):,}개 → "
        f"이번 확인 {int(selection.get('inspected_clusters') or 0):,}개 → "
        f"민감 {int(selection.get('skipped_sensitive_clusters') or 0):,}개·"
        f"근거 없음 {int(selection.get('skipped_no_evidence_clusters') or 0):,}개 제외 → "
        f"생성 대상 추정 {int(selection.get('selected_clusters') or 0):,}개"
    )
    st_module.caption(
        f"요청 상한 {int(selection.get('selection_limit') or 0):,}개 · "
        f"범위 밖 미검사 {int(selection.get('deferred_uninspected_clusters') or 0):,}개 · "
        "실제 Gemini 생성은 수행하지 않은 읽기 전용 추정입니다."
    )


def _render_topic_angle_failure_diagnostics(topic: dict, *, st_module=st) -> None:
    failure = topic.get("failure_diagnostics") or {}
    st_module.markdown("**Gemini 주제 방향 최종 실패**")
    if not bool(failure.get("available")):
        reason = str(failure.get("reason") or "기록 구조 없음").strip()
        st_module.caption(f"최종 실패 원인을 집계할 수 없습니다. · {reason}")
        return

    st_module.caption(
        f"최종 실패 전체 {int(failure.get('terminal_failure_count') or 0):,}회 · "
        f"현재 조건 {int(failure.get('current_runtime_failure_count') or 0):,}회 · "
        f"재시도 후 최종 실패 {int(failure.get('retried_terminal_failure_count') or 0):,}회 · "
        f"저장된 실제 재시도 대기 {float(failure.get('total_retry_wait_seconds') or 0):g}초"
    )
    categories = failure.get("failure_categories") or []
    if categories:
        rows = [
            {
                "원인": str(item.get("label") or item.get("category") or "기타"),
                "전체": int(item.get("count") or 0),
                "현재 조건": int(item.get("current_runtime_count") or 0),
            }
            for item in categories
        ]
        st_module.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st_module.caption("저장된 최종 실패 표본이 없습니다.")


def _render_p2_operation_summary(
    con,
    *,
    app_id: str,
    items_per_request: int,
    thinking_level: str,
    timeout_seconds: int,
    min_opportunity_score: float,
    topic_diagnostic=None,
    st_module=st,
) -> None:
    report = build_operation_diagnostic_report(
        con,
        app_id=app_id,
        items_per_request=items_per_request,
        thinking_level=thinking_level,
        timeout_seconds=timeout_seconds,
        min_opportunity_score=min_opportunity_score,
        topic_diagnostic=topic_diagnostic,
        portal_days=7,
        refresh_run_limit=10,
    )
    runtime = report["runtime"]
    topic = report["topic_angle"]
    portal = report["portal_requests"]
    collection = report["collection_separation"]
    action = report["next_action"]

    st_module.markdown("---")
    st_module.markdown("**P2 통합 운영 판단**")
    st_module.caption(
        f"진단 시각 {report['generated_at']} · 현재 조건 "
        f"{runtime['items_per_request']}개·{runtime['thinking_level']}·"
        f"{runtime['timeout_seconds']}초 · SELECT 집계만 수행"
    )

    summary_columns = st_module.columns(5)
    summary_columns[0].metric(
        "다음 판단",
        str(action["label"]),
        help=str(action["reason"]),
        border=True,
    )
    summary_columns[1].metric(
        "현재 조건 성공 표본",
        (
            f"{int(topic['matching_successful_requests']):,}회 · "
            f"{int(topic['requested_items']):,}개"
        ),
        "충족" if bool(topic["sample_sufficient"]) else "미충족",
        help=(
            f"성공 요청 {TARGET_COMPLETED_REQUESTS}회와 요청 글감 "
            f"{TARGET_REQUESTED_ITEMS}개가 최소 판단 기준입니다."
        ),
        border=True,
    )
    summary_columns[2].metric(
        "현재 조건 검증 실패",
        f"{int(topic['current_validation_failure_count']):,}회",
        f"다른 조건 {int(topic['other_runtime_validation_failure_count']):,}회",
        help=(
            "현재 처리량·사고 수준·제한 시간이 모두 일치한 응답 검증 실패와 "
            "다른 과거 실행 조건의 실패를 분리합니다."
        ),
        border=True,
    )
    summary_columns[3].metric(
        "포털 최종 오류",
        f"{int(portal['failed_request_count']):,}회",
        f"최근 {int(portal['days']):,}일",
        help="NAVER·Daum 논리 요청이 재시도 후에도 최종 실패한 횟수입니다.",
        border=True,
    )
    summary_columns[4].metric(
        "분리 보존된 Gemini 문제",
        f"{int(collection['isolated_gemini_problem_count']):,}회",
        str(collection["status"]),
        help=(
            "출처 수집은 성공으로 유지됐지만 선택적 Gemini 후처리는 "
            "부분 성공 또는 실패로 별도 기록된 최근 실행 수입니다."
        ),
        border=True,
    )

    action_label = str(action["label"])
    action_reason = str(action["reason"])
    if action_label in {
        "저장 계약 점검",
        "현재 조건 응답 검증 점검",
        "출처 수집 점검",
    }:
        st_module.warning(f"{action_label}: {action_reason}")
    elif action_label == "현재 설정 유지":
        st_module.success(f"{action_label}: {action_reason}")
    else:
        st_module.info(f"{action_label}: {action_reason}")

    _render_topic_angle_candidate_selection(topic, st_module=st_module)
    _render_topic_angle_failure_diagnostics(topic, st_module=st_module)

    st_module.markdown(f"**NAVER·Daum 실제 요청 · 최근 {int(portal['days'])}일**")
    if bool(portal["available"]):
        portal_rows = []
        for source_name, label in (("naver", "NAVER"), ("daum", "Daum")):
            item = portal["sources"][source_name]
            portal_rows.append(
                {
                    "포털": label,
                    "논리 요청": int(item["request_count"]),
                    "실제 시도": int(item["attempt_count"]),
                    "재시도": int(item["retry_count"]),
                    "최종 오류": int(item["failed_request_count"]),
                    "결과 0건": int(item["zero_result_count"]),
                    "신규 저장": int(item["newly_saved_count"]),
                    "갱신": int(item["updated_count"]),
                    "오류율": f"{float(item['error_rate_percent']):.1f}%",
                    "마지막 요청": item["last_request_at"] or "기록 없음",
                }
            )
        st_module.dataframe(
            pd.DataFrame(portal_rows),
            hide_index=True,
            width="stretch",
        )
        st_module.caption(
            f"전체 논리 요청 {int(portal['request_count']):,}회 · "
            f"실제 시도 {int(portal['attempt_count']):,}회 · "
            f"재시도 {int(portal['retry_count']):,}회"
        )
    else:
        st_module.caption(
            "아직 NAVER·Daum 실제 요청 원장이 없어 포털 호출량을 집계할 수 없습니다."
        )

    st_module.markdown(
        f"**출처 수집·Gemini 분리 · 최근 {int(collection['run_limit'])}회**"
    )
    if bool(collection["available"]):
        separation_columns = st_module.columns(4)
        separation_columns[0].metric(
            "출처 수집 성공",
            f"{int(collection['source_success_count']):,}회",
            border=True,
        )
        separation_columns[1].metric(
            "출처 부분·실패",
            f"{int(collection['source_problem_count']):,}회",
            border=True,
        )
        separation_columns[2].metric(
            "Gemini 성공",
            f"{int(collection['gemini_success_count']):,}회",
            f"부분·실패 {int(collection['gemini_problem_count']):,}회",
            border=True,
        )
        separation_columns[3].metric(
            "Gemini 생략",
            f"{int(collection['gemini_skipped_count']):,}회",
            border=True,
        )
        st_module.caption(
            f"최근 수집 실행 {int(collection['run_count']):,}회 · "
            f"Gemini 상세 기록 {int(collection['gemini_recorded_count']):,}회 · "
            f"최근 실행 {collection['latest_run_at'] or '기록 없음'}"
        )
    else:
        st_module.caption(
            "아직 수집 실행과 출처별 상세 이력이 없어 분리 상태를 집계할 수 없습니다."
        )


def render_topic_angle_quality_diagnostic_panel(
    con,
    *,
    app_id: str,
    items_per_request: int,
    thinking_level: str,
    timeout_seconds: int,
    min_opportunity_score: float,
    st_module=st,
) -> None:
    diagnostic = build_topic_angle_quality_diagnostic(
        con,
        app_id=app_id,
        items_per_request=items_per_request,
        thinking_level=thinking_level,
        timeout_seconds=timeout_seconds,
        min_opportunity_score=min_opportunity_score,
    )
    contract = diagnostic.contract
    operation = diagnostic.operation
    backlog = diagnostic.backlog
    remaining_requests = max(
        0,
        TARGET_COMPLETED_REQUESTS - operation.matching_runtime_request_count,
    )
    remaining_items = max(0, TARGET_REQUESTED_ITEMS - operation.requested_items)
    remaining_sample_label = (
        "충족"
        if remaining_requests == 0 and remaining_items == 0
        else f"{remaining_requests:,}회 · {remaining_items:,}개"
    )

    with st_module.expander("주제 방향 v6 품질·운영 진단", expanded=False):
        st_module.caption(
            "현재 DuckDB의 v6 방향 저장값과 해당 버전 Gemini 호출 기록만 읽습니다. "
            "외부 API를 호출하거나 설정·방향·원문을 자동 변경하지 않습니다."
        )

        metrics = st_module.columns(5)
        metrics[0].metric(
            "진단 상태",
            diagnostic.status,
            help="저장 계약, 근거 연결, 응답 검증과 최소 운영 표본을 종합한 읽기 전용 상태입니다.",
            border=True,
        )
        metrics[1].metric(
            "v6 글감·방향",
            f"{contract.cluster_count:,}개 · {contract.direction_count:,}개",
            help="feature_version 6으로 저장된 글감과 방향 수입니다.",
            border=True,
        )
        metrics[2].metric(
            "계약 완전성",
            _percent(contract.contract_completion_rate),
            help="필수 설명·검색어·수요 근거·원문 연결·점수 합계가 모두 유효한 방향 비율입니다.",
            border=True,
        )
        metrics[3].metric(
            "원문 근거 연결률",
            _percent(contract.evidence_link_rate),
            help="방향에 저장된 원문 ID가 현재 source_items에 실제 존재하는 비율입니다.",
            border=True,
        )
        metrics[4].metric(
            "v6 운영 표본",
            (
                f"{operation.matching_runtime_request_count:,}회 · "
                f"{operation.requested_items:,}개"
            ),
            help=(
                f"현재 설정과 일치한 성공 요청 {TARGET_COMPLETED_REQUESTS}회와 "
                f"요청 글감 {TARGET_REQUESTED_ITEMS}개가 최소 판단 기준입니다. "
                "설정 상한보다 실제 대기 글감이 적은 부분 배치도 표본에 포함합니다."
            ),
            border=True,
        )

        if diagnostic.status in {"저장 데이터 점검", "응답 검증 주의"}:
            st_module.warning(diagnostic.summary)
        elif diagnostic.status == "정상 관찰":
            st_module.success(diagnostic.summary)
        else:
            st_module.info(diagnostic.summary)

        st_module.markdown("**판단 근거**")
        for reason in diagnostic.reasons:
            st_module.markdown(f"- {reason}")

        st_module.markdown("**v6 호출 조건과 안정성**")
        operation_rows = pd.DataFrame(
            [
                {
                    "항목": "완료 요청",
                    "값": f"{operation.completed_request_count:,}회",
                    "설명": "재시도 중 행을 제외하고 최종 상태가 기록된 독립 요청 수",
                },
                {
                    "항목": "성공 요청",
                    "값": f"{operation.successful_request_count:,}회",
                    "설명": "success 또는 success_after_retry로 끝난 요청 수",
                },
                {
                    "항목": "현재 조건 일치",
                    "값": f"{operation.matching_runtime_request_count:,}회",
                    "설명": (
                        f"설정 상한 {int(items_per_request)}개·{thinking_level}·"
                        f"{int(timeout_seconds)}초가 모두 일치한 성공 요청"
                    ),
                },
                {
                    "항목": "추가 최소 표본",
                    "값": remaining_sample_label,
                    "설명": (
                        "최소 판단 기준을 채우기 위해 더 필요한 현재 조건 일치 성공 요청과 "
                        "요청 글감 수를 각각 계산한 값"
                    ),
                },
                {
                    "항목": "응답 검증 실패",
                    "값": f"{operation.validation_failure_count:,}회",
                    "설명": "HTTP 응답 후 v6 JSON 계약을 저장하지 못한 현재 조건의 최종 요청",
                },
                {
                    "항목": "다른 조건 검증 실패",
                    "값": f"{operation.other_runtime_validation_failure_count:,}회",
                    "설명": "현재 상태 판단에서는 제외하고 참고값으로만 표시하는 과거 다른 실행 조건",
                },
                {
                    "항목": "재시도 대기",
                    "값": f"{operation.retrying_attempt_count:,}회",
                    "설명": "429·일시 장애 등으로 같은 요청 안에서 다시 시도한 행",
                },
                {
                    "항목": "평균·최대 생성 토큰",
                    "값": (
                        f"{operation.average_generation_tokens:,} · "
                        f"{operation.maximum_generation_tokens:,}"
                    ),
                    "설명": "현재 조건과 일치한 성공 요청의 출력 토큰과 사고 토큰 합계",
                },
                {
                    "항목": "평균 소요",
                    "값": _duration(operation.average_duration_ms),
                    "설명": "현재 조건과 일치한 성공 v6 요청의 API 처리 평균 시간",
                },
            ]
        )
        st_module.dataframe(operation_rows, hide_index=True, width="stretch")
        st_module.caption(
            f"설정 상한이 {max(1, int(items_per_request))}개여도 실제 대기 글감이 더 적으면 "
            "그 성공 요청은 실제 요청 수만큼 현재 운영 표본에 포함됩니다. "
            "다른 설정 상한·사고 수준·제한 시간의 성공 요청은 제외합니다."
        )

        st_module.markdown("**저장 계약 상세**")
        quality_rows = pd.DataFrame(
            [
                {
                    "항목": "3방향 완성 글감",
                    "값": (
                        f"{contract.complete_cluster_count:,}/"
                        f"{contract.cluster_count:,}개"
                    ),
                },
                {
                    "항목": "1순위 계약 완전성",
                    "값": _percent(contract.primary_contract_completion_rate),
                },
                {
                    "항목": "JSON 형식 이상",
                    "값": f"{contract.invalid_json_count:,}개 방향",
                },
                {
                    "항목": "필수값 누락",
                    "값": f"{contract.missing_required_count:,}개 방향",
                },
                {
                    "항목": "점수 범위·합계 이상",
                    "값": f"{contract.score_issue_count:,}개 방향",
                },
                {
                    "항목": "점수 정렬 이상",
                    "값": f"{contract.ordering_issue_count:,}개 글감",
                },
                {
                    "항목": "원문 연결 이상",
                    "값": f"{contract.broken_evidence_link_count:,}개 방향",
                },
                {
                    "항목": "짧은 단일어 검색어",
                    "값": (
                        f"{contract.short_single_query_count:,}/"
                        f"{contract.query_count:,}개 "
                        f"({_percent(contract.short_single_query_rate)})"
                    ),
                },
                {
                    "항목": "방향 점수",
                    "값": (
                        f"평균 {contract.average_score:.1f}점 · "
                        f"최저 {contract.minimum_score if contract.minimum_score is not None else '없음'} · "
                        f"최고 {contract.maximum_score if contract.maximum_score is not None else '없음'}"
                    ),
                },
            ]
        )
        st_module.dataframe(quality_rows, hide_index=True, width="stretch")
        st_module.caption(
            "짧은 단일어 검색어는 공백 없는 8자 이하 검색어를 별도 관찰한 값입니다. "
            "그 자체를 오류로 판정하지 않으며 실제 검색 의도와 함께 검토합니다."
        )

        backlog_columns = st_module.columns(3)
        backlog_columns[0].metric(
            "현재 대상 글감",
            f"{backlog.eligible_cluster_count:,}개",
            help=(
                f"글감 기회 점수 {float(min_opportunity_score):g}점 이상 "
                "추천·검토 글감입니다."
            ),
            border=True,
        )
        backlog_columns[1].metric(
            "분석 완료·대기",
            (
                f"{backlog.completed_cluster_count:,} · "
                f"{backlog.pending_cluster_count:,}"
            ),
            help="프로필과 방향 3개가 모두 있는 글감과 아직 분석이 필요한 글감 수입니다.",
            border=True,
        )
        backlog_columns[2].metric(
            "대기 해소 예상",
            f"약 {backlog.estimated_runs_to_clear:,}회",
            help=(
                f"요청당 최대 {max(1, int(items_per_request))}개를 "
                "기준으로 단순 계산한 값입니다."
            ),
            border=True,
        )

        if diagnostic.issue_examples:
            st_module.markdown("**최근 점검 사례**")
            st_module.dataframe(
                pd.DataFrame(diagnostic.issue_examples),
                hide_index=True,
                width="stretch",
            )

        _render_p2_operation_summary(
            con,
            app_id=app_id,
            items_per_request=items_per_request,
            thinking_level=thinking_level,
            timeout_seconds=timeout_seconds,
            min_opportunity_score=min_opportunity_score,
            topic_diagnostic=diagnostic,
            st_module=st_module,
        )

        st_module.caption(
            "이 화면은 문제를 발견해도 처리량·사고 수준·프롬프트·군집 기준을 자동 변경하지 않습니다. "
            "최소 표본이 충족된 뒤 반복되는 한 가지 원인만 별도 작업으로 수정합니다."
        )
