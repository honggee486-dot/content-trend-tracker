from __future__ import annotations

import json
from pathlib import Path

from src.services.trend_cluster_live_progress import (
    _clear_context,
    _final_progress_state,
    _load_progress,
    _request_progress,
    _set_context,
    build_live_display_status,
    record_live_progress,
)
from src.database import connect_database


def test_request_progress_reads_view_and_sent_topic_count() -> None:
    request_text = "지시문\n\n" + json.dumps(
        {
            "batch_id": "cluster_batch_0001:title:0002",
            "view": "title",
            "candidates": [{"candidate_no": 1}, {"candidate_no": 2}],
        },
        ensure_ascii=False,
    )

    assert _request_progress(request_text) == ("title", 2)


def test_live_status_shows_view_topic_count_and_request_number() -> None:
    status = build_live_display_status(
        {
            "completed_batches": 0,
            "max_batches": 20,
        },
        {
            "phase": "calling",
            "analysis_view_label": "제목 기준",
            "topic_count": 1_842,
            "stage_topic_count": 8_671,
            "request_number": 2,
        },
    )

    assert status == (
        "실행 중 · 1/20차 · 제목 기준 군집 비교 호출 중 · "
        "1,842개 주제 · 2번째 요청"
    )


def test_progress_record_is_saved_with_additive_table(tmp_path: Path) -> None:
    db_path = tmp_path / "progress.duckdb"
    _set_context("job-1", db_path)
    try:
        record_live_progress(
            phase="calling",
            analysis_view="identity",
            request_number=3,
            topic_count=777,
            stage_topic_count=4_000,
            estimated_input_tokens=224_000,
        )
    finally:
        _clear_context()

    with connect_database(db_path) as con:
        progress = _load_progress(con, "job-1")

    assert progress["phase"] == "calling"
    assert progress["analysis_view"] == "identity"
    assert progress["analysis_view_label"] == "날짜·회차·제품·방향 기준"
    assert progress["request_number"] == 3
    assert progress["topic_count"] == 777
    assert progress["stage_topic_count"] == 4_000
    assert progress["estimated_input_tokens"] == 224_000


def test_overlap_final_progress_is_distinct_from_completed() -> None:
    state = _final_progress_state("skipped_overlap", exit_code=0)

    assert state == {
        "phase": "skipped_overlap",
        "message": "기존 작업 진행 중으로 생략 · Gemini 호출 및 DB 반영 없음",
        "event_status": "skipped",
        "event_detail": "실행 전 중복 차단 · Gemini 호출 및 DB 반영 없음",
    }

    assert _final_progress_state("success", exit_code=0)["phase"] == "completed"
    assert _final_progress_state("partial", exit_code=0)["phase"] == "completed"
    assert _final_progress_state("failed", exit_code=1)["phase"] == "failed"
