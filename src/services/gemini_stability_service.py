"""Gemini 글감 분석 이력을 읽어 보수적인 처리량 추천을 계산합니다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import duckdb

from src.services.topic_angle_ai_service import TOPIC_ANGLE_FEATURE_ID


MIN_COMPLETED_RUNS = 3
MIN_REQUESTED_CLUSTERS = 60
GENERATION_TOKEN_WARNING = 65_000
DEFAULT_THINKING_LEVEL = "medium"
ALLOWED_THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high"})


@dataclass(frozen=True)
class GeminiRunWindow:
    limit: int
    run_count: int
    requested_clusters: int
    generated_clusters: int
    skipped_clusters: int
    successful_runs: int
    partial_runs: int
    failed_runs: int
    request_count: int
    retry_count: int
    average_duration_ms: int

    @property
    def save_rate(self) -> float | None:
        if self.requested_clusters <= 0:
            return None
        return self.generated_clusters / self.requested_clusters

    @property
    def partial_failure_rate(self) -> float | None:
        if self.run_count <= 0:
            return None
        return (self.partial_runs + self.failed_runs) / self.run_count

    @property
    def retry_rate(self) -> float | None:
        if self.request_count <= 0:
            return None
        return self.retry_count / self.request_count


@dataclass(frozen=True)
class GeminiCallMetrics:
    attempt_count: int
    terminal_attempt_count: int
    successful_attempt_count: int
    validation_failure_count: int
    other_failure_count: int
    retrying_attempt_count: int
    average_generation_tokens: int
    maximum_generation_tokens: int
    near_limit_count: int
    average_duration_ms: int
    recorded_requested_item_count: int
    average_requested_item_count: float
    maximum_requested_item_count: int
    thinking_level_counts: tuple[tuple[str, int], ...]
    finish_reason_counts: tuple[tuple[str, int], ...]
    max_tokens_count: int
    missing_finish_reason_count: int
    rate_limit_affected_request_count: int = 0
    retry_recovered_request_count: int = 0
    rate_limited_final_request_count: int = 0
    ungrouped_retry_attempt_count: int = 0
    rate_limit_attempt_count: int = 0
    quota_exhausted_count: int = 0
    timeout_count: int = 0
    network_error_count: int = 0
    server_error_count: int = 0
    invalid_request_count: int = 0
    retry_wait_total_seconds: float = 0.0
    retry_wait_average_seconds: float = 0.0
    retry_wait_max_seconds: float = 0.0

    @property
    def validation_failure_rate(self) -> float | None:
        if self.terminal_attempt_count <= 0:
            return None
        return self.validation_failure_count / self.terminal_attempt_count

    @property
    def success_rate(self) -> float | None:
        if self.terminal_attempt_count <= 0:
            return None
        return self.successful_attempt_count / self.terminal_attempt_count


@dataclass(frozen=True)
class GeminiStabilityRecommendation:
    evaluation_status: str
    current_items_per_request: int
    recommended_items_per_request: int
    recommendation_label: str
    thinking_recommendation: str
    sample_sufficient: bool
    reasons: tuple[str, ...]
    recent_10: GeminiRunWindow
    recent_30: GeminiRunWindow
    calls: GeminiCallMetrics


def _int_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _average(values: Iterable[int]) -> int:
    normalized = [max(0, int(value)) for value in values]
    if not normalized:
        return 0
    return int(round(sum(normalized) / len(normalized)))


def _load_run_rows(
    con: duckdb.DuckDBPyConnection,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT cr.started_at, cr.run_type, cr.status AS run_status,
               crs.status AS source_status, crs.duration_ms,
               crs.request_count, crs.retry_count,
               crs.updated_count AS generated_clusters,
               crs.skipped_count
        FROM collection_run_sources crs
        JOIN collection_runs cr ON cr.run_id = crs.run_id
        WHERE crs.source_name = 'topic_angles'
          AND cr.status <> 'running'
          AND COALESCE(crs.updated_count, 0) + COALESCE(crs.skipped_count, 0) > 0
        ORDER BY cr.started_at DESC, cr.run_id DESC
        LIMIT ?
        """,
        [max(1, min(int(limit), 100))],
    ).fetchall()
    columns = [str(item[0]) for item in con.description]
    return [dict(zip(columns, row)) for row in rows]


def _summarize_runs(rows: list[dict[str, Any]], *, limit: int) -> GeminiRunWindow:
    selected = rows[: max(1, int(limit))]
    generated = sum(_int_value(row.get("generated_clusters")) for row in selected)
    skipped = sum(_int_value(row.get("skipped_count")) for row in selected)
    statuses = [str(row.get("source_status") or "").strip() for row in selected]
    durations = [
        _int_value(row.get("duration_ms"))
        for row in selected
        if row.get("duration_ms") is not None
    ]
    return GeminiRunWindow(
        limit=max(1, int(limit)),
        run_count=len(selected),
        requested_clusters=generated + skipped,
        generated_clusters=generated,
        skipped_clusters=skipped,
        successful_runs=sum(status == "success" for status in statuses),
        partial_runs=sum(status == "partial_success" for status in statuses),
        failed_runs=sum(status == "failure" for status in statuses),
        request_count=sum(_int_value(row.get("request_count")) for row in selected),
        retry_count=sum(_int_value(row.get("retry_count")) for row in selected),
        average_duration_ms=_average(durations),
    )


def _load_call_rows(
    con: duckdb.DuckDBPyConnection,
    *,
    app_id: str,
    oldest_started_at: Any = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    available_columns = {
        str(row[1])
        for row in con.execute("PRAGMA table_info('gemini_api_calls')").fetchall()
    }

    def optional_column(name: str, sql_type: str) -> str:
        if name in available_columns:
            return name
        return f"CAST(NULL AS {sql_type}) AS {name}"

    parameters: list[Any] = [str(app_id or "").strip(), TOPIC_ANGLE_FEATURE_ID]
    time_condition = ""
    if oldest_started_at is not None:
        time_condition = "AND created_at >= ?"
        parameters.append(oldest_started_at)
    parameters.append(max(1, min(int(limit), 1000)))
    rows = con.execute(
        f"""
        SELECT created_at, status, error_type, attempt_number,
               retry_wait_seconds, output_tokens, thought_tokens,
               total_tokens, duration_ms,
               {optional_column('requested_item_count', 'INTEGER')},
               {optional_column('configured_items_per_request', 'INTEGER')},
               {optional_column('thinking_level', 'VARCHAR')},
               {optional_column('request_timeout_seconds', 'INTEGER')},
               {optional_column('finish_reason', 'VARCHAR')},
               {optional_column('finish_message', 'VARCHAR')},
               {optional_column('request_hash', 'VARCHAR')},
               {optional_column('content_pack_id', 'VARCHAR')},
               {optional_column('http_status', 'INTEGER')},
               {optional_column('retry_reason', 'VARCHAR')}
        FROM gemini_api_calls
        WHERE app_id = ?
          AND feature_id = ?
          AND cache_hit = FALSE
          {time_condition}
        ORDER BY created_at DESC, call_id DESC
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    columns = [str(item[0]) for item in con.description]
    return [dict(zip(columns, row)) for row in rows]


def _summarize_calls(rows: list[dict[str, Any]]) -> GeminiCallMetrics:
    terminal_rows = [row for row in rows if str(row.get("status") or "") != "retrying"]
    successful_statuses = {"success", "success_after_retry"}

    def _is_max_tokens(r: dict[str, Any]) -> bool:
        return str(r.get("finish_reason") or "").strip().upper() == "MAX_TOKENS"

    def _is_quota(r: dict[str, Any]) -> bool:
        err = str(r.get("error_type") or "").strip().casefold()
        retry_r = str(r.get("retry_reason") or "").strip().casefold()
        return (
            err == "daily_quota_exhausted"
            or retry_r == "daily_quota_exhausted"
            or "quota" in err
            or "daily_quota" in retry_r
        )

    def _is_rate_limit(r: dict[str, Any]) -> bool:
        if _is_quota(r):
            return False
        http_s = r.get("http_status")
        err = str(r.get("error_type") or "").strip().casefold()
        retry_r = str(r.get("retry_reason") or "").strip().casefold()
        st = str(r.get("status") or "").strip().casefold()
        return (
            http_s == 429
            or err in ("rate_limited", "rate_limit_timeout")
            or retry_r in ("rate_limited", "rate_limit_timeout")
            or st in ("rate_limited", "rate_limit_timeout")
            or "rate" in err
            or "rate" in retry_r
        )

    def _is_timeout(r: dict[str, Any]) -> bool:
        st = str(r.get("status") or "").strip().casefold()
        err = str(r.get("error_type") or "").strip().casefold()
        return (
            st == "request_timeout"
            or err == "request_timeout"
            or "timeout" in st
            or "timeout" in err
        )

    def _is_network(r: dict[str, Any]) -> bool:
        st = str(r.get("status") or "").strip().casefold()
        err = str(r.get("error_type") or "").strip().casefold()
        return (
            st == "network_error"
            or err == "network_error"
            or "network" in st
            or "network" in err
        )

    def _is_server(r: dict[str, Any]) -> bool:
        http_s = r.get("http_status")
        st = str(r.get("status") or "").strip().casefold()
        err = str(r.get("error_type") or "").strip().casefold()
        if http_s is not None:
            try:
                if 500 <= int(http_s) <= 599:
                    return True
            except (ValueError, TypeError):
                pass
        return (
            err in ("service_unavailable", "server_error")
            or st in ("service_unavailable", "server_error")
        )

    def _is_validation(r: dict[str, Any]) -> bool:
        st = str(r.get("status") or "").strip().casefold()
        err = str(r.get("error_type") or "").strip().casefold()
        return st == "response_validation_error" or err == "response_validation_error"

    def _is_invalid_request(r: dict[str, Any]) -> bool:
        http_s = r.get("http_status")
        st = str(r.get("status") or "").strip().casefold()
        err = str(r.get("error_type") or "").strip().casefold()
        if http_s is not None:
            try:
                if int(http_s) == 400:
                    return True
            except (ValueError, TypeError):
                pass
        return st == "invalid_request" or err == "invalid_request"

    # Terminal rows non-overlapping classification
    max_token_rows = [r for r in terminal_rows if _is_max_tokens(r)]
    rem_1 = [r for r in terminal_rows if r not in max_token_rows]

    quota_terminal_rows = [r for r in rem_1 if _is_quota(r)]
    rem_2 = [r for r in rem_1 if r not in quota_terminal_rows]

    rate_limit_terminal_rows = [r for r in rem_2 if _is_rate_limit(r)]
    rem_3 = [r for r in rem_2 if r not in rate_limit_terminal_rows]

    timeout_terminal_rows = [r for r in rem_3 if _is_timeout(r)]
    rem_4 = [r for r in rem_3 if r not in timeout_terminal_rows]

    network_terminal_rows = [r for r in rem_4 if _is_network(r)]
    rem_5 = [r for r in rem_4 if r not in network_terminal_rows]

    server_terminal_rows = [r for r in rem_5 if _is_server(r)]
    rem_6 = [r for r in rem_5 if r not in server_terminal_rows]

    validation_rows = [r for r in rem_6 if _is_validation(r)]
    rem_7 = [r for r in rem_6 if r not in validation_rows]

    invalid_request_terminal_rows = [r for r in rem_7 if _is_invalid_request(r)]
    rem_8 = [r for r in rem_7 if r not in invalid_request_terminal_rows]

    successful_rows = [
        r for r in rem_8 if str(r.get("status") or "").strip().casefold() in successful_statuses
    ]
    other_failures = [r for r in rem_8 if r not in successful_rows]

    # Attempt counts across ALL rows
    retrying_attempt_count = sum(
        str(row.get("status") or "").strip().casefold() == "retrying" for row in rows
    )
    rate_limit_attempt_count = sum(
        _is_rate_limit(r) or str(r.get("status") or "").strip().casefold() == "retrying"
        for r in rows
    )
    quota_exhausted_count = sum(_is_quota(r) for r in rows)
    timeout_count = sum(_is_timeout(r) for r in terminal_rows)
    network_error_count = sum(_is_network(r) for r in rows)
    server_error_count = sum(_is_server(r) for r in rows)
    invalid_request_count = sum(_is_invalid_request(r) for r in rows)

    # Request batch chain grouping (by request_hash)
    batch_groups: dict[str, list[dict[str, Any]]] = {}
    ungrouped_retries = 0

    for r in rows:
        req_hash = str(r.get("request_hash") or "").strip()
        if req_hash:
            batch_groups.setdefault(req_hash, []).append(r)
        else:
            if str(r.get("status") or "").strip().casefold() == "retrying":
                ungrouped_retries += 1

    rate_limit_affected_batches = 0
    retry_recovered_batches = 0
    rate_limited_final_batches = 0

    for req_hash, group in batch_groups.items():
        is_affected = any(
            _is_rate_limit(r)
            or str(r.get("status") or "").strip().casefold() == "retrying"
            for r in group
        )
        if is_affected:
            rate_limit_affected_batches += 1
            has_success = any(
                str(r.get("status") or "").strip().casefold() in successful_statuses
                for r in group
            )
            if has_success:
                retry_recovered_batches += 1
            else:
                rate_limited_final_batches += 1

    # Retry wait time calculations
    retry_waits = []
    for r in rows:
        w = r.get("retry_wait_seconds")
        if w is not None:
            try:
                wf = float(w)
                if wf > 0.0:
                    retry_waits.append(wf)
            except (TypeError, ValueError):
                pass

    total_wait = float(sum(retry_waits))
    avg_wait = float(total_wait / len(retry_waits)) if retry_waits else 0.0
    max_wait = float(max(retry_waits, default=0.0))

    generation_tokens: list[int] = []
    for row in rows:
        output = row.get("output_tokens")
        thought = row.get("thought_tokens")
        if output is None and thought is None:
            continue
        generation_tokens.append(_int_value(output) + _int_value(thought))

    durations = [
        _int_value(row.get("duration_ms"))
        for row in rows
        if row.get("duration_ms") is not None
    ]
    requested_counts = [
        _int_value(row.get("requested_item_count"))
        for row in rows
        if row.get("requested_item_count") is not None
    ]
    thinking_counts: dict[str, int] = {}
    finish_counts: dict[str, int] = {}
    for row in rows:
        thinking_level = str(row.get("thinking_level") or "").strip().casefold()
        if thinking_level:
            thinking_counts[thinking_level] = thinking_counts.get(thinking_level, 0) + 1
        finish_reason = str(row.get("finish_reason") or "").strip().upper()
        if finish_reason:
            finish_counts[finish_reason] = finish_counts.get(finish_reason, 0) + 1

    return GeminiCallMetrics(
        attempt_count=len(rows),
        terminal_attempt_count=len(terminal_rows),
        successful_attempt_count=len(successful_rows),
        validation_failure_count=len(validation_rows),
        other_failure_count=len(other_failures),
        retrying_attempt_count=retrying_attempt_count,
        average_generation_tokens=_average(generation_tokens),
        maximum_generation_tokens=max(generation_tokens, default=0),
        near_limit_count=sum(
            token_count >= GENERATION_TOKEN_WARNING for token_count in generation_tokens
        ),
        average_duration_ms=_average(durations),
        recorded_requested_item_count=len(requested_counts),
        average_requested_item_count=(
            sum(requested_counts) / len(requested_counts) if requested_counts else 0.0
        ),
        maximum_requested_item_count=max(requested_counts, default=0),
        thinking_level_counts=tuple(sorted(thinking_counts.items())),
        finish_reason_counts=tuple(sorted(finish_counts.items())),
        max_tokens_count=len(max_token_rows),
        missing_finish_reason_count=sum(
            not str(row.get("finish_reason") or "").strip() for row in rows
        ),
        rate_limit_affected_request_count=rate_limit_affected_batches,
        retry_recovered_request_count=retry_recovered_batches,
        rate_limited_final_request_count=rate_limited_final_batches,
        ungrouped_retry_attempt_count=ungrouped_retries,
        rate_limit_attempt_count=rate_limit_attempt_count,
        quota_exhausted_count=quota_exhausted_count,
        timeout_count=timeout_count,
        network_error_count=network_error_count,
        server_error_count=server_error_count,
        invalid_request_count=len(invalid_request_terminal_rows),
        retry_wait_total_seconds=total_wait,
        retry_wait_average_seconds=avg_wait,
        retry_wait_max_seconds=max_wait,
    )


def _percent_text(value: float | None) -> str:
    return "기록 없음" if value is None else f"{value * 100:.1f}%"


def _normalize_thinking_level(value: object) -> str:
    normalized = str(value or "").strip().casefold()
    return normalized if normalized in ALLOWED_THINKING_LEVELS else DEFAULT_THINKING_LEVEL


def _recommend(
    recent: GeminiRunWindow,
    calls: GeminiCallMetrics,
    *,
    current_items_per_request: int,
) -> tuple[str, int, str, bool, tuple[str, ...]]:
    sample_sufficient = (
        recent.run_count >= MIN_COMPLETED_RUNS
        and recent.requested_clusters >= MIN_REQUESTED_CLUSTERS
    )
    if not sample_sufficient:
        reasons = (
            "처리량을 줄이기에는 표본이 부족합니다. "
            f"최근 완료 실행 {recent.run_count}회·요청 글감 {recent.requested_clusters}개이며, "
            f"최소 {MIN_COMPLETED_RUNS}회·{MIN_REQUESTED_CLUSTERS}개가 필요합니다.",
            f"표본이 쌓이는 동안 현재 {current_items_per_request}개 설정을 유지하고 자동 변경하지 않습니다.",
        )
        return (
            "표본 부족",
            current_items_per_request,
            f"{current_items_per_request}개 유지",
            False,
            reasons,
        )

    save_rate = recent.save_rate or 0.0
    partial_failure_rate = recent.partial_failure_rate or 0.0
    retry_rate = recent.retry_rate or 0.0
    validation_rate = calls.validation_failure_rate

    severe_reasons: list[str] = []
    if save_rate < 0.75:
        severe_reasons.append(f"최근 30회 저장률이 {_percent_text(save_rate)}로 75% 미만입니다.")
    if partial_failure_rate >= 0.30:
        severe_reasons.append(
            f"부분 성공·실패 실행 비율이 {_percent_text(partial_failure_rate)}로 30% 이상입니다."
        )
    if (
        validation_rate is not None
        and calls.terminal_attempt_count >= 3
        and validation_rate >= 0.20
    ):
        severe_reasons.append(
            f"응답 검증 실패율이 {_percent_text(validation_rate)}로 20% 이상입니다."
        )
    if calls.near_limit_count >= 2:
        severe_reasons.append(
            f"생성 토큰 {GENERATION_TOKEN_WARNING:,} 이상 호출이 {calls.near_limit_count}회 반복됐습니다."
        )
    if severe_reasons:
        recommended = min(current_items_per_request, 20)
        if recommended < current_items_per_request:
            severe_reasons.append(
                "한 번에 보내는 글감 수를 20개로 낮춘 뒤 최소 3회 이상 다시 관찰하는 편이 안전합니다."
            )
            return (
                "조정 권장",
                recommended,
                "20개로 낮추기 권장",
                True,
                tuple(severe_reasons),
            )
        severe_reasons.append(
            f"현재 {current_items_per_request}개 설정은 유지하되 처리량 외 원인을 추가 점검하는 편이 안전합니다."
        )
        return (
            "유지·추가 점검",
            recommended,
            f"{recommended}개 유지·추가 점검",
            True,
            tuple(severe_reasons),
        )

    caution_reasons: list[str] = []
    if save_rate < 0.90:
        caution_reasons.append(f"최근 30회 저장률이 {_percent_text(save_rate)}로 90% 미만입니다.")
    if partial_failure_rate >= 0.15:
        caution_reasons.append(
            f"부분 성공·실패 실행 비율이 {_percent_text(partial_failure_rate)}로 15% 이상입니다."
        )
    if (
        validation_rate is not None
        and calls.terminal_attempt_count >= 3
        and validation_rate >= 0.10
    ):
        caution_reasons.append(
            f"응답 검증 실패율이 {_percent_text(validation_rate)}로 10% 이상입니다."
        )
    if calls.near_limit_count >= 1:
        caution_reasons.append(
            f"생성 토큰 {GENERATION_TOKEN_WARNING:,} 이상 호출이 확인됐습니다."
        )
    if recent.request_count >= 5 and retry_rate >= 0.20:
        caution_reasons.append(f"재시도율이 {_percent_text(retry_rate)}로 20% 이상입니다.")
    if caution_reasons:
        recommended = min(current_items_per_request, 25)
        if recommended < current_items_per_request:
            caution_reasons.append(
                "25개로 한 단계만 낮추고 저장률과 검증 실패가 개선되는지 먼저 확인하는 편이 좋습니다."
            )
            return (
                "조정 권장",
                recommended,
                "25개로 낮추기 권장",
                True,
                tuple(caution_reasons),
            )
        caution_reasons.append(
            f"현재 {current_items_per_request}개 설정을 유지하면서 저장률과 검증 실패를 더 관찰합니다."
        )
        return (
            "유지·관찰",
            recommended,
            f"{recommended}개 유지·관찰",
            True,
            tuple(caution_reasons),
        )

    stable_reasons = (
        f"최근 30회 저장률이 {_percent_text(save_rate)}입니다.",
        f"부분 성공·실패 실행 비율은 {_percent_text(partial_failure_rate)}입니다.",
        (
            "생성 토큰 한도 근접 기록이 없고 현재 처리량을 낮춰야 할 반복 신호가 확인되지 않았습니다."
            if calls.near_limit_count == 0
            else "현재 기록에서는 처리량 축소를 요구할 반복 신호가 확인되지 않았습니다."
        ),
    )
    return (
        "유지 권장",
        current_items_per_request,
        f"{current_items_per_request}개 유지",
        True,
        stable_reasons,
    )


def get_gemini_stability_recommendation(
    con: duckdb.DuckDBPyConnection,
    *,
    app_id: str,
    current_items_per_request: int = 30,
    current_thinking_level: str = DEFAULT_THINKING_LEVEL,
) -> GeminiStabilityRecommendation:
    """현재 저장 기록만 읽고 Gemini 글감 처리량 추천을 반환합니다."""
    run_rows = _load_run_rows(con, limit=30)
    recent_10 = _summarize_runs(run_rows, limit=10)
    recent_30 = _summarize_runs(run_rows, limit=30)
    oldest_started_at = run_rows[-1].get("started_at") if run_rows else None
    call_rows = _load_call_rows(
        con,
        app_id=app_id,
        oldest_started_at=oldest_started_at,
    )
    calls = _summarize_calls(call_rows)
    current_limit = max(1, min(int(current_items_per_request), 30))
    status, recommended, label, sufficient, reasons = _recommend(
        recent_30,
        calls,
        current_items_per_request=current_limit,
    )
    return GeminiStabilityRecommendation(
        evaluation_status=status,
        current_items_per_request=current_limit,
        recommended_items_per_request=recommended,
        recommendation_label=label,
        thinking_recommendation=f"{_normalize_thinking_level(current_thinking_level)} 유지",
        sample_sufficient=sufficient,
        reasons=reasons,
        recent_10=recent_10,
        recent_30=recent_30,
        calls=calls,
    )
