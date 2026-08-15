from __future__ import annotations

from datetime import datetime, timedelta

from src.operational_logs_ui import _gemini_frame


def test_gemini_frame_shows_actual_send_finish_and_common_rate_wait() -> None:
    started = datetime(2026, 8, 15, 12, 10, 0)
    finished = started + timedelta(seconds=8)
    frame = _gemini_frame(
        [
            {
                "created_at": started,
                "started_at": started,
                "finished_at": finished,
                "rate_limit_wait_seconds": 42.25,
                "status": "success",
                "model_name": "gemini-3.5-flash-lite",
                "feature_id": "trend_cluster_grouping_v3",
                "feature_version": "7",
                "attempt_number": 1,
                "cache_hit": False,
                "requested_item_count": 300,
                "input_tokens": 98368,
                "output_tokens": 3212,
                "thought_tokens": 0,
                "total_tokens": 101580,
                "http_status": 200,
                "finish_reason": "STOP",
                "duration_ms": 8000,
                "error_type": "",
                "error_message": "",
            }
        ]
    )

    assert frame.loc[0, "전송 시작"] == "2026-08-15 12:10:00"
    assert frame.loc[0, "완료"] == "2026-08-15 12:10:08"
    assert frame.loc[0, "상태"] == "성공"
    assert frame.loc[0, "요청 항목"] == "300"
    assert frame.loc[0, "입력 토큰"] == 98368
    assert frame.loc[0, "공통 제한 대기(초)"] == "42.25"
    assert frame.loc[0, "API 시간(초)"] == "8.00"


def test_gemini_frame_keeps_in_progress_request_visible_before_response() -> None:
    started = datetime(2026, 8, 15, 12, 20, 0)
    frame = _gemini_frame(
        [
            {
                "created_at": started,
                "started_at": started,
                "finished_at": None,
                "rate_limit_wait_seconds": 60.75,
                "status": "in_progress",
                "model_name": "gemini-3.5-flash-lite",
                "feature_id": "trend_cluster_grouping_v3",
                "feature_version": "",
                "attempt_number": 1,
                "cache_hit": False,
                "requested_item_count": None,
                "input_tokens": None,
                "output_tokens": None,
                "thought_tokens": None,
                "total_tokens": None,
                "http_status": None,
                "finish_reason": "",
                "duration_ms": 0,
                "error_type": "",
                "error_message": "",
            }
        ]
    )

    assert frame.loc[0, "전송 시작"] == "2026-08-15 12:20:00"
    assert frame.loc[0, "완료"] == "-"
    assert frame.loc[0, "상태"] == "전송 중"
    assert frame.loc[0, "요청 항목"] == "-"
    assert frame.loc[0, "입력 토큰"] == "-"
    assert frame.loc[0, "공통 제한 대기(초)"] == "60.75"
    assert frame.loc[0, "API 시간(초)"] == "-"
