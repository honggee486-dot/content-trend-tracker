from __future__ import annotations

from functools import wraps
from typing import Any

from src.config import PROJECT_ROOT
from src.services.scheduler_service import SchedulerStatus, get_refresh_scheduler_status


_SCHEDULER_HEADING = "#### 예약 실행 설정"


def _setting_label(value: bool | None) -> str:
    if value is True:
        return "사용"
    if value is False:
        return "사용 안 함"
    return "확인 불가"


def build_scheduler_wake_status_text(status: SchedulerStatus) -> str:
    """Windows 예약 작업의 절전 대응 상태를 짧은 화면 문구로 만듭니다."""
    if not status.supported:
        return "절전 대응 · Windows 작업 스케줄러에서만 확인할 수 있습니다."
    if not status.registered:
        return (
            "절전 대응 · 자동 수집 작업을 등록하면 예약 시 PC 깨우기와 "
            "놓친 예약 실행 상태를 확인할 수 있습니다."
        )

    text = (
        "절전 대응 · 예약 시 PC 깨우기: "
        f"{_setting_label(status.wake_to_run)} · 놓친 예약 실행: "
        f"{_setting_label(status.start_when_available)} · "
        "Windows 전원 계획은 변경하지 않습니다."
    )
    if status.wake_to_run is False or status.start_when_available is False:
        text += " · 아래 등록·변경 시 절전 대응 설정을 다시 활성화합니다."
    return text


def install_scheduler_wake_status_ui(st_module: Any) -> None:
    """예약 실행 설정 제목 아래에 실제 WakeToRun/StartWhenAvailable 상태를 표시합니다."""
    original_markdown = getattr(st_module, "markdown", None)
    original_caption = getattr(st_module, "caption", None)
    if (
        not callable(original_markdown)
        or not callable(original_caption)
        or getattr(st_module, "_scheduler_wake_status_ui_installed", False)
    ):
        return

    st_module._scheduler_wake_status_ui_installed = True

    @wraps(original_markdown)
    def wrapped_markdown(value: Any, *args: Any, **kwargs: Any):
        result = original_markdown(value, *args, **kwargs)
        if str(value or "").strip() != _SCHEDULER_HEADING:
            return result

        try:
            status = get_refresh_scheduler_status(PROJECT_ROOT)
        except Exception:
            original_caption(
                "절전 대응 · 작업 스케줄러 상태 확인 실패 · "
                "Windows 전원 계획은 변경하지 않습니다."
            )
        else:
            original_caption(build_scheduler_wake_status_text(status))
        return result

    st_module.markdown = wrapped_markdown
