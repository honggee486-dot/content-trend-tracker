from __future__ import annotations

from src.clustering_job_status_ui import (
    _render_live_progress,
    build_clustering_metric_values,
    build_recent_clustering_attempt_notice,
    render_clustering_job_error,
    _rewrite_job_message,
    _snapshot_value,
    install_clustering_job_status_ui,
)


class _Column:
    def __init__(self, owner, index: int) -> None:
        self.owner = owner
        self.index = index

    def markdown(self, value, *args, **kwargs):
        self.owner.column_markdowns.append((self.index, value, args, kwargs))

    def button(self, label, *args, **kwargs):
        self.owner.column_buttons.append((self.index, label, args, kwargs))
        return self.owner.next_column_click


class _FakeStreamlit:
    def __init__(self) -> None:
        self.markdowns = []
        self.buttons = []
        self.warnings = []
        self.column_markdowns = []
        self.column_buttons = []
        self.column_specs = []
        self.next_column_click = False
        self.rerun_count = 0

    def markdown(self, value, *args, **kwargs):
        self.markdowns.append((value, args, kwargs))

    def button(self, label, *args, **kwargs):
        self.buttons.append((label, args, kwargs))
        return False

    def warning(self, value, *args, **kwargs):
        self.warnings.append((value, args, kwargs))
        return value

    def columns(self, spec, **kwargs):
        self.column_specs.append((spec, kwargs))
        return [_Column(self, index) for index in range(len(spec))]

    def rerun(self):
        self.rerun_count += 1


def test_refresh_button_is_rendered_immediately_beside_job_heading() -> None:
    fake = _FakeStreamlit()
    install_clustering_job_status_ui(fake)

    result = fake.markdown("#### 최근 2단계 군집 작업")

    assert result is None
    assert fake.column_specs == [
        (
            [1.65, 1.05, 7.30],
            {"gap": "small", "vertical_alignment": "center"},
        )
    ]
    assert fake.column_markdowns[0][0:2] == (0, "#### 최근 2단계 군집 작업")
    assert fake.column_buttons[0][0:2] == (1, "상태 새로고침")
    assert fake.column_buttons[0][3]["key"] == "refresh_clustering_job_status"
    assert fake.column_buttons[0][3]["width"] == "stretch"


def test_legacy_full_width_refresh_button_renders_progress_then_is_suppressed(monkeypatch) -> None:
    fake = _FakeStreamlit()
    rendered = []
    monkeypatch.setattr(
        "src.clustering_job_status_ui._render_live_progress",
        lambda st_module: rendered.append(st_module),
    )
    install_clustering_job_status_ui(fake)
    fake.markdown("#### 최근 2단계 군집 작업")

    clicked = fake.button(
        "군집 작업 상태 새로고침",
        key="refresh_clustering_job_status",
        width="stretch",
    )
    second = fake.button(
        "군집 작업 상태 새로고침",
        key="refresh_clustering_job_status",
        width="stretch",
    )

    assert clicked is False
    assert second is False
    assert fake.buttons == []
    assert rendered == [fake]


def test_header_refresh_click_triggers_rerun() -> None:
    fake = _FakeStreamlit()
    fake.next_column_click = True
    install_clustering_job_status_ui(fake)

    fake.markdown("#### 최근 2단계 군집 작업")

    assert fake.rerun_count == 1


def test_live_progress_uses_app_compatible_database_configuration(monkeypatch) -> None:
    calls = []

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

    def fake_connect(path, *, read_only=False):
        calls.append((path, read_only))
        return _Connection()

    monkeypatch.setattr(
        "src.clustering_job_status_ui.connect_database",
        fake_connect,
    )
    monkeypatch.setattr(
        "src.services.trend_clustering_job_service.get_representative_clustering_job",
        lambda con: None,
    )

    _render_live_progress(_FakeStreamlit())

    assert len(calls) == 1
    assert calls[0][1] is False


def test_legacy_batch_count_is_displayed_as_single_snapshot() -> None:
    assert _snapshot_value("1/20") == "1/1"
    assert _snapshot_value("0/20") == "0/1"


def test_legacy_request_summary_gets_explicit_api_label() -> None:
    assert _rewrite_job_message(
        "요청 4회 | 불확실 0개 | 실패 요청 0회"
    ) == "실제 Gemini 요청 4회 | 불확실 0개 | 실패 요청 0회"
    current = "분석 관점 4개 | 실제 Gemini 요청 8회"
    assert _rewrite_job_message(current) == current


def test_skipped_overlap_metrics_are_displayed_as_not_executed() -> None:
    values = build_clustering_metric_values(
        {
            "status": "skipped_overlap",
            "completed_batches": 0,
            "max_batches": 20,
            "processed_units": 0,
            "processed_source_items": 0,
            "remaining_items": 0,
            "total_tokens": 0,
        }
    )

    assert values == {
        "snapshot": "미실행",
        "processed_units": "미실행",
        "processed_source_items": "미실행",
        "remaining_items": "확인 안 함",
        "total_tokens": "미호출",
    }


def test_newer_overlap_attempt_is_described_separately_from_active_job() -> None:
    notice = build_recent_clustering_attempt_notice(
        {"job_id": "active", "status": "running"},
        {
            "job_id": "attempt",
            "status": "skipped_overlap",
            "finished_at": "2026-08-06 14:24:22",
            "error_message": "다른 군집 처리 작업이 이미 실행 중입니다.",
        },
    )

    assert notice == (
        "최근 실행 시도 · 2026-08-06 14:24:22 당시 기존 군집 작업이 "
        "실행 중이어서 새 요청을 시작하지 않았습니다."
    )
    assert "이미 실행 중입니다" not in notice
    assert build_recent_clustering_attempt_notice(
        None,
        {
            "job_id": "attempt",
            "status": "skipped_overlap",
            "created_at": "2026-08-06T14:24:22",
        },
    ) == notice
    assert build_recent_clustering_attempt_notice(
        {"job_id": "same"},
        {"job_id": "same", "status": "success"},
    ) == ""


def test_skipped_overlap_error_is_not_rendered_as_current_warning() -> None:
    fake = _FakeStreamlit()

    render_clustering_job_error(
        fake,
        {
            "status": "skipped_overlap",
            "error_message": "다른 군집 처리 작업이 이미 실행 중입니다.",
        },
    )

    assert fake.warnings == []


def test_failed_job_error_still_renders_as_warning() -> None:
    fake = _FakeStreamlit()

    render_clustering_job_error(
        fake,
        {"status": "failed", "error_message": "호출 실패"},
    )

    assert fake.warnings[0][0] == "호출 실패"
