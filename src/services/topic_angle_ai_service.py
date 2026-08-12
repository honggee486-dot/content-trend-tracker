from __future__ import annotations

import hashlib
import json
import math
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from queue import Empty, Queue
from typing import Any, Callable
from uuid import uuid4

import duckdb

from src.config import GeminiConfig
from src.services.gemini_service import (
    GeminiHttpError,
    call_gemini_structured_output,
    effective_gemini_timeout_seconds,
    normalize_gemini_api_result,
    normalize_gemini_thinking_level,
    record_gemini_api_call,
    scan_sensitive_fields,
)
from src.services.topic_angle_demand_contract import (
    DIRECTION_SCORE_LIMITS,
    build_evidence_contract,
    public_cluster_payload,
    stable_score_sort,
    validate_direction_contract,
)
from src.services.trend_discovery_service import get_trend_cluster_items

TOPIC_ANGLE_FEATURE_ID = "trend_topic_angle_batch_v1"
TOPIC_ANGLE_FEATURE_VERSION = "6"

TOPIC_ANGLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cluster_id": {"type": "string"},
                    "display_title": {"type": "string"},
                    "summary": {"type": "string"},
                    "content_plan": {
                        "type": "object",
                        "properties": {
                            "audience": {"type": "string"},
                            "purpose": {"type": "string"},
                            "category": {"type": "string"},
                            "target_length": {
                                "type": "integer",
                                "minimum": 1200,
                                "maximum": 4000,
                            },
                            "title_rules": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 4,
                                "items": {"type": "string"},
                            },
                            "outline": {
                                "type": "array",
                                "minItems": 4,
                                "maxItems": 6,
                                "items": {"type": "string"},
                            },
                            "forbidden_expressions": {
                                "type": "array",
                                "minItems": 3,
                                "maxItems": 6,
                                "items": {"type": "string"},
                            },
                            "timeliness": {
                                "type": "object",
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "enum": [
                                            "breaking",
                                            "short_lived",
                                            "ongoing",
                                            "evergreen",
                                        ],
                                    },
                                    "publish_priority": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "maximum": 5,
                                    },
                                    "freshness_window_hours": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "maximum": 8760,
                                    },
                                    "recheck_before_writing": {"type": "boolean"},
                                    "reason": {"type": "string"},
                                },
                                "required": [
                                    "type",
                                    "publish_priority",
                                    "freshness_window_hours",
                                    "recheck_before_writing",
                                    "reason",
                                ],
                                "additionalProperties": False,
                            },
                            "evidence_plan": {
                                "type": "object",
                                "properties": {
                                    "required_source_types": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": 4,
                                        "items": {"type": "string"},
                                    },
                                    "evidence_gaps": {
                                        "type": "array",
                                        "maxItems": 4,
                                        "items": {"type": "string"},
                                    },
                                    "official_search_queries": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": 3,
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": [
                                    "required_source_types",
                                    "evidence_gaps",
                                    "official_search_queries",
                                ],
                                "additionalProperties": False,
                            },
                            "primary_direction_reason": {"type": "string"},
                        },
                        "required": [
                            "audience",
                            "purpose",
                            "category",
                            "target_length",
                            "title_rules",
                            "outline",
                            "forbidden_expressions",
                            "timeliness",
                            "evidence_plan",
                            "primary_direction_reason",
                        ],
                        "additionalProperties": False,
                    },
                    "verification_points": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 5,
                        "items": {"type": "string"},
                    },
                    "directions": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "angle": {"type": "string"},
                                "rationale": {"type": "string"},
                                "search_queries": {
                                    "type": "array", "minItems": 1, "maxItems": 3,
                                    "items": {"type": "string"},
                                },
                                "search_intent": {"type": "string"},
                                "reader_question": {"type": "string"},
                                "demand_evidence": {
                                    "type": "array", "minItems": 1, "maxItems": 3,
                                    "items": {"type": "string"},
                                },
                                "evidence_source_ids": {
                                    "type": "array", "minItems": 1, "maxItems": 3,
                                    "items": {"type": "string"},
                                },
                                "score_breakdown": {
                                    "type": "object",
                                    "properties": {
                                        "search_intent_fit": {"type": "integer", "minimum": 0, "maximum": 35},
                                        "demand_signal_support": {"type": "integer", "minimum": 0, "maximum": 30},
                                        "evidence_availability": {"type": "integer", "minimum": 0, "maximum": 20},
                                        "differentiation": {"type": "integer", "minimum": 0, "maximum": 10},
                                        "timeliness_practicality": {"type": "integer", "minimum": 0, "maximum": 5},
                                    },
                                    "required": ["search_intent_fit", "demand_signal_support", "evidence_availability", "differentiation", "timeliness_practicality"],
                                    "additionalProperties": False,
                                },
                                "score_reasons": {
                                    "type": "array", "minItems": 1, "maxItems": 5,
                                    "items": {"type": "string"},
                                },
                            },
                            "required": [
                                "label", "angle", "rationale", "search_queries",
                                "search_intent", "reader_question", "demand_evidence",
                                "evidence_source_ids", "score_breakdown", "score_reasons",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": [
                    "cluster_id",
                    "display_title",
                    "summary",
                    "content_plan",
                    "verification_points",
                    "directions",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clusters"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class TopicAngleBatchResult:
    status: str
    requested_clusters: int
    generated_clusters: int
    generated_angles: int
    skipped_sensitive_clusters: int
    attempts: int
    error_type: str = ""
    error_message: str = ""
    requested_batches: int = 0
    completed_batches: int = 0
    failed_batches: int = 0
    items_per_request: int = 0
    max_parallel_requests: int = 0
    duration_seconds: float = 0.0
    min_opportunity_score: float = 0.0


@dataclass(frozen=True)
class _AttemptRecord:
    attempt_number: int
    status: str
    http_status: int | None
    error_type: str
    retry_reason: str
    retry_wait_seconds: float
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    duration_ms: int
    error_message: str
    thought_tokens: int | None = None
    finish_reason: str = ""
    finish_message: str = ""


@dataclass(frozen=True)
class _BatchExecutionResult:
    batch_number: int
    clusters: tuple[dict[str, Any], ...]
    request_hash: str
    enrichments: dict[str, dict[str, Any]]
    validation_errors: tuple[str, ...]
    attempts: tuple[_AttemptRecord, ...]
    status: str
    error_type: str = ""
    error_message: str = ""
    response_text: str = ""


@dataclass(frozen=True)
class TopicAnglePreparation:
    """DB에서 읽은 Gemini 글감 요청 준비 상태입니다.

    준비가 끝난 뒤에는 DuckDB 연결을 닫아도 `batches`만으로 외부 API를
    호출할 수 있습니다. 예약 수집은 이 경계를 이용해 긴 네트워크 대기 중
    메인 DB 파일을 점유하지 않습니다.
    """

    status: str
    clusters: tuple[dict[str, Any], ...]
    batches: tuple[tuple[dict[str, Any], ...], ...]
    skipped_sensitive_clusters: int
    items_per_request: int
    max_parallel_requests: int
    min_opportunity_score: float
    started_at: float


@dataclass(frozen=True)
class TopicAngleExecution:
    """DB 연결 없이 완료한 Gemini 네트워크 요청 결과입니다."""

    preparation: TopicAnglePreparation
    results: tuple[_BatchExecutionResult, ...]


ProgressCallback = Callable[[float, str], None]


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_public_text(value: Any, field: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    return "" if scan_sensitive_fields([(field, text)]) else text


def _format_seconds(value: float) -> str:
    seconds = max(0, int(round(value)))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _emit_progress(
    progress_callback: ProgressCallback | None,
    status_callback: Callable[[str], None] | None,
    value: float,
    message: str,
) -> None:
    if progress_callback is not None:
        progress_callback(max(0.0, min(1.0, float(value))), message)
    elif status_callback is not None:
        status_callback(message)


def _missing_clusters(
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int,
    min_opportunity_score: float,
) -> tuple[list[dict[str, Any]], int]:
    rows = con.execute(
        """
        SELECT tc.cluster_id, tc.canonical_title, tc.trend_score,
               tc.opportunity_score, tc.recommendation_status, tc.last_seen_at,
               COUNT(tca.angle_id) AS angle_count
        FROM trend_clusters tc
        LEFT JOIN trend_cluster_ai_angles tca ON tca.cluster_id = tc.cluster_id
        LEFT JOIN trend_cluster_ai_profiles tcp ON tcp.cluster_id = tc.cluster_id
        WHERE COALESCE(tc.recommendation_status, 'review') IN ('recommended', 'review')
          AND COALESCE(tc.opportunity_score, 0) >= ?
        GROUP BY tc.cluster_id, tc.canonical_title, tc.trend_score,
                 tc.opportunity_score, tc.recommendation_status, tc.last_seen_at
        HAVING COUNT(tca.angle_id) < 3
            OR MAX(
                CASE
                    WHEN tcp.cluster_id IS NOT NULL
                     AND COALESCE(TRIM(tcp.content_plan_json), '') NOT IN ('', '{}')
                    THEN 1 ELSE 0
                END
            ) = 0
        ORDER BY tc.opportunity_score DESC, tc.trend_score DESC, tc.last_seen_at DESC
        LIMIT ?
        """,
        [float(min_opportunity_score), max(1, min(int(limit), 400))],
    ).fetchall()
    columns = [item[0] for item in con.description]
    candidates: list[dict[str, Any]] = []
    skipped_sensitive = 0
    for row in rows:
        cluster = dict(zip(columns, row))
        title = _safe_public_text(cluster.get("canonical_title"), "글감 제목")
        if not title:
            skipped_sensitive += 1
            continue
        items = get_trend_cluster_items(con, str(cluster["cluster_id"]))
        evidence, evidence_map = build_evidence_contract(
            items,
            safe_public_text=_safe_public_text,
            maximum=8,
        )
        if not evidence:
            continue
        candidates.append(
            {
                "cluster_id": _clean_text(cluster.get("cluster_id")),
                "topic": title,
                "trend_score": float(cluster.get("trend_score") or 0),
                "opportunity_score": float(cluster.get("opportunity_score") or 0),
                "signals": evidence,
                "evidence_source_map": evidence_map,
            }
        )
    return candidates, skipped_sensitive


def _build_request(
    config: GeminiConfig,
    clusters: list[dict[str, Any]],
) -> tuple[str, str]:
    public_clusters = [public_cluster_payload(cluster) for cluster in clusters]
    score_rubric = ", ".join(
        f"{key}=0~{maximum}" for key, maximum in DIRECTION_SCORE_LIMITS.items()
    )
    instructions = (
        "아래 공개 트렌드 글감들을 검토해 화면과 AI 요청서에 저장할 분석 정보를 만드세요. "
        "각 글감마다 display_title, summary, content_plan, verification_points 3~5개와 "
        "서로 겹치지 않는 정보성 directions 3개를 반환하세요. "
        "표시 제목·요약·작성 설정의 기존 원칙을 유지하고 확인되지 않은 사실을 확정하지 마세요. "
        "각 신호의 evidence_id(E1~E8)는 이번 요청 안에서만 유효한 근거 ID입니다. "
        "방향마다 search_intent에는 독자가 검색하는 목적, reader_question에는 실제 질문 한 문장, "
        "demand_evidence에는 입력 신호에서 확인되는 관심 근거 1~3개, evidence_source_ids에는 "
        "그 근거를 뒷받침하는 E ID 1~3개를 넣으세요. 요청에 없는 E ID는 사용하지 마세요. "
        "score_breakdown은 다음 범위로 보수적으로 채우세요: " + score_rubric + ". "
        "검색량이 제공되지 않았다면 검색량을 만들거나 추정하지 마세요. approximate_interest는 "
        "Google Trends의 근사 관심 신호일 뿐 실제 검색량이 아닙니다. "
        "discovery_query, keyword, observation_count, 반복 수, 제공된 신호값과 조회 지표를 "
        "수요 연결 근거로 사용하되 값이 없는 지표를 만들어내지 마세요. "
        "세 방향은 핵심 설명·변화 비교·실용 정보처럼 서로 다른 독자 질문을 다루고, "
        "각 방향의 score_reasons에 점수 판단 이유를 1~5개 적으세요. "
        "모델이 반환한 배열 순서는 참고하지 않으며 프로그램이 검증된 하위 점수 합계로 다시 정렬합니다. "
        "content_plan의 primary_direction_reason은 가장 유력한 방향의 이유를 작성하되 프로그램에서 "
        "최종 정렬 후 다시 검증합니다. 입력된 공개 신호만 사용하세요."
    )
    request_text = instructions + "\n\n[글감 목록]\n" + json.dumps(
        {"clusters": public_clusters},
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    hash_payload = {
        "app_id": config.app_id,
        "feature_id": TOPIC_ANGLE_FEATURE_ID,
        "feature_version": TOPIC_ANGLE_FEATURE_VERSION,
        "model": config.model,
        "thinking_level": config.topic_angle_thinking_level,
        "clusters": public_clusters,
    }
    request_hash = hashlib.sha256(
        json.dumps(hash_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return request_text, request_hash


def _validated_enrichments(
    raw_response: str,
    requested_clusters: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    requested_ids = set(requested_clusters)
    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        return {}, [f"주제 방향 응답 JSON 오류: {exc.msg}"]
    if not isinstance(parsed, dict) or not isinstance(parsed.get("clusters"), list):
        return {}, ["주제 방향 응답에 clusters 배열이 없습니다."]

    validated: dict[str, dict[str, Any]] = {}
    for cluster_index, cluster in enumerate(parsed["clusters"], start=1):
        if not isinstance(cluster, dict):
            errors.append(f"clusters[{cluster_index}]가 객체가 아닙니다.")
            continue
        cluster_id = _clean_text(cluster.get("cluster_id"))
        if cluster_id not in requested_ids:
            errors.append(f"요청하지 않은 cluster_id가 반환됐습니다: {cluster_id}")
            continue

        display_title = _clean_text(cluster.get("display_title"))
        summary = _clean_text(cluster.get("summary"))
        raw_points = cluster.get("verification_points")
        verification_points: list[str] = []
        point_keys: set[str] = set()
        for item in raw_points if isinstance(raw_points, list) else []:
            point = _clean_text(item)
            key = point.casefold()
            if point and key not in point_keys:
                point_keys.add(key)
                verification_points.append(point[:500])
        verification_points = verification_points[:5]
        if not display_title or not summary or not 3 <= len(verification_points) <= 5:
            errors.append(f"{cluster_id}의 표시 제목·요약·확인 항목이 올바르지 않습니다.")
            continue

        raw_plan = cluster.get("content_plan")
        if not isinstance(raw_plan, dict):
            errors.append(f"{cluster_id}의 AI 요청서 기본 설정이 없습니다.")
            continue
        audience = _clean_text(raw_plan.get("audience"))
        purpose = _clean_text(raw_plan.get("purpose"))
        category = _clean_text(raw_plan.get("category"))
        try:
            target_length = int(raw_plan.get("target_length"))
        except (TypeError, ValueError):
            target_length = 0

        def clean_plan_items(
            value: Any,
            *,
            max_items: int,
            max_length: int,
        ) -> list[str]:
            result: list[str] = []
            seen: set[str] = set()
            for item in value if isinstance(value, list) else []:
                text = _clean_text(item)
                key = text.casefold()
                if text and key not in seen:
                    seen.add(key)
                    result.append(text[:max_length])
            return result[:max_items]

        title_rules = clean_plan_items(
            raw_plan.get("title_rules"),
            max_items=4,
            max_length=300,
        )
        outline = clean_plan_items(
            raw_plan.get("outline"),
            max_items=6,
            max_length=500,
        )
        forbidden_expressions = clean_plan_items(
            raw_plan.get("forbidden_expressions"),
            max_items=6,
            max_length=120,
        )

        raw_timeliness = raw_plan.get("timeliness")
        timeliness_type = ""
        publish_priority = 0
        freshness_window_hours = 0
        recheck_before_writing: bool | None = None
        timeliness_reason = ""
        if isinstance(raw_timeliness, dict):
            timeliness_type = _clean_text(raw_timeliness.get("type")).casefold()
            try:
                publish_priority = int(raw_timeliness.get("publish_priority"))
            except (TypeError, ValueError):
                publish_priority = 0
            try:
                freshness_window_hours = int(
                    raw_timeliness.get("freshness_window_hours")
                )
            except (TypeError, ValueError):
                freshness_window_hours = 0
            raw_recheck = raw_timeliness.get("recheck_before_writing")
            recheck_before_writing = raw_recheck if isinstance(raw_recheck, bool) else None
            timeliness_reason = _clean_text(raw_timeliness.get("reason"))

        raw_evidence_plan = raw_plan.get("evidence_plan")
        required_source_types: list[str] = []
        evidence_gaps: list[str] = []
        official_search_queries: list[str] = []
        if isinstance(raw_evidence_plan, dict):
            required_source_types = clean_plan_items(
                raw_evidence_plan.get("required_source_types"),
                max_items=4,
                max_length=160,
            )
            evidence_gaps = clean_plan_items(
                raw_evidence_plan.get("evidence_gaps"),
                max_items=4,
                max_length=300,
            )
            official_search_queries = clean_plan_items(
                raw_evidence_plan.get("official_search_queries"),
                max_items=3,
                max_length=200,
            )
        primary_direction_reason = _clean_text(
            raw_plan.get("primary_direction_reason")
        )

        if (
            not audience
            or not purpose
            or not category
            or not 1200 <= target_length <= 4000
            or not 2 <= len(title_rules) <= 4
            or not 4 <= len(outline) <= 6
            or not 3 <= len(forbidden_expressions) <= 6
            or timeliness_type
            not in {"breaking", "short_lived", "ongoing", "evergreen"}
            or not 1 <= publish_priority <= 5
            or not 1 <= freshness_window_hours <= 8760
            or recheck_before_writing is None
            or not timeliness_reason
            or not 1 <= len(required_source_types) <= 4
            or len(evidence_gaps) > 4
            or not 1 <= len(official_search_queries) <= 3
            or not primary_direction_reason
        ):
            errors.append(f"{cluster_id}의 AI 요청서 기본 설정이 올바르지 않습니다.")
            continue
        content_plan = {
            "audience": audience[:300],
            "purpose": purpose[:500],
            "category": category[:100],
            "target_length": target_length,
            "title_rules": title_rules,
            "outline": outline,
            "forbidden_expressions": forbidden_expressions,
            "timeliness": {
                "type": timeliness_type,
                "publish_priority": publish_priority,
                "freshness_window_hours": freshness_window_hours,
                "recheck_before_writing": recheck_before_writing,
                "reason": timeliness_reason[:500],
            },
            "evidence_plan": {
                "required_source_types": required_source_types,
                "evidence_gaps": evidence_gaps,
                "official_search_queries": official_search_queries,
            },
            "primary_direction_reason": primary_direction_reason[:500],
        }

        directions = cluster.get("directions")
        if not isinstance(directions, list) or len(directions) != 3:
            errors.append(f"{cluster_id}의 주제 방향은 정확히 3개여야 합니다.")
            continue
        evidence_map = dict(
            requested_clusters[cluster_id].get("evidence_source_map") or {}
        )
        normalized_directions: list[dict[str, Any]] = []
        seen_angles: set[str] = set()
        for direction_index, direction in enumerate(directions, start=1):
            normalized, validation_error = validate_direction_contract(
                direction,
                evidence_map=evidence_map,
            )
            if normalized is None:
                errors.append(
                    f"{cluster_id} 방향 {direction_index}: {validation_error}"
                )
                continue
            angle_key = str(normalized["angle"]).casefold()
            if angle_key in seen_angles:
                errors.append(
                    f"{cluster_id}에 중복된 주제 방향이 있습니다: {normalized['angle']}"
                )
                continue
            seen_angles.add(angle_key)
            normalized_directions.append(normalized)
        if len(normalized_directions) != 3:
            continue
        normalized_directions = stable_score_sort(normalized_directions)
        primary_direction = normalized_directions[0]
        primary_reason = (
            primary_direction.get("score_reasons") or [primary_direction["rationale"]]
        )[0]
        content_plan["primary_direction_reason"] = (
            f"검증된 방향 점수 {primary_direction['direction_score']}점으로 1순위입니다. "
            f"{primary_reason}"
        )[:500]
        validated[cluster_id] = {
            "display_title": display_title[:200],
            "summary": summary[:1500],
            "content_plan": content_plan,
            "verification_points": verification_points,
            "directions": normalized_directions,
        }
    return validated, errors


def list_cluster_ai_angles(
    con: duckdb.DuckDBPyConnection,
    cluster_id: str,
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT angle_id, cluster_id, canonical_title, angle_order,
               angle_label, angle_text, rationale, search_queries_json,
               search_intent, reader_question, demand_evidence_json,
               evidence_source_ids_json, score_breakdown_json, direction_score,
               score_reasons_json, model_name, feature_version, created_at, updated_at
        FROM trend_cluster_ai_angles
        WHERE cluster_id = ?
        ORDER BY angle_order
        """,
        [cluster_id],
    ).fetchall()
    columns = [item[0] for item in con.description]
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(zip(columns, row))
        try:
            queries = json.loads(item.pop("search_queries_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            queries = []
        item["search_queries"] = queries if isinstance(queries, list) else []
        for json_column, output_key, fallback in (
            ("demand_evidence_json", "demand_evidence", []),
            ("evidence_source_ids_json", "evidence_source_ids", []),
            ("score_breakdown_json", "score_breakdown", {}),
            ("score_reasons_json", "score_reasons", []),
        ):
            try:
                parsed = json.loads(item.pop(json_column) or json.dumps(fallback))
            except (TypeError, json.JSONDecodeError):
                parsed = fallback
            item[output_key] = parsed if isinstance(parsed, type(fallback)) else fallback
        result.append(item)
    return result


def get_cluster_ai_profile(
    con: duckdb.DuckDBPyConnection,
    cluster_id: str,
) -> dict[str, Any] | None:
    row = con.execute(
        """
        SELECT cluster_id, canonical_title, display_title, summary,
               verification_points_json, content_plan_json,
               model_name, feature_version, created_at, updated_at
        FROM trend_cluster_ai_profiles
        WHERE cluster_id = ?
        """,
        [cluster_id],
    ).fetchone()
    if row is None:
        return None
    columns = [item[0] for item in con.description]
    result = dict(zip(columns, row))
    try:
        points = json.loads(result.pop("verification_points_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        points = []
    try:
        content_plan = json.loads(result.pop("content_plan_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        content_plan = {}
    result["verification_points"] = points if isinstance(points, list) else []
    result["content_plan"] = content_plan if isinstance(content_plan, dict) else {}
    return result


def _execute_batch_request(
    *,
    batch_number: int,
    clusters: list[dict[str, Any]],
    start_delay_seconds: float,
    config: GeminiConfig,
    event_queue: Queue[dict[str, Any]],
    sleep_func: Callable[[float], None],
) -> _BatchExecutionResult:
    if start_delay_seconds > 0:
        event_queue.put(
            {
                "type": "scheduled",
                "batch_number": batch_number,
                "delay": start_delay_seconds,
            }
        )
        sleep_func(start_delay_seconds)
    event_queue.put({"type": "started", "batch_number": batch_number})

    request_text, request_hash = _build_request(config, clusters)
    requested_clusters = {str(item["cluster_id"]): item for item in clusters}
    attempt_records: list[_AttemptRecord] = []
    attempt = 0
    waited_seconds = 0.0
    effective_thinking_level = normalize_gemini_thinking_level(
        config.topic_angle_thinking_level,
        fallback=config.draft_thinking_level,
    )
    effective_timeout_seconds = effective_gemini_timeout_seconds(
        config,
        config.topic_angle_timeout_seconds,
    )

    while True:
        attempt += 1
        call_started = time.perf_counter()
        try:
            api_result = call_gemini_structured_output(
                config,
                request_text,
                request_hash,
                feature_id=TOPIC_ANGLE_FEATURE_ID,
                response_schema=TOPIC_ANGLE_SCHEMA,
                use_google_search=False,
                thinking_level=effective_thinking_level,
                timeout_seconds=effective_timeout_seconds,
            )
            (
                output_text,
                input_tokens,
                output_tokens,
                thought_tokens,
                total_tokens,
                finish_reason,
                finish_message,
            ) = normalize_gemini_api_result(api_result)
            enrichments, validation_errors = _validated_enrichments(
                output_text,
                requested_clusters,
            )
            duration_ms = int((time.perf_counter() - call_started) * 1000)
            if not enrichments:
                message = "; ".join(validation_errors[:5]) or "유효한 글감 분석 결과가 없습니다."
                attempt_records.append(
                    _AttemptRecord(
                        attempt,
                        "response_validation_error",
                        200,
                        "response_validation_error",
                        "",
                        0,
                        input_tokens,
                        output_tokens,
                        total_tokens,
                        duration_ms,
                        message,
                        thought_tokens,
                        finish_reason,
                        finish_message,
                    )
                )
                return _BatchExecutionResult(
                    batch_number,
                    tuple(clusters),
                    request_hash,
                    {},
                    tuple(validation_errors),
                    tuple(attempt_records),
                    "response_validation_error",
                    "response_validation_error",
                    message,
                    output_text,
                )

            status = "success_after_retry" if attempt > 1 else "success"
            message = "; ".join(validation_errors[:5])
            attempt_records.append(
                _AttemptRecord(
                    attempt,
                    status,
                    200,
                    "",
                    "",
                    0,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    duration_ms,
                    message,
                    thought_tokens,
                    finish_reason,
                    finish_message,
                )
            )
            return _BatchExecutionResult(
                batch_number,
                tuple(clusters),
                request_hash,
                enrichments,
                tuple(validation_errors),
                tuple(attempt_records),
                status,
                error_message=message,
                response_text=output_text,
            )
        except GeminiHttpError as exc:
            info = exc.info
            duration_ms = int((time.perf_counter() - call_started) * 1000)
            delay = (
                info.retry_delay_seconds
                if info.retry_delay_seconds is not None
                else config.retry_wait_seconds
            )
            can_retry = (
                info.retryable
                and delay >= 0
                and waited_seconds + delay <= config.retry_max_wait_seconds
            )
            attempt_records.append(
                _AttemptRecord(
                    attempt,
                    "retrying" if can_retry else info.error_type,
                    info.http_status or None,
                    info.error_type,
                    info.error_type if can_retry else "",
                    delay if can_retry else 0,
                    None,
                    None,
                    None,
                    duration_ms,
                    info.message,
                    finish_reason=info.finish_reason,
                    finish_message=info.finish_message,
                )
            )
            if not can_retry:
                status = (
                    "rate_limit_timeout"
                    if info.retryable and info.error_type == "rate_limited"
                    else info.error_type
                )
                return _BatchExecutionResult(
                    batch_number,
                    tuple(clusters),
                    request_hash,
                    {},
                    (),
                    tuple(attempt_records),
                    status,
                    info.error_type,
                    info.message,
                )
            waited_seconds += delay
            event_queue.put(
                {
                    "type": "retry",
                    "batch_number": batch_number,
                    "delay": delay,
                    "waited": waited_seconds,
                    "max_wait": config.retry_max_wait_seconds,
                }
            )
            sleep_func(delay)


def _record_batch_attempts(
    con: duckdb.DuckDBPyConnection,
    *,
    config: GeminiConfig,
    result: _BatchExecutionResult,
) -> None:
    synthetic_pack_id = f"topic_angle_batch_{result.request_hash[:20]}"
    request_text, _ = _build_request(config, list(result.clusters))
    last_attempt_number = result.attempts[-1].attempt_number if result.attempts else 0
    for item in result.attempts:
        record_gemini_api_call(
            con,
            config=config,
            content_pack_id=synthetic_pack_id,
            request_hash=result.request_hash,
            feature_id=TOPIC_ANGLE_FEATURE_ID,
            feature_version=TOPIC_ANGLE_FEATURE_VERSION,
            attempt_number=item.attempt_number,
            cache_hit=False,
            status=item.status,
            http_status=item.http_status,
            error_type=item.error_type,
            retry_reason=item.retry_reason,
            retry_wait_seconds=item.retry_wait_seconds,
            input_tokens=item.input_tokens,
            output_tokens=item.output_tokens,
            total_tokens=item.total_tokens,
            duration_ms=item.duration_ms,
            error_message=item.error_message,
            thought_tokens=item.thought_tokens,
            request_text=request_text,
            response_text=(
                result.response_text
                if item.attempt_number == last_attempt_number
                else ""
            ),
            requested_item_count=len(result.clusters),
            configured_items_per_request=max(1, int(config.topic_angle_batch_limit)),
            thinking_level=normalize_gemini_thinking_level(
                config.topic_angle_thinking_level,
                fallback=config.draft_thinking_level,
            ),
            request_timeout_seconds=effective_gemini_timeout_seconds(
                config,
                config.topic_angle_timeout_seconds,
            ),
            finish_reason=item.finish_reason,
            finish_message=item.finish_message,
        )


def _save_batch_enrichments(
    con: duckdb.DuckDBPyConnection,
    *,
    config: GeminiConfig,
    result: _BatchExecutionResult,
) -> int:
    if not result.enrichments:
        return 0
    now = datetime.now()
    title_by_id = {
        str(item["cluster_id"]): str(item["topic"]) for item in result.clusters
    }
    con.execute("BEGIN TRANSACTION")
    try:
        for cluster_id, enrichment in result.enrichments.items():
            canonical_title = title_by_id.get(cluster_id, "")
            con.execute(
                "DELETE FROM trend_cluster_ai_profiles WHERE cluster_id = ?",
                [cluster_id],
            )
            con.execute(
                """
                INSERT INTO trend_cluster_ai_profiles(
                    cluster_id, canonical_title, display_title, summary,
                    verification_points_json, content_plan_json,
                    model_name, feature_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    cluster_id,
                    canonical_title,
                    enrichment["display_title"],
                    enrichment["summary"],
                    json.dumps(enrichment["verification_points"], ensure_ascii=False),
                    json.dumps(enrichment["content_plan"], ensure_ascii=False),
                    config.model,
                    TOPIC_ANGLE_FEATURE_VERSION,
                    now,
                    now,
                ],
            )
            con.execute(
                "DELETE FROM trend_cluster_ai_angles WHERE cluster_id = ?",
                [cluster_id],
            )
            for order, direction in enumerate(enrichment["directions"], start=1):
                con.execute(
                    """
                    INSERT INTO trend_cluster_ai_angles(
                        angle_id, cluster_id, canonical_title, angle_order,
                        angle_label, angle_text, rationale, search_queries_json,
                        search_intent, reader_question, demand_evidence_json,
                        evidence_source_ids_json, score_breakdown_json, direction_score,
                        score_reasons_json, model_name, feature_version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        f"tcangle_{uuid4().hex}",
                        cluster_id,
                        canonical_title,
                        order,
                        direction["label"],
                        direction["angle"],
                        direction["rationale"],
                        json.dumps(direction["search_queries"], ensure_ascii=False),
                        direction["search_intent"],
                        direction["reader_question"],
                        json.dumps(direction["demand_evidence"], ensure_ascii=False),
                        json.dumps(direction["evidence_source_ids"], ensure_ascii=False),
                        json.dumps(direction["score_breakdown"], ensure_ascii=False),
                        direction["direction_score"],
                        json.dumps(direction["score_reasons"], ensure_ascii=False),
                        config.model,
                        TOPIC_ANGLE_FEATURE_VERSION,
                        now,
                        now,
                    ],
                )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(result.enrichments)


def _preparation_result(preparation: TopicAnglePreparation) -> TopicAngleBatchResult:
    duration_seconds = max(0.0, time.perf_counter() - preparation.started_at)
    return TopicAngleBatchResult(
        preparation.status,
        len(preparation.clusters),
        0,
        0,
        preparation.skipped_sensitive_clusters,
        0,
        requested_batches=len(preparation.batches),
        completed_batches=0,
        failed_batches=0,
        items_per_request=preparation.items_per_request,
        max_parallel_requests=preparation.max_parallel_requests,
        duration_seconds=duration_seconds,
        min_opportunity_score=preparation.min_opportunity_score,
    )


def prepare_missing_topic_angles(
    con: duckdb.DuckDBPyConnection,
    *,
    config: GeminiConfig,
    limit: int | None = None,
    status_callback: Callable[[str], None] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> TopicAnglePreparation:
    """Gemini에 보낼 공개 글감만 DB에서 읽어 메모리 배치로 준비합니다."""
    overall_started = time.perf_counter()
    min_opportunity_score = max(
        0.0, min(100.0, float(config.topic_angle_min_opportunity_score))
    )
    items_per_request = max(1, int(config.topic_angle_batch_limit))
    max_parallel = max(1, int(config.topic_angle_max_parallel_requests))
    if not config.api_key:
        return TopicAnglePreparation(
            "missing_api_key",
            (),
            (),
            0,
            items_per_request,
            max_parallel,
            min_opportunity_score,
            overall_started,
        )

    total_limit = int(limit) if limit is not None else items_per_request * max_parallel
    total_limit = max(1, min(total_limit, items_per_request * max_parallel, 400))

    _emit_progress(
        progress_callback,
        status_callback,
        0.04,
        f"글감 기회 점수 {min_opportunity_score:g}점 이상에서 Gemini 분석이 없는 글감을 확인하고 있습니다.",
    )
    clusters, skipped_sensitive = _missing_clusters(
        con,
        limit=total_limit,
        min_opportunity_score=min_opportunity_score,
    )
    cluster_tuple = tuple(clusters)
    if not clusters:
        return TopicAnglePreparation(
            "nothing_to_generate",
            cluster_tuple,
            (),
            skipped_sensitive,
            items_per_request,
            max_parallel,
            min_opportunity_score,
            overall_started,
        )

    batches = tuple(
        tuple(clusters[index : index + items_per_request])
        for index in range(0, len(clusters), items_per_request)
    )[:max_parallel]
    _emit_progress(
        progress_callback,
        status_callback,
        0.10,
        (
            f"글감 기회 점수 {min_opportunity_score:g}점 이상 대상 {len(clusters):,}개를 "
            f"요청당 최대 {items_per_request:,}개로 나눠 Gemini 요청 {len(batches)}개를 준비했습니다. "
            f"요청별 최대 {_format_seconds(config.topic_angle_timeout_seconds)}까지 기다립니다."
        ),
    )
    return TopicAnglePreparation(
        "ready",
        cluster_tuple,
        batches,
        skipped_sensitive,
        items_per_request,
        max_parallel,
        min_opportunity_score,
        overall_started,
    )


def execute_prepared_topic_angles(
    preparation: TopicAnglePreparation,
    *,
    config: GeminiConfig,
    status_callback: Callable[[str], None] | None = None,
    progress_callback: ProgressCallback | None = None,
    sleep_func: Callable[[float], None] = time.sleep,
    poll_interval_seconds: float = 0.25,
) -> TopicAngleExecution:
    """준비된 배치를 DB 연결 없이 Gemini에 전송하고 응답을 검증합니다."""
    if preparation.status != "ready" or not preparation.batches:
        return TopicAngleExecution(preparation, ())

    batches = [list(batch) for batch in preparation.batches]
    batch_count = len(batches)
    event_queue: Queue[dict[str, Any]] = Queue()
    batch_started_at: dict[int, float] = {}
    batch_retry_messages: dict[int, str] = {}
    futures: dict[Future[_BatchExecutionResult], int] = {}
    results: dict[int, _BatchExecutionResult] = {}

    with ThreadPoolExecutor(max_workers=batch_count) as executor:
        for index, batch in enumerate(batches, start=1):
            start_delay = float(config.topic_angle_request_stagger_seconds) * (index - 1)
            future = executor.submit(
                _execute_batch_request,
                batch_number=index,
                clusters=batch,
                start_delay_seconds=start_delay,
                config=config,
                event_queue=event_queue,
                sleep_func=sleep_func,
            )
            futures[future] = index

        pending: set[Future[_BatchExecutionResult]] = set(futures)
        while pending:
            while True:
                try:
                    event = event_queue.get_nowait()
                except Empty:
                    break
                batch_number = int(event.get("batch_number") or 0)
                if event.get("type") == "started":
                    batch_started_at[batch_number] = time.perf_counter()
                elif event.get("type") == "retry":
                    batch_retry_messages[batch_number] = (
                        f"{float(event.get('delay') or 0):g}초 후 재시도 "
                        f"({float(event.get('waited') or 0):g}/"
                        f"{float(event.get('max_wait') or 0):g}초)"
                    )

            done, pending = wait(
                pending,
                timeout=max(0.05, float(poll_interval_seconds)),
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                result = future.result()
                results[result.batch_number] = result
                batch_retry_messages.pop(result.batch_number, None)

            elapsed = time.perf_counter() - preparation.started_at
            completed = len(results)
            now = time.perf_counter()
            retry_note = ""
            if batch_retry_messages:
                retry_note = " · " + " / ".join(
                    f"{number}차 {message}"
                    for number, message in sorted(batch_retry_messages.items())
                )
            progress_value = min(
                0.92,
                0.14
                + 0.62 * (completed / batch_count)
                + 0.16 * min(elapsed / max(1, config.topic_angle_timeout_seconds), 1.0),
            )
            total_items = len(preparation.clusters)
            successful_items = sum(
                len(batches[bn - 1]) for bn, res in results.items() if res.enrichments
            )
            processing_items = sum(
                len(batches[bn - 1])
                for bn in range(1, batch_count + 1)
                if bn in batch_started_at and bn not in results
            )
            batch_states: list[str] = []
            for batch_number, batch in enumerate(batches, start=1):
                if batch_number in results:
                    state = "완료" if results[batch_number].enrichments else "실패"
                    batch_states.append(f"{batch_number}차 {state}({len(batch)}개)")
                    continue
                started_at = batch_started_at.get(batch_number)
                if started_at is None:
                    scheduled_delay = float(config.topic_angle_request_stagger_seconds) * (
                        batch_number - 1
                    )
                    wait_left = max(0.0, scheduled_delay - elapsed)
                    batch_states.append(
                        f"{batch_number}차 시작 대기 {_format_seconds(wait_left)}({len(batch)}개)"
                    )
                else:
                    request_elapsed = now - started_at
                    request_remaining = max(
                        0.0, config.topic_angle_timeout_seconds - request_elapsed
                    )
                    batch_states.append(
                        f"{batch_number}차 응답 대기 {_format_seconds(request_elapsed)}"
                        f"/제한까지 최대 {_format_seconds(config.topic_angle_timeout_seconds)}"
                        f"(남은 시간 {_format_seconds(request_remaining)}, {len(batch)}개)"
                    )
            message = (
                f"Gemini 분석 전체 {total_items:,}개 중 응답 완료 {successful_items:,}개 · "
                f"처리 중 {processing_items:,}개 · 전체 경과 {_format_seconds(elapsed)} · "
                + " | ".join(batch_states)
                + retry_note
            )
            _emit_progress(progress_callback, status_callback, progress_value, message)

    return TopicAngleExecution(
        preparation,
        tuple(results[number] for number in sorted(results)),
    )


def finalize_prepared_topic_angles(
    con: duckdb.DuckDBPyConnection,
    *,
    config: GeminiConfig,
    execution: TopicAngleExecution,
    status_callback: Callable[[str], None] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> TopicAngleBatchResult:
    """Gemini 사용량과 검증된 글감 결과만 짧은 DB 구간에서 저장합니다."""
    preparation = execution.preparation
    if preparation.status != "ready":
        return _preparation_result(preparation)

    generated_clusters = 0
    completed_batches = 0
    failed_batches = 0
    total_attempts = 0
    error_messages: list[str] = []
    error_types: list[str] = []

    for result in execution.results:
        _record_batch_attempts(con, config=config, result=result)
        total_attempts += len(result.attempts)
        if result.enrichments:
            try:
                saved_count = _save_batch_enrichments(con, config=config, result=result)
            except Exception as exc:
                failed_batches += 1
                error_types.append("database_save_error")
                error_messages.append(f"{result.batch_number}차 저장 실패: {exc}")
                continue
            generated_clusters += saved_count
            completed_batches += 1
        else:
            failed_batches += 1
            if result.error_type:
                error_types.append(result.error_type)
            if result.error_message:
                error_messages.append(
                    f"{result.batch_number}차 요청: {result.error_message}"
                )

    missing_count = len(preparation.clusters) - generated_clusters
    if missing_count > 0:
        error_messages.append(f"이번 실행에서 저장되지 않은 글감 {missing_count:,}개")

    batch_count = len(preparation.batches)
    if generated_clusters == len(preparation.clusters) and failed_batches == 0:
        status = "success_after_retry" if total_attempts > batch_count else "success"
    elif generated_clusters > 0:
        status = "partial_success"
    else:
        status = error_types[0] if error_types else "response_validation_error"

    duration_seconds = time.perf_counter() - preparation.started_at
    _emit_progress(
        progress_callback,
        status_callback,
        0.98,
        (
            f"응답 검증과 저장 완료 · 글감 {generated_clusters:,}/{len(preparation.clusters):,}개"
            f" · 방향 {generated_clusters * 3:,}개 · 총 {_format_seconds(duration_seconds)}"
        ),
    )
    return TopicAngleBatchResult(
        status,
        len(preparation.clusters),
        generated_clusters,
        generated_clusters * 3,
        preparation.skipped_sensitive_clusters,
        total_attempts,
        error_types[0] if error_types else "",
        "; ".join(error_messages[:8]),
        requested_batches=batch_count,
        completed_batches=completed_batches,
        failed_batches=failed_batches,
        items_per_request=preparation.items_per_request,
        max_parallel_requests=preparation.max_parallel_requests,
        duration_seconds=duration_seconds,
        min_opportunity_score=preparation.min_opportunity_score,
    )


def generate_missing_topic_angles(
    con: duckdb.DuckDBPyConnection,
    *,
    config: GeminiConfig,
    limit: int | None = None,
    status_callback: Callable[[str], None] | None = None,
    progress_callback: ProgressCallback | None = None,
    sleep_func: Callable[[float], None] = time.sleep,
    poll_interval_seconds: float = 0.25,
) -> TopicAngleBatchResult:
    """기존 호출 호환용 단일 연결 래퍼입니다.

    Streamlit 수동 작업은 기존처럼 한 함수로 호출하고, 예약 수집은 준비·API·저장
    세 함수를 따로 호출해 네트워크 대기 중 DuckDB 연결을 닫습니다.
    """
    preparation = prepare_missing_topic_angles(
        con,
        config=config,
        limit=limit,
        status_callback=status_callback,
        progress_callback=progress_callback,
    )
    if preparation.status != "ready":
        return _preparation_result(preparation)
    execution = execute_prepared_topic_angles(
        preparation,
        config=config,
        status_callback=status_callback,
        progress_callback=progress_callback,
        sleep_func=sleep_func,
        poll_interval_seconds=poll_interval_seconds,
    )
    return finalize_prepared_topic_angles(
        con,
        config=config,
        execution=execution,
        status_callback=status_callback,
        progress_callback=progress_callback,
    )
