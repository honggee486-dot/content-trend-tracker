from __future__ import annotations

from datetime import datetime, timedelta

import src.operational_logs_ui as log_ui


class _Context:
    def __init__(self, owner, label: str) -> None:
        self.owner = owner
        self.label = label

    def __enter__(self):
        self.owner.events.append(("enter", self.label))
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.owner.events.append(("exit", self.label))
        return False


class _FakeStreamlit:
    def __init__(self) -> None:
        self.events = []

    def expander(self, label, *args, **kwargs):
        self.events.append(("create", str(label)))
        return _Context(self, str(label))


def test_operational_logs_render_after_batch_expander_closes(monkeypatch) -> None:
    fake = _FakeStreamlit()
    rendered = []
    monkeypatch.setattr(
        log_ui,
        "render_operational_logs",
        lambda st_module: rendered.append((st_module, list(st_module.events))),
    )
    log_ui.install_operational_logs_ui(fake)

    with fake.expander("배치별 토큰·품질 로그", expanded=False):
        fake.events.append(("body", "batch"))

    assert fake.events == [
        ("create", "배치별 토큰·품질 로그"),
        ("enter", "배치별 토큰·품질 로그"),
        ("body", "batch"),
        ("exit", "배치별 토큰·품질 로그"),
    ]
    assert rendered == [(fake, list(fake.events))]


def test_other_expanders_are_not_followed_by_operational_logs(monkeypatch) -> None:
    fake = _FakeStreamlit()
    rendered = []
    monkeypatch.setattr(
        log_ui,
        "render_operational_logs",
        lambda st_module: rendered.append(st_module),
    )
    log_ui.install_operational_logs_ui(fake)

    with fake.expander("다른 로그", expanded=False):
        pass

    assert rendered == []


def test_operational_log_installer_is_idempotent() -> None:
    fake = _FakeStreamlit()
    log_ui.install_operational_logs_ui(fake)
    first = fake.expander
    log_ui.install_operational_logs_ui(fake)

    assert fake.expander is first


def test_program_frame_hides_zero_values_for_start_rows_and_names_run() -> None:
    rows = [
        {
            "event_id": "event-1",
            "event_time": datetime(2026, 8, 6, 13, 0, 0),
            "event_type": "task",
            "status": "started",
            "source": "collection_history",
            "action": "실행 기록 · manual_refresh",
            "detail": "실행 ID collection_abcdef123456",
            "item_count": 0,
            "duration_ms": 0,
            "correlation_id": "collection_abcdef123456",
        },
        {
            "event_id": "event-2",
            "event_time": datetime(2026, 8, 6, 13, 1, 0),
            "event_type": "task",
            "status": "completed",
            "source": "collection_history",
            "action": "실행 기록 종료",
            "detail": "실행 ID collection_abcdef123456",
            "item_count": 0,
            "duration_ms": 60000,
            "correlation_id": "collection_abcdef123456",
        },
    ]

    frame = log_ui._program_frame(
        rows,
        run_types={"collection_abcdef123456": "manual_refresh"},
    )

    assert frame.iloc[0]["작업"] == "최신 데이터 수집 실행 시작"
    assert frame.iloc[0]["항목"] == "-"
    assert frame.iloc[0]["시간(초)"] == "-"
    assert frame.iloc[0]["상세"] == ""
    assert frame.iloc[1]["작업"] == "최신 데이터 수집 실행 종료"
    assert frame.iloc[1]["시간(초)"] == "60.00"
    assert "최신 데이터 수집" in frame.iloc[1]["실행"]


def test_program_filters_and_flow_order_group_execution_chronologically() -> None:
    base = datetime(2026, 8, 6, 13, 0, 0)
    rows = [
        {
            "event_id": "b2",
            "event_time": base + timedelta(minutes=4),
            "event_type": "api",
            "correlation_id": "run-b",
        },
        {
            "event_id": "a2",
            "event_time": base + timedelta(minutes=3),
            "event_type": "stage",
            "correlation_id": "run-a",
        },
        {
            "event_id": "b1",
            "event_time": base + timedelta(minutes=2),
            "event_type": "api",
            "correlation_id": "run-b",
        },
        {
            "event_id": "a1",
            "event_time": base + timedelta(minutes=1),
            "event_type": "button",
            "correlation_id": "run-a",
        },
    ]

    api_rows = log_ui.filter_program_rows(rows, type_filter="API만")
    assert [row["event_id"] for row in api_rows] == ["b2", "b1"]

    flow_rows = log_ui.order_program_rows(rows, flow_order=True)
    assert [row["event_id"] for row in flow_rows] == ["b1", "b2", "a1", "a2"]
