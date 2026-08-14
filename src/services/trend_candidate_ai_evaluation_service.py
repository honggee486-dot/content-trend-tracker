from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import uuid4

from src.config import DEFAULT_DB_PATH, GeminiConfig, get_gemini_config
from src.database import connect_database
from src.services.gemini_model_service import (
    MODEL_PURPOSE_DATA_REVIEW,
    build_gemini_config_for_purpose,
)
from src.services.gemini_service import (
    GeminiHttpError,
    call_gemini_structured_output,
    normalize_gemini_api_result,
    record_gemini_api_call,
    scan_sensitive_fields,
)
from src.services.trend_cluster_token_runtime import (
    AdaptiveInputTokenEstimator,
    CLUSTERING_HARD_INPUT_TOKENS,
    CLUSTERING_TARGET_INPUT_TOKENS,
    GLOBAL_TOKEN_ESTIMATOR,
    GLOBAL_TPM_LIMITER,
    SlidingWindowTpmLimiter,
    calibrate_estimator_from_connection,
)

FEATURE_ID = "trend_candidate_ai_evaluation_v1"
FEATURE_VERSION = "1"
THINKING_LEVEL = "minimal"
MAX_ITEMS_PER_REQUEST = 120

_SCORE_FIELDS = (
    "ai_trend_score",
    "ai_opportunity_score",
    "ai_evidence_quality_score",
    "search_value_score",
    "information_value_score",
    "practicality_score",
    "durability_score",
    "fact_check_difficulty_score",
)

EVALUATION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "evaluations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cluster_id": {"type": "string"},
                    **{
                        field: {"type": "integer", "minimum": 0, "maximum": 100}
                        for field in _SCORE_FIELDS
                    },
                    "recommendation_status": {
                        "type": "string",
                        "enum": ["recommended", "review", "hold"],
                    },
                    "reason": {"type": "string"},
                },
                "required": [
                    "cluster_id",
                    *_SCORE_FIELDS,
                    "recommendation_status",
                    "reason",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["evaluations"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class CandidateEvaluationChunk:
    batch_number: int
    candidates: tuple[dict[str, Any], ...]
    request_text: str
    estimated_tokens: int


@dataclass(frozen=True)
class CandidateEvaluationPreparation:
    status: str
    run_id: str
    candidates: tuple[dict[str, Any], ...]
    chunks: tuple[CandidateEvaluationChunk, ...]
    current_cluster_count: int
    reused_clusters: int
    skipped_sensitive_clusters: int
    oversized_cluster_ids: tuple[str, ...]


@dataclass(frozen=True)
class CandidateEvaluationExecution:
    preparation: CandidateEvaluationPreparation
    evaluations: tuple[dict[str, Any], ...]
    calls: tuple[dict[str, Any], ...]


def ensure_trend_candidate_ai_evaluation_schema(con: Any) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS trend_cluster_ai_evaluations (
            cluster_id VARCHAR PRIMARY KEY,
            ai_trend_score INTEGER NOT NULL DEFAULT 0,
            ai_opportunity_score INTEGER NOT NULL DEFAULT 0,
            ai_evidence_quality_score INTEGER NOT NULL DEFAULT 0,
            search_value_score INTEGER NOT NULL DEFAULT 0,
            information_value_score INTEGER NOT NULL DEFAULT 0,
            practicality_score INTEGER NOT NULL DEFAULT 0,
            durability_score INTEGER NOT NULL DEFAULT 0,
            fact_check_difficulty_score INTEGER NOT NULL DEFAULT 0,
            recommendation_status VARCHAR NOT NULL DEFAULT 'review',
            reason VARCHAR NOT NULL DEFAULT '',
            content_hash VARCHAR NOT NULL,
            model_name VARCHAR NOT NULL,
            feature_version VARCHAR NOT NULL,
            request_hash VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS trend_candidate_ai_evaluation_request_metrics (
            request_hash VARCHAR PRIMARY KEY,
            run_id VARCHAR NOT NULL,
            batch_number INTEGER NOT NULL,
            model_name VARCHAR NOT NULL,
            requested_item_count INTEGER NOT NULL DEFAULT 0,
            estimated_input_tokens INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER,
            output_tokens INTEGER,
            thought_tokens INTEGER,
            total_tokens INTEGER,
            tpm_wait_seconds DOUBLE NOT NULL DEFAULT 0,
            duration_ms BIGINT NOT NULL DEFAULT 0,
            status VARCHAR NOT NULL,
            http_status INTEGER,
            error_type VARCHAR NOT NULL DEFAULT '',
            error_message VARCHAR NOT NULL DEFAULT '',
            finish_reason VARCHAR NOT NULL DEFAULT '',
            finish_message VARCHAR NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL
        )
        """
    )


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _safe_public_text(value: Any, field: str) -> str:
    text = _clean(value)
    if not text:
        return ""
    return "" if scan_sensitive_fields([(field, text)]) else text


def _number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (parsed == parsed and abs(parsed) != float("inf")):
        return None
    return int(parsed) if parsed.is_integer() else round(parsed, 3)


def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "cluster_id": candidate["cluster_id"],
        "topic": candidate["topic"],
        "item_count": int(candidate.get("item_count") or 0),
        "independent_evidence_count": int(candidate.get("independent_evidence_count") or 0),
        "source_type_count": int(candidate.get("source_type_count") or 0),
        "publisher_count": int(candidate.get("publisher_count") or 0),
        "source_types": list(candidate.get("source_types") or ()),
        "first_seen_at": _clean(candidate.get("first_seen_at")),
        "last_seen_at": _clean(candidate.get("last_seen_at")),
        "rediscovery_signal": _number(candidate.get("rediscovery_signal")),
        "evidence": list(candidate.get("evidence") or ()),
    }


def _content_hash(candidate: dict[str, Any]) -> str:
    payload = {
        "public": _public_candidate(candidate),
        "evidence_signature": _clean(candidate.get("evidence_signature")),
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _build_request_text(candidates: Sequence[dict[str, Any]]) -> str:
    instructions = (
        "아래 항목은 최근 활성 트렌드 창에서 1차 규칙 군집과 2차 Gemini 군집을 거쳐 확정된 최종 글감입니다. "
        "각 글감을 독립적으로 평가하세요. 기존 프로그램 점수는 제공하지 않으므로 그 점수에 맞추려 하지 마세요. "
        "입력에 실제로 있는 제목·출처 종류·발행처 수·독립 근거 수·관측·신호만 사용하고 검색량이나 사실을 새로 만들지 마세요. "
        "ai_trend_score는 현재 관심의 강도와 여러 출처에서의 반복성을 0~100으로, "
        "ai_opportunity_score는 정보성 블로그 글감으로 실제 작성할 가치와 확장성을 0~100으로, "
        "ai_evidence_quality_score는 독립성·다양성·구체성·작성에 쓸 수 있는 자료 완성도를 0~100으로 평가하세요. "
        "search_value_score는 독자의 검색 목적이 명확하고 답을 찾을 가치가 있는 정도, "
        "information_value_score는 단순 화제를 넘어 확인 가능한 정보를 충분히 제공할 수 있는 정도, "
        "practicality_score는 독자가 행동·판단·이해에 활용할 수 있는 정도, "
        "durability_score는 짧은 순간 화제가 아니라 검색·참고 가치가 유지될 정도를 각각 0~100으로 평가하세요. "
        "fact_check_difficulty_score는 높을수록 시점 의존·민감성·수치 검증 등으로 사실 확인이 어려운 글감입니다. "
        "recommendation_status는 전체 평가를 종합해 recommended, review, hold 중 하나를 선택하세요. "
        "reason은 가장 중요한 장점·약점을 180자 안팎의 짧은 한국어로 작성하세요. "
        "요청에 있는 cluster_id를 빠뜨리거나 새 ID를 만들지 말고 각 ID를 정확히 한 번 반환하세요.\n"
    )
    payload = {
        "evaluation_scope": "current_final_trend_clusters",
        "candidates": [_public_candidate(candidate) for candidate in candidates],
    }
    return instructions + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def partition_candidate_evaluations(
    candidates: Sequence[dict[str, Any]],
    *,
    estimator: AdaptiveInputTokenEstimator | None = None,
    target_tokens: int = CLUSTERING_TARGET_INPUT_TOKENS,
    max_items_per_request: int = MAX_ITEMS_PER_REQUEST,
) -> tuple[list[CandidateEvaluationChunk], list[str]]:
    active_estimator = estimator or GLOBAL_TOKEN_ESTIMATOR
    bounded_target = max(1, min(int(target_tokens), CLUSTERING_HARD_INPUT_TOKENS))
    bounded_items = max(1, min(int(max_items_per_request), MAX_ITEMS_PER_REQUEST))
    fixed_characters = len(_build_request_text(()))
    chunks: list[CandidateEvaluationChunk] = []
    oversized: list[str] = []
    current: list[dict[str, Any]] = []
    current_payload_characters = 0

    def payload_characters(candidate: dict[str, Any]) -> int:
        return len(
            json.dumps(
                _public_candidate(candidate),
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        )

    def estimated_for(payload_size: int, count: int) -> int:
        return active_estimator.estimate_characters(
            fixed_characters + payload_size + max(0, count - 1)
        )

    def finalize(rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        request_text = _build_request_text(rows)
        chunks.append(
            CandidateEvaluationChunk(
                batch_number=len(chunks) + 1,
                candidates=tuple(rows),
                request_text=request_text,
                estimated_tokens=active_estimator.estimate_text(request_text),
            )
        )

    for candidate in candidates:
        candidate_chars = payload_characters(candidate)
        trial_count = len(current) + 1
        estimate = estimated_for(
            current_payload_characters + candidate_chars,
            trial_count,
        )
        if current and (estimate > bounded_target or trial_count > bounded_items):
            finalize(current)
            current = []
            current_payload_characters = 0
            estimate = estimated_for(candidate_chars, 1)
        if estimate > CLUSTERING_HARD_INPUT_TOKENS:
            oversized.append(_clean(candidate.get("cluster_id")))
            continue
        current.append(candidate)
        current_payload_characters += candidate_chars
    finalize(current)
    return chunks, oversized


def _load_candidates(con: Any) -> tuple[list[dict[str, Any]], int]:
    cluster_rows = con.execute(
        """
        SELECT cluster_id, canonical_title, item_count, source_type_count,
               publisher_count, source_types_json, first_seen_at, last_seen_at,
               COALESCE(rediscovery_score, 0)
        FROM trend_clusters
        ORDER BY opportunity_score DESC, trend_score DESC, last_seen_at DESC
        """
    ).fetchall()

    source_rows = con.execute(
        """
        SELECT tci.cluster_id, s.source_item_id, s.raw_title, s.normalized_url,
               s.source_type, s.source_name, s.published_at, s.observed_at,
               s.signal_value, s.observation_count, s.metadata_json
        FROM trend_cluster_items tci
        JOIN source_items s ON s.source_item_id = tci.source_item_id
        ORDER BY tci.cluster_id, COALESCE(s.signal_value, 0) DESC,
                 COALESCE(s.published_at, s.observed_at, s.imported_at) DESC
        """
    ).fetchall()

    evidence_by_cluster: dict[str, list[dict[str, Any]]] = {}
    evidence_keys: dict[str, set[str]] = {}
    signatures: dict[str, list[str]] = {}
    for (
        cluster_id,
        source_item_id,
        raw_title,
        normalized_url,
        source_type,
        source_name,
        published_at,
        observed_at,
        signal_value,
        observation_count,
        metadata_json,
    ) in source_rows:
        key = _clean(cluster_id)
        if not key:
            continue
        try:
            metadata = json.loads(_clean(metadata_json) or "{}")
        except json.JSONDecodeError:
            metadata = {}
        title = _safe_public_text(
            metadata.get("item_title") if isinstance(metadata, dict) else "",
            "근거 제목",
        ) or _safe_public_text(raw_title, "근거 제목")
        normalized_evidence_key = _clean(normalized_url) or title.casefold()
        if normalized_evidence_key:
            evidence_keys.setdefault(key, set()).add(normalized_evidence_key)
        signature_piece = "|".join(
            [
                _clean(source_item_id),
                title,
                _clean(observation_count),
                _clean(signal_value),
            ]
        )
        signatures.setdefault(key, []).append(signature_piece)
        bucket = evidence_by_cluster.setdefault(key, [])
        if not title or any(item.get("title") == title for item in bucket) or len(bucket) >= 5:
            continue
        row: dict[str, Any] = {
            "title": title[:260],
            "source_type": _clean(source_type)[:80],
            "publisher": _safe_public_text(source_name, "발행처")[:160],
            "published_at": _clean(published_at or observed_at),
            "observation_count": max(1, int(observation_count or 1)),
        }
        numeric_candidates = {
            "signal_value": signal_value,
            "view_count": metadata.get("view_count") if isinstance(metadata, dict) else None,
            "view_delta": metadata.get("view_delta") if isinstance(metadata, dict) else None,
            "views_per_hour": metadata.get("views_per_hour") if isinstance(metadata, dict) else None,
            "topic_score": metadata.get("topic_score") if isinstance(metadata, dict) else None,
            "traffic_count": metadata.get("traffic_count") if isinstance(metadata, dict) else None,
            "rank": metadata.get("rank") if isinstance(metadata, dict) else None,
        }
        for field, raw_value in numeric_candidates.items():
            value = _number(raw_value)
            if value is not None:
                row[field] = value
        bucket.append(row)

    candidates: list[dict[str, Any]] = []
    skipped_sensitive = 0
    for (
        cluster_id,
        canonical_title,
        item_count,
        source_type_count,
        publisher_count,
        source_types_json,
        first_seen_at,
        last_seen_at,
        rediscovery_score,
    ) in cluster_rows:
        key = _clean(cluster_id)
        topic = _safe_public_text(canonical_title, "글감 제목")
        if not key or not topic:
            skipped_sensitive += 1
            continue
        try:
            source_types = json.loads(_clean(source_types_json) or "[]")
        except json.JSONDecodeError:
            source_types = []
        candidate = {
            "cluster_id": key,
            "topic": topic[:260],
            "item_count": max(0, int(item_count or 0)),
            "independent_evidence_count": len(evidence_keys.get(key, set())),
            "source_type_count": max(0, int(source_type_count or 0)),
            "publisher_count": max(0, int(publisher_count or 0)),
            "source_types": source_types if isinstance(source_types, list) else [],
            "first_seen_at": first_seen_at,
            "last_seen_at": last_seen_at,
            "rediscovery_signal": _number(rediscovery_score),
            "evidence": evidence_by_cluster.get(key, []),
            "evidence_signature": hashlib.sha256(
                "\n".join(sorted(signatures.get(key, []))).encode("utf-8")
            ).hexdigest(),
        }
        candidate["content_hash"] = _content_hash(candidate)
        candidates.append(candidate)
    return candidates, skipped_sensitive


def prepare_trend_candidate_ai_evaluation(
    con: Any,
    *,
    config: GeminiConfig,
    estimator: AdaptiveInputTokenEstimator | None = None,
) -> CandidateEvaluationPreparation:
    ensure_trend_candidate_ai_evaluation_schema(con)
    calibrate_estimator_from_connection(con)
    run_id = "trend_eval_" + uuid4().hex
    candidates, skipped_sensitive = _load_candidates(con)
    current_count = len(candidates) + skipped_sensitive
    if not candidates:
        return CandidateEvaluationPreparation(
            "nothing_to_evaluate", run_id, (), (), current_count, 0,
            skipped_sensitive, (),
        )

    rows = con.execute(
        """
        SELECT cluster_id, content_hash, model_name, feature_version
        FROM trend_cluster_ai_evaluations
        """
    ).fetchall()
    existing = {
        _clean(row[0]): (_clean(row[1]), _clean(row[2]), _clean(row[3]))
        for row in rows
    }
    pending: list[dict[str, Any]] = []
    reused = 0
    for candidate in candidates:
        saved = existing.get(candidate["cluster_id"])
        if saved == (candidate["content_hash"], config.model, FEATURE_VERSION):
            reused += 1
        else:
            pending.append(candidate)

    if not pending:
        return CandidateEvaluationPreparation(
            "nothing_to_evaluate", run_id, (), (), current_count, reused,
            skipped_sensitive, (),
        )
    if not config.api_key:
        return CandidateEvaluationPreparation(
            "missing_api_key", run_id, tuple(pending), (), current_count, reused,
            skipped_sensitive, (),
        )

    chunks, oversized = partition_candidate_evaluations(
        pending,
        estimator=estimator,
    )
    return CandidateEvaluationPreparation(
        "ready",
        run_id,
        tuple(pending),
        tuple(chunks),
        current_count,
        reused,
        skipped_sensitive,
        tuple(value for value in oversized if value),
    )


def _parse_evaluations(
    response_text: str,
    requested: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        return [], [f"글감 평가 응답 JSON 오류: {exc.msg}"]
    if not isinstance(payload, dict) or not isinstance(payload.get("evaluations"), list):
        return [], ["글감 평가 응답에 evaluations 배열이 없습니다."]

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in payload["evaluations"]:
        if not isinstance(row, dict):
            errors.append("글감 평가 항목이 객체가 아닙니다.")
            continue
        cluster_id = _clean(row.get("cluster_id"))
        if cluster_id not in requested:
            errors.append(f"요청하지 않은 cluster_id가 반환됐습니다: {cluster_id}")
            continue
        if cluster_id in seen:
            errors.append(f"중복 cluster_id가 반환됐습니다: {cluster_id}")
            continue
        scores: dict[str, int] = {}
        valid = True
        for field in _SCORE_FIELDS:
            try:
                value = int(row.get(field))
            except (TypeError, ValueError, OverflowError):
                value = -1
            if not 0 <= value <= 100:
                errors.append(f"{cluster_id}의 {field} 점수가 0~100 범위가 아닙니다.")
                valid = False
                break
            scores[field] = value
        status = _clean(row.get("recommendation_status"))
        reason = _clean(row.get("reason"))
        if status not in {"recommended", "review", "hold"} or not reason:
            errors.append(f"{cluster_id}의 추천 상태 또는 이유가 올바르지 않습니다.")
            valid = False
        if not valid:
            continue
        seen.add(cluster_id)
        result.append(
            {
                "cluster_id": cluster_id,
                **scores,
                "recommendation_status": status,
                "reason": reason[:700],
                "content_hash": requested[cluster_id]["content_hash"],
            }
        )
    missing = set(requested) - seen
    if missing:
        errors.append(f"응답에서 {len(missing)}개 cluster_id가 누락됐습니다.")
    return result, errors


def execute_prepared_candidate_ai_evaluation(
    preparation: CandidateEvaluationPreparation,
    *,
    config: GeminiConfig,
    api_call: Callable[..., tuple[Any, ...]] = call_gemini_structured_output,
    estimator: AdaptiveInputTokenEstimator | None = None,
    limiter: SlidingWindowTpmLimiter | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> CandidateEvaluationExecution:
    if preparation.status != "ready":
        return CandidateEvaluationExecution(preparation, (), ())

    active_estimator = estimator or GLOBAL_TOKEN_ESTIMATOR
    active_limiter = limiter or GLOBAL_TPM_LIMITER
    evaluations: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    total_chunks = len(preparation.chunks)
    stop_after_error = False

    for index, chunk in enumerate(preparation.chunks, start=1):
        if stop_after_error:
            break
        if progress_callback is not None:
            progress_callback(
                (index - 1) / max(1, total_chunks),
                f"Flash-Lite 전체 글감 평가 {index}/{total_chunks} 요청 중 ({len(chunk.candidates):,}개)",
            )
        request_hash = hashlib.sha256(
            (
                f"{FEATURE_ID}|{FEATURE_VERSION}|{config.model}|{THINKING_LEVEL}|"
                f"{chunk.request_text}"
            ).encode("utf-8")
        ).hexdigest()
        reservation = active_limiter.reserve(
            request_hash,
            min(chunk.estimated_tokens, CLUSTERING_HARD_INPUT_TOKENS),
        )
        started = time.perf_counter()
        requested = {candidate["cluster_id"]: candidate for candidate in chunk.candidates}
        try:
            raw_result = api_call(
                config,
                chunk.request_text,
                request_hash,
                feature_id=FEATURE_ID,
                response_schema=EVALUATION_RESPONSE_SCHEMA,
                use_google_search=False,
                thinking_level=THINKING_LEVEL,
                timeout_seconds=min(max(30, int(config.timeout_seconds)), 240),
            )
            (
                response_text,
                input_tokens,
                output_tokens,
                thought_tokens,
                total_tokens,
                finish_reason,
                finish_message,
            ) = normalize_gemini_api_result(raw_result)
            duration_ms = int((time.perf_counter() - started) * 1000)
            active_limiter.reconcile(request_hash, input_tokens)
            parsed, errors = _parse_evaluations(response_text, requested)
            evaluations.extend(
                {**row, "request_hash": request_hash}
                for row in parsed
            )
            status = "success" if not errors else "partial"
            error_type = "" if not errors else "response_validation_error"
            error_message = "; ".join(errors[:5])
            active_estimator.observe(
                request_characters=len(chunk.request_text),
                estimated_tokens=chunk.estimated_tokens,
                actual_tokens=input_tokens,
                status=status,
                error_type=error_type,
            )
            calls.append(
                {
                    "batch_number": chunk.batch_number,
                    "request_hash": request_hash,
                    "request_text": chunk.request_text,
                    "response_text": response_text,
                    "requested_item_count": len(chunk.candidates),
                    "status": status,
                    "http_status": 200,
                    "error_type": error_type,
                    "error_message": error_message,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "thought_tokens": thought_tokens,
                    "total_tokens": total_tokens,
                    "finish_reason": finish_reason,
                    "finish_message": finish_message,
                    "duration_ms": duration_ms,
                    "estimated_input_tokens": chunk.estimated_tokens,
                    "tpm_wait_seconds": reservation.wait_seconds,
                }
            )
        except GeminiHttpError as exc:
            info = exc.info
            duration_ms = int((time.perf_counter() - started) * 1000)
            active_estimator.observe(
                request_characters=len(chunk.request_text),
                estimated_tokens=chunk.estimated_tokens,
                actual_tokens=None,
                status="failed",
                error_type=info.error_type,
            )
            calls.append(
                {
                    "batch_number": chunk.batch_number,
                    "request_hash": request_hash,
                    "request_text": chunk.request_text,
                    "response_text": "",
                    "requested_item_count": len(chunk.candidates),
                    "status": "failed",
                    "http_status": info.http_status or None,
                    "error_type": info.error_type,
                    "error_message": info.message,
                    "input_tokens": None,
                    "output_tokens": None,
                    "thought_tokens": None,
                    "total_tokens": None,
                    "finish_reason": info.finish_reason,
                    "finish_message": info.finish_message,
                    "duration_ms": duration_ms,
                    "estimated_input_tokens": chunk.estimated_tokens,
                    "tpm_wait_seconds": reservation.wait_seconds,
                }
            )
            # 전체 평가 실험이므로 같은 장애에서 후속 묶음을 연속 소모하지 않습니다.
            stop_after_error = True
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            calls.append(
                {
                    "batch_number": chunk.batch_number,
                    "request_hash": request_hash,
                    "request_text": chunk.request_text,
                    "response_text": "",
                    "requested_item_count": len(chunk.candidates),
                    "status": "failed",
                    "http_status": None,
                    "error_type": "unexpected_error",
                    "error_message": str(exc),
                    "input_tokens": None,
                    "output_tokens": None,
                    "thought_tokens": None,
                    "total_tokens": None,
                    "finish_reason": "",
                    "finish_message": "",
                    "duration_ms": duration_ms,
                    "estimated_input_tokens": chunk.estimated_tokens,
                    "tpm_wait_seconds": reservation.wait_seconds,
                }
            )
            stop_after_error = True
        if progress_callback is not None:
            progress_callback(
                index / max(1, total_chunks),
                f"Flash-Lite 전체 글감 평가 {index}/{total_chunks} 처리 완료",
            )

    return CandidateEvaluationExecution(
        preparation,
        tuple(evaluations),
        tuple(calls),
    )


def finalize_prepared_candidate_ai_evaluation(
    con: Any,
    *,
    config: GeminiConfig,
    execution: CandidateEvaluationExecution,
    record_call: Callable[..., None] = record_gemini_api_call,
) -> dict[str, Any]:
    ensure_trend_candidate_ai_evaluation_schema(con)
    preparation = execution.preparation
    if preparation.status != "ready":
        return {
            "status": preparation.status,
            "run_id": preparation.run_id,
            "current_clusters": preparation.current_cluster_count,
            "requested_clusters": len(preparation.candidates),
            "evaluated_clusters": 0,
            "reused_clusters": preparation.reused_clusters,
            "failed_clusters": len(preparation.candidates),
            "requested_batches": len(preparation.chunks),
            "completed_batches": 0,
            "failed_batches": 0,
            "model": config.model,
            "input_tokens": 0,
            "output_tokens": 0,
            "thought_tokens": 0,
            "total_tokens": 0,
            "estimated_input_tokens": sum(chunk.estimated_tokens for chunk in preparation.chunks),
            "tpm_wait_seconds": 0.0,
            "api_duration_seconds": 0.0,
            "error_message": "",
        }

    now = datetime.now()
    for row in execution.evaluations:
        con.execute(
            """
            INSERT INTO trend_cluster_ai_evaluations(
                cluster_id, ai_trend_score, ai_opportunity_score,
                ai_evidence_quality_score, search_value_score,
                information_value_score, practicality_score, durability_score,
                fact_check_difficulty_score, recommendation_status, reason,
                content_hash, model_name, feature_version, request_hash,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cluster_id) DO UPDATE SET
                ai_trend_score = EXCLUDED.ai_trend_score,
                ai_opportunity_score = EXCLUDED.ai_opportunity_score,
                ai_evidence_quality_score = EXCLUDED.ai_evidence_quality_score,
                search_value_score = EXCLUDED.search_value_score,
                information_value_score = EXCLUDED.information_value_score,
                practicality_score = EXCLUDED.practicality_score,
                durability_score = EXCLUDED.durability_score,
                fact_check_difficulty_score = EXCLUDED.fact_check_difficulty_score,
                recommendation_status = EXCLUDED.recommendation_status,
                reason = EXCLUDED.reason,
                content_hash = EXCLUDED.content_hash,
                model_name = EXCLUDED.model_name,
                feature_version = EXCLUDED.feature_version,
                request_hash = EXCLUDED.request_hash,
                updated_at = EXCLUDED.updated_at
            """,
            [
                row["cluster_id"],
                *[int(row[field]) for field in _SCORE_FIELDS],
                row["recommendation_status"],
                row["reason"],
                row["content_hash"],
                config.model,
                FEATURE_VERSION,
                row["request_hash"],
                now,
                now,
            ],
        )

    for call in execution.calls:
        con.execute(
            """
            INSERT INTO trend_candidate_ai_evaluation_request_metrics(
                request_hash, run_id, batch_number, model_name,
                requested_item_count, estimated_input_tokens,
                input_tokens, output_tokens, thought_tokens, total_tokens,
                tpm_wait_seconds, duration_ms, status, http_status,
                error_type, error_message, finish_reason, finish_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_hash) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                batch_number = EXCLUDED.batch_number,
                model_name = EXCLUDED.model_name,
                requested_item_count = EXCLUDED.requested_item_count,
                estimated_input_tokens = EXCLUDED.estimated_input_tokens,
                input_tokens = EXCLUDED.input_tokens,
                output_tokens = EXCLUDED.output_tokens,
                thought_tokens = EXCLUDED.thought_tokens,
                total_tokens = EXCLUDED.total_tokens,
                tpm_wait_seconds = EXCLUDED.tpm_wait_seconds,
                duration_ms = EXCLUDED.duration_ms,
                status = EXCLUDED.status,
                http_status = EXCLUDED.http_status,
                error_type = EXCLUDED.error_type,
                error_message = EXCLUDED.error_message,
                finish_reason = EXCLUDED.finish_reason,
                finish_message = EXCLUDED.finish_message,
                created_at = EXCLUDED.created_at
            """,
            [
                call["request_hash"],
                preparation.run_id,
                int(call["batch_number"]),
                config.model,
                int(call["requested_item_count"]),
                int(call["estimated_input_tokens"]),
                call["input_tokens"],
                call["output_tokens"],
                call["thought_tokens"],
                call["total_tokens"],
                float(call["tpm_wait_seconds"] or 0.0),
                int(call["duration_ms"] or 0),
                call["status"],
                call["http_status"],
                call["error_type"],
                call["error_message"],
                call["finish_reason"],
                call["finish_message"],
                now,
            ],
        )
        record_call(
            con,
            config=config,
            content_pack_id=f"trend_candidate_eval_{call['request_hash'][:20]}",
            request_hash=call["request_hash"],
            feature_id=FEATURE_ID,
            feature_version=FEATURE_VERSION,
            attempt_number=1,
            cache_hit=False,
            status=call["status"],
            http_status=call["http_status"],
            error_type=call["error_type"],
            retry_reason="",
            retry_wait_seconds=0,
            input_tokens=call["input_tokens"],
            output_tokens=call["output_tokens"],
            thought_tokens=call["thought_tokens"],
            total_tokens=call["total_tokens"],
            duration_ms=call["duration_ms"],
            error_message=call["error_message"],
            request_text=call["request_text"],
            response_text=call["response_text"],
            requested_item_count=call["requested_item_count"],
            configured_items_per_request=MAX_ITEMS_PER_REQUEST,
            thinking_level=THINKING_LEVEL,
            request_timeout_seconds=min(max(30, int(config.timeout_seconds)), 240),
            finish_reason=call["finish_reason"],
            finish_message=call["finish_message"],
        )

    requested_ids = {row["cluster_id"] for row in preparation.candidates}
    evaluated_ids = {row["cluster_id"] for row in execution.evaluations}
    completed_batches = sum(
        1 for call in execution.calls if call["status"] in {"success", "partial"}
    )
    failed_batches = sum(1 for call in execution.calls if call["status"] == "failed")
    errors = [
        _clean(call.get("error_message"))
        for call in execution.calls
        if _clean(call.get("error_message"))
    ]
    failed_clusters = max(0, len(requested_ids - evaluated_ids))
    if not requested_ids:
        status = "nothing_to_evaluate"
    elif failed_clusters == 0:
        status = "success"
    elif evaluated_ids:
        status = "partial"
    else:
        status = "failed"
    return {
        "status": status,
        "run_id": preparation.run_id,
        "current_clusters": preparation.current_cluster_count,
        "requested_clusters": len(requested_ids),
        "evaluated_clusters": len(evaluated_ids),
        "reused_clusters": preparation.reused_clusters,
        "failed_clusters": failed_clusters,
        "requested_batches": len(preparation.chunks),
        "completed_batches": completed_batches,
        "failed_batches": failed_batches,
        "oversized_clusters": len(preparation.oversized_cluster_ids),
        "model": config.model,
        "estimated_input_tokens": sum(int(call.get("estimated_input_tokens") or 0) for call in execution.calls),
        "input_tokens": sum(int(call.get("input_tokens") or 0) for call in execution.calls),
        "output_tokens": sum(int(call.get("output_tokens") or 0) for call in execution.calls),
        "thought_tokens": sum(int(call.get("thought_tokens") or 0) for call in execution.calls),
        "total_tokens": sum(int(call.get("total_tokens") or 0) for call in execution.calls),
        "tpm_wait_seconds": round(sum(float(call.get("tpm_wait_seconds") or 0.0) for call in execution.calls), 2),
        "api_duration_seconds": round(sum(int(call.get("duration_ms") or 0) for call in execution.calls) / 1000.0, 2),
        "error_message": "; ".join(errors[:5]),
    }


def run_trend_candidate_ai_evaluation(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    api_call: Callable[..., tuple[Any, ...]] = call_gemini_structured_output,
    estimator: AdaptiveInputTokenEstimator | None = None,
    limiter: SlidingWindowTpmLimiter | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> tuple[dict[str, Any], str]:
    database = Path(db_path).resolve()
    with connect_database(database) as con:
        config = build_gemini_config_for_purpose(
            con,
            MODEL_PURPOSE_DATA_REVIEW,
            base_config=get_gemini_config(),
        )
        preparation = prepare_trend_candidate_ai_evaluation(
            con,
            config=config,
            estimator=estimator,
        )

    execution = execute_prepared_candidate_ai_evaluation(
        preparation,
        config=config,
        api_call=api_call,
        estimator=estimator,
        limiter=limiter,
        progress_callback=progress_callback,
    )

    with connect_database(database) as con:
        result = finalize_prepared_candidate_ai_evaluation(
            con,
            config=config,
            execution=execution,
        )
    return result, _clean(result.get("error_message"))


def get_cluster_ai_evaluation(con: Any, cluster_id: str) -> dict[str, Any] | None:
    ensure_trend_candidate_ai_evaluation_schema(con)
    row = con.execute(
        """
        SELECT cluster_id, ai_trend_score, ai_opportunity_score,
               ai_evidence_quality_score, search_value_score,
               information_value_score, practicality_score, durability_score,
               fact_check_difficulty_score, recommendation_status, reason,
               model_name, feature_version, updated_at
        FROM trend_cluster_ai_evaluations
        WHERE cluster_id = ?
        """,
        [_clean(cluster_id)],
    ).fetchone()
    if row is None:
        return None
    columns = [item[0] for item in con.description]
    return dict(zip(columns, row))


def get_candidate_ai_evaluation_summary(con: Any) -> dict[str, Any]:
    ensure_trend_candidate_ai_evaluation_schema(con)
    current_clusters, evaluated_clusters = con.execute(
        """
        SELECT COUNT(tc.cluster_id), COUNT(te.cluster_id)
        FROM trend_clusters tc
        LEFT JOIN trend_cluster_ai_evaluations te ON te.cluster_id = tc.cluster_id
        """
    ).fetchone()
    latest = con.execute(
        """
        SELECT run_id
        FROM trend_candidate_ai_evaluation_request_metrics
        ORDER BY created_at DESC, batch_number DESC
        LIMIT 1
        """
    ).fetchone()
    run_summary: dict[str, Any] = {}
    if latest:
        run_id = _clean(latest[0])
        row = con.execute(
            """
            SELECT COUNT(*), SUM(requested_item_count), SUM(estimated_input_tokens),
                   SUM(COALESCE(input_tokens, 0)), SUM(COALESCE(output_tokens, 0)),
                   SUM(COALESCE(thought_tokens, 0)), SUM(COALESCE(total_tokens, 0)),
                   SUM(tpm_wait_seconds), SUM(duration_ms),
                   SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END),
                   MAX(model_name), MAX(created_at)
            FROM trend_candidate_ai_evaluation_request_metrics
            WHERE run_id = ?
            """,
            [run_id],
        ).fetchone()
        run_summary = {
            "run_id": run_id,
            "request_count": int(row[0] or 0),
            "requested_items": int(row[1] or 0),
            "estimated_input_tokens": int(row[2] or 0),
            "input_tokens": int(row[3] or 0),
            "output_tokens": int(row[4] or 0),
            "thought_tokens": int(row[5] or 0),
            "total_tokens": int(row[6] or 0),
            "tpm_wait_seconds": float(row[7] or 0.0),
            "duration_ms": int(row[8] or 0),
            "failed_requests": int(row[9] or 0),
            "model": _clean(row[10]),
            "created_at": row[11],
        }
    return {
        "current_clusters": int(current_clusters or 0),
        "evaluated_clusters": int(evaluated_clusters or 0),
        "latest_run": run_summary,
    }
