"""실제 DuckDB의 P2 운영 지표를 읽기 전용으로 한 번에 집계합니다."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import duckdb

from src.services.topic_angle_candidate_diagnostic_service import (
    collect_topic_angle_candidate_diagnostics,
)
from src.services.topic_angle_failure_diagnostic_service import (
    build_topic_angle_failure_diagnostic,
)
from src.services.topic_angle_quality_diagnostic_service import (
    TopicAngleQualityDiagnostic,
    build_topic_angle_quality_diagnostic,
)
from src.services.trend_clustering_diagnostic_service import (
    build_trend_clustering_trial_diagnostic,
)
from src.services.trend_clustering_throttle_diagnostic_service import (
    build_trend_clustering_throttle_diagnostic,
)


_PORTAL_SOURCES = ("naver", "daum")
_GEMINI_PROBLEM_STATUSES = {"partial_success", "failure"}
_TOPIC_ANGLE_SELECTION_TABLES = (
    "trend_clusters",
    "trend_cluster_ai_angles",
    "trend_cluster_ai_profiles",
    "trend_cluster_items",
    "source_items",
)


def _table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    rows = con.execute("SHOW TABLES").fetchall()
    return str(table_name) in {str(row[0]) for row in rows}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat(sep=" ", timespec="seconds"))
    return str(value)


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100, 1)


def _empty_portal_source(source_name: str) -> dict[str, Any]:
    return {
        "source_name": source_name,
        "request_count": 0,
        "attempt_count": 0,
        "retry_count": 0,
        "successful_request_count": 0,
        "failed_request_count": 0,
        "zero_result_count": 0,
        "result_count": 0,
        "newly_saved_count": 0,
        "updated_count": 0,
        "error_rate_percent": 0.0,
        "zero_result_rate_percent": 0.0,
        "last_request_at": None,
    }


def _load_portal_metrics(
    con: duckdb.DuckDBPyConnection,
    *,
    days: int,
    now: datetime,
) -> dict[str, Any]:
    bounded_days = 7 if int(days) <= 7 else 30
    sources = {name: _empty_portal_source(name) for name in _PORTAL_SOURCES}
    if not _table_exists(con, "collection_query_requests"):
        return {
            "available": False,
            "days": bounded_days,
            "sources": sources,
            "request_count": 0,
            "attempt_count": 0,
            "retry_count": 0,
            "failed_request_count": 0,
        }

    cursor = con.execute(
        """
        SELECT source_name,
               COUNT(*) AS request_count,
               COALESCE(SUM(attempt_count), 0) AS attempt_count,
               COALESCE(SUM(retry_count), 0) AS retry_count,
               COALESCE(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), 0)
                   AS successful_request_count,
               COALESCE(SUM(CASE WHEN status = 'failure' THEN 1 ELSE 0 END), 0)
                   AS failed_request_count,
               COALESCE(SUM(
                   CASE WHEN status = 'success' AND result_count = 0 THEN 1 ELSE 0 END
               ), 0) AS zero_result_count,
               COALESCE(SUM(result_count), 0) AS result_count,
               COALESCE(SUM(newly_saved_count), 0) AS newly_saved_count,
               COALESCE(SUM(updated_count), 0) AS updated_count,
               MAX(finished_at) AS last_request_at
        FROM collection_query_requests
        WHERE finished_at >= ?
          AND source_name IN ('naver', 'daum')
        GROUP BY source_name
        ORDER BY source_name
        """,
        [now - timedelta(days=bounded_days)],
    )
    columns = [str(item[0]) for item in cursor.description]
    for values in cursor.fetchall():
        row = dict(zip(columns, values))
        source_name = str(row.get("source_name") or "")
        if source_name not in sources:
            continue
        request_count = int(row.get("request_count") or 0)
        successful = int(row.get("successful_request_count") or 0)
        failed = int(row.get("failed_request_count") or 0)
        zero_result = int(row.get("zero_result_count") or 0)
        sources[source_name] = {
            "source_name": source_name,
            "request_count": request_count,
            "attempt_count": int(row.get("attempt_count") or 0),
            "retry_count": int(row.get("retry_count") or 0),
            "successful_request_count": successful,
            "failed_request_count": failed,
            "zero_result_count": zero_result,
            "result_count": int(row.get("result_count") or 0),
            "newly_saved_count": int(row.get("newly_saved_count") or 0),
            "updated_count": int(row.get("updated_count") or 0),
            "error_rate_percent": _rate(failed, request_count),
            "zero_result_rate_percent": _rate(zero_result, successful),
            "last_request_at": _iso(row.get("last_request_at")),
        }

    return {
        "available": True,
        "days": bounded_days,
        "sources": sources,
        "request_count": sum(item["request_count"] for item in sources.values()),
        "attempt_count": sum(item["attempt_count"] for item in sources.values()),
        "retry_count": sum(item["retry_count"] for item in sources.values()),
        "failed_request_count": sum(
            item["failed_request_count"] for item in sources.values()
        ),
    }


def _load_collection_separation_metrics(
    con: duckdb.DuckDBPyConnection,
    *,
    refresh_run_limit: int,
) -> dict[str, Any]:
    bounded_limit = max(1, min(int(refresh_run_limit), 100))
    if not (
        _table_exists(con, "collection_runs")
        and _table_exists(con, "collection_run_sources")
    ):
        return {
            "available": False,
            "run_limit": bounded_limit,
            "run_count": 0,
            "source_success_count": 0,
            "source_problem_count": 0,
            "gemini_recorded_count": 0,
            "gemini_success_count": 0,
            "gemini_problem_count": 0,
            "gemini_skipped_count": 0,
            "isolated_gemini_problem_count": 0,
            "latest_run_at": None,
            "status": "기록 없음",
        }

    cursor = con.execute(
        """
        WITH recent_refresh_runs AS (
            SELECT run_id, status AS source_status, started_at
            FROM collection_runs
            WHERE run_type IN ('background_refresh', 'manual_refresh')
              AND status NOT IN ('running', 'skipped_overlap')
            ORDER BY started_at DESC, run_id DESC
            LIMIT ?
        )
        SELECT r.run_id, r.source_status, r.started_at,
               COALESCE(s.status, '') AS gemini_status
        FROM recent_refresh_runs r
        LEFT JOIN collection_run_sources s
          ON s.run_id = r.run_id
         AND s.source_name = 'topic_angles'
        ORDER BY r.started_at DESC, r.run_id DESC
        """,
        [bounded_limit],
    )
    columns = [str(item[0]) for item in cursor.description]
    rows = [dict(zip(columns, values)) for values in cursor.fetchall()]

    source_success_count = sum(
        str(row.get("source_status") or "") == "success" for row in rows
    )
    source_problem_count = sum(
        str(row.get("source_status") or "") in {"partial_success", "failure"}
        for row in rows
    )
    gemini_recorded_count = sum(bool(str(row.get("gemini_status") or "")) for row in rows)
    gemini_success_count = sum(
        str(row.get("gemini_status") or "") == "success" for row in rows
    )
    gemini_problem_count = sum(
        str(row.get("gemini_status") or "") in _GEMINI_PROBLEM_STATUSES
        for row in rows
    )
    gemini_skipped_count = sum(
        str(row.get("gemini_status") or "") == "skipped" for row in rows
    )
    isolated_gemini_problem_count = sum(
        str(row.get("source_status") or "") == "success"
        and str(row.get("gemini_status") or "") in _GEMINI_PROBLEM_STATUSES
        for row in rows
    )

    if not rows:
        status = "기록 없음"
    elif isolated_gemini_problem_count:
        status = "분리 보존 확인"
    elif gemini_problem_count:
        status = "복합 점검"
    elif source_problem_count:
        status = "출처 수집 점검"
    else:
        status = "정상 관찰"

    return {
        "available": True,
        "run_limit": bounded_limit,
        "run_count": len(rows),
        "source_success_count": source_success_count,
        "source_problem_count": source_problem_count,
        "gemini_recorded_count": gemini_recorded_count,
        "gemini_success_count": gemini_success_count,
        "gemini_problem_count": gemini_problem_count,
        "gemini_skipped_count": gemini_skipped_count,
        "isolated_gemini_problem_count": isolated_gemini_problem_count,
        "latest_run_at": _iso(rows[0].get("started_at")) if rows else None,
        "status": status,
    }


def _unavailable_candidate_selection(
    *,
    selection_limit: int,
    missing_tables: list[str] | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "selected_is_estimate": True,
        "total_clusters": 0,
        "eligible_status_clusters": 0,
        "score_eligible_clusters": 0,
        "already_complete_clusters": 0,
        "generation_needed_clusters": 0,
        "inspected_clusters": 0,
        "skipped_sensitive_clusters": 0,
        "skipped_no_evidence_clusters": 0,
        "selected_clusters": 0,
        "deferred_uninspected_clusters": 0,
        "min_opportunity_score": 0.0,
        "selection_limit": selection_limit,
        "missing_tables": list(missing_tables or []),
        "error_type": type(error).__name__ if error is not None else "",
        "error_message": str(error)[:500] if error is not None else "",
    }


def _load_topic_angle_candidate_selection(
    con: duckdb.DuckDBPyConnection,
    *,
    min_opportunity_score: float,
    selection_limit: int,
) -> dict[str, Any]:
    """현재 요청 상한의 주제 방향 선정 흐름을 읽기 전용으로 추정합니다."""
    bounded_limit = max(1, min(int(selection_limit), 400))
    missing_tables = [
        name for name in _TOPIC_ANGLE_SELECTION_TABLES if not _table_exists(con, name)
    ]
    if missing_tables:
        return _unavailable_candidate_selection(
            selection_limit=bounded_limit,
            missing_tables=missing_tables,
        )

    try:
        diagnostics = collect_topic_angle_candidate_diagnostics(
            con,
            min_opportunity_score=min_opportunity_score,
            selection_limit=bounded_limit,
            selected_clusters=0,
        )
    except Exception as exc:
        return _unavailable_candidate_selection(
            selection_limit=bounded_limit,
            error=exc,
        )

    metadata = diagnostics.as_metadata()
    estimated_selected = max(
        0,
        diagnostics.inspected_clusters
        - diagnostics.skipped_sensitive_clusters
        - diagnostics.skipped_no_evidence_clusters,
    )
    metadata["selected_clusters"] = estimated_selected
    return {
        "available": True,
        "selected_is_estimate": True,
        **metadata,
        "missing_tables": [],
        "error_type": "",
        "error_message": "",
    }


def _next_action(
    *,
    topic_status: str,
    sample_sufficient: bool,
    current_validation_failures: int,
    collection: dict[str, Any],
    clustering: dict[str, Any],
    clustering_throttle: dict[str, Any],
) -> tuple[str, str]:
    if topic_status == "저장 데이터 점검":
        return (
            "저장 계약 점검",
            "v6 저장 데이터의 계약·근거 연결 문제 사례를 먼저 확인합니다.",
        )
    if current_validation_failures > 0:
        return (
            "현재 조건 응답 검증 점검",
            "현재 처리량·사고 수준·제한 시간과 일치한 실패 원인 하나를 우선 확인합니다.",
        )
    if int(collection.get("source_problem_count") or 0) > 0:
        return (
            "출처 수집 점검",
            "최근 출처 수집 부분 성공·실패의 출처와 오류 유형을 먼저 확인합니다.",
        )
    if bool(clustering.get("available")):
        if bool(clustering.get("sample_available")):
            if not bool(clustering.get("trial_contract_ok")):
                return (
                    "군집 시험 계약 점검",
                    "최대 배치 수·배치당 1차 군집 수·순차 실행 시각 계약 중 맞지 않는 실행을 확인합니다.",
                )
            if str(clustering.get("job_status") or "") == "failed":
                if int(clustering_throttle.get("provider_daily_quota_count") or 0) > 0:
                    return (
                        "Gemini 일일 quota 점검",
                        "최신 군집 작업이 실패했고 최근 요청 표본에 일일 quota 소진 기록이 있어 공급자 quota 상태를 함께 확인합니다.",
                    )
                if int(clustering_throttle.get("provider_rate_limit_count") or 0) > 0:
                    return (
                        "Gemini rate limit 점검",
                        "최신 군집 작업이 실패했고 최근 요청 표본에 rate limit 기록이 있어 공급자 제한과 재시도 대기를 함께 확인합니다.",
                    )
                return (
                    "군집 실행 실패 점검",
                    "최신 2단계 군집 작업의 오류 메시지와 마지막 배치를 먼저 확인합니다.",
                )
            if int(clustering.get("review_signal_count") or 0) > 0:
                return (
                    "군집 표본 검토",
                    "기존 군집 연결·새 군집과 불확실·충돌 차단·검토 대상을 표본으로 확인합니다.",
                )
        else:
            return (
                "군집 시험 표본 추가",
                "현재 설정을 바꾸지 않고 2단계 군집 작업을 실행해 배치·토큰 표본을 확보합니다.",
            )
    if not sample_sufficient:
        return (
            "운영 표본 추가",
            "현재 조건을 바꾸지 않고 성공 요청 4회·요청 글감 60개까지 표본을 모읍니다.",
        )
    return (
        "현재 설정 유지",
        "즉시 수정할 반복 문제가 확인되지 않아 현재 조건을 유지하고 관찰합니다.",
    )


def build_operation_diagnostic_report(
    con: duckdb.DuckDBPyConnection,
    *,
    app_id: str,
    items_per_request: int,
    thinking_level: str,
    timeout_seconds: int,
    min_opportunity_score: float,
    topic_diagnostic: TopicAngleQualityDiagnostic | None = None,
    portal_days: int = 7,
    refresh_run_limit: int = 10,
    now: datetime | None = None,
) -> dict[str, Any]:
    """외부 호출이나 DB 변경 없이 P2 운영 판단용 요약을 반환합니다."""
    current = now or datetime.now()
    topic = topic_diagnostic
    if topic is None:
        topic = build_topic_angle_quality_diagnostic(
            con,
            app_id=app_id,
            items_per_request=items_per_request,
            thinking_level=thinking_level,
            timeout_seconds=timeout_seconds,
            min_opportunity_score=min_opportunity_score,
        )
    failure_diagnostics = build_topic_angle_failure_diagnostic(
        con,
        app_id=app_id,
        items_per_request=items_per_request,
        thinking_level=thinking_level,
        timeout_seconds=timeout_seconds,
    )
    candidate_selection = _load_topic_angle_candidate_selection(
        con,
        min_opportunity_score=min_opportunity_score,
        selection_limit=items_per_request,
    )
    portal = _load_portal_metrics(con, days=portal_days, now=current)
    collection = _load_collection_separation_metrics(
        con,
        refresh_run_limit=refresh_run_limit,
    )
    clustering = build_trend_clustering_trial_diagnostic(con)
    clustering_throttle = build_trend_clustering_throttle_diagnostic(con)
    action, action_reason = _next_action(
        topic_status=topic.status,
        sample_sufficient=topic.operation.sample_sufficient,
        current_validation_failures=topic.operation.validation_failure_count,
        collection=collection,
        clustering=clustering,
        clustering_throttle=clustering_throttle,
    )

    return {
        "generated_at": _iso(current),
        "read_only": True,
        "runtime": {
            "items_per_request": int(items_per_request),
            "thinking_level": str(thinking_level),
            "timeout_seconds": int(timeout_seconds),
            "min_opportunity_score": float(min_opportunity_score),
        },
        "topic_angle": {
            "status": topic.status,
            "summary": topic.summary,
            "reasons": list(topic.reasons),
            "matching_successful_requests": (
                topic.operation.matching_runtime_request_count
            ),
            "requested_items": topic.operation.requested_items,
            "sample_sufficient": topic.operation.sample_sufficient,
            "current_validation_failure_count": (
                topic.operation.validation_failure_count
            ),
            "other_runtime_validation_failure_count": (
                topic.operation.other_runtime_validation_failure_count
            ),
            "average_generation_tokens": topic.operation.average_generation_tokens,
            "maximum_generation_tokens": topic.operation.maximum_generation_tokens,
            "average_duration_ms": topic.operation.average_duration_ms,
            "contract_completion_rate": topic.contract.contract_completion_rate,
            "evidence_link_rate": topic.contract.evidence_link_rate,
            "pending_cluster_count": topic.backlog.pending_cluster_count,
            "estimated_runs_to_clear": topic.backlog.estimated_runs_to_clear,
            "failure_diagnostics": failure_diagnostics,
            "candidate_selection": candidate_selection,
        },
        "portal_requests": portal,
        "collection_separation": collection,
        "trend_clustering": clustering,
        "trend_clustering_throttle": clustering_throttle,
        "next_action": {
            "label": action,
            "reason": action_reason,
        },
    }
