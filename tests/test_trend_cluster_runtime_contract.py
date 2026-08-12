from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.services.trend_cluster_runtime_contract import (
    CLUSTERING_BATCH_SIZE,
    CLUSTERING_FEATURE_VERSION,
    CLUSTERING_MAX_BATCHES,
    CLUSTERING_REQUEST_CONCURRENCY,
    COMPACT_CANDIDATE_FIELDS,
    COMPACT_EXISTING_OPTION_FIELDS,
    COMPACT_SAFETY_FIELDS,
    REMOVED_REQUEST_FIELDS,
    build_compact_cluster_request_text,
    build_sparse_title_request_text,
    install_clustering_settings_ui_contract,
    install_trend_cluster_runtime_contract,
)
from src.services.trend_cluster_sparse_orchestrator import (
    classify_sparse_multi_view_batch,
)
from src.services.trend_cluster_sparse_protocol import (
    SPARSE_RESPONSE_SCHEMA,
    conservative_must_merge_profiles,
    select_all_topic_candidates,
)
from src.services.trend_cluster_token_runtime import (
    CLUSTERING_TARGET_INPUT_TOKENS,
    CLUSTERING_TPM_LIMIT,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _candidate() -> dict:
    return {
        "candidate_id": "candidate-1",
        "title": "삼성전자 갤럭시 S26 국내 출시",
        "examples": ["갤럭시 S26 한국 출시", "삼성 신제품 국내 공개"],
        "item_count": 12,
        "source_types": ["naver_news", "daum_web"],
        "publishers": ["매체 A", "매체 B"],
        "first_seen_at": "2026-08-05 10:00:00",
        "last_seen_at": "2026-08-06 01:00:00",
        "first_stage_rule_ids": ["must_merge:product_action"],
        "safety_profile": {
            "dates": ["2026-08-06"],
            "numbered_events": [],
            "products": ["s26"],
            "actions": ["product_release"],
            "directions": [],
            "subjects": ["삼성전자", "갤럭시"],
            "title_fingerprints": ["opaque-title-hash"],
        },
        "existing_cluster_candidates": [
            {
                "cluster_id": "private-cluster-id",
                "title": "삼성전자 갤럭시 S26 출시",
                "item_count": 20,
                "first_seen_at": "2026-08-04 00:00:00",
                "last_seen_at": "2026-08-06 00:00:00",
                "examples": ["갤럭시 S26 국내 출시"],
            }
        ],
    }


def test_compact_request_keeps_event_identity_and_removes_low_value_fields() -> None:
    request_text = build_compact_cluster_request_text("batch-1", [_candidate()])
    payload = json.loads(request_text.split("\n\n", 1)[1])
    candidate = payload["candidates"][0]

    assert tuple(candidate) == COMPACT_CANDIDATE_FIELDS
    assert tuple(candidate["safety_profile"]) == tuple(
        field for field in COMPACT_SAFETY_FIELDS if candidate["safety_profile"].get(field)
    )
    assert tuple(candidate["existing_options"][0]) == COMPACT_EXISTING_OPTION_FIELDS
    assert candidate["existing_options"][0]["option_id"] == 1
    assert "private-cluster-id" not in request_text
    assert "opaque-title-hash" not in request_text
    for field_name in REMOVED_REQUEST_FIELDS:
        assert field_name not in candidate
        assert field_name not in candidate["existing_options"][0]


def test_runtime_installer_sets_token_based_sparse_contract() -> None:
    original_calculation = lambda *_args, **_kwargs: None
    review = SimpleNamespace(
        FEATURE_VERSION="4",
        MAX_CANDIDATES_PER_REQUEST=300,
        RESPONSE_SCHEMA={},
        _request_text=None,
        select_cluster_batch_candidates=None,
        classify_cluster_batch=None,
    )
    jobs = SimpleNamespace(
        CLUSTERING_JOB_BATCH_SIZE=300,
        CLUSTERING_JOB_MAX_BATCHES=5,
        calculate_prepared_trend_rankings=original_calculation,
    )
    discovery = SimpleNamespace(
        AI_CLUSTERING_FEATURE_VERSION="4",
        select_cluster_batch_candidates=None,
        classify_cluster_batch=None,
        calculate_prepared_trend_rankings=original_calculation,
        record_gemini_api_call=lambda *_args, **_kwargs: None,
        _ai_clustering_settings=lambda _con: {
            "max_items": 4000,
            "batch_size": 200,
            "max_batches": 5,
            "model": "gemini-3.5-flash-lite",
        },
    )
    safety = SimpleNamespace(must_merge_profiles=lambda *_args: "legacy")

    install_trend_cluster_runtime_contract(
        review_module=review,
        job_module=jobs,
        discovery_module=discovery,
        safety_module=safety,
    )

    assert CLUSTERING_BATCH_SIZE == 50_000
    assert CLUSTERING_MAX_BATCHES == 1
    assert CLUSTERING_REQUEST_CONCURRENCY == 1
    assert CLUSTERING_FEATURE_VERSION == "7"
    assert CLUSTERING_TARGET_INPUT_TOKENS == 225_000
    assert CLUSTERING_TPM_LIMIT == 250_000
    assert review.FEATURE_VERSION == "7"
    assert review.MAX_CANDIDATES_PER_REQUEST == 50_000
    assert review.RESPONSE_SCHEMA is SPARSE_RESPONSE_SCHEMA
    assert review._request_text is build_sparse_title_request_text
    assert review.select_cluster_batch_candidates is select_all_topic_candidates
    assert review.classify_cluster_batch is classify_sparse_multi_view_batch
    assert discovery.select_cluster_batch_candidates is select_all_topic_candidates
    assert discovery.classify_cluster_batch is classify_sparse_multi_view_batch
    assert safety.must_merge_profiles is conservative_must_merge_profiles
    assert jobs.CLUSTERING_JOB_BATCH_SIZE == 50_000
    assert jobs.CLUSTERING_JOB_MAX_BATCHES == 1
    assert jobs.calculate_prepared_trend_rankings is discovery.calculate_prepared_trend_rankings

    settings = discovery._ai_clustering_settings(object())
    assert settings["max_items"] == 50_000
    assert settings["batch_size"] == 50_000
    assert settings["max_batches"] == 1
    assert settings["model"] == "gemini-3.5-flash-lite"


def test_settings_ui_installer_explains_token_target_and_sequential_execution() -> None:
    ui = SimpleNamespace(
        _FIXED_CLUSTERING_NUMBER_INPUTS={},
        _GEMINI_CAPTION_REPLACEMENTS=(),
    )

    install_clustering_settings_ui_contract(ui)

    batch = ui._FIXED_CLUSTERING_NUMBER_INPUTS["Gemini 요청 1회당 1차 군집"]
    requests = ui._FIXED_CLUSTERING_NUMBER_INPUTS[
        "백그라운드 작업 1회당 최대 Gemini 요청"
    ]
    assert batch["value"] == 50_000
    assert batch["max_value"] == 50_000
    assert "225,000" in batch["help"]
    assert "후보 수가 아니라" in batch["help"]
    assert requests["value"] == 1
    assert requests["max_value"] == 1
    assert "한 번만" in requests["help"]
    assert "순차 호출" in requests["help"]
    assert any(
        "최종 입력 토큰" in replacement[1]
        for replacement in ui._GEMINI_CAPTION_REPLACEMENTS
    )


def test_all_runtime_entrypoints_install_the_contract() -> None:
    package_init = (PROJECT_ROOT / "src" / "__init__.py").read_text(encoding="utf-8")
    worker = (PROJECT_ROOT / "scripts" / "process_cluster_backlog.py").read_text(
        encoding="utf-8"
    )
    scheduled = (PROJECT_ROOT / "scripts" / "refresh_trends_safe.py").read_text(
        encoding="utf-8"
    )

    assert "install_trend_cluster_runtime_contract()" in package_init
    assert "install_clustering_settings_ui_contract" in package_init
    assert "install_trend_cluster_runtime_contract()" in worker
    assert "install_trend_cluster_runtime_contract()" in scheduled
