from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from threading import Event, Thread
from typing import Any, Callable, Mapping
from uuid import uuid4


DEFAULT_LOCK_HEARTBEAT_INTERVAL_SECONDS = 30.0
DEFAULT_LOCK_LEASE_SECONDS = 180


class LockHeartbeatThread:
    """파일 잠금의 lease를 DB 연결 없이 주기적으로 갱신합니다."""

    def __init__(
        self,
        callback: Callable[[], bool],
        *,
        interval_seconds: float = DEFAULT_LOCK_HEARTBEAT_INTERVAL_SECONDS,
        thread_name: str = "lock-lease-heartbeat",
    ) -> None:
        self._callback = callback
        self._interval_seconds = max(0.01, float(interval_seconds))
        self._stop_event = Event()
        self._thread = Thread(
            target=self._run,
            name=str(thread_name or "lock-lease-heartbeat"),
            daemon=True,
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._started and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                if not bool(self._callback()):
                    return
            except Exception:
                return


def lock_lease_is_current(
    heartbeat_at: str,
    lease_seconds: int,
    *,
    now: datetime | None = None,
) -> bool:
    """기존 형식은 호환하고 새 형식은 마지막 heartbeat의 lease를 검사합니다."""
    heartbeat_text = str(heartbeat_at or "").strip()
    try:
        bounded_lease_seconds = int(lease_seconds or 0)
    except (TypeError, ValueError, OverflowError):
        bounded_lease_seconds = 0
    if not heartbeat_text or bounded_lease_seconds <= 0:
        return True

    try:
        heartbeat = datetime.fromisoformat(heartbeat_text)
    except ValueError:
        # 손상된 메타데이터만으로 실행 중인 작업을 제거하지 않습니다.
        return True

    if now is not None:
        current = now
    elif heartbeat.tzinfo is not None:
        current = datetime.now(tz=heartbeat.tzinfo)
    else:
        current = datetime.now()

    if current.tzinfo is None and heartbeat.tzinfo is not None:
        current = current.replace(tzinfo=heartbeat.tzinfo)
    elif current.tzinfo is not None and heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=current.tzinfo)

    return heartbeat >= current - timedelta(seconds=max(1, bounded_lease_seconds))


def replace_json_file_atomically(
    path: str | Path,
    payload: Mapping[str, Any],
) -> None:
    """같은 디렉터리의 임시 파일을 이용해 잠금 메타데이터를 원자적으로 교체합니다."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    descriptor = -1
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            json.dump(dict(payload), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
