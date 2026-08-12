from __future__ import annotations

from pathlib import Path

from src.services.topic_angle_backlog_resume_service import (
    resume_deferred_topic_angles,
    should_resume_deferred_topic_angles,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _result(*, status: str = "partial", processed: int = 402, remaining: int = 1200):
    return {
        "ranking": {
            "clusters": 8200,
            "ai_clustering": {
                "status": status,
                "processed_items": processed,
                "remaining_items": remaining,
            },
        },
        "topic_angles": {
            "status": "deferred_for_clustering_backlog",
            "remaining_items": remaining,
            "requested_clusters": 0,
            "generated_clusters": 0,
            "generated_angles": 0,
        },
        "warnings": {},
    }


def test_saved_partial_clustering_resumes_topic_angles_despite_backlog(tmp_path: Path) -> None:
    result = _result()
    calls = []

    updated, warning = resume_deferred_topic_angles(
        result,
        runner=lambda path: calls.append(path) or (
            {
                "status": "success",
                "requested_clusters": 20,
                "generated_clusters": 18,
                "generated_angles": 54,
            },
            "",
        ),
        db_path=tmp_path / "test.duckdb",
    )

    assert calls == [tmp_path / "test.duckdb"]
    assert warning == ""
    assert updated["topic_angles"]["status"] == "success"
    assert updated["topic_angles"]["generated_clusters"] == 18
    assert updated["topic_angles"]["clustering_remaining_items"] == 1200
    assert updated["topic_angles"]["resumed_with_clustering_backlog"] is True


def test_overlap_or_no_processed_items_keeps_topic_angles_deferred(tmp_path: Path) -> None:
    overlap = _result(status="skipped_overlap", processed=0, remaining=0)
    empty = _result(status="partial", processed=0, remaining=1200)
    calls = []

    for result in (overlap, empty):
        updated, warning = resume_deferred_topic_angles(
            result,
            runner=lambda path: calls.append(path) or ({"status": "success"}, ""),
            db_path=tmp_path / "test.duckdb",
        )
        assert updated["topic_angles"]["status"] == "deferred_for_clustering_backlog"
        assert warning == ""

    assert calls == []


def test_non_deferred_result_is_not_called_again(tmp_path: Path) -> None:
    result = _result()
    result["topic_angles"] = {"status": "success", "generated_clusters": 10}

    assert should_resume_deferred_topic_angles(result) is False
    updated, _warning = resume_deferred_topic_angles(
        result,
        runner=lambda _path: (_ for _ in ()).throw(AssertionError("should not run")),
        db_path=tmp_path / "test.duckdb",
    )
    assert updated["topic_angles"]["generated_clusters"] == 10


def test_warning_is_preserved_without_cancelling_collection(tmp_path: Path) -> None:
    result = _result()

    updated, warning = resume_deferred_topic_angles(
        result,
        runner=lambda _path: (
            {
                "status": "partial_success",
                "generated_clusters": 3,
                "generated_angles": 9,
            },
            "일부 요청 재시도 필요",
        ),
        db_path=tmp_path / "test.duckdb",
    )

    assert warning == "일부 요청 재시도 필요"
    assert updated["warnings"]["topic_angles"] == "일부 요청 재시도 필요"
    assert updated["topic_angles"]["status"] == "partial_success"


def test_manual_and_scheduled_collection_wrappers_apply_resume_policy() -> None:
    dashboard = (PROJECT_ROOT / "scripts" / "refresh_trends_dashboard.py").read_text(
        encoding="utf-8"
    )
    scheduled = (PROJECT_ROOT / "scripts" / "refresh_trends_safe.py").read_text(
        encoding="utf-8"
    )

    assert "resume_deferred_topic_angles" in dashboard
    assert "runner=base_refresh._run_background_topic_angles" in dashboard
    assert "resume_deferred_topic_angles" in scheduled
    assert "runner=base_refresh._run_background_topic_angles" in scheduled
