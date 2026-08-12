from __future__ import annotations

from src.scheduler_wake_status_ui import (
    build_scheduler_wake_status_text,
    install_scheduler_wake_status_ui,
)
from src.services.scheduler_service import SchedulerStatus


class _FakeStreamlit:
    def __init__(self) -> None:
        self.markdowns: list[str] = []
        self.captions: list[str] = []

    def markdown(self, value, *args, **kwargs):
        self.markdowns.append(str(value))
        return value

    def caption(self, value, *args, **kwargs):
        self.captions.append(str(value))
        return value


def test_wake_status_text_reports_enabled_settings_without_changing_power_plan() -> None:
    text = build_scheduler_wake_status_text(
        SchedulerStatus(
            supported=True,
            registered=True,
            wake_to_run=True,
            start_when_available=True,
        )
    )

    assert "예약 시 PC 깨우기: 사용" in text
    assert "놓친 예약 실행: 사용" in text
    assert "Windows 전원 계획은 변경하지 않습니다" in text


def test_wake_status_text_reports_disabled_setting_and_repair_hint() -> None:
    text = build_scheduler_wake_status_text(
        SchedulerStatus(
            supported=True,
            registered=True,
            wake_to_run=False,
            start_when_available=True,
        )
    )

    assert "예약 시 PC 깨우기: 사용 안 함" in text
    assert "등록·변경 시 절전 대응 설정을 다시 활성화" in text


def test_scheduler_heading_renders_actual_sleep_settings(monkeypatch) -> None:
    fake = _FakeStreamlit()
    calls = []

    monkeypatch.setattr(
        "src.scheduler_wake_status_ui.get_refresh_scheduler_status",
        lambda project_root: calls.append(project_root)
        or SchedulerStatus(
            supported=True,
            registered=True,
            wake_to_run=True,
            start_when_available=True,
        ),
    )

    install_scheduler_wake_status_ui(fake)
    fake.markdown("#### 예약 실행 설정")
    fake.markdown("#### 다른 설정")

    assert len(calls) == 1
    assert len(fake.captions) == 1
    assert "예약 시 PC 깨우기: 사용" in fake.captions[0]
    assert "놓친 예약 실행: 사용" in fake.captions[0]


def test_scheduler_heading_handles_status_query_failure(monkeypatch) -> None:
    fake = _FakeStreamlit()

    def fail(_project_root):
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(
        "src.scheduler_wake_status_ui.get_refresh_scheduler_status",
        fail,
    )

    install_scheduler_wake_status_ui(fake)
    fake.markdown("#### 예약 실행 설정")

    assert fake.captions == [
        "절전 대응 · 작업 스케줄러 상태 확인 실패 · Windows 전원 계획은 변경하지 않습니다."
    ]


def test_scheduler_wake_status_ui_is_installed_from_package_runtime() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    package_init = (root / "src" / "__init__.py").read_text(encoding="utf-8")

    assert "install_scheduler_wake_status_ui" in package_init
    assert 'install_scheduler_wake_status_ui(sys.modules["streamlit"])' in package_init
