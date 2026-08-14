from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4

from src.config import GeminiConfig
from src.services.trend_cluster_token_runtime import GLOBAL_TOKEN_ESTIMATOR

DEFAULT_RPM_LIMIT = 5
DEFAULT_TPM_LIMIT = 250_000
DEFAULT_TPM_SAFETY_MARGIN = 10_000
DEFAULT_WINDOW_SECONDS = 60.0
DEFAULT_BOUNDARY_GUARD_SECONDS = 0.75
_STATE_VERSION = 1
_PROCESS_LOCK = threading.RLock()


class GeminiRateLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeminiRateLimitPolicy:
    rpm_limit: int
    tpm_limit: int
    effective_tpm_limit: int
    window_seconds: float


@dataclass(frozen=True)
class GeminiRateLimitReservation:
    request_id: str
    scope_key: str
    model_name: str
    estimated_input_tokens: int
    wait_seconds: float
    reserved_at_epoch: float


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return min(max(value, minimum), maximum)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _normalized_model_name(value: Any) -> str:
    model = str(value or "").strip()
    if model.startswith("models/"):
        model = model.split("/", 1)[1]
    return model or "unknown-model"


def _model_env_key(model_name: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "_", model_name.upper()).strip("_")
    return normalized or "UNKNOWN_MODEL"


def _scope_key(config: GeminiConfig) -> str:
    raw = str(config.quota_scope_id or config.app_id or "default").strip() or "default"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def resolve_gemini_rate_limit_policy(config: GeminiConfig) -> GeminiRateLimitPolicy:
    model_name = _normalized_model_name(config.model)
    model_key = _model_env_key(model_name)
    global_rpm = _env_int(
        "GEMINI_RATE_LIMIT_RPM",
        DEFAULT_RPM_LIMIT,
        minimum=1,
        maximum=10_000,
    )
    global_tpm = _env_int(
        "GEMINI_RATE_LIMIT_TPM",
        DEFAULT_TPM_LIMIT,
        minimum=1,
        maximum=100_000_000,
    )
    rpm_limit = _env_int(
        f"GEMINI_RATE_LIMIT_{model_key}_RPM",
        global_rpm,
        minimum=1,
        maximum=10_000,
    )
    tpm_limit = _env_int(
        f"GEMINI_RATE_LIMIT_{model_key}_TPM",
        global_tpm,
        minimum=1,
        maximum=100_000_000,
    )
    margin = _env_int(
        "GEMINI_RATE_LIMIT_TPM_SAFETY_MARGIN",
        DEFAULT_TPM_SAFETY_MARGIN,
        minimum=0,
        maximum=max(0, tpm_limit - 1),
    )
    return GeminiRateLimitPolicy(
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
        effective_tpm_limit=max(1, tpm_limit - margin),
        window_seconds=DEFAULT_WINDOW_SECONDS,
    )


def default_gemini_rate_limit_state_path() -> Path:
    override = os.getenv("CONTENT_TREND_GEMINI_RATE_LIMIT_STATE_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = os.getenv("LOCALAPPDATA", "").strip()
        base = Path(root) if root else Path(tempfile.gettempdir())
    else:
        root = os.getenv("XDG_STATE_HOME", "").strip()
        base = Path(root) if root else Path(tempfile.gettempdir())
    return base / "content-trend-tracker" / "gemini_rate_limit_state.json"


def gemini_common_rate_limit_enabled() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST", "").strip():
        return False
    disabled = os.getenv("CONTENT_TREND_DISABLE_GEMINI_RATE_LIMIT", "").strip().casefold()
    return disabled not in {"1", "true", "yes", "on"}


@contextmanager
def _exclusive_file_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _PROCESS_LOCK:
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class SharedGeminiRateLimiter:
    """프로세스가 달라도 같은 Gemini 모델의 최근 60초 RPM·TPM 예약을 공유합니다."""

    def __init__(
        self,
        *,
        state_path: Path | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        boundary_guard_seconds: float = DEFAULT_BOUNDARY_GUARD_SECONDS,
        token_estimator: Callable[[str], int] | None = None,
    ) -> None:
        self.state_path = state_path or default_gemini_rate_limit_state_path()
        self.lock_path = self.state_path.with_suffix(self.state_path.suffix + ".lock")
        self._clock = clock
        self._sleeper = sleeper
        self._boundary_guard_seconds = max(0.0, float(boundary_guard_seconds))
        self._token_estimator = token_estimator or GLOBAL_TOKEN_ESTIMATOR.estimate_text

    def _load_entries(self) -> list[dict[str, Any]]:
        try:
            raw = self.state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise GeminiRateLimitError(
                f"Gemini 공통 제한 상태를 읽을 수 없습니다: {exc}"
            ) from exc
        if not raw.strip():
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, dict) or payload.get("version") != _STATE_VERSION:
            return []
        rows = payload.get("reservations")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.state_path.with_name(
            f"{self.state_path.name}.{os.getpid()}.{uuid4().hex}.tmp"
        )
        payload = {"version": _STATE_VERSION, "reservations": entries}
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temp_path, self.state_path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise GeminiRateLimitError(
                f"Gemini 공통 제한 상태를 저장할 수 없습니다: {exc}"
            ) from exc

    @staticmethod
    def _pruned(
        entries: list[dict[str, Any]],
        now: float,
        window: float,
    ) -> list[dict[str, Any]]:
        cutoff = now - window
        result: list[dict[str, Any]] = []
        for row in entries:
            try:
                reserved_at = float(row.get("reserved_at_epoch", 0.0))
            except (TypeError, ValueError):
                continue
            if reserved_at > cutoff:
                clean = dict(row)
                clean["reserved_at_epoch"] = reserved_at
                clean["input_tokens"] = max(0, _safe_int(row.get("input_tokens"), 0))
                result.append(clean)
        result.sort(key=lambda row: float(row.get("reserved_at_epoch", 0.0)))
        return result

    @staticmethod
    def _matching(
        entries: list[dict[str, Any]],
        *,
        scope_key: str,
        model_name: str,
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in entries
            if str(row.get("scope_key") or "") == scope_key
            and str(row.get("model_name") or "") == model_name
        ]

    def _required_wait(
        self,
        matching: list[dict[str, Any]],
        *,
        now: float,
        requested_tokens: int,
        policy: GeminiRateLimitPolicy,
    ) -> float:
        candidate_delays = {0.0}
        for row in matching:
            reserved_at = float(row.get("reserved_at_epoch", 0.0))
            candidate_delays.add(
                max(
                    0.0,
                    reserved_at
                    + policy.window_seconds
                    - now
                    + self._boundary_guard_seconds,
                )
            )
        for delay in sorted(candidate_delays):
            future_now = now + delay
            active = [
                row
                for row in matching
                if float(row.get("reserved_at_epoch", 0.0))
                > future_now - policy.window_seconds
            ]
            used_tokens = sum(
                max(0, _safe_int(row.get("input_tokens"), 0)) for row in active
            )
            if (
                len(active) + 1 <= policy.rpm_limit
                and used_tokens + requested_tokens <= policy.effective_tpm_limit
            ):
                return delay
        return policy.window_seconds + self._boundary_guard_seconds

    def reserve(
        self,
        config: GeminiConfig,
        request_text: str,
    ) -> GeminiRateLimitReservation:
        policy = resolve_gemini_rate_limit_policy(config)
        model_name = _normalized_model_name(config.model)
        scope_key = _scope_key(config)
        estimated_tokens = max(1, int(self._token_estimator(str(request_text or ""))))
        if estimated_tokens > policy.effective_tpm_limit:
            raise GeminiRateLimitError(
                "Gemini 단일 요청의 예상 입력 토큰이 내부 안전 상한을 초과했습니다. "
                f"예상 {estimated_tokens:,} / 안전 상한 {policy.effective_tpm_limit:,}"
            )

        request_id = f"gemlimit_{uuid4().hex}"
        waited = 0.0
        while True:
            with _exclusive_file_lock(self.lock_path):
                now = float(self._clock())
                entries = self._pruned(self._load_entries(), now, policy.window_seconds)
                matching = self._matching(
                    entries,
                    scope_key=scope_key,
                    model_name=model_name,
                )
                delay = self._required_wait(
                    matching,
                    now=now,
                    requested_tokens=estimated_tokens,
                    policy=policy,
                )
                if delay <= 0.0:
                    entries.append(
                        {
                            "request_id": request_id,
                            "scope_key": scope_key,
                            "model_name": model_name,
                            "input_tokens": estimated_tokens,
                            "reserved_at_epoch": now,
                        }
                    )
                    self._write_entries(entries)
                    return GeminiRateLimitReservation(
                        request_id=request_id,
                        scope_key=scope_key,
                        model_name=model_name,
                        estimated_input_tokens=estimated_tokens,
                        wait_seconds=waited,
                        reserved_at_epoch=now,
                    )
                self._write_entries(entries)
            self._sleeper(delay)
            waited += delay

    def reconcile(
        self,
        reservation: GeminiRateLimitReservation,
        actual_input_tokens: int | None,
    ) -> None:
        if actual_input_tokens is None:
            return
        actual_tokens = max(1, int(actual_input_tokens))
        with _exclusive_file_lock(self.lock_path):
            now = float(self._clock())
            entries = self._pruned(
                self._load_entries(),
                now,
                DEFAULT_WINDOW_SECONDS,
            )
            for row in entries:
                if str(row.get("request_id") or "") == reservation.request_id:
                    row["input_tokens"] = actual_tokens
                    break
            self._write_entries(entries)


GLOBAL_GEMINI_RATE_LIMITER = SharedGeminiRateLimiter()
