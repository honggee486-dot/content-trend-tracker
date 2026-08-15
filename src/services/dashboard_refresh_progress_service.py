from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable


PROGRESS_FILENAME = "dashboard_refresh_progress.json"
_RUNNING_STATUS = "running"
_TERMINAL_STATUSES = {"success", "failure"}
_MAX_STEPS = 120


@dataclass(frozen=True)
class DashboardRefreshProgress:
    status: str
    value: int
    message: str
    run_id: str
    pid: int
    started_at: str
    updated_at: str
    finished_at: str = ""
    summary: str = ""
    error_message: str = ""
    steps: tuple[dict[str, Any], ...] = ()

    @property
    def active(self) -> bool:
        return self.status == _RUNNING_STATUS

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES


def default_dashboard_refresh_progress_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path(tempfile.gettempdir())
    return root / "content-trend-tracker" / PROGRESS_FILENAME


def _now_text(now: datetime | None = None) -> str:
    return (now or datetime.now()).isoformat(timespec="seconds")


def _as_int(value: Any, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        number = 0
    number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def _progress_value(value: Any, *, maximum: int) -> int:
    try:
        numeric = round(float(value or 0))
    except (TypeError, ValueError, OverflowError):
        numeric = 0
    return _as_int(numeric, maximum=maximum)


def _parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _elapsed_seconds(started_at: str, event_time: str) -> float:
    started = _parse_time(started_at)
    current = _parse_time(event_time)
    if started is None or current is None:
        return 0.0
    return max(0.0, (current - started).total_seconds())


def _normalize_steps(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "time": str(item.get("time") or "").strip(),
                "elapsed_seconds": max(
                    0.0,
                    float(item.get("elapsed_seconds") or 0.0),
                ),
                "value": _as_int(item.get("value"), maximum=100),
                "message": str(item.get("message") or "").strip(),
                "status": str(item.get("status") or "running").strip(),
            }
        )
    return tuple(result[-_MAX_STEPS:])


def _append_step(
    steps: tuple[dict[str, Any], ...],
    *,
    started_at: str,
    event_time: str,
    value: int,
    message: str,
    status: str,
) -> tuple[dict[str, Any], ...]:
    clean_message = str(message or "").strip()
    clean_status = str(status or "running").strip()
    clean_value = _as_int(value, maximum=100)
    if steps:
        latest = steps[-1]
        if (
            int(latest.get("value") or 0) == clean_value
            and str(latest.get("message") or "") == clean_message
            and str(latest.get("status") or "") == clean_status
        ):
            return steps
    row = {
        "time": event_time,
        "elapsed_seconds": _elapsed_seconds(started_at, event_time),
        "value": clean_value,
        "message": clean_message,
        "status": clean_status,
    }
    return tuple((*steps, row)[-_MAX_STEPS:])


def _progress_from_payload(payload: Any) -> DashboardRefreshProgress | None:
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("status") or "").strip().casefold()
    if status not in {_RUNNING_STATUS, *_TERMINAL_STATUSES}:
        return None
    started_at = str(payload.get("started_at") or "").strip()
    updated_at = str(payload.get("updated_at") or "").strip()
    if not started_at or not updated_at:
        return None
    return DashboardRefreshProgress(
        status=status,
        value=_as_int(payload.get("value"), maximum=100),
        message=str(payload.get("message") or "").strip(),
        run_id=str(payload.get("run_id") or "").strip(),
        pid=_as_int(payload.get("pid")),
        started_at=started_at,
        updated_at=updated_at,
        finished_at=str(payload.get("finished_at") or "").strip(),
        summary=str(payload.get("summary") or "").strip(),
        error_message=str(payload.get("error_message") or "").strip(),
        steps=_normalize_steps(payload.get("steps")),
    )


def read_dashboard_refresh_progress(
    path: str | Path | None = None,
) -> DashboardRefreshProgress | None:
    progress_path = Path(path or default_dashboard_refresh_progress_path())
    try:
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return _progress_from_payload(payload)


def write_dashboard_refresh_progress(
    progress: DashboardRefreshProgress,
    path: str | Path | None = None,
) -> DashboardRefreshProgress:
    """진행 상태 저장 실패가 실제 수집 성공을 취소하지 않도록 최선형으로 기록합니다."""
    progress_path = Path(path or default_dashboard_refresh_progress_path())
    temporary = progress_path.with_name(f".{progress_path.name}.{os.getpid()}.tmp")
    try:
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(asdict(progress), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, progress_path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
    return progress


def start_dashboard_refresh_progress(
    *,
    pid: int,
    run_id: str = "",
    message: str = "최신 데이터 수집 준비 중",
    path: str | Path | None = None,
    now: datetime | None = None,
) -> DashboardRefreshProgress:
    timestamp = _now_text(now)
    clean_message = str(message or "최신 데이터 수집 준비 중").strip()
    steps = _append_step(
        (),
        started_at=timestamp,
        event_time=timestamp,
        value=1,
        message=clean_message,
        status="started",
    )
    return write_dashboard_refresh_progress(
        DashboardRefreshProgress(
            status=_RUNNING_STATUS,
            value=1,
            message=clean_message,
            run_id=str(run_id or "").strip(),
            pid=_as_int(pid),
            started_at=timestamp,
            updated_at=timestamp,
            steps=steps,
        ),
        path,
    )


def update_dashboard_refresh_progress(
    value: int | float,
    message: str,
    *,
    pid: int | None = None,
    run_id: str | None = None,
    path: str | Path | None = None,
    now: datetime | None = None,
) -> DashboardRefreshProgress:
    current = read_dashboard_refresh_progress(path)
    timestamp = _now_text(now)
    started_at = current.started_at if current is not None else timestamp
    progress_value = _progress_value(value, maximum=99)
    clean_message = str(message or "수집 중").strip()
    steps = _append_step(
        current.steps if current is not None else (),
        started_at=started_at,
        event_time=timestamp,
        value=progress_value,
        message=clean_message,
        status="running",
    )
    return write_dashboard_refresh_progress(
        DashboardRefreshProgress(
            status=_RUNNING_STATUS,
            value=progress_value,
            message=clean_message,
            run_id=(
                str(run_id).strip()
                if run_id is not None
                else (current.run_id if current is not None else "")
            ),
            pid=(
                _as_int(pid)
                if pid is not None
                else _as_int(current.pid if current is not None else 0)
            ),
            started_at=started_at,
            updated_at=timestamp,
            steps=steps,
        ),
        path,
    )


def finish_dashboard_refresh_progress(
    *,
    success: bool,
    message: str,
    summary: str = "",
    error_message: str = "",
    pid: int | None = None,
    run_id: str | None = None,
    path: str | Path | None = None,
    now: datetime | None = None,
) -> DashboardRefreshProgress:
    current = read_dashboard_refresh_progress(path)
    timestamp = _now_text(now)
    started_at = current.started_at if current is not None else timestamp
    clean_message = str(message or ("수집 완료" if success else "수집 실패")).strip()
    progress_value = 100 if success else _as_int(
        current.value if current else 0,
        maximum=99,
    )
    final_status = "success" if success else "failure"
    steps = _append_step(
        current.steps if current is not None else (),
        started_at=started_at,
        event_time=timestamp,
        value=progress_value,
        message=clean_message,
        status=final_status,
    )
    return write_dashboard_refresh_progress(
        DashboardRefreshProgress(
            status=final_status,
            value=progress_value,
            message=clean_message,
            run_id=(
                str(run_id).strip()
                if run_id is not None
                else (current.run_id if current is not None else "")
            ),
            pid=(
                _as_int(pid)
                if pid is not None
                else _as_int(current.pid if current is not None else 0)
            ),
            started_at=started_at,
            updated_at=timestamp,
            finished_at=timestamp,
            summary=str(summary or "").strip(),
            error_message=str(error_message or "").strip(),
            steps=steps,
        ),
        path,
    )


def clear_dashboard_refresh_progress(path: str | Path | None = None) -> None:
    progress_path = Path(path or default_dashboard_refresh_progress_path())
    try:
        progress_path.unlink()
    except OSError:
        pass


def is_dashboard_refresh_active(
    progress: DashboardRefreshProgress | None,
    *,
    project_root: str | Path | None = None,
    is_process_alive: Callable[[int], bool] | None = None,
    process_identity_reader: Callable[[int], str] | None = None,
    refresh_lock_inspector: Callable[..., Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """진행 상태 파일이 running이어도 실제 수집 잠금과 프로세스 생존 여부를 검증합니다."""
    if progress is None or not progress.active:
        return False

    pid = int(progress.pid or 0)
    from src.config import PROJECT_ROOT
    from src.services.trend_refresh_lock_service import (
        _is_process_alive,
        inspect_trend_refresh_lock,
    )
    from src.services.process_identity_service import get_process_start_identity

    alive_check = is_process_alive or _is_process_alive
    identity_reader = process_identity_reader or get_process_start_identity
    lock_inspector = refresh_lock_inspector or inspect_trend_refresh_lock
    root = Path(project_root or PROJECT_ROOT).resolve()

    lock_status = lock_inspector(
        root,
        is_process_alive=alive_check,
        process_identity_reader=identity_reader,
    )

    # 1. 수집 잠금이 활성이고 소유자 PID가 일치하면 확실히 실행 중인 정상 작업입니다.
    if lock_status.active and lock_status.owner is not None:
        if pid <= 0 or lock_status.owner.pid == pid:
            return True

    # 2. OS 프로세스 자체가 종료되었으면 확실히 중단된 작업입니다.
    if pid <= 0 or not alive_check(pid):
        return False

    # 3. PID는 살아있으나 잠금이 비활성인 경우:
    #    프로세스 시작 직후 잠금 파일 생성 전의 짧은 준비 구간(30초)인지 확인합니다.
    started_time = _parse_time(progress.started_at)
    current_time = now or datetime.now()
    if started_time is not None:
        try:
            elapsed = (current_time - started_time).total_seconds()
            if 0.0 <= elapsed < 30.0:
                return True
        except TypeError:
            pass

    return False
