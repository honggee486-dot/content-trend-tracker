from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError

from src.adapters.daum_search_adapter import DaumSearchAdapter, DaumSearchError
from src.adapters.naver_search_adapter import NaverSearchAdapter
from src.database import connect_database, init_database
from src.query_discovery_diagnostics_ui import render_query_discovery_diagnostics
from src.services.collection_history_service import finish_collection_run, start_collection_run
from src.services.portal_request_ledger_service import record_portal_request_attempt
from src.services.portal_request_schema_service import ensure_portal_request_ledger_schema
from src.services.query_discovery_diagnostics_service import get_query_discovery_diagnostics


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class _FilterColumn:
    def selectbox(self, _label: str, *, options, **_kwargs):
        return options[0]


class _FakeStreamlit:
    def __init__(self) -> None:
        self.metrics: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.captions: list[str] = []
        self.dataframes: list[object] = []

    def subheader(self, _value: str) -> None:
        return None

    def columns(self, count: int) -> list[_FilterColumn]:
        return [_FilterColumn() for _ in range(count)]

    def container(self, **_kwargs) -> _Context:
        return _Context()

    def expander(self, *_args, **_kwargs) -> _Context:
        return _Context()

    def metric(self, *args, **kwargs) -> None:
        self.metrics.append((args, kwargs))

    def caption(self, value: str) -> None:
        self.captions.append(value)

    def dataframe(self, value, **_kwargs) -> None:
        self.dataframes.append(value)


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _finish_refresh_run(con, run_id: str) -> None:
    finish_collection_run(
        con,
        run_id,
        result={
            "naver": {
                "status": "partial",
                "request_count": 3,
                "retry_count": 1,
                "items_added": 1,
                "items_updated": 1,
                "items_skipped": 0,
            },
            "daum": {
                "status": "failed",
                "request_count": 1,
                "retry_count": 0,
                "items_added": 0,
                "items_updated": 0,
                "items_skipped": 0,
            },
            "errors": {"daum": "HTTP 429"},
            "warnings": {"naver": "일부 재시도"},
            "timings": {"naver": 0.1, "daum": 0.1},
        },
    )


def test_portal_attempts_flush_as_logical_requests_and_saved_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "portal-request-ledger.duckdb"
    init_database(db_path)
    started = datetime(2026, 7, 31, 5, 0, 0)

    with connect_database(db_path) as con:
        run_id = start_collection_run(con, "manual_refresh", started_at=started)
        retry_error = HTTPError("https://example.com", 429, "too many", None, None)
        record_portal_request_attempt(
            source_name="naver",
            source_type="naver_news",
            discovery_query="AI 검색",
            request_page=1,
            requested_result_count=10,
            result_count=0,
            duration_ms=100,
            started_at=started,
            finished_at=started + timedelta(milliseconds=100),
            error=retry_error,
        )
        record_portal_request_attempt(
            source_name="naver",
            source_type="naver_news",
            discovery_query="AI 검색",
            request_page=1,
            requested_result_count=10,
            result_count=2,
            duration_ms=80,
            started_at=started + timedelta(milliseconds=200),
            finished_at=started + timedelta(milliseconds=280),
        )
        record_portal_request_attempt(
            source_name="naver",
            source_type="naver_blog",
            discovery_query="빈 검색",
            request_page=1,
            requested_result_count=10,
            result_count=0,
            duration_ms=40,
            started_at=started,
            finished_at=started + timedelta(milliseconds=40),
        )
        record_portal_request_attempt(
            source_name="daum",
            source_type="daum_web",
            discovery_query="오류 검색",
            request_page=1,
            requested_result_count=10,
            result_count=0,
            duration_ms=60,
            started_at=started,
            finished_at=started + timedelta(milliseconds=60),
            error=retry_error,
        )
        con.executemany(
            """
            INSERT INTO collection_query_discoveries(
                run_id, source_name, source_type, discovery_query, source_item_id,
                external_id, source_url, is_new, result_rank, discovered_at
            ) VALUES (?, 'naver', 'naver_news', 'AI 검색', ?, ?, ?, ?, ?, ?)
            """,
            [
                [run_id, "item-new", "ext-new", "https://example.com/new", True, 1, started],
                [run_id, "item-old", "ext-old", "https://example.com/old", False, 2, started],
            ],
        )
        _finish_refresh_run(con, run_id)

        cursor = con.execute(
            """
            SELECT source_type, discovery_query, status, attempt_count, retry_count,
                   result_count, newly_saved_count, updated_count, skipped_count,
                   http_status, error_type
            FROM collection_query_requests
            WHERE run_id = ?
            ORDER BY source_type, discovery_query
            """,
            [run_id],
        )
        rows = {row[1]: row for row in cursor.fetchall()}
        diagnostics = get_query_discovery_diagnostics(con, days=7, now=started)

    ai_row = rows["AI 검색"]
    assert ai_row[2] == "success"
    assert ai_row[3:9] == (2, 1, 2, 1, 1, 0)
    assert ai_row[9] == 429
    assert ai_row[10] == "http_429"

    empty_row = rows["빈 검색"]
    assert empty_row[2] == "success"
    assert empty_row[5] == 0

    error_row = rows["오류 검색"]
    assert error_row[2] == "failure"
    assert error_row[9] == 429

    assert diagnostics["request_count"] == 3
    assert diagnostics["attempt_count"] == 4
    assert diagnostics["request_retry_count"] == 1
    assert diagnostics["successful_request_count"] == 2
    assert diagnostics["failed_request_count"] == 1
    assert diagnostics["zero_result_count"] == 1
    assert diagnostics["request_result_count"] == 2
    assert diagnostics["request_new_count"] == 1
    assert diagnostics["request_updated_count"] == 1
    assert diagnostics["request_error_rate_percent"] == 33.3
    assert diagnostics["zero_result_rate_percent"] == 50.0
    assert diagnostics["requests_per_new_item"] == 3.0


def test_adapters_capture_success_zero_result_and_wrapped_http_error(tmp_path: Path) -> None:
    db_path = tmp_path / "adapter-request-ledger.duckdb"
    init_database(db_path)

    naver = NaverSearchAdapter(
        "client",
        "secret",
        opener=lambda *_args, **_kwargs: _Response({"items": [], "total": 0}),
    )

    def raise_http_error(*_args, **_kwargs):
        raise HTTPError("https://example.com", 429, "too many", None, None)

    daum = DaumSearchAdapter("rest-key", opener=raise_http_error)

    with connect_database(db_path) as con:
        run_id = start_collection_run(con, "manual_refresh")
        assert naver.search(search_type="news", query="빈 결과", display=10, page=1) == []
        try:
            daum.search(search_type="web", query="오류 결과", size=10, page=1)
        except DaumSearchError:
            pass
        else:
            raise AssertionError("Daum HTTP 429가 예외로 전달되어야 합니다.")
        _finish_refresh_run(con, run_id)

        rows = con.execute(
            """
            SELECT source_name, discovery_query, status, result_count, http_status, error_type
            FROM collection_query_requests
            WHERE run_id = ?
            ORDER BY source_name
            """,
            [run_id],
        ).fetchall()

    assert rows == [
        ("daum", "오류 결과", "failure", 0, 429, "http_429"),
        ("naver", "빈 결과", "success", 0, None, None),
    ]


def test_query_diagnostics_ui_shows_request_efficiency_and_historical_scope(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "portal-request-ui.duckdb"
    init_database(db_path)
    now = datetime.now()
    with connect_database(db_path) as con:
        ensure_portal_request_ledger_schema(con)
        con.execute(
            """
            INSERT INTO collection_query_requests(
                run_id, source_name, source_type, discovery_query, request_page,
                requested_result_count, status, attempt_count, retry_count, result_count,
                newly_saved_count, updated_count, skipped_count, duration_ms,
                started_at, finished_at, created_at
            ) VALUES ('run-ui', 'naver', 'naver_news', 'AI 검색', 1,
                      10, 'success', 1, 0, 1, 1, 0, 0, 50, ?, ?, ?)
            """,
            [now, now, now],
        )
        con.execute(
            """
            INSERT INTO collection_query_discoveries(
                run_id, source_name, source_type, discovery_query, source_item_id,
                external_id, source_url, is_new, result_rank, discovered_at
            ) VALUES ('run-ui', 'naver', 'naver_news', 'AI 검색', 'item-ui',
                      'ext-ui', 'https://example.com/ui', TRUE, 1, ?)
            """,
            [now],
        )
        fake_st = _FakeStreamlit()
        render_query_discovery_diagnostics(con, st_module=fake_st)

    metric_labels = [str(args[0]) for args, _kwargs in fake_st.metrics]
    assert "논리 검색 요청" in metric_labels
    assert "결과 0건" in metric_labels
    assert "요청 오류" in metric_labels
    assert "신규 1건당 요청" in metric_labels
    assert "사용된 검색어" in metric_labels
    assert all(kwargs.get("border") is True for _args, kwargs in fake_st.metrics)
    assert len(fake_st.dataframes) == 4
    assert {"출처", "상태", "마지막 정상", "연속 문제"}.issubset(
        set(fake_st.dataframes[0].columns)
    )
    assert {"요청", "실제 시도", "오류율", "신규 1건당 요청"}.issubset(
        set(fake_st.dataframes[1].columns)
    )
    assert {"상태", "HTTP", "오류 유형", "오류 요약"}.issubset(
        set(fake_st.dataframes[2].columns)
    )
    assert {"발견", "신규율", "평균 순위", "최고 순위"}.issubset(
        set(fake_st.dataframes[3].columns)
    )
    assert any("0.10.66 이전에는 결과 0건이었던 검색 요청" in value for value in fake_st.captions)
