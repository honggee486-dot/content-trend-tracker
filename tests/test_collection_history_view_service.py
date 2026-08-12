from datetime import datetime, timedelta
from types import SimpleNamespace

from src.services.collection_history_view_service import (
    RUN_DISPLAY_STATUS_REVIEW,
    annotate_collection_run_display_statuses,
    filter_collection_runs,
    inspect_collection_history_lock_state,
    list_collection_run_source_map,
    topic_angle_run_summary,
)


class _FakeCursor:
    description = [
        ("run_id",),
        ("source_name",),
        ("status",),
        ("duration_ms",),
        ("request_count",),
        ("retry_count",),
        ("newly_saved_count",),
        ("updated_count",),
        ("skipped_count",),
        ("error_message",),
    ]

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.parameters = None
        self.query = ""

    def execute(self, query, parameters):
        self.query = str(query)
        self.parameters = list(parameters)
        return _FakeCursor(self.rows)


def _topic_row(
    status: str,
    *,
    saved: int = 0,
    missing: int = 0,
    directions: int = 0,
    error_message: str = "",
):
    return {
        "source_name": "topic_angles",
        "status": status,
        "duration_ms": 1000,
        "request_count": 1,
        "retry_count": 0,
        "newly_saved_count": directions,
        "updated_count": saved,
        "skipped_count": missing,
        "error_message": error_message,
    }


def test_source_map_loads_multiple_runs_in_one_query():
    con = _FakeConnection(
        [
            ("run-1", "naver", "success", 10, 1, 0, 2, 3, 0, ""),
            ("run-1", "topic_angles", "success", 20, 1, 0, 9, 3, 0, ""),
            ("run-2", "topic_angles", "partial_success", 30, 1, 0, 6, 2, 1, ""),
        ]
    )

    result = list_collection_run_source_map(con, ["run-1", "run-2", "run-1"])

    assert con.parameters == ["run-1", "run-2"]
    assert "WHERE run_id IN (?, ?)" in con.query
    assert [row["source_name"] for row in result["run-1"]] == ["naver", "topic_angles"]
    assert result["run-2"][0]["updated_count"] == 2


def test_topic_angle_summary_distinguishes_complete_problem_skipped_and_missing():
    assert topic_angle_run_summary([_topic_row("success", saved=40, directions=120)]) == {
        "category": "complete",
        "label": "완료 40/40개",
        "saved_clusters": 40,
        "requested_clusters": 40,
        "missing_clusters": 0,
    }
    assert topic_angle_run_summary(
        [_topic_row("partial_success", saved=34, missing=6, directions=102)]
    )["label"] == "부분 34/40개"
    assert topic_angle_run_summary(
        [_topic_row("failure", saved=0, missing=40)]
    )["category"] == "problem"
    assert topic_angle_run_summary(
        [_topic_row("skipped", error_message="Gemini API 키 없음")]
    )["label"] == "API 키 없음"
    assert topic_angle_run_summary([])["category"] == "missing"


def test_lock_state_reads_refresh_and_clustering_activity_without_mutation(tmp_path):
    calls = []

    def refresh_inspector(root):
        calls.append(("refresh", root))
        return SimpleNamespace(active=False)

    def clustering_inspector(root):
        calls.append(("clustering", root))
        return SimpleNamespace(active=True)

    result = inspect_collection_history_lock_state(
        tmp_path,
        refresh_inspector=refresh_inspector,
        clustering_inspector=clustering_inspector,
    )

    assert result == {
        "known": True,
        "refresh_active": False,
        "clustering_active": True,
    }
    assert calls == [("refresh", tmp_path), ("clustering", tmp_path)]


def test_stale_running_display_requires_known_inactive_locks():
    now = datetime(2026, 8, 8, 9, 0, 0)
    runs = [
        {"run_id": "old", "status": "running", "started_at": now - timedelta(hours=7)},
        {"run_id": "recent", "status": "running", "started_at": now - timedelta(hours=1)},
        {"run_id": "done", "status": "success", "started_at": now - timedelta(hours=9)},
    ]

    inactive = annotate_collection_run_display_statuses(
        runs,
        lock_state={"known": True, "refresh_active": False, "clustering_active": False},
        now=now,
    )
    active = annotate_collection_run_display_statuses(
        runs,
        lock_state={"known": True, "refresh_active": True, "clustering_active": False},
        now=now,
    )
    unknown = annotate_collection_run_display_statuses(
        runs,
        lock_state={"known": False, "refresh_active": False, "clustering_active": False},
        now=now,
    )

    assert inactive[0]["display_status"] == RUN_DISPLAY_STATUS_REVIEW
    assert inactive[1]["display_status"] == "running"
    assert inactive[2]["display_status"] == "success"
    assert active[0]["display_status"] == "running"
    assert unknown[0]["display_status"] == "running"
    assert runs[0].get("display_status") is None


def test_collection_run_filter_combines_type_status_and_gemini_state():
    runs = [
        {"run_id": "run-1", "run_type": "background_refresh", "status": "success"},
        {"run_id": "run-2", "run_type": "background_refresh", "status": "success"},
        {"run_id": "run-3", "run_type": "topic_angle_generation", "status": "partial_success"},
        {"run_id": "run-4", "run_type": "manual_refresh", "status": "failure"},
        {
            "run_id": "run-5",
            "run_type": "manual_refresh",
            "status": "running",
            "display_status": RUN_DISPLAY_STATUS_REVIEW,
        },
    ]
    source_map = {
        "run-1": [_topic_row("success", saved=40, directions=120)],
        "run-2": [_topic_row("skipped")],
        "run-3": [_topic_row("partial_success", saved=30, missing=10, directions=90)],
    }

    assert [
        row["run_id"]
        for row in filter_collection_runs(runs, source_map, gemini_state="problem")
    ] == ["run-3"]
    assert [
        row["run_id"]
        for row in filter_collection_runs(
            runs,
            source_map,
            run_type="background_refresh",
            run_status="success",
            gemini_state="complete",
        )
    ] == ["run-1"]
    assert [
        row["run_id"]
        for row in filter_collection_runs(runs, source_map, gemini_state="missing")
    ] == ["run-4", "run-5"]
    assert [
        row["run_id"]
        for row in filter_collection_runs(
            runs,
            source_map,
            run_status=RUN_DISPLAY_STATUS_REVIEW,
        )
    ] == ["run-5"]
