from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from src.config import PROJECT_ROOT
from src.services import web_update_service as update_service

STARTUP_TIMEOUT_SECONDS = 8.0
STARTUP_POLL_SECONDS = 0.10
MANAGED_APP_PORT = 8518
STARTED_STATES = frozenset(
    {
        "waiting_for_app",
        "checking",
        "applying",
        "restarting",
        "success",
    }
)
FAILED_STATES = frozenset(
    {
        "failed",
        "failed_restarted",
        "failed_restart_required",
    }
)


def update_bootstrap_log_path() -> Path:
    return update_service.update_state_directory() / "update_restart_bootstrap.log"


def app_runtime_state_path() -> Path:
    configured = str(os.environ.get("CONTENT_TREND_TRACKER_RUNTIME_STATE") or "").strip()
    return Path(configured) if configured else (
        update_service.update_state_directory() / "app_runtime.json"
    )


def app_update_request_path() -> Path:
    configured = str(os.environ.get("CONTENT_TREND_TRACKER_UPDATE_REQUEST") or "").strip()
    return Path(configured) if configured else (
        update_service.update_state_directory() / "app_update_request.json"
    )


def _bootstrap_log_tail(path: Path, *, maximum_chars: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max(200, int(maximum_chars)) :].strip()


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _positive_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _managed_runtime_state(
    *,
    project_root: Path,
    parent_pid: int,
) -> dict[str, Any]:
    state_path = app_runtime_state_path().resolve()
    state = _read_json_object(state_path)
    if not state:
        raise RuntimeError(
            "웹 업데이트는 run_app.bat으로 실행한 관리형 앱에서만 사용할 수 있습니다. "
            "앱을 종료한 뒤 run_app.bat으로 다시 실행하세요."
        )

    try:
        managed_root = Path(str(state.get("project_root") or "")).resolve()
    except (OSError, ValueError, TypeError):
        managed_root = Path()
    if managed_root != project_root.resolve():
        raise RuntimeError("관리형 앱의 프로젝트 경로가 현재 저장소와 다릅니다.")

    supervisor_pid = _positive_int(state.get("supervisor_pid"))
    supervisor_start_ticks = _positive_int(state.get("supervisor_start_ticks"))
    streamlit_pid = _positive_int(state.get("streamlit_pid"))
    streamlit_start_ticks = _positive_int(state.get("streamlit_start_ticks"))
    port = _positive_int(state.get("port"))
    if streamlit_pid != int(parent_pid):
        raise RuntimeError(
            "현재 Streamlit 프로세스가 앱 관리자에 등록된 인스턴스와 다릅니다. "
            "중복 실행을 종료한 뒤 run_app.bat으로 다시 실행하세요."
        )
    if supervisor_pid <= 0 or supervisor_start_ticks <= 0:
        raise RuntimeError("앱 관리자 프로세스 정보를 확인하지 못했습니다.")
    if streamlit_start_ticks <= 0:
        raise RuntimeError("현재 Streamlit 프로세스 시작 정보를 확인하지 못했습니다.")
    if port != MANAGED_APP_PORT:
        raise RuntimeError(
            f"관리형 앱 포트가 {MANAGED_APP_PORT}이 아닙니다: {port}"
        )

    environment_supervisor = _positive_int(
        os.environ.get("CONTENT_TREND_TRACKER_SUPERVISOR_PID")
    )
    environment_supervisor_ticks = _positive_int(
        os.environ.get("CONTENT_TREND_TRACKER_SUPERVISOR_START_TICKS")
    )
    environment_port = _positive_int(
        os.environ.get("CONTENT_TREND_TRACKER_APP_PORT")
    )
    if environment_supervisor and environment_supervisor != supervisor_pid:
        raise RuntimeError("앱 관리자 PID가 실행 환경과 상태 파일에서 다릅니다.")
    if (
        environment_supervisor_ticks
        and environment_supervisor_ticks != supervisor_start_ticks
    ):
        raise RuntimeError("앱 관리자 시작 정보가 실행 환경과 상태 파일에서 다릅니다.")
    if environment_port and environment_port != port:
        raise RuntimeError("앱 포트가 실행 환경과 상태 파일에서 다릅니다.")

    return {
        **state,
        "runtime_state_path": str(state_path),
        "update_request_path": str(app_update_request_path().resolve()),
        "supervisor_pid": supervisor_pid,
        "supervisor_start_ticks": supervisor_start_ticks,
        "streamlit_pid": streamlit_pid,
        "streamlit_start_ticks": streamlit_start_ticks,
        "port": port,
    }


def _stop_worker(process: Any) -> None:
    try:
        if process.poll() is not None:
            return
    except Exception:
        return
    try:
        process.terminate()
        process.wait(timeout=2)
        return
    except Exception:
        pass
    try:
        process.kill()
    except Exception:
        pass


def _wait_for_worker_start(
    process: Any,
    *,
    bootstrap_log: Path,
    timeout_seconds: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    deadline = monotonic() + max(1.0, float(timeout_seconds))
    while monotonic() < deadline:
        status = update_service.read_update_status()
        state = str(status.get("status") or "").strip()
        if state in STARTED_STATES:
            return
        if state in FAILED_STATES:
            message = str(status.get("message") or "").strip()
            detail = _bootstrap_log_tail(bootstrap_log)
            raise RuntimeError(
                message
                or detail
                or "업데이트 요청 프로세스가 시작 단계에서 실패했습니다."
            )
        try:
            exit_code = process.poll()
        except Exception:
            exit_code = None
        if exit_code is not None:
            detail = _bootstrap_log_tail(bootstrap_log)
            raise RuntimeError(
                "업데이트 요청 프로세스가 시작 확인 전에 종료됐습니다"
                f"(종료 코드 {exit_code})."
                + (f"\n{detail}" if detail else "")
            )
        sleep(STARTUP_POLL_SECONDS)

    _stop_worker(process)
    detail = _bootstrap_log_tail(bootstrap_log)
    raise RuntimeError(
        "업데이트 요청 프로세스가 제한 시간 안에 시작 상태를 기록하지 못했습니다."
        + (f"\n{detail}" if detail else "")
    )


def launch_update_and_restart_verified(
    candidate: update_service.WorkBranchCandidate,
    project_root: str | Path = PROJECT_ROOT,
    *,
    parent_pid: int | None = None,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    powershell_executable: str | None = None,
    startup_timeout_seconds: float = STARTUP_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """관리형 Streamlit 종료 요청을 전달하고 supervisor 시작 확인 후 반환합니다."""
    root = Path(project_root).resolve()
    branch_name = update_service.validate_work_branch_name(candidate.branch_name)
    expected_sha = str(candidate.commit_sha or "").strip().casefold()
    if not update_service.re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("적용 대상 커밋 SHA가 올바르지 않습니다.")

    script = root / "scripts" / "apply_update_and_restart.ps1"
    if not script.is_file():
        raise RuntimeError(f"웹 업데이트 요청 스크립트를 찾을 수 없습니다: {script}")

    process_id = int(parent_pid or os.getpid())
    runtime = _managed_runtime_state(
        project_root=root,
        parent_pid=process_id,
    )
    status_path = update_service.update_status_path()
    apply_log_path = update_service.update_log_path()
    bootstrap_log = update_bootstrap_log_path()
    bootstrap_log.parent.mkdir(parents=True, exist_ok=True)

    status_payload = {
        "status": "requested",
        "stage": "launch",
        "branch_name": branch_name,
        "expected_sha": expected_sha,
        "message": "앱 관리자에게 업데이트 적용과 재시작을 요청하고 있습니다.",
        "supervisor_pid": runtime["supervisor_pid"],
        "streamlit_pid": process_id,
        "port": runtime["port"],
    }
    update_service.write_update_status(status_payload)

    command = [
        powershell_executable or update_service._powershell_executable(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-ProjectRoot",
        str(root),
        "-BranchName",
        branch_name,
        "-ExpectedSha",
        expected_sha,
        "-ParentPid",
        str(process_id),
        "-ParentStartTicks",
        str(runtime["streamlit_start_ticks"]),
        "-SupervisorPid",
        str(runtime["supervisor_pid"]),
        "-SupervisorStartTicks",
        str(runtime["supervisor_start_ticks"]),
        "-AppPort",
        str(runtime["port"]),
        "-RuntimeStatePath",
        str(runtime["runtime_state_path"]),
        "-RequestPath",
        str(runtime["update_request_path"]),
        "-StatusPath",
        str(status_path),
        "-LogPath",
        str(apply_log_path),
    ]

    creationflags = 0
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )

    try:
        with bootstrap_log.open("w", encoding="utf-8", errors="replace") as output:
            process = popen_factory(
                command,
                cwd=str(root),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                close_fds=False,
                shell=False,
                creationflags=creationflags,
            )
        _wait_for_worker_start(
            process,
            bootstrap_log=bootstrap_log,
            timeout_seconds=startup_timeout_seconds,
            monotonic=monotonic,
            sleep=sleep,
        )
    except Exception as exc:
        update_service.write_update_status(
            {
                **status_payload,
                "status": "failed",
                "stage": "launch_failed",
                "message": f"업데이트 요청 프로세스를 시작하지 못했습니다: {exc}",
            }
        )
        raise
    return int(process.pid)


def install_web_update_launch_contract() -> None:
    current = getattr(update_service, "launch_update_and_restart", None)
    if getattr(current, "_verified_web_update_launcher", False):
        return
    launch_update_and_restart_verified._verified_web_update_launcher = True  # type: ignore[attr-defined]
    update_service.launch_update_and_restart = launch_update_and_restart_verified
