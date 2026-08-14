from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import pandas as pd

from src.clustering_batch_log_ui import (
    BATCH_LOG_HELP,
    REQUEST_DETAIL_HELP,
    format_clustering_batch_log_frame,
    format_clustering_request_detail_frame,
    install_clustering_batch_log_ui,
    load_clustering_request_detail_rows,
)


def _batch_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "배치": 1,
                "상태": "success",
                "검색 미처리": 4000,
                "전체 1차 군집": 3997,
                "요청 1차 군집": 350,
                "요청 원문": 351,
                "입력 토큰": 112345,
                "출력 토큰": 23456,
                "사고 토큰": 0,
                "총 토큰": 135801,
                "시간(ms)": 123456,
                "오류": "",
            }
        ]
    )


def _detail_row() -> dict[str, object]:
    return {
        "batch_number": 1,
        "request_number": 2,
        "created_at": datetime(2026, 8, 15, 4, 32, 53),
        "model_name": "gemini-3.5-flash-lite",
        "analysis_view": "event",
        "requested_item_count": 282,
        "estimated_input_tokens": 95000,
        "input_tokens": 98368,
        "output_tokens": 3212,
        "thought_tokens": 0,
        "total_tokens": 101580,
        "tpm_wait_seconds": 42.25,
        "duration_ms": 9720,
        "http_status": 200,
        "status": "success",
        "error_type": "",
        "finish_reason": "STOP",
    }


def test_batch_log_uses_clear_labels_seconds_and_comma_formatting() -> None:
    formatted, matched = format_clustering_batch_log_frame(_batch_frame())

    assert matched is True
    assert "전체 1차 후보" in formatted.columns
    assert "2차 검토 후보" in formatted.columns
    assert "전체 1차 군집" not in formatted.columns
    assert "요청 1차 군집" not in formatted.columns
    assert "시간(초)" in formatted.columns
    assert "시간(ms)" not in formatted.columns
    assert formatted.loc[0, "검색 미처리"] == "4,000"
    assert formatted.loc[0, "전체 1차 후보"] == "3,997"
    assert formatted.loc[0, "2차 검토 후보"] == "350"
    assert formatted.loc[0, "입력 토큰"] == "112,345"
    assert formatted.loc[0, "총 토큰"] == "135,801"
    assert formatted.loc[0, "시간(초)"] == "123.5"
    assert "225,000토큰" in BATCH_LOG_HELP
    assert "여러 요청을 합산" in BATCH_LOG_HELP
    assert "요청별 상세 표" in BATCH_LOG_HELP


def test_unrelated_dataframe_is_not_changed() -> None:
    original = pd.DataFrame([{"항목": 1000}])

    formatted, matched = format_clustering_batch_log_frame(original)

    assert matched is False
    assert formatted is original


def test_request_detail_formatter_exposes_comparable_request_metrics() -> None:
    frame = format_clustering_request_detail_frame([_detail_row()])

    assert frame.loc[0, "배치"] == 1
    assert frame.loc[0, "요청"] == 2
    assert frame.loc[0, "시각"] == "04:32:53"
    assert frame.loc[0, "관점"] == "사건"
    assert frame.loc[0, "후보"] == "282"
    assert frame.loc[0, "예상 입력"] == "95,000"
    assert frame.loc[0, "실제 입력"] == "98,368"
    assert frame.loc[0, "출력"] == "3,212"
    assert frame.loc[0, "총 토큰"] == "101,580"
    assert frame.loc[0, "TPM 대기(초)"] == "42.2"
    assert frame.loc[0, "API 시간(초)"] == "9.7"
    assert frame.loc[0, "HTTP"] == "200"
    assert frame.loc[0, "상태"] == "success"
    assert frame.loc[0, "종료"] == "STOP"


def test_request_detail_loader_matches_only_calls_inside_job_batch() -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE trend_clustering_job_batches (
            job_id VARCHAR,
            batch_number INTEGER,
            started_at TIMESTAMP,
            finished_at TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE TABLE gemini_api_calls (
            request_hash VARCHAR,
            feature_id VARCHAR,
            model_name VARCHAR,
            attempt_number INTEGER,
            requested_item_count INTEGER,
            input_tokens BIGINT,
            output_tokens BIGINT,
            thought_tokens BIGINT,
            total_tokens BIGINT,
            duration_ms BIGINT,
            http_status INTEGER,
            status VARCHAR,
            error_type VARCHAR,
            finish_reason VARCHAR,
            created_at TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE TABLE trend_clustering_request_metrics (
            request_hash VARCHAR,
            feature_id VARCHAR,
            analysis_view VARCHAR,
            requested_item_count INTEGER,
            estimated_input_tokens BIGINT,
            actual_input_tokens BIGINT,
            tpm_wait_seconds DOUBLE,
            duration_ms BIGINT
        )
        """
    )
    started = datetime(2026, 8, 15, 4, 30, 0)
    finished = started + timedelta(minutes=5)
    con.execute(
        "INSERT INTO trend_clustering_job_batches VALUES (?, 1, ?, ?)",
        ["job-1", started, finished],
    )
    con.execute(
        """
        INSERT INTO gemini_api_calls VALUES
        ('req-match', 'trend_cluster_grouping_v3', 'gemini-3.5-flash-lite', 1,
         282, 98368, 3212, 0, 101580, 9720, 200, 'success', '', 'STOP', ?),
        ('req-topic', 'trend_topic_angle_batch_v1', 'gemini-3.7-flash', 1,
         5, 8000, 1000, 500, 9500, 40000, 500, 'failed', 'service_unavailable', '', ?),
        ('req-late', 'trend_cluster_grouping_v3', 'gemini-3.5-flash-lite', 1,
         100, 20000, 1000, 0, 21000, 1000, 200, 'success', '', 'STOP', ?)
        """,
        [
            started + timedelta(minutes=2),
            started + timedelta(minutes=3),
            finished + timedelta(seconds=1),
        ],
    )
    con.execute(
        """
        INSERT INTO trend_clustering_request_metrics VALUES
        ('req-match', 'trend_cluster_grouping_v3', 'event', 282, 95000, 98368, 42.25, 9720)
        """
    )

    rows = load_clustering_request_detail_rows(con, job_id="job-1")

    assert len(rows) == 1
    assert rows[0]["request_number"] == 1
    assert rows[0]["analysis_view"] == "event"
    assert rows[0]["requested_item_count"] == 282
    assert rows[0]["estimated_input_tokens"] == 95000
    assert rows[0]["input_tokens"] == 98368
    assert rows[0]["tpm_wait_seconds"] == 42.25
    assert rows[0]["http_status"] == 200


def test_installer_adds_summary_and_request_detail_tables() -> None:
    rendered = []
    captions = []
    markdowns = []

    class FakeStreamlit:
        def dataframe(self, data, *args, **kwargs):
            rendered.append((data, args, kwargs))
            return "rendered"

        def caption(self, value):
            captions.append(value)

        def markdown(self, value):
            markdowns.append(value)

    fake = FakeStreamlit()
    install_clustering_batch_log_ui(fake, detail_loader=lambda: [_detail_row()])

    result = fake.dataframe(_batch_frame(), hide_index=True, width="stretch")

    assert result == "rendered"
    assert captions == [BATCH_LOG_HELP, REQUEST_DETAIL_HELP]
    assert markdowns == ["**실제 Gemini 요청별 토큰·시간 비교**"]
    assert len(rendered) == 2
    assert rendered[0][0].loc[0, "총 토큰"] == "135,801"
    assert rendered[0][0].loc[0, "시간(초)"] == "123.5"
    assert rendered[0][2] == {"hide_index": True, "width": "stretch"}
    assert rendered[1][0].loc[0, "실제 입력"] == "98,368"
    assert rendered[1][0].loc[0, "API 시간(초)"] == "9.7"


def test_installer_does_not_load_details_for_unrelated_table() -> None:
    calls = []

    class FakeStreamlit:
        def dataframe(self, data, *args, **kwargs):
            return "rendered"

        def caption(self, value):
            pass

        def markdown(self, value):
            pass

    fake = FakeStreamlit()
    install_clustering_batch_log_ui(
        fake,
        detail_loader=lambda: calls.append("loaded") or [],
    )

    fake.dataframe(pd.DataFrame([{"항목": 1}]))

    assert calls == []
