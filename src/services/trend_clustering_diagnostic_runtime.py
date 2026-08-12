from __future__ import annotations

from functools import wraps
from typing import Any

TOKEN_PARTITIONED_MAX_BATCHES = 1
P2_DIAGNOSTIC_TRIAL_LAUNCHER = "p2_diagnostic_trial"
LEGACY_ABSOLUTE_BATCH_SIZE = 300


def _current_scan_candidate_limit() -> int:
    from src.services.trend_cluster_sparse_protocol import (
        CLUSTERING_SCAN_CANDIDATE_LIMIT,
    )

    return int(CLUSTERING_SCAN_CANDIDATE_LIMIT)


def _decorate_contract(result: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(result)
    decorated.setdefault("contract_mode", "")
    decorated.setdefault("absolute_batch_size_limit", 0)
    decorated.setdefault("token_partitioned_contract", False)

    if not bool(decorated.get("available")):
        return decorated

    scan_limit = max(0, int(decorated.get("scan_limit") or 0))
    configured_batch_size = max(
        0,
        int(decorated.get("configured_batch_size") or 0),
    )
    configured_max_batches = max(
        0,
        int(decorated.get("configured_max_batches") or 0),
    )
    launcher = str(decorated.get("launcher") or "")

    current_scan_limit = _current_scan_candidate_limit()
    token_partitioned_contract = (
        scan_limit == current_scan_limit
        and configured_batch_size == current_scan_limit
        and configured_max_batches == TOKEN_PARTITIONED_MAX_BATCHES
    )
    if not token_partitioned_contract:
        decorated["contract_mode"] = "legacy_fixed_batch"
        decorated["absolute_batch_size_limit"] = LEGACY_ABSOLUTE_BATCH_SIZE
        decorated["token_partitioned_contract"] = False
        return decorated

    decorated["contract_mode"] = "token_partitioned_snapshot"
    decorated["absolute_batch_size_limit"] = current_scan_limit
    decorated["token_partitioned_contract"] = True
    decorated["trial_mode"] = launcher == P2_DIAGNOSTIC_TRIAL_LAUNCHER

    batch_size_contract_ok = (
        max(0, int(decorated.get("maximum_first_stage_units") or 0))
        <= configured_batch_size
    )
    decorated["batch_size_contract_ok"] = batch_size_contract_ok
    trial_contract_ok = (
        bool(decorated.get("sample_available"))
        and bool(decorated.get("completed_within_configured_limit"))
        and batch_size_contract_ok
        and bool(decorated.get("sequential_execution_ok"))
    )
    decorated["trial_contract_ok"] = trial_contract_ok

    if not bool(decorated.get("sample_available")):
        return decorated
    if not bool(decorated.get("sequential_execution_ok")):
        decorated["status"] = "순차 실행 점검"
    elif not trial_contract_ok:
        decorated["status"] = "시험 계약 점검"
    elif launcher == P2_DIAGNOSTIC_TRIAL_LAUNCHER:
        decorated["status"] = "P2 표본 계약 확인"
    else:
        decorated["status"] = "토큰 분할 계약 확인"
    return decorated


def install_trend_clustering_diagnostic_contract() -> None:
    """P2 군집 진단을 현재 토큰 분할 스냅샷 계약과 읽기 호환시킵니다."""
    from src.services import trend_clustering_diagnostic_service as diagnostic_module

    original = getattr(
        diagnostic_module,
        "build_trend_clustering_trial_diagnostic",
        None,
    )
    if not callable(original) or getattr(
        original,
        "_trend_cluster_token_diagnostic_contract",
        False,
    ):
        return

    @wraps(original)
    def current_contract(con):
        return _decorate_contract(dict(original(con)))

    current_contract._trend_cluster_token_diagnostic_contract = True  # type: ignore[attr-defined]
    diagnostic_module.build_trend_clustering_trial_diagnostic = current_contract
