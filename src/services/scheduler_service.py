from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from math import ceil
from pathlib import Path
import csv
import platform
import re
import subprocess
import xml.etree.ElementTree as ET


TASK_NAME = "ContentTrendTracker_AutoRefresh"
MIN_SCHEDULE_INTERVAL_MINUTES = 5
MAX_SCHEDULE_INTERVAL_MINUTES = 1439
DEFAULT_MAX_RETRIES = 2


@dataclass(frozen=True)
class SchedulerStatus:
    supported: bool
    registered: bool
    interval_minutes: int | None = None
    next_run: str = ""
    state: str = ""
    action_matches_project: bool | None = None
    wake_to_run: bool | None = None
    start_when_available: bool | None = None
    message: str = ""


@dataclass(frozen=True)
class SchedulerCommandResult:
    success: bool
    message: str


@dataclass(frozen=True)
class QuotaIntervalRecommendation:
    planned_calls_per_run: int
    retry_worst_calls_per_run: int
    normal_min_interval_minutes: int
    retry_safe_min_interval_minutes: int

    def runs_per_day(self, interval_minutes: int) -> int:
        return ceil(1440 / max(1, int(interval_minutes)))

    def planned_calls_per_day(self, interval_minutes: int) -> int:
        return self.runs_per_day(interval_minutes) * self.planned_calls_per_run

    def retry_worst_calls_per_day(self, interval_minutes: int) -> int:
        return self.runs_per_day(interval_minutes) * self.retry_worst_calls_per_run


def calculate_quota_interval_recommendation(
    *,
    portal_query_limit: int,
    portal_pages_per_query: int,
    naver_daily_limit: int,
    kakao_daily_limit: int,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> QuotaIntervalRecommendation:
    query_count = max(0, int(portal_query_limit))
    page_count = max(1, int(portal_pages_per_query))
    planned_calls = query_count * 2 * page_count
    retry_multiplier = max(1, 1 + int(max_retries))
    retry_worst_calls = planned_calls * retry_multiplier

    normal_interval = _minimum_interval_for_limits(
        calls_per_run=planned_calls,
        daily_limits=(naver_daily_limit, kakao_daily_limit),
    )
    retry_safe_interval = _minimum_interval_for_limits(
        calls_per_run=retry_worst_calls,
        daily_limits=(naver_daily_limit, kakao_daily_limit),
    )
    return QuotaIntervalRecommendation(
        planned_calls_per_run=planned_calls,
        retry_worst_calls_per_run=retry_worst_calls,
        normal_min_interval_minutes=normal_interval,
        retry_safe_min_interval_minutes=retry_safe_interval,
    )


def _minimum_interval_for_limits(*, calls_per_run: int, daily_limits: tuple[int, ...]) -> int:
    if calls_per_run <= 0:
        return MIN_SCHEDULE_INTERVAL_MINUTES
    valid_limits = [max(0, int(value)) for value in daily_limits if int(value) > 0]
    if not valid_limits:
        return MAX_SCHEDULE_INTERVAL_MINUTES
    max_runs = min(limit // calls_per_run for limit in valid_limits)
    if max_runs <= 0:
        return MAX_SCHEDULE_INTERVAL_MINUTES
    return max(MIN_SCHEDULE_INTERVAL_MINUTES, min(MAX_SCHEDULE_INTERVAL_MINUTES, ceil(1440 / max_runs)))


def get_refresh_scheduler_status(project_root: str | Path) -> SchedulerStatus:
    if platform.system() != "Windows":
        return SchedulerStatus(
            supported=False,
            registered=False,
            message="Windows 작업 스케줄러에서만 사용할 수 있습니다.",
        )

    query_result = _run_schtasks(["/Query", "/TN", TASK_NAME, "/FO", "CSV", "/NH"])
    if query_result.returncode != 0:
        return SchedulerStatus(supported=True, registered=False, message="등록된 자동 수집 작업이 없습니다.")

    next_run = ""
    state = ""
    try:
        rows = list(csv.reader(StringIO(query_result.stdout.strip())))
        if rows and len(rows[0]) >= 3:
            next_run = str(rows[0][1] or "").strip()
            state = str(rows[0][2] or "").strip()
    except csv.Error:
        pass

    xml_result = _run_schtasks(["/Query", "/TN", TASK_NAME, "/XML"])
    interval_minutes: int | None = None
    action_matches: bool | None = None
    wake_to_run: bool | None = None
    start_when_available: bool | None = None
    if xml_result.returncode == 0 and xml_result.stdout.strip():
        interval_minutes, task_action = _parse_task_xml(xml_result.stdout)
        wake_to_run, start_when_available = _parse_task_runtime_settings(xml_result.stdout)
        if task_action:
            expected_batch = str(Path(project_root).resolve() / "run_trend_refresh.bat").casefold()
            action_matches = expected_batch in task_action.casefold()

    return SchedulerStatus(
        supported=True,
        registered=True,
        interval_minutes=interval_minutes,
        next_run=next_run,
        state=state,
        action_matches_project=action_matches,
        wake_to_run=wake_to_run,
        start_when_available=start_when_available,
        message="자동 수집 작업이 등록되어 있습니다.",
    )


def register_or_update_refresh_scheduler(
    project_root: str | Path,
    *,
    interval_minutes: int,
) -> SchedulerCommandResult:
    if platform.system() != "Windows":
        return SchedulerCommandResult(False, "Windows 작업 스케줄러에서만 등록할 수 있습니다.")

    interval = int(interval_minutes)
    if not MIN_SCHEDULE_INTERVAL_MINUTES <= interval <= MAX_SCHEDULE_INTERVAL_MINUTES:
        return SchedulerCommandResult(
            False,
            f"수집 간격은 {MIN_SCHEDULE_INTERVAL_MINUTES}~{MAX_SCHEDULE_INTERVAL_MINUTES}분이어야 합니다.",
        )

    root = Path(project_root).resolve()
    batch_path = root / "run_trend_refresh.bat"
    if not batch_path.is_file():
        return SchedulerCommandResult(False, f"실행 파일을 찾을 수 없습니다: {batch_path}")

    escaped_batch_path = str(batch_path).replace("'", "''")
    task_action = (
        "powershell.exe -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden "
        f"-ExecutionPolicy Bypass -Command \"& '{escaped_batch_path}'\""
    )
    result = _run_schtasks(
        [
            "/Create",
            "/TN",
            TASK_NAME,
            "/TR",
            task_action,
            "/SC",
            "MINUTE",
            "/MO",
            str(interval),
            "/IT",
            "/RL",
            "LIMITED",
            "/F",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "알 수 없는 오류"
        return SchedulerCommandResult(
            False,
            "작업 스케줄러 등록에 실패했습니다. 앱을 관리자 권한으로 다시 실행해야 할 수 있습니다. "
            f"세부 내용: {detail}",
        )

    settings_result = _enable_sleep_aware_task_settings()
    if settings_result.returncode != 0:
        detail = settings_result.stderr.strip() or settings_result.stdout.strip() or "알 수 없는 오류"
        return SchedulerCommandResult(
            False,
            "자동 수집 작업은 등록됐지만 절전 대응 설정을 적용하지 못했습니다. "
            f"세부 내용: {detail}",
        )

    settings_check = _run_schtasks(["/Query", "/TN", TASK_NAME, "/XML"])
    if settings_check.returncode != 0 or not settings_check.stdout.strip():
        detail = settings_check.stderr.strip() or settings_check.stdout.strip() or "작업 XML 조회 실패"
        return SchedulerCommandResult(
            False,
            "자동 수집 작업은 등록됐지만 절전 대응 설정을 확인하지 못했습니다. "
            f"세부 내용: {detail}",
        )
    wake_to_run, start_when_available = _parse_task_runtime_settings(settings_check.stdout)
    if wake_to_run is not True or start_when_available is not True:
        return SchedulerCommandResult(
            False,
            "자동 수집 작업은 등록됐지만 WakeToRun/StartWhenAvailable 설정이 활성화되지 않았습니다.",
        )

    return SchedulerCommandResult(
        True,
        (
            f"자동 수집 작업을 {interval}분 간격으로 등록·갱신했습니다. "
            "절전 깨우기와 놓친 예약의 가능한 시점 실행을 사용합니다. "
            f"첫 실행은 등록 시점에서 약 {interval}분 뒤입니다."
        ),
    )


def delete_refresh_scheduler() -> SchedulerCommandResult:
    if platform.system() != "Windows":
        return SchedulerCommandResult(False, "Windows 작업 스케줄러에서만 삭제할 수 있습니다.")

    result = _run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "등록된 작업 없음"
        return SchedulerCommandResult(False, f"자동 수집 작업을 삭제하지 못했습니다. 세부 내용: {detail}")
    return SchedulerCommandResult(True, "자동 수집 작업을 삭제했습니다.")


def _enable_sleep_aware_task_settings() -> subprocess.CompletedProcess[str]:
    escaped_task_name = TASK_NAME.replace("'", "''")
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"$task = Get-ScheduledTask -TaskName '{escaped_task_name}' -TaskPath '\\' -ErrorAction Stop; "
        "$task.Settings.WakeToRun = $true; "
        "$task.Settings.StartWhenAvailable = $true; "
        "Set-ScheduledTask -InputObject $task -ErrorAction Stop | Out-Null"
    )
    return _run_powershell(script)


def _run_schtasks(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["schtasks", *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return subprocess.CompletedProcess(
        args=completed.args,
        returncode=completed.returncode,
        stdout=_decode_windows_output(completed.stdout),
        stderr=_decode_windows_output(completed.stderr),
    )


def _run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return subprocess.CompletedProcess(
        args=completed.args,
        returncode=completed.returncode,
        stdout=_decode_windows_output(completed.stdout),
        stderr=_decode_windows_output(completed.stderr),
    )


def _decode_windows_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if value.startswith((b"\xff\xfe", b"\xfe\xff")):
        return value.decode("utf-16", errors="replace")
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


def _parse_task_xml(xml_text: str) -> tuple[int | None, str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None, ""

    interval_text = _find_first_text(root, "Interval")
    command = _find_first_text(root, "Command")
    arguments = _find_first_text(root, "Arguments")
    return _parse_iso_duration_minutes(interval_text), f"{command} {arguments}".strip()


def _parse_task_runtime_settings(xml_text: str) -> tuple[bool | None, bool | None]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None, None
    return (
        _parse_xml_bool(_find_first_text(root, "WakeToRun")),
        _parse_xml_bool(_find_first_text(root, "StartWhenAvailable")),
    )


def _parse_xml_bool(value: str) -> bool | None:
    normalized = str(value or "").strip().casefold()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    return None


def _find_first_text(root: ET.Element, local_name: str) -> str:
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == local_name:
            return str(element.text or "").strip()
    return ""


def _parse_iso_duration_minutes(value: str) -> int | None:
    match = re.fullmatch(
        r"P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
        str(value or "").strip(),
    )
    if not match:
        return None
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    total_minutes = days * 1440 + hours * 60 + minutes + (1 if seconds else 0)
    return total_minutes or None
