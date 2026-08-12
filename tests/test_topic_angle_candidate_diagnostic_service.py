from __future__ import annotations

from types import SimpleNamespace

import src.services.topic_angle_ai_service as topic_angle_module
import src.services.topic_angle_candidate_diagnostic_service as service


class _Cursor:
    def __init__(self, *, aggregate, rows) -> None:
        self.aggregate = aggregate
        self.rows = rows
        self.mode = ""

    def execute(self, sql, arguments):
        assert arguments
        self.mode = "aggregate" if "COUNT(*) AS total_clusters" in sql else "rows"
        return self

    def fetchone(self):
        assert self.mode == "aggregate"
        return self.aggregate

    def fetchall(self):
        assert self.mode == "rows"
        return self.rows


def test_candidate_diagnostics_separate_each_selection_stage() -> None:
    con = _Cursor(
        aggregate=(8409, 300, 126, 121, 5),
        rows=(
            ("cluster-selected", "정상 후보"),
            ("cluster-sensitive", "민감 후보"),
            ("cluster-no-evidence", "근거 없는 후보"),
        ),
    )
    outcomes = {
        "cluster-selected": "selected",
        "cluster-sensitive": "sensitive",
        "cluster-no-evidence": "no_evidence",
    }

    diagnostics = service.collect_topic_angle_candidate_diagnostics(
        con,
        min_opportunity_score=50,
        selection_limit=3,
        selected_clusters=1,
        candidate_inspector=lambda _con, cluster_id, _title: outcomes[cluster_id],
    )

    assert diagnostics.total_clusters == 8409
    assert diagnostics.eligible_status_clusters == 300
    assert diagnostics.score_eligible_clusters == 126
    assert diagnostics.already_complete_clusters == 121
    assert diagnostics.generation_needed_clusters == 5
    assert diagnostics.inspected_clusters == 3
    assert diagnostics.skipped_sensitive_clusters == 1
    assert diagnostics.skipped_no_evidence_clusters == 1
    assert diagnostics.selected_clusters == 1
    assert diagnostics.deferred_uninspected_clusters == 2
    assert diagnostics.selection_limit == 3

    detail = service.format_topic_angle_candidate_diagnostics(diagnostics)
    assert "전체 군집 8,409개" in detail
    assert "기회 점수 50점 이상 126개" in detail
    assert "기존 방향 완료 121개" in detail
    assert "근거 없음 1개" in detail
    assert "이번 생성 대상 1개" in detail
    assert "요청 범위 밖 미검사 2개" in detail


def test_runtime_logs_diagnostics_without_changing_preparation(monkeypatch) -> None:
    con = _Cursor(
        aggregate=(10, 8, 6, 3, 3),
        rows=(("cluster-1", "후보 1"),),
    )
    preparation = SimpleNamespace(clusters=({"cluster_id": "cluster-1"},))
    config = SimpleNamespace(
        topic_angle_batch_limit=15,
        topic_angle_max_parallel_requests=1,
        topic_angle_min_opportunity_score=50.0,
    )
    events = []

    monkeypatch.setattr(
        topic_angle_module,
        "prepare_missing_topic_angles",
        lambda _con, *args, **kwargs: preparation,
    )
    monkeypatch.setattr(
        service,
        "collect_topic_angle_candidate_diagnostics",
        lambda *args, **kwargs: service.TopicAngleCandidateDiagnostics(
            total_clusters=10,
            eligible_status_clusters=8,
            score_eligible_clusters=6,
            already_complete_clusters=3,
            generation_needed_clusters=3,
            inspected_clusters=3,
            skipped_sensitive_clusters=1,
            skipped_no_evidence_clusters=1,
            selected_clusters=1,
            deferred_uninspected_clusters=0,
            min_opportunity_score=50.0,
            selection_limit=15,
        ),
    )
    monkeypatch.setattr(
        service,
        "record_program_event",
        lambda **kwargs: events.append(kwargs) or True,
    )

    service.install_topic_angle_candidate_diagnostic_contract()
    result = topic_angle_module.prepare_missing_topic_angles(con, config=config)

    assert result is preparation
    assert events[-1]["action"] == "주제 방향 대상 선정 상세"
    assert events[-1]["status"] == "completed"
    assert events[-1]["item_count"] == 1
    assert events[-1]["metadata"]["already_complete_clusters"] == 3


def test_all_topic_angle_entry_points_install_diagnostics() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for relative_path in (
        "src/__init__.py",
        "scripts/refresh_trends_safe.py",
        "scripts/refresh_trends_dashboard.py",
        "scripts/process_cluster_backlog.py",
    ):
        text = (root / relative_path).read_text(encoding="utf-8")
        assert "install_topic_angle_candidate_diagnostic_contract" in text
