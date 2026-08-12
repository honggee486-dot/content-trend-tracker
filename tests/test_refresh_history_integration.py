from __future__ import annotations

from pathlib import Path

from scripts import refresh_trends
from src.database import connect_database


def _result() -> dict[str, object]:
    return {
        "youtube": None,
        "google_trends": None,
        "wikipedia": None,
        "naver": {
            "status": "success",
            "items_read": 1,
            "items_added": 1,
            "items_updated": 0,
            "items_skipped": 0,
            "request_count": 1,
            "retry_count": 0,
        },
        "daum": None,
        "errors": {},
        "warnings": {},
        "timings": {"naver": 0.1},
    }


def test_background_refresh_records_background_run_type(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "background.duckdb"
    monkeypatch.setattr(refresh_trends, "DEFAULT_DB_PATH", db_path)

    seen_run_ids: list[str] = []

    def run_body(collection_run_id: str) -> tuple[int, dict[str, object]]:
        seen_run_ids.append(collection_run_id)
        return 0, _result()

    monkeypatch.setattr(refresh_trends, "_run_refresh_body", run_body)

    assert refresh_trends._run_refresh() == 0
    assert len(seen_run_ids) == 1
    assert seen_run_ids[0].startswith("collection_")

    with connect_database(db_path) as con:
        row = con.execute(
            "SELECT run_type, status FROM collection_runs"
        ).fetchone()
    assert row == ("background_refresh", "success")


def test_background_refresh_exception_records_failure(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "background_failure.duckdb"
    monkeypatch.setattr(refresh_trends, "DEFAULT_DB_PATH", db_path)

    seen_run_ids: list[str] = []

    def fail(collection_run_id: str) -> tuple[int, dict[str, object]]:
        seen_run_ids.append(collection_run_id)
        raise RuntimeError("orchestration failed")

    monkeypatch.setattr(refresh_trends, "_run_refresh_body", fail)

    try:
        refresh_trends._run_refresh()
    except RuntimeError as exc:
        assert str(exc) == "orchestration failed"
    else:
        raise AssertionError("원래 예외가 다시 발생해야 합니다.")

    assert len(seen_run_ids) == 1
    assert seen_run_ids[0].startswith("collection_")

    with connect_database(db_path) as con:
        row = con.execute(
            "SELECT run_type, status, error_message FROM collection_runs"
        ).fetchone()
    assert row == ("background_refresh", "failure", "orchestration failed")


def test_background_exit_code_fails_when_all_reported_sources_failed() -> None:
    result: dict[str, object] = {
        "youtube": None,
        "google_trends": None,
        "wikipedia": None,
        "naver": {"status": "failed", "items_read": 0},
        "daum": {"status": "failed", "items_read": 0},
        "errors": {
            "naver": "NAVER request failed",
            "daum": "Daum request failed",
        },
    }
    source_specs = [
        ("youtube", "YouTube"),
        ("google_trends", "Google Trends"),
        ("wikipedia", "위키백과"),
        ("naver", "NAVER"),
        ("daum", "Daum"),
    ]

    assert refresh_trends._background_exit_code(result, source_specs) == 1


def test_background_exit_code_keeps_partial_or_unchanged_result_successful() -> None:
    source_specs = [
        ("youtube", "YouTube"),
        ("naver", "NAVER"),
        ("daum", "Daum"),
    ]
    partial_result: dict[str, object] = {
        "youtube": None,
        "naver": {"status": "partial", "items_read": 3},
        "daum": {"status": "failed", "items_read": 0},
        "errors": {"daum": "Daum request failed"},
    }
    unchanged_result: dict[str, object] = {
        "youtube": {"status": "skipped", "items_read": 0},
        "naver": None,
        "daum": {"status": "failed", "items_read": 0},
        "errors": {"daum": "Daum request failed"},
    }

    assert refresh_trends._background_exit_code(partial_result, source_specs) == 0
    assert refresh_trends._background_exit_code(unchanged_result, source_specs) == 0
