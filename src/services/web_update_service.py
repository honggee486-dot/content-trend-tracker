from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from src.config import DEFAULT_DB_PATH, PROJECT_ROOT
from src.database import connect_database

EXPECTED_REPOSITORY = "honggee486-dot/content-trend-tracker"
DEFAULT_BRANCH = "main"
WORK_BRANCH_PATTERN = re.compile(
    r"^work/(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?P<suffix>(?:[-._][A-Za-z0-9][A-Za-z0-9._-]*)?)$"
)
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
UPDATE_ACTIVE_STATUSES = frozenset(
    {"requested", "waiting_for_app", "checking", "applying", "restarting"}
)
UPDATE_STATUS_MAX_AGE_SECONDS = 30 * 60


@dataclass(frozen=True)
class WorkBranchCandidate:
    branch_name: str
    remote_ref: str
    commit_sha: str
    committed_at: str
    ahead: int
    behind: int
    changed_files: int
    eligible: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkBranchCandidate":
        return cls(
            branch_name=str(value.get("branch_name") or ""),
            remote_ref=str(value.get("remote_ref") or ""),
            commit_sha=str(value.get("commit_sha") or ""),
            committed_at=str(value.get("committed_at") or ""),
            ahead=int(value.get("ahead") or 0),
            behind=int(value.get("behind") or 0),
            changed_files=int(value.get("changed_files") or 0),
            eligible=bool(value.get("eligible")),
            reason=str(value.get("reason") or ""),
        )


@dataclass(frozen=True)
class UpdateReadiness:
    ready: bool
    blockers: tuple[str, ...]
    current_branch: str
    current_sha: str
    already_applied: bool


@dataclass(frozen=True)
class CommandOutput:
    stdout: str
    stderr: str
    returncode: int


CommandRunner = Callable[[Path, Sequence[str], int], CommandOutput]


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _run_command(
    project_root: Path,
    arguments: Sequence[str],
    timeout_seconds: int = 90,
) -> CommandOutput:
    completed = subprocess.run(
        [str(value) for value in arguments],
        cwd=str(project_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(1, int(timeout_seconds)),
        check=False,
        shell=False,
        creationflags=_creation_flags(),
    )
    result = CommandOutput(
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        returncode=int(completed.returncode),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "출력 없음"
        raise RuntimeError(
            f"명령이 실패했습니다({result.returncode}): "
            f"{' '.join(str(value) for value in arguments)}\n{detail}"
        )
    return result


def _repository_slug(remote_url: str) -> str:
    match = re.search(
        r"(?i)github\.com[/:](?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?$",
        str(remote_url or "").strip(),
    )
    return str(match.group("slug") if match else "")


def validate_work_branch_name(value: object) -> str:
    branch_name = str(value or "").strip()
    if branch_name.startswith("origin/"):
        branch_name = branch_name[7:]
    if not WORK_BRANCH_PATTERN.fullmatch(branch_name):
        raise ValueError(f"허용되지 않는 작업 브랜치명입니다: {branch_name}")
    return branch_name


def _parse_committed_at(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def work_branch_sort_key(candidate: WorkBranchCandidate) -> tuple[Any, ...]:
    match = WORK_BRANCH_PATTERN.fullmatch(candidate.branch_name)
    version = (
        (
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
        )
        if match
        else (-1, -1, -1)
    )
    return (
        *version,
        _parse_committed_at(candidate.committed_at),
        candidate.branch_name.casefold(),
    )


def _parse_ahead_behind(value: str) -> tuple[int, int]:
    parts = str(value or "").strip().split()
    if len(parts) != 2:
        raise RuntimeError(f"ahead/behind 값을 해석하지 못했습니다: {value}")
    behind, ahead = (int(parts[0]), int(parts[1]))
    return ahead, behind


def discover_work_branches(
    project_root: str | Path = PROJECT_ROOT,
    *,
    runner: CommandRunner | None = None,
) -> list[WorkBranchCandidate]:
    root = Path(project_root).resolve()
    active_runner = runner or _run_command
    if not (root / ".git").exists():
        raise RuntimeError("프로젝트 Git 저장소를 찾을 수 없습니다.")

    remote_url = active_runner(
        root,
        ("git", "remote", "get-url", "origin"),
        30,
    ).stdout.strip()
    if _repository_slug(remote_url).casefold() != EXPECTED_REPOSITORY.casefold():
        raise RuntimeError(
            f"origin이 예상 저장소 {EXPECTED_REPOSITORY}와 일치하지 않습니다."
        )

    active_runner(
        root,
        (
            "git",
            "fetch",
            "--no-tags",
            "--prune",
            "origin",
            "+refs/heads/main:refs/remotes/origin/main",
            "+refs/heads/work/*:refs/remotes/origin/work/*",
        ),
        180,
    )
    rows = active_runner(
        root,
        (
            "git",
            "for-each-ref",
            "--format=%(refname:short)\t%(objectname)\t%(committerdate:iso-strict)",
            "refs/remotes/origin/work",
        ),
        60,
    ).stdout.splitlines()

    candidates: list[WorkBranchCandidate] = []
    for raw_row in rows:
        parts = raw_row.split("\t", 2)
        if len(parts) != 3:
            continue
        remote_ref, commit_sha, committed_at = (part.strip() for part in parts)
        try:
            branch_name = validate_work_branch_name(remote_ref)
        except ValueError:
            continue
        ahead, behind = _parse_ahead_behind(
            active_runner(
                root,
                (
                    "git",
                    "rev-list",
                    "--left-right",
                    "--count",
                    f"origin/{DEFAULT_BRANCH}...origin/{branch_name}",
                ),
                60,
            ).stdout
        )
        changed_output = active_runner(
            root,
            (
                "git",
                "diff",
                "--name-only",
                "--diff-filter=ACDMRT",
                f"origin/{DEFAULT_BRANCH}",
                f"origin/{branch_name}",
            ),
            60,
        ).stdout
        changed_files = len(
            [line for line in changed_output.splitlines() if line.strip()]
        )
        eligible = behind == 0 and ahead >= 1
        reason = ""
        if behind:
            reason = f"origin/main보다 {behind}개 커밋 뒤처져 있습니다."
        elif ahead < 1:
            reason = "origin/main보다 앞선 변경이 없습니다."
        candidates.append(
            WorkBranchCandidate(
                branch_name=branch_name,
                remote_ref=f"origin/{branch_name}",
                commit_sha=commit_sha,
                committed_at=committed_at,
                ahead=ahead,
                behind=behind,
                changed_files=changed_files,
                eligible=eligible,
                reason=reason,
            )
        )
    return sorted(candidates, key=work_branch_sort_key, reverse=True)


def _header_value(headers: Mapping[str, Any], name: str) -> str:
    target = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == target:
            return str(value or "").strip()
    return ""


def _host_without_port(value: str) -> str:
    text = str(value or "").strip().casefold()
    if text.startswith("[") and "]" in text:
        return text[1 : text.index("]")]
    if text.count(":") == 1:
        return text.split(":", 1)[0]
    return text


def is_local_request(headers: Mapping[str, Any] | None) -> bool:
    values = headers or {}
    host = _host_without_port(_header_value(values, "host"))
    if host not in LOCAL_HOSTS:
        return False
    for header_name in ("x-forwarded-for", "x-real-ip"):
        remote = _header_value(values, header_name).split(",", 1)[0].strip()
        if remote and _host_without_port(remote) not in LOCAL_HOSTS:
            return False
    return True


def update_state_directory() -> Path:
    base = str(os.environ.get("LOCALAPPDATA") or "").strip()
    root = Path(base) if base else Path(tempfile.gettempdir())
    return root / "content-trend-tracker"


def update_status_path() -> Path:
    return update_state_directory() / "update_restart_status.json"


def update_log_path() -> Path:
    return update_state_directory() / "update_restart.log"


def write_update_status(payload: Mapping[str, Any]) -> Path:
    path = update_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data.setdefault(
        "updated_at",
        datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    temporary = path.with_suffix(f".tmp.{os.getpid()}.json")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def read_update_status() -> dict[str, Any]:
    path = update_status_path()
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _active_update_status() -> str:
    status = read_update_status()
    state = str(status.get("status") or "")
    if state not in UPDATE_ACTIVE_STATUSES:
        return ""
    updated_at = _parse_committed_at(str(status.get("updated_at") or ""))
    age = datetime.now(timezone.utc) - updated_at
    return state if age.total_seconds() <= UPDATE_STATUS_MAX_AGE_SECONDS else ""


def _query_count(con: Any, sql: str) -> int:
    try:
        row = con.execute(sql).fetchone()
    except Exception:
        return 0
    return int(row[0] or 0) if row else 0


def runtime_update_blockers(
    project_root: str | Path = PROJECT_ROOT,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> tuple[str, ...]:
    root = Path(project_root).resolve()
    blockers: list[str] = []
    for name, label in (
        ("trend_refresh.lock", "수집 작업"),
        ("trend_clustering.lock", "2단계 군집 작업"),
    ):
        if (root / "data" / name).exists():
            blockers.append(f"{label} 잠금 파일이 존재합니다.")

    database = Path(db_path).resolve()
    if database.is_file():
        try:
            # 설정 화면이 이미 같은 파일의 읽기·쓰기 연결을 사용하므로 동일 모드로
            # 짧게 조회합니다. DuckDB는 한 프로세스에서 read_only 혼용을 지원하지 않습니다.
            with connect_database(database) as con:
                if _query_count(
                    con,
                    "SELECT COUNT(*) FROM trend_clustering_jobs "
                    "WHERE status IN ('queued', 'running')",
                ):
                    blockers.append("2단계 군집 작업이 실행 중입니다.")
                if _query_count(
                    con,
                    "SELECT COUNT(*) FROM collection_runs "
                    "WHERE status = 'running' AND finished_at IS NULL",
                ):
                    blockers.append("데이터 수집 작업이 실행 중입니다.")
                if _query_count(
                    con,
                    "SELECT COUNT(*) FROM sync_runs "
                    "WHERE status = 'running' AND finished_at IS NULL",
                ):
                    blockers.append("출처 동기화 작업이 실행 중입니다.")
        except Exception as exc:
            blockers.append(f"DB 작업 상태를 안전하게 확인하지 못했습니다: {exc}")

    if _active_update_status():
        blockers.append("다른 웹 업데이트 작업이 이미 진행 중입니다.")
    return tuple(dict.fromkeys(blockers))


def check_update_readiness(
    candidate: WorkBranchCandidate,
    project_root: str | Path = PROJECT_ROOT,
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    runner: CommandRunner | None = None,
) -> UpdateReadiness:
    root = Path(project_root).resolve()
    active_runner = runner or _run_command
    blockers: list[str] = []
    if not candidate.eligible:
        blockers.append(candidate.reason or "적용할 수 없는 작업 브랜치입니다.")
    if not (root / "apply_update.bat").is_file():
        blockers.append("apply_update.bat을 찾을 수 없습니다.")
    if not (root / "scripts" / "apply_update_and_restart.ps1").is_file():
        blockers.append("웹 업데이트 재시작 스크립트를 찾을 수 없습니다.")

    status = active_runner(
        root,
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        30,
    ).stdout
    if status.strip():
        blockers.append("로컬 미커밋 또는 미추적 변경이 있습니다.")

    try:
        current_branch = active_runner(
            root,
            ("git", "symbolic-ref", "--quiet", "--short", "HEAD"),
            30,
        ).stdout.strip()
    except RuntimeError:
        current_branch = ""
        blockers.append("현재 로컬 브랜치를 확인할 수 없습니다.")
    current_sha = active_runner(
        root,
        ("git", "rev-parse", "HEAD^{commit}"),
        30,
    ).stdout.strip()
    already_applied = (
        current_branch == candidate.branch_name
        and current_sha.casefold() == candidate.commit_sha.casefold()
    )
    if already_applied:
        blockers.append("선택한 작업 브랜치의 최신 커밋이 이미 적용되어 있습니다.")

    blockers.extend(runtime_update_blockers(root, db_path))
    unique_blockers = tuple(dict.fromkeys(blockers))
    return UpdateReadiness(
        ready=not unique_blockers,
        blockers=unique_blockers,
        current_branch=current_branch,
        current_sha=current_sha,
        already_applied=already_applied,
    )


def _powershell_executable() -> str:
    executable = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if executable:
        return executable
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if executable:
        return executable
    raise RuntimeError("PowerShell 7 또는 Windows PowerShell 5.1을 찾을 수 없습니다.")


def launch_update_and_restart(
    candidate: WorkBranchCandidate,
    project_root: str | Path = PROJECT_ROOT,
    *,
    parent_pid: int | None = None,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    powershell_executable: str | None = None,
) -> int:
    root = Path(project_root).resolve()
    branch_name = validate_work_branch_name(candidate.branch_name)
    expected_sha = str(candidate.commit_sha or "").strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("적용 대상 커밋 SHA가 올바르지 않습니다.")
    script = root / "scripts" / "apply_update_and_restart.ps1"
    if not script.is_file():
        raise RuntimeError(f"웹 업데이트 스크립트를 찾을 수 없습니다: {script}")

    process_id = int(parent_pid or os.getpid())
    status_path = update_status_path()
    log_path = update_log_path()
    status_payload = {
        "status": "requested",
        "stage": "launch",
        "branch_name": branch_name,
        "expected_sha": expected_sha,
        "message": "업데이트 적용과 앱 재시작을 요청했습니다.",
    }
    write_update_status(status_payload)
    command = [
        powershell_executable or _powershell_executable(),
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
        "-StatusPath",
        str(status_path),
        "-LogPath",
        str(log_path),
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            int(getattr(subprocess, "DETACHED_PROCESS", 0))
            | int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            | int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        )
    try:
        process = popen_factory(
            command,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            shell=False,
            creationflags=creationflags,
        )
    except Exception as exc:
        write_update_status(
            {
                **status_payload,
                "status": "failed",
                "stage": "launch_failed",
                "message": f"업데이트 전용 프로세스를 시작하지 못했습니다: {exc}",
            }
        )
        raise
    return int(process.pid)


def serialize_candidates(
    candidates: Iterable[WorkBranchCandidate],
) -> list[dict[str, Any]]:
    return [candidate.to_dict() for candidate in candidates]


def deserialize_candidates(
    values: Iterable[Mapping[str, Any]],
) -> list[WorkBranchCandidate]:
    return [WorkBranchCandidate.from_dict(value) for value in values]
