from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from pathlib import Path
import subprocess
import sys
from time import monotonic
from typing import Any, Callable, MutableMapping, cast

from src.config import PROJECT_ROOT
from src.services.dashboard_refresh_progress_service import (
    clear_dashboard_refresh_progress,
    finish_dashboard_refresh_progress,
    read_dashboard_refresh_progress,
    start_dashboard_refresh_progress,
)

_ACTION_KEY = "trend_dashboard_pending_action"
_MODEL_KEY = "trend_dashboard_pending_model"
_PROGRESS_KEY = "trend_dashboard_progress"
_FLASH_KEY = "trend_refresh_flash"
_LAUNCH_GUARD_KEY = "trend_dashboard_background_refresh_launching"
_FULL_APP_REFRESH_AT_KEY = "trend_dashboard_background_refresh_last_app_rerun"
_FULL_APP_REFRESH_INTERVAL_SECONDS = 2.0


def format_lock_owner_detail(owner: Any) -> str:
    if owner is None:
        return ""
    launcher = str(getattr(owner, "launcher", "") or "unknown").strip()
    launcher = " ".join(launcher.split())[:120]
    started_at = str(getattr(owner, "started_at", "") or "").strip()[:80]
    try:
        pid = max(0, int(getattr(owner, "pid", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        pid = 0
    parts = [f"실행기 {launcher}"]
    if started_at:
        parts.append(f"시작 {started_at}")
    if pid:
        parts.append(f"PID {pid}")
    return " · ".join(parts)


@dataclass(frozen=True)
class TrendDashboardActionGuard:
    refresh_active: bool
    clustering_active: bool
    refresh_owner_detail: str = ""
    clustering_owner_detail: str = ""

    def is_disabled(self, action: str) -> bool:
        normalized = str(action or "").strip()
        if normalized == "refresh":
            return self.refresh_active
        if normalized in {"rebuild", "angles"}:
            return self.refresh_active or self.clustering_active
        return False

    def reason_for(self, action: str) -> str:
        normalized = str(action or "").strip()
        if self.refresh_active:
            action_text = {
                "refresh": "새 수집을 시작하지 않았습니다.",
                "rebuild": "저장 자료 정리를 시작하지 않았습니다.",
                "angles": "주제 방향 생성을 시작하지 않았습니다.",
            }.get(normalized, "새 작업을 시작하지 않았습니다.")
            detail = f" · {self.refresh_owner_detail}" if self.refresh_owner_detail else ""
            return f"최신 데이터 수집이 실행 중이어서 {action_text}{detail}"
        if self.clustering_active and normalized in {"rebuild", "angles"}:
            action_text = (
                "새 군집 작업을 시작하지 않았습니다."
                if normalized == "rebuild"
                else "주제 방향 생성을 시작하지 않았습니다."
            )
            detail = (
                f" · {self.clustering_owner_detail}"
                if self.clustering_owner_detail
                else ""
            )
            return f"기존 2차 군집 작업이 실행 중이어서 {action_text}{detail}"
        return ""

    def notices(self) -> tuple[str, ...]:
        result: list[str] = []
        if self.refresh_active:
            detail = f" · {self.refresh_owner_detail}" if self.refresh_owner_detail else ""
            result.append(f"최신 데이터 수집이 실행 중입니다.{detail}")
        if self.clustering_active:
            detail = (
                f" · {self.clustering_owner_detail}"
                if self.clustering_owner_detail
                else ""
            )
            result.append(f"현재 다른 군집 처리 작업이 실행 중입니다.{detail}")
        return tuple(result)


def build_trend_dashboard_action_guard(
    *,
    refresh_status: Any,
    clustering_status: Any,
    active_clustering_job: dict[str, Any] | None,
) -> TrendDashboardActionGuard:
    refresh_active = bool(getattr(refresh_status, "active", False))
    clustering_lock_active = bool(getattr(clustering_status, "active", False))
    job_active = bool(
        isinstance(active_clustering_job, dict)
        and str(active_clustering_job.get("status") or "") in {"queued", "running"}
        and str(active_clustering_job.get("display_status") or "") != "stale"
    )
    clustering_owner_detail = format_lock_owner_detail(
        getattr(clustering_status, "owner", None)
    )
    if not clustering_owner_detail and job_active:
        launcher = " ".join(
            str(active_clustering_job.get("launcher") or "dashboard").split()
        )[:120]
        started_at = " ".join(
            str(
                active_clustering_job.get("started_at")
                or active_clustering_job.get("created_at")
                or ""
            ).split()
        )[:80]
        clustering_owner_detail = f"실행기 {launcher}"
        if started_at:
            clustering_owner_detail += f" · 시작 {started_at}"
    return TrendDashboardActionGuard(
        refresh_active=refresh_active,
        clustering_active=clustering_lock_active or job_active,
        refresh_owner_detail=format_lock_owner_detail(
            getattr(refresh_status, "owner", None)
        ),
        clustering_owner_detail=clustering_owner_detail,
    )


def _creation_flags() -> int:
    flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return flags


def launch_dashboard_background_refresh(
    *,
    project_root: str | Path = PROJECT_ROOT,
    python_executable: str | Path | None = None,
    popen_factory: Callable[..., Any] = subprocess.Popen,
) -> int:
    """화면 수동 수집을 예약 수집과 같은 별도 Python 프로세스로 시작합니다."""
    root = Path(project_root).resolve()
    script = root / "scripts" / "refresh_trends_dashboard.py"
    if not script.is_file():
        raise FileNotFoundError(f"백그라운드 수집 실행기를 찾을 수 없습니다: {script}")
    python = Path(python_executable or sys.executable).resolve()
    if not python.is_file():
        raise FileNotFoundError(f"Python 실행기를 찾을 수 없습니다: {python}")

    process = popen_factory(
        [str(python), str(script)],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_creation_flags(),
        close_fds=True,
    )
    return max(0, int(getattr(process, "pid", 0) or 0))


def _state(st_module: Any) -> MutableMapping[str, Any] | None:
    value = getattr(st_module, "session_state", None)
    if value is None:
        return None
    if not all(hasattr(value, name) for name in ("get", "pop", "__setitem__")):
        return None
    return cast(MutableMapping[str, Any], value)


def _should_request_full_app_rerun(
    state: MutableMapping[str, Any],
    *,
    now: float,
    interval_seconds: float = _FULL_APP_REFRESH_INTERVAL_SECONDS,
) -> bool:
    """Fragment 최초 렌더는 유지하고 이후 주기 실행에서만 전체 앱을 갱신합니다."""
    try:
        last = float(state.get(_FULL_APP_REFRESH_AT_KEY) or 0.0)
    except (TypeError, ValueError, OverflowError):
        last = 0.0
    current = max(0.0, float(now))
    if last <= 0.0:
        state[_FULL_APP_REFRESH_AT_KEY] = current
        return False
    if current - last < max(0.5, float(interval_seconds)):
        return False
    state[_FULL_APP_REFRESH_AT_KEY] = current
    return True


def _request_full_app_rerun(st_module: Any) -> None:
    rerun = getattr(st_module, "rerun", None)
    if not callable(rerun):
        return
    try:
        rerun(scope="app")
    except TypeError:
        rerun()


def sync_dashboard_background_progress(st_module: Any) -> None:
    """별도 프로세스의 상태 파일을 현재 Streamlit 세션의 기존 게이지 상태로 옮깁니다."""
    state = _state(st_module)
    if state is None:
        return
    progress = read_dashboard_refresh_progress()
    if progress is None:
        return
    if progress.active:
        state[_PROGRESS_KEY] = {
            "value": progress.value,
            "message": progress.message or "최신 데이터 수집·분석 중",
            "status": progress.status,
            "run_id": progress.run_id,
            "pid": progress.pid,
            "started_at": progress.started_at,
            "updated_at": progress.updated_at,
        }
        return

    state.pop(_PROGRESS_KEY, None)
    state.pop(_FULL_APP_REFRESH_AT_KEY, None)
    if progress.status == "success":
        state[_FLASH_KEY] = {
            "summary": progress.summary or progress.message or "최신 데이터 수집·분석을 완료했습니다.",
            "source_details": [],
            "maintenance_detail": None,
            "ranking_detail": None,
            "topic_angle_detail": None,
            "warnings": [],
        }
    else:
        warning = progress.error_message or progress.summary or progress.message
        state[_FLASH_KEY] = {
            "summary": progress.summary or "최신 데이터 수집·분석을 완료하지 못했습니다.",
            "source_details": [],
            "maintenance_detail": None,
            "ranking_detail": None,
            "topic_angle_detail": None,
            "warnings": [warning] if warning else [],
        }
    clear_dashboard_refresh_progress()


def _prepare_flash(pid: int, *, error: Exception | None = None) -> dict[str, Any]:
    if error is None:
        return {
            "summary": (
                "최신 데이터 수집·분석을 백그라운드에서 시작했습니다"
                + (f" · PID {pid}" if pid else "")
                + " · 앱을 계속 사용할 수 있으며 진행 게이지와 프로그램 로그에서 현재 단계를 확인할 수 있습니다."
            ),
            "source_details": [],
            "maintenance_detail": None,
            "ranking_detail": None,
            "topic_angle_detail": None,
            "warnings": [],
        }
    return {
        "summary": "최신 데이터 수집·분석 백그라운드 작업을 시작하지 못했습니다.",
        "source_details": [],
        "maintenance_detail": None,
        "ranking_detail": None,
        "topic_angle_detail": None,
        "warnings": [str(error)],
    }


def _install_page_config_progress_sync(st_module: Any) -> None:
    original = getattr(st_module, "set_page_config", None)
    if not callable(original) or getattr(original, "_dashboard_progress_sync", False):
        return

    @wraps(original)
    def wrapped(*args: Any, **kwargs: Any):
        sync_dashboard_background_progress(st_module)
        return original(*args, **kwargs)

    wrapped._dashboard_progress_sync = True  # type: ignore[attr-defined]
    st_module.set_page_config = wrapped


def _install_progress_fragment(st_module: Any) -> None:
    original = getattr(st_module, "progress", None)
    fragment = getattr(st_module, "fragment", None)
    if (
        not callable(original)
        or not callable(fragment)
        or getattr(original, "_dashboard_progress_fragment", False)
    ):
        return

    @wraps(original)
    def wrapped(*args: Any, **kwargs: Any):
        state = _state(st_module)
        progress_state = state.get(_PROGRESS_KEY) if state is not None else None
        if not (
            isinstance(progress_state, dict)
            and str(progress_state.get("status") or "") == "running"
        ):
            return original(*args, **kwargs)

        @fragment(run_every=2.5)
        def live_progress():
            current = read_dashboard_refresh_progress()
            if current is None:
                rendered = original(*args, **kwargs)
                current_state = _state(st_module)
                if current_state is not None and _should_request_full_app_rerun(
                    current_state,
                    now=monotonic(),
                ):
                    _request_full_app_rerun(st_module)
                return rendered
            if current.active:
                text = f"진행률 {current.value}% · {current.message or '수집 중'}"
                rendered = original(current.value, text=text)
                current_state = _state(st_module)
                if current_state is not None and _should_request_full_app_rerun(
                    current_state,
                    now=monotonic(),
                ):
                    _request_full_app_rerun(st_module)
                return rendered
            sync_dashboard_background_progress(st_module)
            _request_full_app_rerun(st_module)
            return None

        return live_progress()

    wrapped._dashboard_progress_fragment = True  # type: ignore[attr-defined]
    st_module.progress = wrapped


def install_dashboard_background_refresh(st_module: Any) -> None:
    """수집 버튼을 별도 프로세스로 실행하고 기존 게이지를 상태 파일로 이어 줍니다."""
    _install_page_config_progress_sync(st_module)
    _install_progress_fragment(st_module)

    original = getattr(st_module, "rerun", None)
    if not callable(original) or getattr(
        original,
        "_dashboard_background_refresh",
        False,
    ):
        return

    @wraps(original)
    def wrapped(*args: Any, **kwargs: Any):
        state = _state(st_module)
        if (
            state is not None
            and str(state.get(_ACTION_KEY) or "").strip() == "refresh"
            and not bool(state.get(_LAUNCH_GUARD_KEY))
        ):
            state[_LAUNCH_GUARD_KEY] = True
            try:
                pid = launch_dashboard_background_refresh()
                start_dashboard_refresh_progress(
                    pid=pid,
                    message="백그라운드 수집 프로세스 시작 중",
                )
                state[_PROGRESS_KEY] = {
                    "value": 1,
                    "message": "백그라운드 수집 프로세스 시작 중",
                    "status": "running",
                    "pid": pid,
                }
                state.pop(_FULL_APP_REFRESH_AT_KEY, None)
                state[_FLASH_KEY] = _prepare_flash(pid)
            except Exception as exc:
                finish_dashboard_refresh_progress(
                    success=False,
                    message="백그라운드 수집 프로세스 시작 실패",
                    summary="최신 데이터 수집·분석 백그라운드 작업을 시작하지 못했습니다.",
                    error_message=str(exc),
                )
                state.pop(_PROGRESS_KEY, None)
                state.pop(_FULL_APP_REFRESH_AT_KEY, None)
                state[_FLASH_KEY] = _prepare_flash(0, error=exc)
            finally:
                state.pop(_ACTION_KEY, None)
                state.pop(_MODEL_KEY, None)
                state.pop(_LAUNCH_GUARD_KEY, None)
        return original(*args, **kwargs)

    wrapped._dashboard_background_refresh = True  # type: ignore[attr-defined]
    st_module.rerun = wrapped
