from __future__ import annotations

from pathlib import Path
import platform
from typing import Callable


ProcessAliveCheck = Callable[[int], bool]
ProcessIdentityReader = Callable[[int], str]


def get_process_start_identity(process_id: int) -> str:
    """PID 재사용을 구분할 수 있는 프로세스 시작 식별자를 최선형으로 반환합니다."""
    pid = int(process_id or 0)
    if pid <= 0:
        return ""
    system = platform.system()
    if system == "Windows":
        return _get_windows_process_start_identity(pid)
    if system == "Linux":
        return _get_linux_process_start_identity(pid)
    return ""


def process_identity_matches(
    process_id: int,
    expected_identity: str,
    *,
    is_process_alive: ProcessAliveCheck,
    identity_reader: ProcessIdentityReader | None = None,
) -> bool:
    """기존 잠금은 호환하고, 식별자가 있으면 PID와 시작 식별자를 함께 확인합니다."""
    pid = int(process_id or 0)
    if pid <= 0 or not is_process_alive(pid):
        return False

    expected = str(expected_identity or "").strip()
    if not expected:
        # 이전 형식의 잠금은 PID 생존 여부만으로 계속 읽습니다.
        return True

    reader = identity_reader or get_process_start_identity
    try:
        actual = str(reader(pid) or "").strip()
    except Exception:
        actual = ""
    if not actual:
        # 권한·플랫폼 제약으로 확인하지 못한 경우 살아 있는 작업을 잘못 지우지 않습니다.
        return True
    return actual == expected


def _get_windows_process_start_identity(process_id: int) -> str:
    try:
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        get_process_times = kernel32.GetProcessTimes
        get_process_times.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        get_process_times.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = open_process(
            process_query_limited_information,
            False,
            int(process_id),
        )
        if not handle:
            return ""
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not get_process_times(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return ""
            ticks = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
            return f"windows-filetime:{ticks}" if ticks > 0 else ""
        finally:
            close_handle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return ""


def _get_linux_process_start_identity(process_id: int) -> str:
    try:
        stat_text = Path(f"/proc/{int(process_id)}/stat").read_text(
            encoding="utf-8"
        )
        closing_parenthesis = stat_text.rfind(")")
        if closing_parenthesis < 0:
            return ""
        fields_after_command = stat_text[closing_parenthesis + 2 :].split()
        # /proc/<pid>/stat의 22번째 필드(starttime)는 여기서 인덱스 19입니다.
        start_ticks = int(fields_after_command[19])
        return f"linux-start-ticks:{start_ticks}" if start_ticks > 0 else ""
    except (FileNotFoundError, OSError, IndexError, TypeError, ValueError):
        return ""
