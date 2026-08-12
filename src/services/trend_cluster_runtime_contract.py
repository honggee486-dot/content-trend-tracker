from __future__ import annotations

from dataclasses import replace
from functools import wraps
import json
from typing import Any, Iterable

from src.services.trend_cluster_job_runtime_contract import install_job_token_contract
from src.services.trend_cluster_sparse_orchestrator import (
    aggregate_call_metrics,
    classify_sparse_multi_view_batch,
)
from src.services.trend_cluster_sparse_protocol import (
    CLUSTERING_FEATURE_VERSION,
    CLUSTERING_SCAN_CANDIDATE_LIMIT,
    SPARSE_RESPONSE_SCHEMA,
    build_sparse_request_text,
    conservative_must_merge_profiles,
    select_all_topic_candidates,
)
from src.services.trend_cluster_token_runtime import (
    CLUSTERING_TARGET_INPUT_TOKENS,
    CLUSTERING_TPM_LIMIT,
    calibrate_estimator_from_connection,
    get_call_metrics,
    record_request_metrics,
)
from src.services.trend_cluster_topic_partition import partition_topic_chunks

CLUSTERING_BATCH_SIZE = CLUSTERING_SCAN_CANDIDATE_LIMIT
CLUSTERING_MAX_ITEMS = CLUSTERING_SCAN_CANDIDATE_LIMIT
# 한 작업은 미처리 전체 스냅샷을 한 번만 준비합니다. 제목·사건·식별·기존
# 군집 관점과 토큰 분할 요청은 그 안에서 순차 실행되므로 외부 반복을 곱하지 않습니다.
CLUSTERING_MAX_BATCHES = 1
CLUSTERING_REQUEST_CONCURRENCY = 1
COMPACT_CANDIDATE_FIELDS = (
    "candidate_id",
    "title",
    "examples",
    "safety_profile",
    "existing_options",
)
COMPACT_SAFETY_FIELDS = (
    "dates",
    "numbered_events",
    "products",
    "actions",
    "directions",
    "subjects",
)
COMPACT_EXISTING_OPTION_FIELDS = (
    "option_id",
    "title",
    "examples",
)
REMOVED_REQUEST_FIELDS = (
    "item_count",
    "source_types",
    "publishers",
    "first_seen_at",
    "last_seen_at",
    "first_stage_rule_ids",
    "title_fingerprints",
)


def _compact_safety_profile(candidate: dict[str, Any]) -> dict[str, list[str]]:
    profile = dict(candidate.get("safety_profile") or {})
    result: dict[str, list[str]] = {}
    for field_name in COMPACT_SAFETY_FIELDS:
        values = [
            str(value).strip()
            for value in profile.get(field_name) or ()
            if str(value).strip()
        ]
        if values:
            result[field_name] = values
    return result


def _compact_existing_options(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    from src.services.trend_cluster_safety_service import build_existing_option_payload

    return [
        {
            "option_id": int(option.get("option_id") or 0),
            "title": str(option.get("title") or ""),
            "examples": [
                str(value)
                for value in option.get("examples") or ()
                if str(value).strip()
            ][:2],
        }
        for option in build_existing_option_payload(candidate)
    ]


def build_compact_cluster_request_text(
    batch_id: str,
    candidates: Iterable[dict[str, Any]],
) -> str:
    """이전 압축 요청 미리보기와 테스트를 위한 호환 함수입니다."""
    payload = {
        "batch_id": str(batch_id or "cluster_batch_0001"),
        "candidates": [
            {
                "candidate_id": str(candidate.get("candidate_id") or ""),
                "title": str(candidate.get("title") or ""),
                "examples": [
                    str(value)
                    for value in candidate.get("examples") or ()
                    if str(value).strip()
                ][:3],
                "safety_profile": _compact_safety_profile(candidate),
                "existing_options": _compact_existing_options(candidate),
            }
            for candidate in candidates
        ],
    }
    return "2차 군집 압축 요청 미리보기\n\n" + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def build_sparse_title_request_text(
    batch_id: str,
    candidates: Iterable[dict[str, Any]],
) -> str:
    numbered = [
        (index, dict(candidate))
        for index, candidate in enumerate(candidates, start=1)
    ]
    return build_sparse_request_text(batch_id, "title", numbered)


def _install_settings_wrapper(discovery_module: Any) -> None:
    original = getattr(discovery_module, "_ai_clustering_settings", None)
    if not callable(original) or getattr(
        original,
        "_trend_cluster_adaptive_contract",
        False,
    ):
        return

    @wraps(original)
    def adaptive_settings(con):
        settings = dict(original(con))
        calibrate_estimator_from_connection(con)
        settings["max_items"] = CLUSTERING_MAX_ITEMS
        settings["batch_size"] = CLUSTERING_BATCH_SIZE
        settings["max_batches"] = CLUSTERING_MAX_BATCHES
        return settings

    adaptive_settings._trend_cluster_adaptive_contract = True  # type: ignore[attr-defined]
    discovery_module._ai_clustering_settings = adaptive_settings


def _install_calculation_metrics_wrapper(discovery_module: Any) -> None:
    original = getattr(discovery_module, "calculate_prepared_trend_rankings", None)
    if not callable(original) or getattr(
        original,
        "_trend_cluster_adaptive_contract",
        False,
    ):
        return

    @wraps(original)
    def adaptive_calculation(*args, **kwargs):
        result = original(*args, **kwargs)
        metrics = aggregate_call_metrics(result.ai_clustering_calls)
        if not metrics["request_count"]:
            return result
        batch_log = dict(result.batch_log)
        batch_log.update(metrics)
        ai_clustering = dict(result.ai_clustering)
        ai_clustering.update(metrics)
        return replace(
            result,
            ai_clustering=ai_clustering,
            batch_log=batch_log,
        )

    adaptive_calculation._trend_cluster_adaptive_contract = True  # type: ignore[attr-defined]
    discovery_module.calculate_prepared_trend_rankings = adaptive_calculation


def _install_call_metrics_wrapper(discovery_module: Any) -> None:
    original = getattr(discovery_module, "record_gemini_api_call", None)
    if not callable(original) or getattr(
        original,
        "_trend_cluster_adaptive_contract",
        False,
    ):
        return

    @wraps(original)
    def adaptive_record(con, *args, **kwargs):
        request_hash = str(kwargs.get("request_hash") or "")
        metrics = get_call_metrics(request_hash)
        if metrics:
            kwargs["duration_ms"] = int(metrics.get("duration_ms") or 0)
        result = original(con, *args, **kwargs)
        if metrics:
            record_request_metrics(
                con,
                request_hash=request_hash,
                model_name=str(kwargs.get("config").model),
            )
        return result

    adaptive_record._trend_cluster_adaptive_contract = True  # type: ignore[attr-defined]
    discovery_module.record_gemini_api_call = adaptive_record


def install_trend_cluster_runtime_contract(
    *,
    review_module: Any | None = None,
    job_module: Any | None = None,
    discovery_module: Any | None = None,
    safety_module: Any | None = None,
) -> None:
    """앱·작업자에 보수적 1차와 토큰·TPM 기반 희소 2차 계약을 설치합니다."""
    if review_module is None:
        from src.services import trend_cluster_ai_review_service as review_module
    if discovery_module is None:
        from src.services import trend_discovery_service as discovery_module
    if job_module is None:
        from src.services import trend_clustering_job_service as job_module
    if safety_module is None:
        from src.services import trend_cluster_safety_service as safety_module
    from src.services import trend_cluster_sparse_executor as sparse_executor

    safety_module.must_merge_profiles = conservative_must_merge_profiles
    sparse_executor.partition_for_view = partition_topic_chunks

    review_module.FEATURE_VERSION = CLUSTERING_FEATURE_VERSION
    review_module.MAX_CANDIDATES_PER_REQUEST = CLUSTERING_BATCH_SIZE
    review_module.RESPONSE_SCHEMA = SPARSE_RESPONSE_SCHEMA
    review_module._request_text = build_sparse_title_request_text
    review_module.select_cluster_batch_candidates = select_all_topic_candidates
    review_module.classify_cluster_batch = classify_sparse_multi_view_batch

    discovery_module.AI_CLUSTERING_FEATURE_VERSION = CLUSTERING_FEATURE_VERSION
    discovery_module.select_cluster_batch_candidates = select_all_topic_candidates
    discovery_module.classify_cluster_batch = classify_sparse_multi_view_batch

    job_module.CLUSTERING_JOB_BATCH_SIZE = CLUSTERING_BATCH_SIZE
    job_module.CLUSTERING_JOB_MAX_BATCHES = CLUSTERING_MAX_BATCHES
    install_job_token_contract(
        job_module,
        scan_limit=CLUSTERING_MAX_ITEMS,
        max_batches=CLUSTERING_MAX_BATCHES,
    )

    _install_settings_wrapper(discovery_module)
    _install_calculation_metrics_wrapper(discovery_module)
    _install_call_metrics_wrapper(discovery_module)
    job_module.calculate_prepared_trend_rankings = (
        discovery_module.calculate_prepared_trend_rankings
    )


def install_clustering_settings_ui_contract(ui_module: Any) -> None:
    """기존 숫자 설정을 내부 스캔 상한으로 바꾸고 실제 토큰 기준을 안내합니다."""
    contracts = dict(getattr(ui_module, "_FIXED_CLUSTERING_NUMBER_INPUTS", {}) or {})
    contracts["Gemini 요청 1회당 1차 군집"] = {
        "value": CLUSTERING_BATCH_SIZE,
        "min_value": 20,
        "max_value": CLUSTERING_BATCH_SIZE,
        "step": 100,
        "help": (
            "이 값은 요청당 후보 수가 아니라 한 작업의 내부 스캔 안전 상한입니다. "
            f"실제 2차 요청은 최종 예상 입력 {CLUSTERING_TARGET_INPUT_TOKENS:,}토큰 이하로 "
            "주제순 자동 분할됩니다."
        ),
    }
    contracts["백그라운드 작업 1회당 최대 Gemini 요청"] = {
        "value": CLUSTERING_MAX_BATCHES,
        "min_value": 1,
        "max_value": CLUSTERING_MAX_BATCHES,
        "step": 1,
        "help": (
            "외부 작업 스냅샷은 한 번만 준비합니다. 그 안에서 제목·사건·식별·기존 군집 "
            "관점과 토큰 분할 요청을 하나씩 순차 호출하며, 응답 검증이 끝난 뒤 다음 요청을 보냅니다."
        ),
    }
    ui_module._FIXED_CLUSTERING_NUMBER_INPUTS = contracts

    replacements = list(getattr(ui_module, "_GEMINI_CAPTION_REPLACEMENTS", ()) or ())
    for old in (
        "Flash-Lite는 1차 군집 최대 350개를",
        "Flash-Lite는 1차 군집 최대 300개를",
        "Flash-Lite는 1차 군집 최대 200개를",
    ):
        replacement = (
            old,
            "Flash-Lite는 후보 수 대신 최종 입력 토큰을 기준으로 주제순 분할하고",
        )
        if replacement not in replacements:
            replacements.append(replacement)
    ui_module._GEMINI_CAPTION_REPLACEMENTS = tuple(replacements)
