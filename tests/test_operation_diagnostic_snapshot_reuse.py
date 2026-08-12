from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.database import connect_database, init_database
import src.services.operation_diagnostic_report_service as report_service
from src.services.topic_angle_quality_diagnostic_service import (
    build_topic_angle_quality_diagnostic,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_operation_report_reuses_precomputed_topic_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "snapshot-reuse.duckdb"
    init_database(db_path)

    with connect_database(db_path, read_only=True) as con:
        diagnostic = build_topic_angle_quality_diagnostic(
            con,
            app_id="content-trend-tracker",
            items_per_request=15,
            thinking_level="medium",
            timeout_seconds=600,
            min_opportunity_score=50,
        )

        def fail_duplicate_quality_query(*args, **kwargs):
            raise AssertionError("주제 방향 품질 진단을 다시 조회하면 안 됩니다.")

        monkeypatch.setattr(
            report_service,
            "build_topic_angle_quality_diagnostic",
            fail_duplicate_quality_query,
        )
        report = report_service.build_operation_diagnostic_report(
            con,
            app_id="content-trend-tracker",
            items_per_request=15,
            thinking_level="medium",
            timeout_seconds=600,
            min_opportunity_score=50,
            topic_diagnostic=diagnostic,
            now=datetime(2026, 8, 7, 16, 0, 0),
        )

    assert report["read_only"] is True
    assert report["topic_angle"]["status"] == diagnostic.status
    assert report["topic_angle"]["matching_successful_requests"] == (
        diagnostic.operation.matching_runtime_request_count
    )
    assert report["topic_angle"]["pending_cluster_count"] == (
        diagnostic.backlog.pending_cluster_count
    )


def test_topic_angle_quality_ui_passes_existing_diagnostic_to_p2_summary() -> None:
    source = (PROJECT_ROOT / "src" / "topic_angle_quality_diagnostic_ui.py").read_text(
        encoding="utf-8"
    )

    assert "topic_diagnostic=diagnostic" in source
    assert "topic_diagnostic=topic_diagnostic" in source
