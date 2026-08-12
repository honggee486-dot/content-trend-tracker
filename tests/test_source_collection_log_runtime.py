from __future__ import annotations

import pytest

from src.services import source_collection_log_runtime as runtime


class _Adapter:
    pass


def test_source_collection_logging_records_start_and_actual_request_counts(
    monkeypatch,
    tmp_path,
) -> None:
    from src.services import trend_discovery_service as discovery

    captured = []
    monkeypatch.setattr(
        runtime,
        "record_program_event",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    def fake_refresh(*args, **kwargs):
        return {
            "naver": {
                "status": "success",
                "items_read": 120,
                "planned_request_count": 15,
                "request_count": 12,
                "successful_requests": 12,
                "failed_requests": 0,
                "skipped_requests": 3,
                "retry_count": 1,
            },
            "timings": {"naver": 1.25},
            "errors": {},
            "warnings": {},
        }

    monkeypatch.setattr(discovery, "refresh_trend_sources_short_connections", fake_refresh)
    runtime.install_source_collection_logging()

    result = discovery.refresh_trend_sources_short_connections(
        tmp_path / "test.duckdb",
        naver_adapter=_Adapter(),
        daum_adapter=None,
        google_trends_adapter=None,
        wikipedia_adapter=None,
        youtube_adapter=None,
        collection_run_id="collection_test",
    )

    assert result["naver"]["items_read"] == 120
    assert [row["status"] for row in captured] == ["started", "completed"]
    assert all(row["action"] == "NAVER 검색 API" for row in captured)
    assert all(row["correlation_id"] == "collection_test" for row in captured)
    assert captured[1]["item_count"] == 120
    assert captured[1]["duration_ms"] == 1250
    assert "실제 요청 12회/계획 15회" in captured[1]["detail"]
    assert "재시도 1" in captured[1]["detail"]


def test_each_provider_uses_its_own_timing(monkeypatch, tmp_path) -> None:
    from src.services import trend_discovery_service as discovery

    captured = []
    monkeypatch.setattr(
        runtime,
        "record_program_event",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    def fake_refresh(*args, **kwargs):
        return {
            "naver": {"items_read": 10},
            "daum": {"items_read": 20},
            "timings": {"naver": 2.5, "daum": 7.75},
            "errors": {},
            "warnings": {},
        }

    monkeypatch.setattr(discovery, "refresh_trend_sources_short_connections", fake_refresh)
    runtime.install_source_collection_logging()
    discovery.refresh_trend_sources_short_connections(
        tmp_path / "test.duckdb",
        naver_adapter=_Adapter(),
        daum_adapter=_Adapter(),
        google_trends_adapter=None,
        wikipedia_adapter=None,
        youtube_adapter=None,
    )

    completed = {
        row["action"]: row["duration_ms"]
        for row in captured
        if row["status"] == "completed"
    }
    assert completed == {
        "NAVER 검색 API": 2500,
        "Daum 검색 API": 7750,
    }


def test_progress_boundaries_keep_source_completion_before_ranking(monkeypatch, tmp_path) -> None:
    from src.services import trend_discovery_service as discovery

    captured = []
    monkeypatch.setattr(
        runtime,
        "record_program_event",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    def fake_refresh(*args, **kwargs):
        progress = kwargs["progress_callback"]
        progress(0.08, "1/7 YouTube 교환 파일 확인 중")
        progress(0.22, "2/7 Google Trends 네트워크 요청 중")
        progress(0.32, "3/7 위키백과 네트워크 요청 중")
        progress(0.43, "4/7 포털 탐색어와 호출 한도 확인 중")
        progress(0.52, "5/7 NAVER·Daum 네트워크 요청 동시 실행 중")
        progress(0.86, "7/7 통합 군집 자료 읽는 중")
        return {
            "youtube": {"items_read": 1},
            "google_trends": {"items_read": 2},
            "wikipedia": {"items_read": 3},
            "naver": {"items_read": 4},
            "daum": {"items_read": 5},
            "timings": {
                "youtube": 0.1,
                "google_trends": 0.2,
                "wikipedia": 0.3,
                "naver": 0.4,
                "daum": 0.5,
            },
            "errors": {},
            "warnings": {},
        }

    monkeypatch.setattr(discovery, "refresh_trend_sources_short_connections", fake_refresh)
    runtime.install_source_collection_logging()
    discovery.refresh_trend_sources_short_connections(
        tmp_path / "test.duckdb",
        youtube_adapter=_Adapter(),
        google_trends_adapter=_Adapter(),
        wikipedia_adapter=_Adapter(),
        naver_adapter=_Adapter(),
        daum_adapter=_Adapter(),
    )

    starts = {
        row["action"]: row["event_time"]
        for row in captured
        if row["status"] == "started"
    }
    completed = {
        row["action"]: row["event_time"]
        for row in captured
        if row["status"] == "completed"
    }
    assert set(starts) == set(completed)
    assert all(completed[action] >= starts[action] for action in starts)
    assert completed["NAVER 검색 API"] <= completed["Daum 검색 API"]
    assert completed["Wikimedia Pageviews API"] <= starts["NAVER 검색 API"]


def test_source_collection_logging_marks_provider_failure(monkeypatch, tmp_path) -> None:
    from src.services import trend_discovery_service as discovery

    captured = []
    monkeypatch.setattr(
        runtime,
        "record_program_event",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    def fake_refresh(*args, **kwargs):
        return {
            "daum": {
                "status": "failed",
                "items_read": 0,
                "request_count": 2,
                "successful_requests": 0,
                "failed_requests": 2,
                "skipped_requests": 0,
                "retry_count": 1,
            },
            "timings": {"daum": 3.0},
            "errors": {"daum": "HTTP 500"},
            "warnings": {},
        }

    monkeypatch.setattr(discovery, "refresh_trend_sources_short_connections", fake_refresh)
    runtime.install_source_collection_logging()

    discovery.refresh_trend_sources_short_connections(
        tmp_path / "test.duckdb",
        naver_adapter=None,
        daum_adapter=_Adapter(),
        google_trends_adapter=None,
        wikipedia_adapter=None,
        youtube_adapter=None,
    )

    assert captured[-1]["status"] == "failed"
    assert captured[-1]["action"] == "Daum 검색 API"
    assert captured[-1]["duration_ms"] == 3000
    assert "HTTP 500" in captured[-1]["detail"]


def test_downstream_ranking_failure_does_not_relabel_completed_sources_as_api_failures(
    monkeypatch,
    tmp_path,
) -> None:
    from src.services import trend_discovery_service as discovery

    captured = []
    monkeypatch.setattr(
        runtime,
        "record_program_event",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    def fake_refresh(*args, **kwargs):
        progress = kwargs["progress_callback"]
        progress(0.52, "5/7 NAVER·Daum 네트워크 요청 동시 실행 중")
        progress(0.86, "7/7 통합 군집 자료 읽는 중")
        raise RuntimeError("duplicate cluster key")

    monkeypatch.setattr(discovery, "refresh_trend_sources_short_connections", fake_refresh)
    runtime.install_source_collection_logging()

    with pytest.raises(RuntimeError, match="duplicate cluster key"):
        discovery.refresh_trend_sources_short_connections(
            tmp_path / "test.duckdb",
            naver_adapter=_Adapter(),
            daum_adapter=_Adapter(),
            google_trends_adapter=None,
            wikipedia_adapter=None,
            youtube_adapter=None,
        )

    finished = [row for row in captured if row["status"] != "started"]
    assert {row["action"] for row in finished} == {"NAVER 검색 API", "Daum 검색 API"}
    assert all(row["status"] == "completed" for row in finished)
    assert all("후속 군집·순위 처리" in row["detail"] for row in finished)
    assert all("출처 수집 중 전체 작업 중단" not in row["detail"] for row in finished)


def test_source_still_running_when_exception_occurs_is_marked_failed(
    monkeypatch,
    tmp_path,
) -> None:
    from src.services import trend_discovery_service as discovery

    captured = []
    monkeypatch.setattr(
        runtime,
        "record_program_event",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    def fake_refresh(*args, **kwargs):
        kwargs["progress_callback"](
            0.52,
            "5/7 NAVER·Daum 네트워크 요청 동시 실행 중",
        )
        raise RuntimeError("network interrupted")

    monkeypatch.setattr(discovery, "refresh_trend_sources_short_connections", fake_refresh)
    runtime.install_source_collection_logging()

    with pytest.raises(RuntimeError, match="network interrupted"):
        discovery.refresh_trend_sources_short_connections(
            tmp_path / "test.duckdb",
            naver_adapter=_Adapter(),
            daum_adapter=_Adapter(),
            google_trends_adapter=None,
            wikipedia_adapter=None,
            youtube_adapter=None,
        )

    finished = [row for row in captured if row["status"] == "failed"]
    assert len(finished) == 2
    assert all("출처 수집 중 전체 작업 중단" in row["detail"] for row in finished)
