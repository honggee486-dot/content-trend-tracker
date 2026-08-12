from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import platform
from time import sleep
from typing import Callable
from uuid import uuid4

from src.services.lock_lease_service import (
    DEFAULT_LOCK_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_LOCK_LEASE_SECONDS,
    LockHeartbeatThread,
    lock_lease_is_current,
    replace_json_file_atomically,
)
from src.services.process_identity_service import (
    ProcessIdentityReader,
    get_process_start_identity,
    process_identity_matches,
)


LOCK_FILENAME = "trend_refresh.lock"
LOCK_METADATA_GRACE_ATTEMPTS = 3
LOCK_METADATA_GRACE_SECONDS = 0.02
LOCK_HEARTBEAT_INTERVAL_SECONDS = DEFAULT_LOCK_HEARTBEAT_INTERVAL_SECONDS
LOCK_LEASE_SECONDS = DEFAULT_LOCK_LEASE_SECONDS


@dataclass(frozen=True)
class TrendRefreshLockOwner:
    pid: int
    started_at: str
    launcher: str
    token: str
    process_start_identity: str = ""
    heartbeat_at: str = ""
    lease_seconds: int = 0


@dataclass(frozen=True)
class TrendRefreshLockAttempt:
    acquired: bool
    lock: "TrendRefreshLock | None" = None
    active_owner: TrendRefreshLockOwner | None = None
    message: str = ""


@dataclass(frozen=True)
class TrendRefreshLockStatus:
    exists: bool
    active: bool
    owner: TrendRefreshLockOwner | None = None


class TrendRefreshLock:
    def __init__(self, path: Path, owner: TrendRefreshLockOwner) -> None:
        self.path = path
        self.owner = owner
        self._released = False
        self._heartbeat = LockHeartbeatThread(
            self._heartbeat_tick,
            interval_seconds=LOCK_HEARTBEAT_INTERVAL_SECONDS,
            thread_name="trend-refresh-lock-heartbeat",
        )
        self._heartbeat.start()

    def _heartbeat_tick(self) -> bool:
        try:
            if self.refresh_heartbeat():
                return True
        except Exception:
            pass
        if self._released:
            return False
        current = _read_lock_owner(self.path)
        if current is None:
            return self.path.exists()
        return current.token == self.owner.token

    def refresh_heartbeat(self, *, now: datetime | None = None) -> bool:
        if self._released:
            return False
        current = _read_lock_owner(self.path)
        if current is None or current.token != self.owner.token:
            return False
        updated = replace(
            current,
            heartbeat_at=(now or datetime.now()).isoformat(timespec="seconds"),
            lease_seconds=max(1, int(current.lease_seconds or LOCK_LEASE_SECONDS)),
        )
        try:
            replace_json_file_atomically(self.path, _owner_payload(updated))
        except OSError:
            return False
        confirmed = _read_lock_owner(self.path)
        if confirmed is None or confirmed.token != self.owner.token:
            return False
        self.owner = confirmed
        return True

    def release(self) -> None:
        if self._released:
            return
        self._heartbeat.stop()
        self._released = True

        current = _read_lock_owner(self.path)
        if current is None or current.token != self.owner.token:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> "TrendRefreshLock":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


def _owner_is_active(
    owner: TrendRefreshLockOwner | None,
    *,
    alive_check: Callable[[int], bool],
    identity_reader: ProcessIdentityReader,
) -> bool:
    if owner is None:
        return False
    if not process_identity_matches(
        owner.pid,
        owner.process_start_identity,
        is_process_alive=alive_check,
        identity_reader=identity_reader,
    ):
        return False
    return lock_lease_is_current(owner.heartbeat_at, owner.lease_seconds)


def inspect_trend_refresh_lock(
    project_root: str | Path,
    *,
    is_process_alive: Callable[[int], bool] | None = None,
    process_identity_reader: ProcessIdentityReader | None = None,
) -> TrendRefreshLockStatus:
    """잠금을 획득하거나 정리하지 않고 현재 소유자 생존 여부만 확인합니다."""
    lock_path = Path(project_root).resolve() / "data" / LOCK_FILENAME
    if not lock_path.exists():
        return TrendRefreshLockStatus(exists=False, active=False)
    owner = _read_lock_owner_with_grace(lock_path)
    alive_check = is_process_alive or _is_process_alive
    identity_reader = process_identity_reader or get_process_start_identity
    return TrendRefreshLockStatus(
        exists=True,
        active=_owner_is_active(
            owner,
            alive_check=alive_check,
            identity_reader=identity_reader,
        ),
        owner=owner,
    )


def acquire_trend_refresh_lock(
    project_root: str | Path,
    *,
    launcher: str,
    process_id: int | None = None,
    now: datetime | None = None,
    is_process_alive: Callable[[int], bool] | None = None,
    process_identity_reader: ProcessIdentityReader | None = None,
) -> TrendRefreshLockAttempt:
    root = Path(project_root).resolve()
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / LOCK_FILENAME
    alive_check = is_process_alive or _is_process_alive
    identity_reader = process_identity_reader or get_process_start_identity
    resolved_pid = int(process_id if process_id is not None else os.getpid())
    try:
        process_start_identity = str(identity_reader(resolved_pid) or "").strip()
    except Exception:
        process_start_identity = ""
    current = now or datetime.now()
    owner = TrendRefreshLockOwner(
        pid=resolved_pid,
        started_at=current.isoformat(timespec="seconds"),
        launcher=str(launcher or "unknown").strip() or "unknown",
        token=uuid4().hex,
        process_start_identity=process_start_identity,
        heartbeat_at=current.isoformat(timespec="seconds"),
        lease_seconds=LOCK_LEASE_SECONDS,
    )

    for _ in range(2):
        try:
            _create_lock_file(lock_path, owner)
            return TrendRefreshLockAttempt(
                acquired=True,
                lock=TrendRefreshLock(lock_path, owner),
                message="수집 실행 잠금을 획득했습니다.",
            )
        except FileExistsError:
            existing = _read_lock_owner_with_grace(lock_path)
            if _owner_is_active(
                existing,
                alive_check=alive_check,
                identity_reader=identity_reader,
            ):
                return TrendRefreshLockAttempt(
                    acquired=False,
                    active_owner=existing,
                    message=(
                        "다른 트렌드 수집 또는 순위 작업이 이미 실행 중이어서 "
                        "이번 실행을 생략합니다."
                    ),
                )
            try:
                lock_path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                return TrendRefreshLockAttempt(
                    acquired=False,
                    active_owner=existing,
                    message=f"오래된 수집 잠금을 정리하지 못했습니다: {exc}",
                )

    existing = _read_lock_owner(lock_path)
    return TrendRefreshLockAttempt(
        acquired=False,
        active_owner=existing,
        message="수집 실행 잠금을 획득하지 못해 이번 실행을 생략합니다.",
    )


def run_with_trend_refresh_lock(
    project_root: str | Path,
    *,
    launcher: str,
    runner: Callable[[], int],
    message_callback: Callable[[str], None] = print,
    overlap_callback: Callable[[TrendRefreshLockAttempt], None] | None = None,
) -> int:
    attempt = acquire_trend_refresh_lock(project_root, launcher=launcher)
    if not attempt.acquired or attempt.lock is None:
        active_owner = attempt.active_owner
        owner_detail = (
            f" PID={active_owner.pid} 시작={active_owner.started_at}"
            if active_owner is not None
            else ""
        )
        message_callback(f"[SKIP] {attempt.message}{owner_detail}")
        if overlap_callback is not None:
            try:
                overlap_callback(attempt)
            except Exception:
                # 이력 기록 실패가 기존의 정상 생략(exit code 0)을 바꾸지 않게 합니다.
                pass
        return 0

    try:
        return int(runner())
    finally:
        attempt.lock.release()


def _owner_payload(owner: TrendRefreshLockOwner) -> dict[str, object]:
    return {
        "pid": owner.pid,
        "started_at": owner.started_at,
        "launcher": owner.launcher,
        "token": owner.token,
        "process_start_identity": owner.process_start_identity,
        "heartbeat_at": owner.heartbeat_at,
        "lease_seconds": owner.lease_seconds,
    }


def _create_lock_file(path: Path, owner: TrendRefreshLockOwner) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    descriptor = os.open(path, flags, 0o600)
    try:
        payload = json.dumps(
            _owner_payload(owner),
            ensure_ascii=False,
            indent=2,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _read_lock_owner_with_grace(path: Path) -> TrendRefreshLockOwner | None:
    owner = _read_lock_owner(path)
    if owner is not None:
        return owner
    for _ in range(LOCK_METADATA_GRACE_ATTEMPTS):
        sleep(LOCK_METADATA_GRACE_SECONDS)
        owner = _read_lock_owner(path)
        if owner is not None or not path.exists():
            return owner
    return None


def _read_lock_owner(path: Path) -> TrendRefreshLockOwner | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = int(payload.get("pid") or 0)
        token = str(payload.get("token") or "").strip()
        if pid <= 0 or not token:
            return None
        return TrendRefreshLockOwner(
            pid=pid,
            started_at=str(payload.get("started_at") or "").strip(),
            launcher=str(payload.get("launcher") or "unknown").strip() or "unknown",
            token=token,
            process_start_identity=str(
                payload.get("process_start_identity") or ""
            ).strip(),
            heartbeat_at=str(payload.get("heartbeat_at") or "").strip(),
            lease_seconds=max(0, int(payload.get("lease_seconds") or 0)),
        )
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _is_process_alive(process_id: int) -> bool:
    pid = int(process_id)
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if platform.system() == "Windows":
        return _is_windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _is_windows_process_alive(process_id: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        get_exit_code_process = kernel32.GetExitCodeProcess
        get_exit_code_process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_exit_code_process.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = open_process(process_query_limited_information, False, int(process_id))
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code_process(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == still_active
        finally:
            close_handle(handle)
    except (AttributeError, OSError, ValueError):
        return False
