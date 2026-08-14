from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from src.config import DEFAULT_DB_PATH, GeminiConfig, get_gemini_config
from src.database import connect_database
from src.services.blog_channel_strategy_service import (
    MANAGED_BLOG_CHANNELS,
    MANAGED_STRATEGY_CODES,
)
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

FEATURE_ID = "trend_blog_ai_routing_v1"
FEATURE_VERSION = "1"
THINKING_LEVEL = "minimal"
ROUTABLE_STATUSES = ("recommended", "review")

ROUTE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "routes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cluster_id": {"type": "string"},
                    "strategy_code": {
                        "type": "string",
                        "enum": list(MANAGED_STRATEGY_CODES),
                    },
                    "confidence": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "reason": {"type": "string"},
                },
                "required": [
                    "cluster_id",
                    "strategy_code",
                    "confidence",
                    "reason",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["routes"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class BlogRouteChunk:
    batch_number: int
    candidates: tuple[dict[str, Any], ...]
    request_text: str
    estimated_tokens: int


@dataclass(frozen=True)
class BlogRoutingPreparation:
    status: str
    candidates: tuple[dict[str, Any], ...]
    chunks: tuple[BlogRouteChunk, ...]
    reused_clusters: int
    skipped_sensitive_clusters: int
    oversized_cluster_ids: tuple[str, ...]


@dataclass(frozen=True)
class BlogRoutingExecution:
    preparation: BlogRoutingPreparation
    routes: tuple[dict[str, Any], ...]
    calls: tuple[dict[str, Any], ...]


def ensure_trend_blog_ai_route_schema(con: Any) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS trend_blog_ai_routes (
            cluster_id VARCHAR PRIMARY KEY,
            strategy_code VARCHAR NOT NULL,
            confidence INTEGER NOT NULL DEFAULT 0,
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


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _safe_public_text(value: Any, field: str) -> str:
    text = _clean(value)
    if not text:
        return ""
    return "" if scan_sensitive_fields([(field, text)]) else text


def _channel_contract() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for channel in MANAGED_BLOG_CHANNELS:
        result.append(
            {
                "strategy_code": _clean(channel.get("strategy_code")),
                "name": _clean(channel.get("profile_name")),
                "category": _clean(channel.get("default_category")),
                "allowed_categories": list(channel.get("allowed_categories") or ()),
                "excluded_categories": list(channel.get("excluded_categories") or ()),
                "target_audience": _clean(channel.get("target_audience")),
                "seo_strategy": _clean(channel.get("seo_strategy")),
            }
        )
    return result


def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "cluster_id": candidate["cluster_id"],
        "title": candidate["title"],
        "display_title": candidate.get("display_title", ""),
        "summary": candidate.get("summary", ""),
        "category": candidate.get("category", ""),
        "source_titles": list(candidate.get("source_titles") or ()),
        "source_types": list(candidate.get("source_types") or ()),
    }


def _content_hash(candidate: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            _public_candidate(candidate),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _build_request_text(candidates: Sequence[dict[str, Any]]) -> str:
    instructions = (
        "아래 트렌드 글감 각각을 제공된 블로그 채널 중 정확히 하나에 배정하세요. "
        "단순 키워드 포함 여부가 아니라 글의 중심 주제, 독자가 검색하는 목적, 실제로 작성할 정보의 성격을 기준으로 판단하세요. "
        "생활 제도·혜택·소비자·주거·금융 기초·교육·교통처럼 일상에서 기준이나 절차를 확인하려는 글은 blogger_life를 우선합니다. "
        "앱·PC·스마트폰·AI 도구를 직접 사용·설정·설치하거나 오류를 해결하려는 글은 blogger_tech를 우선합니다. "
        "국내 장소·매장·지역 서비스·방문 준비처럼 위치와 이용 정보가 핵심이면 naver_local을 선택합니다. "
        "경기·방송·인물·사건·발표·산업 뉴스처럼 시점성이 핵심이고 앞의 전문 채널 목적에 더 잘 맞지 않을 때 blogger_current를 선택합니다. "
        "기술 기업·반도체·원전·산업 협력 같은 뉴스라는 이유만으로 blogger_tech를 선택하지 마세요. 사용·설정·오류 해결 목적이 아니면 보통 blogger_current가 더 적절합니다. "
        "요청에 있는 cluster_id를 빠뜨리거나 새 ID를 만들지 말고 각 ID를 정확히 한 번 반환하세요. "
        "confidence는 분류 확신도를 0~100 정수로, reason은 핵심 판단 이유를 짧게 작성하세요.\n"
    )
    payload = {
        "channels": _channel_contract(),
        "candidates": [_public_candidate(candidate) for candidate in candidates],
    }
    return instructions + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def partition_blog_route_candidates(
    candidates: Sequence[dict[str, Any]],
    *,
    estimator: AdaptiveInputTokenEstimator | None = None,
    target_tokens: int = CLUSTERING_TARGET_INPUT_TOKENS,
) -> tuple[list[BlogRouteChunk], list[str]]:
    active_estimator = estimator or GLOBAL_TOKEN_ESTIMATOR
    bounded_target = max(1, min(int(target_tokens), CLUSTERING_HARD_INPUT_TOKENS))
    fixed_characters = len(_build_request_text(()))
    chunks: list[BlogRouteChunk] = []
    oversized: list[str] = []
    current: list[dict[str, Any]] = []
    current_payload_characters = 0

    def payload_characters(candidate: dict[str, Any]) -> int:
        return len(
            json.dumps(
                _public_candidate(candidate),
                ensure_ascii=False,
                separators=(",", ":"),
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
            BlogRouteChunk(
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
        if current and estimate > bounded_target:
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


def _load_candidates(con: Any) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT tc.cluster_id, tc.canonical_title,
               COALESCE(tcp.display_title, '') AS display_title,
               COALESCE(tcp.summary, '') AS summary,
               COALESCE(tcp.content_plan_json, '{}') AS content_plan_json
        FROM trend_clusters tc
        LEFT JOIN trend_cluster_ai_profiles tcp ON tcp.cluster_id = tc.cluster_id
        WHERE COALESCE(tc.recommendation_status, 'review') IN ('recommended', 'review')
        ORDER BY tc.opportunity_score DESC, tc.trend_score DESC, tc.last_seen_at DESC
        """
    ).fetchall()

    source_rows = con.execute(
        """
        SELECT tci.cluster_id, s.raw_title, s.source_type
        FROM trend_cluster_items tci
        JOIN trend_clusters tc ON tc.cluster_id = tci.cluster_id
        JOIN source_items s ON s.source_item_id = tci.source_item_id
        WHERE COALESCE(tc.recommendation_status, 'review') IN ('recommended', 'review')
        ORDER BY tci.cluster_id, COALESCE(s.signal_value, 0) DESC,
                 COALESCE(s.published_at, s.observed_at, s.imported_at) DESC
        """
    ).fetchall()
    source_titles: dict[str, list[str]] = {}
    source_types: dict[str, list[str]] = {}
    for cluster_id, raw_title, source_type in source_rows:
        key = _clean(cluster_id)
        if not key:
            continue
        title = _safe_public_text(raw_title, "원문 제목")
        if title:
            bucket = source_titles.setdefault(key, [])
            if title not in bucket and len(bucket) < 3:
                bucket.append(title[:240])
        kind = _clean(source_type)
        if kind:
            type_bucket = source_types.setdefault(key, [])
            if kind not in type_bucket:
                type_bucket.append(kind)

    result: list[dict[str, Any]] = []
    for cluster_id, canonical_title, display_title, summary, content_plan_json in rows:
        key = _clean(cluster_id)
        title = _safe_public_text(canonical_title, "글감 제목")
        if not key or not title:
            continue
        try:
            plan = json.loads(_clean(content_plan_json) or "{}")
        except json.JSONDecodeError:
            plan = {}
        category = _safe_public_text(
            plan.get("category") if isinstance(plan, dict) else "",
            "글감 카테고리",
        )
        candidate = {
            "cluster_id": key,
            "title": title[:240],
            "display_title": _safe_public_text(display_title, "표시 제목")[:240],
            "summary": _safe_public_text(summary, "글감 요약")[:900],
            "category": category[:160],
            "source_titles": source_titles.get(key, []),
            "source_types": source_types.get(key, []),
        }
        candidate["content_hash"] = _content_hash(candidate)
        result.append(candidate)
    return result


def prepare_trend_blog_ai_routing(
    con: Any,
    *,
    config: GeminiConfig,
    estimator: AdaptiveInputTokenEstimator | None = None,
) -> BlogRoutingPreparation:
    ensure_trend_blog_ai_route_schema(con)
    calibrate_estimator_from_connection(con)
    candidates = _load_candidates(con)
    if not candidates:
        return BlogRoutingPreparation("nothing_to_route", (), (), 0, 0, ())

    existing_rows = con.execute(
        """
        SELECT cluster_id, content_hash, model_name, feature_version
        FROM trend_blog_ai_routes
        """
    ).fetchall()
    existing = {
        _clean(row[0]): (_clean(row[1]), _clean(row[2]), _clean(row[3]))
        for row in existing_rows
    }
    pending: list[dict[str, Any]] = []
    reused = 0
    skipped_sensitive = 0
    for candidate in candidates:
        cluster_id = candidate["cluster_id"]
        saved = existing.get(cluster_id)
        if saved == (
            candidate["content_hash"],
            config.model,
            FEATURE_VERSION,
        ):
            reused += 1
            continue
        pending.append(candidate)

    if not config.api_key:
        return BlogRoutingPreparation(
            "missing_api_key",
            tuple(pending),
            (),
            reused,
            skipped_sensitive,
            (),
        )
    if not pending:
        return BlogRoutingPreparation(
            "nothing_to_route",
            (),
            (),
            reused,
            skipped_sensitive,
            (),
        )

    chunks, oversized = partition_blog_route_candidates(
        pending,
        estimator=estimator,
    )
    return BlogRoutingPreparation(
        "ready",
        tuple(pending),
        tuple(chunks),
        reused,
        skipped_sensitive,
        tuple(value for value in oversized if value),
    )


def _parse_routes(
    response_text: str,
    requested: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        return [], [f"블로그 분류 응답 JSON 오류: {exc.msg}"]
    if not isinstance(payload, dict) or not isinstance(payload.get("routes"), list):
        return [], ["블로그 분류 응답에 routes 배열이 없습니다."]

    allowed = set(MANAGED_STRATEGY_CODES)
    seen: set[str] = set()
    routes: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in payload["routes"]:
        if not isinstance(row, dict):
            errors.append("블로그 분류 항목이 객체가 아닙니다.")
            continue
        cluster_id = _clean(row.get("cluster_id"))
        if cluster_id not in requested:
            errors.append(f"요청하지 않은 cluster_id가 반환됐습니다: {cluster_id}")
            continue
        if cluster_id in seen:
            errors.append(f"중복 cluster_id가 반환됐습니다: {cluster_id}")
            continue
        strategy_code = _clean(row.get("strategy_code"))
        if strategy_code not in allowed:
            errors.append(f"지원하지 않는 블로그 전략입니다: {strategy_code}")
            continue
        try:
            confidence = int(row.get("confidence"))
        except (TypeError, ValueError, OverflowError):
            confidence = -1
        reason = _clean(row.get("reason"))
        if not 0 <= confidence <= 100 or not reason:
            errors.append(f"{cluster_id}의 confidence/reason이 올바르지 않습니다.")
            continue
        seen.add(cluster_id)
        routes.append(
            {
                "cluster_id": cluster_id,
                "strategy_code": strategy_code,
                "confidence": confidence,
                "reason": reason[:700],
                "content_hash": requested[cluster_id]["content_hash"],
            }
        )
    missing = set(requested) - seen
    if missing:
        errors.append(f"응답에서 {len(missing)}개 cluster_id가 누락됐습니다.")
    return routes, errors


def execute_prepared_blog_routing(
    preparation: BlogRoutingPreparation,
    *,
    config: GeminiConfig,
    api_call: Callable[..., tuple[Any, ...]] = call_gemini_structured_output,
    estimator: AdaptiveInputTokenEstimator | None = None,
    limiter: SlidingWindowTpmLimiter | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> BlogRoutingExecution:
    if preparation.status != "ready":
        return BlogRoutingExecution(preparation, (), ())

    active_estimator = estimator or GLOBAL_TOKEN_ESTIMATOR
    active_limiter = limiter or GLOBAL_TPM_LIMITER
    routes: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    total_chunks = len(preparation.chunks)
    stop_after_error = False

    for index, chunk in enumerate(preparation.chunks, start=1):
        if stop_after_error:
            break
        if progress_callback is not None:
            progress_callback(
                (index - 1) / max(1, total_chunks),
                f"Flash-Lite 블로그 분류 {index}/{total_chunks} 요청 중 ({len(chunk.candidates):,}개)",
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
        requested = {
            candidate["cluster_id"]: candidate for candidate in chunk.candidates
        }
        try:
            raw_result = api_call(
                config,
                chunk.request_text,
                request_hash,
                feature_id=FEATURE_ID,
                response_schema=ROUTE_RESPONSE_SCHEMA,
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
            parsed_routes, errors = _parse_routes(response_text, requested)
            routes.extend(
                {
                    **route,
                    "request_hash": request_hash,
                }
                for route in parsed_routes
            )
            call_status = "success" if not errors else "partial"
            error_type = "" if not errors else "response_validation_error"
            error_message = "; ".join(errors[:5])
            active_estimator.observe(
                request_characters=len(chunk.request_text),
                estimated_tokens=chunk.estimated_tokens,
                actual_tokens=input_tokens,
                status="success" if not errors else "partial",
                error_type=error_type,
            )
            calls.append(
                {
                    "request_hash": request_hash,
                    "request_text": chunk.request_text,
                    "response_text": response_text,
                    "requested_item_count": len(chunk.candidates),
                    "status": call_status,
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
            if info.error_type in {
                "daily_quota_exhausted",
                "rate_limited",
                "authentication_error",
                "permission_error",
                "model_not_found",
                "invalid_request",
            }:
                stop_after_error = True
        if progress_callback is not None:
            progress_callback(
                index / max(1, total_chunks),
                f"Flash-Lite 블로그 분류 {index}/{total_chunks} 처리 완료",
            )

    return BlogRoutingExecution(preparation, tuple(routes), tuple(calls))


def finalize_prepared_blog_routing(
    con: Any,
    *,
    config: GeminiConfig,
    execution: BlogRoutingExecution,
    record_call: Callable[..., None] = record_gemini_api_call,
) -> dict[str, Any]:
    ensure_trend_blog_ai_route_schema(con)
    preparation = execution.preparation
    if preparation.status != "ready":
        return {
            "status": preparation.status,
            "requested_clusters": len(preparation.candidates),
            "routed_clusters": 0,
            "reused_clusters": preparation.reused_clusters,
            "failed_clusters": len(preparation.candidates),
            "requested_batches": len(preparation.chunks),
            "completed_batches": 0,
            "failed_batches": 0,
            "model": config.model,
            "error_message": "",
        }

    now = datetime.now()
    for route in execution.routes:
        con.execute(
            """
            INSERT INTO trend_blog_ai_routes(
                cluster_id, strategy_code, confidence, reason, content_hash,
                model_name, feature_version, request_hash, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cluster_id) DO UPDATE SET
                strategy_code = EXCLUDED.strategy_code,
                confidence = EXCLUDED.confidence,
                reason = EXCLUDED.reason,
                content_hash = EXCLUDED.content_hash,
                model_name = EXCLUDED.model_name,
                feature_version = EXCLUDED.feature_version,
                request_hash = EXCLUDED.request_hash,
                updated_at = EXCLUDED.updated_at
            """,
            [
                route["cluster_id"],
                route["strategy_code"],
                int(route["confidence"]),
                route["reason"],
                route["content_hash"],
                config.model,
                FEATURE_VERSION,
                route["request_hash"],
                now,
                now,
            ],
        )

    for call in execution.calls:
        record_call(
            con,
            config=config,
            content_pack_id=f"trend_blog_route_{call['request_hash'][:20]}",
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
            configured_items_per_request=None,
            thinking_level=THINKING_LEVEL,
            request_timeout_seconds=min(max(30, int(config.timeout_seconds)), 240),
            finish_reason=call["finish_reason"],
            finish_message=call["finish_message"],
        )

    requested_ids = {candidate["cluster_id"] for candidate in preparation.candidates}
    routed_ids = {route["cluster_id"] for route in execution.routes}
    completed_batches = sum(
        1 for call in execution.calls if call["status"] in {"success", "partial"}
    )
    failed_batches = sum(1 for call in execution.calls if call["status"] == "failed")
    errors = [
        _clean(call.get("error_message"))
        for call in execution.calls
        if _clean(call.get("error_message"))
    ]
    failed_clusters = max(0, len(requested_ids - routed_ids))
    if not requested_ids:
        status = "nothing_to_route"
    elif failed_clusters == 0:
        status = "success"
    elif routed_ids:
        status = "partial"
    else:
        status = "failed"
    return {
        "status": status,
        "requested_clusters": len(requested_ids),
        "routed_clusters": len(routed_ids),
        "reused_clusters": preparation.reused_clusters,
        "failed_clusters": failed_clusters,
        "requested_batches": len(preparation.chunks),
        "completed_batches": completed_batches,
        "failed_batches": failed_batches,
        "oversized_clusters": len(preparation.oversized_cluster_ids),
        "model": config.model,
        "input_tokens": sum(int(call.get("input_tokens") or 0) for call in execution.calls),
        "output_tokens": sum(int(call.get("output_tokens") or 0) for call in execution.calls),
        "thought_tokens": sum(int(call.get("thought_tokens") or 0) for call in execution.calls),
        "total_tokens": sum(int(call.get("total_tokens") or 0) for call in execution.calls),
        "error_message": "; ".join(errors[:5]),
    }


def run_trend_blog_ai_routing(
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
        preparation = prepare_trend_blog_ai_routing(
            con,
            config=config,
            estimator=estimator,
        )

    execution = execute_prepared_blog_routing(
        preparation,
        config=config,
        api_call=api_call,
        estimator=estimator,
        limiter=limiter,
        progress_callback=progress_callback,
    )

    with connect_database(database) as con:
        result = finalize_prepared_blog_routing(
            con,
            config=config,
            execution=execution,
        )
    warning = _clean(result.get("error_message"))
    return result, warning
