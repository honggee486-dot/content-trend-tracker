from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import topic_angle_quality_diagnostic_ui as ui


class _FakeColumn:
    def __init__(self, metrics: list[tuple[str, str, str | None]]) -> None:
        self._metrics = metrics

    def metric(
        self,
        label: str,
        value: str,
        delta: str | None = None,
        **_kwargs,
    ) -> None:
        self._metrics.append((label, str(value), None if delta is None else str(delta)))


class _FakeStreamlit:
    def __init__(self) -> None:
        self.metrics: list[tuple[str, str, str | None]] = []
        self.markdowns: list[str] = []
        self.captions: list[str] = []
        self.messages: list[tuple[str, str]] = []
        self.frames: list[pd.DataFrame] = []

    def columns(self, count: int):
        return [_FakeColumn(self.metrics) for _ in range(count)]

    def markdown(self, value: str) -> None:
        self.markdowns.append(str(value))

    def caption(self, value: str) -> None:
        self.captions.append(str(value))

    def dataframe(self, value, **_kwargs) -> None:
        self.frames.append(value)

    def warning(self, value: str) -> None:
        self.messages.append(("warning", str(value)))

    def success(self, value: str) -> None:
        self.messages.append(("success", str(value)))

    def info(self, value: str) -> None:
        self.messages.append(("info", str(value)))


def _report(*, action_label: str = "운영 표본 추가") -> dict:
    return {
        "generated_at": "2026-08-02 21:30:00",
        "read_only": True,
        "runtime": {
            "items_per_request": 15,
            "thinking_level": "high",
            "timeout_seconds": 600,
            "min_opportunity_score": 50.0,
        },
        "topic_angle": {
            "matching_successful_requests": 2,
            "requested_items": 30,
            "sample_sufficient": False,
            "current_validation_failure_count": 0,
            "other_runtime_validation_failure_count": 1,
            "candidate_selection": {
                "available": True,
                "selected_is_estimate": True,
                "total_clusters": 8409,
                "eligible_status_clusters": 300,
                "score_eligible_clusters": 126,
                "already_complete_clusters": 121,
                "generation_needed_clusters": 5,
                "inspected_clusters": 3,
                "skipped_sensitive_clusters": 1,
                "skipped_no_evidence_clusters": 1,
                "selected_clusters": 1,
                "deferred_uninspected_clusters": 2,
                "selection_limit": 15,
                "missing_tables": [],
                "error_type": "",
            },
            "failure_diagnostics": {
                "available": True,
                "reason": "",
                "terminal_failure_count": 3,
                "current_runtime_failure_count": 1,
                "retried_terminal_failure_count": 2,
                "total_retry_wait_seconds": 12.0,
                "failure_categories": [
                    {
                        "category": "max_tokens",
                        "label": "MAX_TOKENS·출력 한도",
                        "count": 1,
                        "current_runtime_count": 0,
                    },
                    {
                        "category": "retry_wait_exhausted",
                        "label": "재시도 후 분당 제한 종료",
                        "count": 2,
                        "current_runtime_count": 1,
                    },
                ],
            },
        },
        "portal_requests": {
            "available": True,
            "days": 7,
            "request_count": 20,
            "attempt_count": 22,
            "retry_count": 2,
            "failed_request_count": 1,
            "sources": {
                "naver": {
                    "request_count": 10,
                    "attempt_count": 11,
                    "retry_count": 1,
                    "failed_request_count": 0,
                    "zero_result_count": 2,
                    "newly_saved_count": 5,
                    "updated_count": 7,
                    "error_rate_percent": 0.0,
                    "last_request_at": "2026-08-02 20:00:00",
                },
                "daum": {
                    "request_count": 10,
                    "attempt_count": 11,
                    "retry_count": 1,
                    "failed_request_count": 1,
                    "zero_result_count": 1,
                    "newly_saved_count": 4,
                    "updated_count": 6,
                    "error_rate_percent": 10.0,
                    "last_request_at": "2026-08-02 20:01:00",
                },
            },
        },
        "collection_separation": {
            "available": True,
            "run_limit": 10,
            "run_count": 8,
            "source_success_count": 8,
            "source_problem_count": 0,
            "gemini_recorded_count": 8,
            "gemini_success_count": 6,
            "gemini_problem_count": 1,
            "gemini_skipped_count": 1,
            "isolated_gemini_problem_count": 1,
            "latest_run_at": "2026-08-02 20:00:00",
            "status": "분리 보존 확인",
        },
        "next_action": {
            "label": action_label,
            "reason": "현재 조건을 유지하고 최소 표본까지 추가로 관찰합니다.",
        },
    }


def test_p2_operation_summary_renders_current_runtime_and_separation(
    monkeypatch,
) -> None:
    fake = _FakeStreamlit()
    monkeypatch.setattr(
        ui,
        "build_operation_diagnostic_report",
        lambda *_args, **_kwargs: _report(),
    )

    ui._render_p2_operation_summary(
        object(),
        app_id="content-trend-tracker",
        items_per_request=15,
        thinking_level="high",
        timeout_seconds=600,
        min_opportunity_score=50,
        topic_diagnostic=None,
        st_module=fake,
    )

    metric_map = {label: (value, delta) for label, value, delta in fake.metrics}
    assert metric_map["다음 판단"][0] == "운영 표본 추가"
    assert metric_map["현재 조건 성공 표본"] == ("2회 · 30개", "미충족")
    assert metric_map["현재 조건 검증 실패"] == ("0회", "다른 조건 1회")
    assert metric_map["포털 최종 오류"] == ("1회", "최근 7일")
    assert metric_map["분리 보존된 Gemini 문제"] == ("1회", "분리 보존 확인")
    assert (
        "info",
        "운영 표본 추가: 현재 조건을 유지하고 최소 표본까지 추가로 관찰합니다.",
    ) in fake.messages
    assert len(fake.frames) == 2
    assert list(fake.frames[0]["원인"]) == [
        "MAX_TOKENS·출력 한도",
        "재시도 후 분당 제한 종료",
    ]
    assert list(fake.frames[1]["포털"]) == ["NAVER", "Daum"]
    assert any("현재 조건 15개·high·600초" in item for item in fake.captions)
    assert any(
        "전체 8,409개 → 상태 통과 300개 → 점수 통과 126개" in item
        for item in fake.captions
    )
    assert any("생성 대상 추정 1개" in item for item in fake.captions)
    assert any("실제 Gemini 생성은 수행하지 않은" in item for item in fake.captions)
    assert any(
        "최종 실패 전체 3회 · 현재 조건 1회" in item for item in fake.captions
    )
    assert "**주제 방향 대상 선정 · 현재 요청 상한**" in fake.markdowns
    assert "**Gemini 주제 방향 최종 실패**" in fake.markdowns


def test_p2_operation_summary_uses_warning_for_actionable_problem(
    monkeypatch,
) -> None:
    fake = _FakeStreamlit()
    monkeypatch.setattr(
        ui,
        "build_operation_diagnostic_report",
        lambda *_args, **_kwargs: _report(
            action_label="현재 조건 응답 검증 점검"
        ),
    )

    ui._render_p2_operation_summary(
        object(),
        app_id="content-trend-tracker",
        items_per_request=15,
        thinking_level="high",
        timeout_seconds=600,
        min_opportunity_score=50,
        topic_diagnostic=None,
        st_module=fake,
    )

    assert any(level == "warning" for level, _message in fake.messages)


def test_main_diagnostic_panel_includes_p2_summary_call() -> None:
    source = Path(ui.__file__).read_text(encoding="utf-8")

    assert "build_operation_diagnostic_report" in source
    assert "_render_p2_operation_summary(" in source
    assert "**P2 통합 운영 판단**" in source
    assert "**주제 방향 대상 선정 · 현재 요청 상한**" in source
    assert "**Gemini 주제 방향 최종 실패**" in source
    assert "**NAVER·Daum 실제 요청" in source
    assert "**출처 수집·Gemini 분리" in source
