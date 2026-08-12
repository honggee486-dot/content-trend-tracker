from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from src.services.trend_cluster_progress_detail_runtime import (
    PROGRESS_FLOW_TEXT,
    build_detailed_display_status,
    build_stage_label,
    enrich_clustering_job,
    install_cluster_progress_detail_contract,
    load_progress_log,
    progress_percent,
)


def test_stage_and_display_explain_view_request_and_topic_count() -> None:
    job = {"completed_batches": 0, "max_batches": 20, "status": "running"}
    progress = {
        "phase": "calling",
        "analysis_view": "title",
        "analysis_view_label": "제목 기준",
        "request_number": 2,
        "topic_count": 1_842,
        "stage_topic_count": 8_671,
    }

    assert build_stage_label(progress) == "2차 주제 군집 · 제목 기준 · Gemini 호출 중"
    assert build_detailed_display_status(job, progress) == (
        "2차 주제 군집 중 · 1/20차 작업 · 제목 기준 비교 · "
        "Gemini 2번째 요청 · 1,842개 주제 · Gemini 호출 중"
    )
    assert progress_percent(job, progress) == 1


def test_progress_log_formats_clock_elapsed_and_stage_duration() -> None:
    started = datetime(2026, 8, 6, 14, 0, 0)
    rows = [
        (
            started + timedelta(seconds=8),
            "completed",
            "2차 주제 군집 · 제목 기준",
            "응답 검증 단계로 이동",
            1200,
            4500,
        ),
        (
            started + timedelta(seconds=3.5),
            "started",
            "2차 주제 군집 · 제목 기준",
            "Gemini 1번째 요청",
            1200,
            0,
        ),
    ]

    class _Cursor:
        def fetchall(self):
            return rows

    class _Connection:
        def execute(self, query, params):
            assert "trend_clustering_job" in query
            assert params == ["job-1", 16]
            return _Cursor()

    result = load_progress_log(_Connection(), "job-1", started_at=started)

    assert result[0] == {
        "시각": "2026-08-06 14:00:03",
        "경과(초)": "3.50",
        "상태": "시작",
        "단계": "2차 주제 군집 · 제목 기준",
        "내용": "Gemini 1번째 요청",
    }
    assert result[1]["경과(초)"] == "8.00"
    assert result[1]["상태"] == "완료"
    assert result[1]["내용"].endswith("단계 소요 4.50초")


def test_enrich_adds_flow_progress_and_log_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.trend_cluster_progress_detail_runtime.load_progress_log",
        lambda con, job_id, started_at=None: [{"단계": "기록"}],
    )
    job = {
        "job_id": "job-1",
        "status": "running",
        "completed_batches": 0,
        "max_batches": 1,
        "phase": "ranking",
        "analysis_view": "existing",
        "analysis_view_label": "기존 군집 연결 기준",
    }

    result = enrich_clustering_job(object(), job)

    assert result["current_stage_label"] == "군집 반영·점수 계산 중"
    assert result["progress_percent"] == 94
    assert result["progress_flow_text"] == PROGRESS_FLOW_TEXT
    assert result["progress_log_rows"] == [{"단계": "기록"}]


def test_skipped_overlap_is_not_presented_as_completed_progress(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.trend_cluster_progress_detail_runtime.load_progress_log",
        lambda con, job_id, started_at=None: [],
    )
    job = {
        "job_id": "skipped",
        "status": "skipped_overlap",
        "completed_batches": 0,
        "max_batches": 20,
        "phase": "completed",
        "message": "2차 군집 작업 종료",
    }

    result = enrich_clustering_job(object(), job)

    assert progress_percent(job, {"phase": "completed"}) == 0
    assert result["progress_percent"] == 0
    assert result["current_stage_label"] == "실행 전 중복 차단"
    assert result["progress_notice"] == (
        "기존 작업 진행 중으로 생략 · Gemini 호출 및 DB 반영 없음"
    )


def test_existing_terminal_status_progress_and_labels_are_preserved(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.trend_cluster_progress_detail_runtime.load_progress_log",
        lambda con, job_id, started_at=None: [],
    )
    cases = (
        ("success", 100, "2차 군집 작업 완료"),
        ("partial", 100, "2차 군집 시험 범위 완료"),
        ("failed", 0, "2차 군집 작업 실패"),
    )

    for status, expected_progress, expected_label in cases:
        result = enrich_clustering_job(
            object(),
            {"job_id": status, "status": status, "completed_batches": 0},
        )
        assert result["progress_percent"] == expected_progress
        assert result["current_stage_label"] == expected_label


def test_stale_running_history_is_presented_as_status_check_needed(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.trend_cluster_progress_detail_runtime.load_progress_log",
        lambda con, job_id, started_at=None: [],
    )
    job = {
        "job_id": "stale-job",
        "status": "running",
        "display_status": "stale",
        "completed_batches": 3,
        "max_batches": 20,
    }

    result = enrich_clustering_job(object(), job)

    assert result["status"] == "running"
    assert result["display_status"] == "상태 확인 필요"
    assert result["current_stage_label"] == "2차 군집 상태 확인 필요"
    assert result["progress_percent"] == 0
    assert "현재 실행 중이라고 단정하지 않습니다" in result["progress_notice"]


def test_installer_wraps_latest_once_and_replaces_request_factory(monkeypatch) -> None:
    from src.services import trend_cluster_live_progress as live_module

    original_factory = live_module._progress_api_call
    original_display = live_module.build_live_display_status
    monkeypatch.setattr(
        "src.services.trend_cluster_progress_detail_runtime.enrich_clustering_job",
        lambda con, job: {**job, "detailed": True},
    )
    module = SimpleNamespace(get_latest_clustering_job=lambda con: {"job_id": "job-1"})
    try:
        install_cluster_progress_detail_contract(module)
        first = module.get_latest_clustering_job
        install_cluster_progress_detail_contract(module)

        assert module.get_latest_clustering_job is first
        assert module.get_latest_clustering_job(object())["detailed"] is True
        assert getattr(live_module._progress_api_call, "_cluster_progress_detail", False)
        assert live_module.build_live_display_status is build_detailed_display_status
    finally:
        live_module._progress_api_call = original_factory
        live_module.build_live_display_status = original_display
