from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable

CLUSTERING_TPM_LIMIT = 250_000
CLUSTERING_TARGET_INPUT_TOKENS = 225_000
CLUSTERING_HARD_INPUT_TOKENS = 245_000
CLUSTERING_INITIAL_TOKENS_PER_CHARACTER = 1.15
CLUSTERING_MIN_TOKENS_PER_CHARACTER = 0.20
CLUSTERING_MAX_TOKENS_PER_CHARACTER = 4.00
CLUSTERING_GROWTH_SUCCESS_STREAK = 3
CLUSTERING_GROWTH_STEP = 0.02
CLUSTERING_OVERRUN_FACTOR = 1.15
CLUSTERING_RATE_LIMIT_FACTOR = 1.25
CLUSTERING_WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class TpmReservation:
    request_id: str
    estimated_tokens: int
    used_before: int
    wait_seconds: float
    sent_at_monotonic: float


@dataclass
class _WindowEntry:
    request_id: str
    sent_at_monotonic: float
    tokens: int


class SlidingWindowTpmLimiter:
    """최근 60초 입력 토큰 예약으로 다음 요청 시점을 제한합니다."""

    def __init__(
        self,
        *,
        limit: int = CLUSTERING_TPM_LIMIT,
        window_seconds: float = CLUSTERING_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.limit = max(1, int(limit))
        self.window_seconds = max(1.0, float(window_seconds))
        self._clock = clock
        self._sleeper = sleeper
        self._entries: deque[_WindowEntry] = deque()
        self._lock = threading.RLock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._entries and self._entries[0].sent_at_monotonic <= cutoff:
            self._entries.popleft()

    def _used(self) -> int:
        return sum(max(0, int(entry.tokens)) for entry in self._entries)

    def reserve(self, request_id: str, estimated_tokens: int) -> TpmReservation:
        requested = max(1, min(int(estimated_tokens), self.limit))
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                self._prune(now)
                used = self._used()
                if used + requested <= self.limit:
                    self._entries.append(
                        _WindowEntry(str(request_id), now, requested)
                    )
                    return TpmReservation(
                        str(request_id), requested, used, waited, now
                    )
                delay = max(
                    0.01,
                    self._entries[0].sent_at_monotonic
                    + self.window_seconds
                    - now
                    + 0.01,
                )
            self._sleeper(delay)
            waited += delay

    def reconcile(self, request_id: str, actual_tokens: int | None) -> None:
        if actual_tokens is None:
            return
        actual = max(1, min(int(actual_tokens), self.limit))
        with self._lock:
            for entry in self._entries:
                if entry.request_id == request_id:
                    entry.tokens = actual
                    break

    def current_usage(self) -> int:
        with self._lock:
            self._prune(self._clock())
            return self._used()


class AdaptiveInputTokenEstimator:
    """배치 확대는 3회 성공 뒤 2%씩, 위험 신호에는 즉시 축소합니다."""

    def __init__(
        self,
        *,
        tokens_per_character: float = CLUSTERING_INITIAL_TOKENS_PER_CHARACTER,
    ) -> None:
        self._ratio = self._clamp(tokens_per_character)
        self._stable_successes = 0
        self._recent_ratios: deque[float] = deque(maxlen=12)
        self._last_calibration_signature: tuple[tuple[float, str, str], ...] = ()
        self._lock = threading.RLock()

    @staticmethod
    def _clamp(value: float) -> float:
        return min(
            max(float(value), CLUSTERING_MIN_TOKENS_PER_CHARACTER),
            CLUSTERING_MAX_TOKENS_PER_CHARACTER,
        )

    @property
    def tokens_per_character(self) -> float:
        with self._lock:
            return float(self._ratio)

    @property
    def stable_successes(self) -> int:
        with self._lock:
            return int(self._stable_successes)

    def estimate_characters(self, count: int) -> int:
        with self._lock:
            ratio = self._ratio
        return max(1, int(math.ceil(max(1, int(count)) * ratio + 256)))

    def estimate_text(self, text: str) -> int:
        return self.estimate_characters(len(str(text or "")))

    def calibrate(
        self,
        samples: Iterable[tuple[int, int, str, str]],
    ) -> None:
        rows: list[tuple[float, str, str]] = []
        for characters, tokens, status, error_type in samples:
            characters = max(0, int(characters or 0))
            tokens = max(0, int(tokens or 0))
            if characters and tokens:
                rows.append(
                    (
                        round(tokens / characters, 8),
                        str(status or "").casefold(),
                        str(error_type or "").casefold(),
                    )
                )
        if not rows:
            return
        signature = tuple(rows[:12])
        with self._lock:
            if signature == self._last_calibration_signature:
                return
            self._last_calibration_signature = signature
            safe_ratio = self._clamp(max(row[0] for row in signature[:5]) * 1.08)
            latest_three_safe = (
                len(signature) >= 3
                and all(
                    status == "success" and not error
                    for _, status, error in signature[:3]
                )
            )
            self._recent_ratios.extend(row[0] for row in signature)
            if safe_ratio > self._ratio:
                self._ratio = safe_ratio
                self._stable_successes = 0
            elif latest_three_safe:
                self._ratio = self._clamp(
                    max(safe_ratio, self._ratio * (1.0 - CLUSTERING_GROWTH_STEP))
                )

    def observe(
        self,
        *,
        request_characters: int,
        estimated_tokens: int,
        actual_tokens: int | None,
        status: str,
        error_type: str = "",
    ) -> None:
        characters = max(1, int(request_characters or 0))
        estimated = max(1, int(estimated_tokens or 0))
        actual = max(0, int(actual_tokens or 0))
        actual_ratio = actual / characters if actual else 0.0
        normalized_error = str(error_type or "").casefold()
        rate_limited = normalized_error in {
            "rate_limited",
            "daily_quota_exhausted",
            "resource_exhausted",
        }
        failed = str(status or "").casefold() != "success"
        overrun = actual > estimated or actual > CLUSTERING_TARGET_INPUT_TOKENS

        with self._lock:
            if actual_ratio:
                self._recent_ratios.append(actual_ratio)
            if rate_limited:
                self._ratio = self._clamp(
                    max(self._ratio, actual_ratio) * CLUSTERING_RATE_LIMIT_FACTOR
                )
                self._stable_successes = 0
                return
            if failed or overrun:
                self._ratio = self._clamp(
                    max(self._ratio, actual_ratio) * CLUSTERING_OVERRUN_FACTOR
                )
                self._stable_successes = 0
                return
            self._stable_successes += 1
            if self._stable_successes < CLUSTERING_GROWTH_SUCCESS_STREAK:
                return
            observed_floor = self._clamp(
                max(list(self._recent_ratios)[-3:] or [self._ratio]) * 1.05
            )
            self._ratio = self._clamp(
                max(observed_floor, self._ratio * (1.0 - CLUSTERING_GROWTH_STEP))
            )
            self._stable_successes = 0


GLOBAL_TOKEN_ESTIMATOR = AdaptiveInputTokenEstimator()
GLOBAL_TPM_LIMITER = SlidingWindowTpmLimiter()
_CALL_METRICS: dict[str, dict[str, Any]] = {}
_CALL_METRICS_LOCK = threading.RLock()


def register_call_metrics(request_hash: str, values: dict[str, Any]) -> None:
    with _CALL_METRICS_LOCK:
        _CALL_METRICS[str(request_hash)] = dict(values)


def get_call_metrics(request_hash: str) -> dict[str, Any]:
    with _CALL_METRICS_LOCK:
        return dict(_CALL_METRICS.get(str(request_hash), {}))


def calibrate_estimator_from_connection(con: Any) -> None:
    try:
        rows = con.execute(
            """
            SELECT request_char_count, actual_input_tokens, status, error_type
            FROM trend_clustering_request_metrics
            WHERE actual_input_tokens > 0
            ORDER BY created_at DESC
            LIMIT 12
            """
        ).fetchall()
    except Exception:
        return
    GLOBAL_TOKEN_ESTIMATOR.calibrate(
        (
            int(row[0] or 0),
            int(row[1] or 0),
            str(row[2] or ""),
            str(row[3] or ""),
        )
        for row in rows
    )


def create_request_metrics_table(con: Any) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS trend_clustering_request_metrics (
            request_hash VARCHAR PRIMARY KEY,
            feature_id VARCHAR NOT NULL,
            feature_version VARCHAR NOT NULL,
            model_name VARCHAR NOT NULL,
            analysis_view VARCHAR NOT NULL,
            requested_item_count INTEGER NOT NULL DEFAULT 0,
            request_char_count INTEGER NOT NULL DEFAULT 0,
            estimated_input_tokens BIGINT NOT NULL DEFAULT 0,
            actual_input_tokens BIGINT,
            target_input_tokens BIGINT NOT NULL DEFAULT 0,
            hard_input_tokens BIGINT NOT NULL DEFAULT 0,
            tpm_limit BIGINT NOT NULL DEFAULT 0,
            tpm_used_before BIGINT NOT NULL DEFAULT 0,
            tpm_wait_seconds DOUBLE NOT NULL DEFAULT 0,
            estimator_tokens_per_character DOUBLE NOT NULL DEFAULT 0,
            duration_ms BIGINT NOT NULL DEFAULT 0,
            finish_reason VARCHAR NOT NULL DEFAULT '',
            status VARCHAR NOT NULL DEFAULT '',
            error_type VARCHAR NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL
        )
        """
    )


def record_request_metrics(
    con: Any,
    *,
    request_hash: str,
    model_name: str,
) -> None:
    values = get_call_metrics(request_hash)
    if not values:
        return
    create_request_metrics_table(con)
    con.execute(
        """
        INSERT INTO trend_clustering_request_metrics(
            request_hash, feature_id, feature_version, model_name,
            analysis_view, requested_item_count, request_char_count,
            estimated_input_tokens, actual_input_tokens,
            target_input_tokens, hard_input_tokens, tpm_limit,
            tpm_used_before, tpm_wait_seconds,
            estimator_tokens_per_character, duration_ms,
            finish_reason, status, error_type, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(request_hash) DO UPDATE SET
            actual_input_tokens = EXCLUDED.actual_input_tokens,
            tpm_wait_seconds = EXCLUDED.tpm_wait_seconds,
            estimator_tokens_per_character = EXCLUDED.estimator_tokens_per_character,
            duration_ms = EXCLUDED.duration_ms,
            finish_reason = EXCLUDED.finish_reason,
            status = EXCLUDED.status,
            error_type = EXCLUDED.error_type
        """,
        [
            str(request_hash),
            str(values.get("feature_id") or ""),
            str(values.get("feature_version") or ""),
            str(model_name or ""),
            str(values.get("analysis_view") or ""),
            int(values.get("requested_item_count") or 0),
            len(str(values.get("request_text") or "")),
            int(values.get("estimated_input_tokens") or 0),
            values.get("input_tokens"),
            int(values.get("target_input_tokens") or 0),
            int(values.get("hard_input_tokens") or 0),
            int(values.get("tpm_limit") or 0),
            int(values.get("tpm_used_before") or 0),
            float(values.get("tpm_wait_seconds") or 0.0),
            float(values.get("estimator_tokens_per_character") or 0.0),
            int(values.get("duration_ms") or 0),
            str(values.get("finish_reason") or ""),
            str(values.get("status") or ""),
            str(values.get("error_type") or ""),
            datetime.now(),
        ],
    )
