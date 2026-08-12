"""Gemini 주제 방향 v6의 저장 품질과 운영 표본을 읽기 전용으로 진단합니다."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

import duckdb

from src.services.topic_angle_ai_service import (
    TOPIC_ANGLE_FEATURE_ID,
    TOPIC_ANGLE_FEATURE_VERSION,
)
from src.services.topic_angle_demand_contract import DIRECTION_SCORE_LIMITS


TARGET_COMPLETED_REQUESTS = 4
TARGET_REQUESTED_ITEMS = 60
MAX_ISSUE_EXAMPLES = 20


@dataclass(frozen=True)
class TopicAngleContractMetrics:
    cluster_count: int
    complete_cluster_count: int
    direction_count: int
    contract_complete_count: int
    primary_direction_count: int
    primary_contract_complete_count: int
    evidence_reference_count: int
    valid_evidence_reference_count: int
    invalid_json_count: int
    missing_required_count: int
    score_issue_count: int
    ordering_issue_count: int
    broken_evidence_link_count: int
    short_single_query_count: int
    query_count: int
    average_score: float
    minimum_score: int | None
    maximum_score: int | None

    @property
    def contract_completion_rate(self) -> float | None:
        if self.direction_count <= 0:
            return None
        return self.contract_complete_count / self.direction_count

    @property
    def primary_contract_completion_rate(self) -> float | None:
        if self.primary_direction_count <= 0:
            return None
        return self.primary_contract_complete_count / self.primary_direction_count

    @property
    def evidence_link_rate(self) -> float | None:
        if self.evidence_reference_count <= 0:
            return None
        return self.valid_evidence_reference_count / self.evidence_reference_count

    @property
    def short_single_query_rate(self) -> float | None:
        if self.query_count <= 0:
            return None
        return self.short_single_query_count / self.query_count


@dataclass(frozen=True)
class TopicAngleOperationMetrics:
    attempt_count: int
    completed_request_count: int
    successful_request_count: int
    validation_failure_count: int
    other_runtime_validation_failure_count: int
    retrying_attempt_count: int
    requested_items: int
    matching_runtime_request_count: int
    average_generation_tokens: int
    maximum_generation_tokens: int
    average_duration_ms: int
    first_recorded_at: Any
    last_recorded_at: Any

    @property
    def sample_sufficient(self) -> bool:
        return (
            self.matching_runtime_request_count >= TARGET_COMPLETED_REQUESTS
            and self.requested_items >= TARGET_REQUESTED_ITEMS
        )


@dataclass(frozen=True)
class TopicAngleBacklogMetrics:
    eligible_cluster_count: int
    completed_cluster_count: int
    pending_cluster_count: int
    estimated_runs_to_clear: int


@dataclass(frozen=True)
class TopicAngleQualityDiagnostic:
    status: str
    summary: str
    reasons: tuple[str, ...]
    contract: TopicAngleContractMetrics
    operation: TopicAngleOperationMetrics
    backlog: TopicAngleBacklogMetrics
    issue_examples: tuple[dict[str, object], ...]


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _json_list(value: Any) -> tuple[list[Any], bool]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return [], False
    return (parsed, True) if isinstance(parsed, list) else ([], False)


def _json_dict(value: Any) -> tuple[dict[str, Any], bool]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}, False
    return (parsed, True) if isinstance(parsed, dict) else ({}, False)


def _clean_text_list(value: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_text(item)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _valid_score_breakdown(value: dict[str, Any]) -> tuple[bool, int]:
    if set(value) != set(DIRECTION_SCORE_LIMITS):
        return False, 0
    total = 0
    for key, maximum in DIRECTION_SCORE_LIMITS.items():
        raw = value.get(key)
        if isinstance(raw, bool):
            return False, 0
        try:
            score = int(raw)
        except (TypeError, ValueError):
            return False, 0
        if score < 0 or score > maximum:
            return False, 0
        total += score
    return True, total


def _is_short_single_query(value: str) -> bool:
    text = re.sub(r"\s+", " ", _clean_text(value))
    tokens = re.findall(r"[0-9A-Za-z가-힣]+", text)
    return bool(text) and len(tokens) == 1 and len(tokens[0]) <= 8


def _existing_source_ids(
    con: duckdb.DuckDBPyConnection,
    source_ids: set[str],
) -> set[str]:
    existing: set[str] = set()
    ordered = sorted(item for item in source_ids if item)
    for index in range(0, len(ordered), 500):
        chunk = ordered[index : index + 500]
        placeholders = ", ".join("?" for _ in chunk)
        rows = con.execute(
            f"SELECT source_item_id FROM source_items WHERE source_item_id IN ({placeholders})",
            chunk,
        ).fetchall()
        existing.update(str(row[0]) for row in rows)
    return existing


def _load_contract_metrics(
    con: duckdb.DuckDBPyConnection,
) -> tuple[TopicAngleContractMetrics, tuple[dict[str, object], ...]]:
    rows = con.execute(
        """
        SELECT angle_id, cluster_id, canonical_title, angle_order,
               search_queries_json, search_intent, reader_question,
               demand_evidence_json, evidence_source_ids_json,
               score_breakdown_json, direction_score, score_reasons_json,
               created_at
        FROM trend_cluster_ai_angles
        WHERE feature_version = ?
        ORDER BY created_at DESC, cluster_id, angle_order
        """,
        [TOPIC_ANGLE_FEATURE_VERSION],
    ).fetchall()
    columns = [str(item[0]) for item in con.description]
    items = [dict(zip(columns, row)) for row in rows]

    parsed_items: list[dict[str, Any]] = []
    all_source_ids: set[str] = set()
    invalid_json_count = 0
    query_count = 0
    short_query_count = 0
    scores: list[int] = []

    for item in items:
        queries, queries_ok = _json_list(item.get("search_queries_json"))
        demand_evidence, demand_ok = _json_list(item.get("demand_evidence_json"))
        evidence_ids, evidence_ok = _json_list(item.get("evidence_source_ids_json"))
        score_breakdown, score_ok = _json_dict(item.get("score_breakdown_json"))
        score_reasons, reasons_ok = _json_list(item.get("score_reasons_json"))
        if not all((queries_ok, demand_ok, evidence_ok, score_ok, reasons_ok)):
            invalid_json_count += 1

        clean_queries = _clean_text_list(queries)
        clean_demand = _clean_text_list(demand_evidence)
        clean_evidence_ids = _clean_text_list(evidence_ids)
        clean_score_reasons = _clean_text_list(score_reasons)
        query_count += len(clean_queries)
        short_query_count += sum(_is_short_single_query(query) for query in clean_queries)
        all_source_ids.update(clean_evidence_ids)

        breakdown_ok, calculated_score = _valid_score_breakdown(score_breakdown)
        try:
            stored_score = int(float(item.get("direction_score")))
        except (TypeError, ValueError):
            stored_score = -1
        if stored_score >= 0:
            scores.append(stored_score)

        parsed_items.append(
            {
                **item,
                "queries": clean_queries,
                "demand_evidence": clean_demand,
                "evidence_ids": clean_evidence_ids,
                "score_reasons": clean_score_reasons,
                "json_ok": all((queries_ok, demand_ok, evidence_ok, score_ok, reasons_ok)),
                "breakdown_ok": breakdown_ok,
                "calculated_score": calculated_score,
                "stored_score": stored_score,
            }
        )

    existing_source_ids = _existing_source_ids(con, all_source_ids)
    contract_complete_count = 0
    primary_direction_count = 0
    primary_contract_complete_count = 0
    evidence_reference_count = 0
    valid_evidence_reference_count = 0
    missing_required_count = 0
    score_issue_count = 0
    broken_evidence_link_count = 0
    issue_examples: list[dict[str, object]] = []

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in parsed_items:
        grouped.setdefault(_clean_text(item.get("cluster_id")), []).append(item)

        evidence_ids = item["evidence_ids"]
        evidence_reference_count += len(evidence_ids)
        valid_evidence_reference_count += sum(
            source_id in existing_source_ids for source_id in evidence_ids
        )
        broken_link = any(source_id not in existing_source_ids for source_id in evidence_ids)
        if broken_link:
            broken_evidence_link_count += 1

        required_ok = (
            bool(_clean_text(item.get("search_intent")))
            and bool(_clean_text(item.get("reader_question")))
            and 1 <= len(item["queries"]) <= 3
            and 1 <= len(item["demand_evidence"]) <= 3
            and 1 <= len(evidence_ids) <= 3
            and 1 <= len(item["score_reasons"]) <= 5
        )
        if not required_ok:
            missing_required_count += 1

        score_valid = (
            bool(item["breakdown_ok"])
            and item["stored_score"] == item["calculated_score"]
            and 0 <= item["stored_score"] <= 100
        )
        if not score_valid:
            score_issue_count += 1

        complete = bool(item["json_ok"] and required_ok and score_valid and not broken_link)
        if complete:
            contract_complete_count += 1

        try:
            order = int(item.get("angle_order") or 0)
        except (TypeError, ValueError):
            order = 0
        if order == 1:
            primary_direction_count += 1
            if complete:
                primary_contract_complete_count += 1

        issues: list[str] = []
        if not item["json_ok"]:
            issues.append("JSON 형식")
        if not required_ok:
            issues.append("필수값 누락")
        if not score_valid:
            issues.append("점수 불일치")
        if broken_link:
            issues.append("원문 근거 연결")
        if any(_is_short_single_query(query) for query in item["queries"]):
            issues.append("짧은 단일어 검색어")
        if issues and len(issue_examples) < MAX_ISSUE_EXAMPLES:
            issue_examples.append(
                {
                    "글감": _clean_text(item.get("canonical_title")) or "(제목 없음)",
                    "순위": order,
                    "점수": item["stored_score"] if item["stored_score"] >= 0 else "없음",
                    "확인 항목": " · ".join(issues),
                }
            )

    complete_cluster_count = sum(
        sorted(int(item.get("angle_order") or 0) for item in cluster_items) == [1, 2, 3]
        for cluster_items in grouped.values()
    )
    ordering_issue_count = 0
    for cluster_items in grouped.values():
        ordered = sorted(cluster_items, key=lambda item: int(item.get("angle_order") or 0))
        ordered_scores = [int(item.get("stored_score", -1)) for item in ordered]
        if (
            [int(item.get("angle_order") or 0) for item in ordered] != [1, 2, 3]
            or any(score < 0 for score in ordered_scores)
            or ordered_scores != sorted(ordered_scores, reverse=True)
        ):
            ordering_issue_count += 1

    metrics = TopicAngleContractMetrics(
        cluster_count=len(grouped),
        complete_cluster_count=complete_cluster_count,
        direction_count=len(parsed_items),
        contract_complete_count=contract_complete_count,
        primary_direction_count=primary_direction_count,
        primary_contract_complete_count=primary_contract_complete_count,
        evidence_reference_count=evidence_reference_count,
        valid_evidence_reference_count=valid_evidence_reference_count,
        invalid_json_count=invalid_json_count,
        missing_required_count=missing_required_count,
        score_issue_count=score_issue_count,
        ordering_issue_count=ordering_issue_count,
        broken_evidence_link_count=broken_evidence_link_count,
        short_single_query_count=short_query_count,
        query_count=query_count,
        average_score=(sum(scores) / len(scores) if scores else 0.0),
        minimum_score=min(scores) if scores else None,
        maximum_score=max(scores) if scores else None,
    )
    return metrics, tuple(issue_examples)


def _load_operation_metrics(
    con: duckdb.DuckDBPyConnection,
    *,
    app_id: str,
    items_per_request: int,
    thinking_level: str,
    timeout_seconds: int,
) -> TopicAngleOperationMetrics:
    rows = con.execute(
        """
        SELECT call_id, request_hash, status, error_type, attempt_number,
               requested_item_count, configured_items_per_request,
               thinking_level, request_timeout_seconds, output_tokens,
               thought_tokens, duration_ms, created_at
        FROM gemini_api_calls
        WHERE app_id = ?
          AND feature_id = ?
          AND feature_version = ?
          AND cache_hit = FALSE
        ORDER BY created_at, attempt_number, call_id
        """,
        [app_id, TOPIC_ANGLE_FEATURE_ID, TOPIC_ANGLE_FEATURE_VERSION],
    ).fetchall()
    columns = [str(item[0]) for item in con.description]
    items = [dict(zip(columns, row)) for row in rows]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(items):
        key = _clean_text(item.get("request_hash")) or f"unkeyed-{index}"
        grouped.setdefault(key, []).append(item)

    terminal_rows: list[dict[str, Any]] = []
    for group in grouped.values():
        terminals = [
            item
            for item in group
            if _clean_text(item.get("status")).casefold() != "retrying"
        ]
        if terminals:
            terminal_rows.append(
                max(terminals, key=lambda item: int(item.get("attempt_number") or 0))
            )

    expected_thinking = _clean_text(thinking_level).casefold()

    def matches_runtime(item: dict[str, Any]) -> bool:
        return (
            int(item.get("configured_items_per_request") or 0) == int(items_per_request)
            and _clean_text(item.get("thinking_level")).casefold() == expected_thinking
            and int(item.get("request_timeout_seconds") or 0) == int(timeout_seconds)
        )

    def is_validation_failure(item: dict[str, Any]) -> bool:
        return (
            _clean_text(item.get("status")).casefold() == "response_validation_error"
            or _clean_text(item.get("error_type")).casefold()
            == "response_validation_error"
        )

    matching_terminal_rows = [item for item in terminal_rows if matches_runtime(item)]
    successful_statuses = {"success", "success_after_retry"}
    successful = [
        item
        for item in terminal_rows
        if _clean_text(item.get("status")).casefold() in successful_statuses
    ]
    matching_successful = [item for item in successful if matches_runtime(item)]
    matching_validation_failures = [
        item for item in matching_terminal_rows if is_validation_failure(item)
    ]
    other_runtime_validation_failures = [
        item
        for item in terminal_rows
        if is_validation_failure(item) and not matches_runtime(item)
    ]
    requested_items = sum(
        int(item.get("requested_item_count") or 0)
        for item in matching_successful
    )
    matching_runtime = len(matching_successful)
    generation_tokens = [
        int(item.get("output_tokens") or 0) + int(item.get("thought_tokens") or 0)
        for item in matching_successful
        if item.get("output_tokens") is not None or item.get("thought_tokens") is not None
    ]
    durations = [
        int(item.get("duration_ms") or 0)
        for item in matching_successful
        if item.get("duration_ms") is not None
    ]
    created_values = [item.get("created_at") for item in items if item.get("created_at")]

    return TopicAngleOperationMetrics(
        attempt_count=len(items),
        completed_request_count=len(terminal_rows),
        successful_request_count=len(successful),
        validation_failure_count=len(matching_validation_failures),
        other_runtime_validation_failure_count=len(other_runtime_validation_failures),
        retrying_attempt_count=sum(
            _clean_text(item.get("status")).casefold() == "retrying" for item in items
        ),
        requested_items=requested_items,
        matching_runtime_request_count=matching_runtime,
        average_generation_tokens=(
            int(round(sum(generation_tokens) / len(generation_tokens)))
            if generation_tokens
            else 0
        ),
        maximum_generation_tokens=max(generation_tokens, default=0),
        average_duration_ms=(
            int(round(sum(durations) / len(durations))) if durations else 0
        ),
        first_recorded_at=min(created_values) if created_values else None,
        last_recorded_at=max(created_values) if created_values else None,
    )


def _load_backlog_metrics(
    con: duckdb.DuckDBPyConnection,
    *,
    min_opportunity_score: float,
    items_per_request: int,
) -> TopicAngleBacklogMetrics:
    row = con.execute(
        """
        SELECT
            COUNT(*) AS eligible_count,
            SUM(
                CASE
                    WHEN (
                        SELECT COUNT(*)
                        FROM trend_cluster_ai_angles tca
                        WHERE tca.cluster_id = tc.cluster_id
                    ) >= 3
                    AND EXISTS (
                        SELECT 1
                        FROM trend_cluster_ai_profiles tcp
                        WHERE tcp.cluster_id = tc.cluster_id
                          AND COALESCE(TRIM(tcp.content_plan_json), '') NOT IN ('', '{}')
                    )
                    THEN 1 ELSE 0
                END
            ) AS completed_count
        FROM trend_clusters tc
        WHERE COALESCE(tc.recommendation_status, 'review') IN ('recommended', 'review')
          AND COALESCE(tc.opportunity_score, 0) >= ?
        """,
        [float(min_opportunity_score)],
    ).fetchone()
    eligible = int((row[0] if row else 0) or 0)
    completed = int((row[1] if row else 0) or 0)
    pending = max(0, eligible - completed)
    per_run = max(1, int(items_per_request))
    return TopicAngleBacklogMetrics(
        eligible_cluster_count=eligible,
        completed_cluster_count=completed,
        pending_cluster_count=pending,
        estimated_runs_to_clear=int(math.ceil(pending / per_run)) if pending else 0,
    )


def build_topic_angle_quality_diagnostic(
    con: duckdb.DuckDBPyConnection,
    *,
    app_id: str,
    items_per_request: int,
    thinking_level: str,
    timeout_seconds: int,
    min_opportunity_score: float,
) -> TopicAngleQualityDiagnostic:
    """현재 DuckDB만 읽어 v6 방향 계약과 운영 표본을 진단합니다."""
    contract, issue_examples = _load_contract_metrics(con)
    operation = _load_operation_metrics(
        con,
        app_id=app_id,
        items_per_request=items_per_request,
        thinking_level=thinking_level,
        timeout_seconds=timeout_seconds,
    )
    backlog = _load_backlog_metrics(
        con,
        min_opportunity_score=min_opportunity_score,
        items_per_request=items_per_request,
    )

    reasons: list[str] = []
    if operation.sample_sufficient:
        reasons.append(
            f"현재 조건 일치 성공 요청 {operation.matching_runtime_request_count}회·"
            f"요청 글감 {operation.requested_items}개로 최소 표본을 충족했습니다."
        )
    else:
        reasons.append(
            f"현재 조건 일치 성공 요청 {operation.matching_runtime_request_count}회·"
            f"요청 글감 {operation.requested_items}개이며, 최소 {TARGET_COMPLETED_REQUESTS}회·"
            f"{TARGET_REQUESTED_ITEMS}개가 필요합니다."
        )

    if operation.other_runtime_validation_failure_count:
        reasons.append(
            "다른 실행 조건의 과거 응답 검증 실패 "
            f"{operation.other_runtime_validation_failure_count}회는 현재 조건 판단에서 제외했습니다."
        )

    contract_rate = contract.contract_completion_rate
    if contract.direction_count:
        reasons.append(
            f"v6 방향 {contract.direction_count}개 중 계약 완전 방향 "
            f"{contract.contract_complete_count}개"
            + (
                f"({contract_rate * 100:.1f}%)입니다."
                if contract_rate is not None
                else "입니다."
            )
        )
    else:
        reasons.append("아직 v6로 저장된 주제 방향이 없습니다.")

    if contract.evidence_reference_count:
        link_rate = contract.evidence_link_rate or 0.0
        reasons.append(
            f"저장된 원문 근거 연결 {contract.valid_evidence_reference_count}/"
            f"{contract.evidence_reference_count}개({link_rate * 100:.1f}%)가 현재 원문과 연결됩니다."
        )

    if backlog.pending_cluster_count:
        reasons.append(
            f"현재 기준 분석 대기 글감은 {backlog.pending_cluster_count}개이며 "
            f"요청당 {max(1, int(items_per_request))}개 기준 약 "
            f"{backlog.estimated_runs_to_clear}회가 필요합니다."
        )
    else:
        reasons.append("현재 기준에서 분석 대기 글감이 없습니다.")

    integrity_issues = (
        contract.invalid_json_count
        + contract.missing_required_count
        + contract.score_issue_count
        + contract.ordering_issue_count
        + contract.broken_evidence_link_count
    )
    if not operation.completed_request_count and not contract.direction_count:
        status = "표본 대기"
        summary = "새 계약의 첫 완료 요청과 저장 결과를 기다리고 있습니다."
    elif integrity_issues:
        status = "저장 데이터 점검"
        summary = "v6 저장 데이터에서 계약 또는 근거 연결 점검 항목이 확인됐습니다."
    elif operation.validation_failure_count:
        status = "응답 검증 주의"
        summary = (
            "현재 설정 상한·사고 수준·제한 시간과 일치한 v6 API 응답 검증 실패가 있어 "
            "다음 완료 요청과 함께 원인을 확인해야 합니다."
        )
    elif not operation.sample_sufficient:
        status = "표본 수집 중"
        summary = "현재 저장 상태는 정상이며 운영 판단에 필요한 표본을 더 모으는 단계입니다."
    else:
        status = "정상 관찰"
        summary = "v6 저장 계약과 운영 표본에서 즉시 수정할 반복 문제가 확인되지 않았습니다."

    return TopicAngleQualityDiagnostic(
        status=status,
        summary=summary,
        reasons=tuple(reasons),
        contract=contract,
        operation=operation,
        backlog=backlog,
        issue_examples=issue_examples,
    )
