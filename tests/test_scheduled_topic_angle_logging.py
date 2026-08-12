from src.services.scheduled_topic_angle_log_service import (
    build_scheduled_topic_angle_event,
    record_scheduled_topic_angle_outcome,
    run_refresh_body_with_topic_angle_log,
)


def test_scheduled_topic_angle_event_records_generated_results() -> None:
    event = build_scheduled_topic_angle_event(
        {
            "status": "success_after_retry",
            "requested_clusters": 20,
            "generated_clusters": 18,
            "generated_angles": 54,
            "duration_seconds": 12.345,
        }
    )

    assert event["status"] == "completed"
    assert event["item_count"] == 18
    assert event["duration_ms"] == 12345
    assert "방향 54개 저장" in event["detail"]


def test_scheduled_topic_angle_event_reports_generation_with_remaining_backlog() -> None:
    event = build_scheduled_topic_angle_event(
        {
            "status": "success",
            "requested_clusters": 20,
            "generated_clusters": 20,
            "generated_angles": 60,
            "clustering_remaining_items": 1200,
            "resumed_with_clustering_backlog": True,
        }
    )

    assert event["status"] == "completed"
    assert "방향 60개 저장" in event["detail"]
    assert "군집 미처리 1,200개" in event["detail"]
    assert "다음 실행에서 계속 처리" in event["detail"]
    assert event["metadata"]["clustering_remaining_items"] == 1200
    assert event["metadata"]["resumed_with_clustering_backlog"] is True


def test_scheduled_topic_angle_event_records_deferred_and_no_work() -> None:
    deferred = build_scheduled_topic_angle_event(
        {
            "status": "deferred_for_clustering_backlog",
            "remaining_items": 4403,
        }
    )
    nothing = build_scheduled_topic_angle_event(
        {
            "status": "nothing_to_generate",
        }
    )

    assert deferred["status"] == "skipped"
    assert "군집 미처리 4,403개" in deferred["detail"]
    assert "보류" in deferred["detail"]
    assert nothing["status"] == "skipped"
    assert "대상이 없습니다" in nothing["detail"]


def test_scheduled_topic_angle_event_records_failures_and_missing_result() -> None:
    failed = build_scheduled_topic_angle_event(
        {
            "status": "unexpected_error",
            "error_message": "temporary failure",
        }
    )
    missing = build_scheduled_topic_angle_event(None)

    assert failed["status"] == "failed"
    assert failed["detail"] == "temporary failure"
    assert missing["status"] == "failed"
    assert "결과가 반환되지 않았습니다" in missing["detail"]


def test_scheduled_topic_angle_outcome_keeps_collection_correlation() -> None:
    captured: dict[str, object] = {}

    def fake_recorder(**kwargs) -> bool:
        captured.update(kwargs)
        return True

    recorded = record_scheduled_topic_angle_outcome(
        {
            "topic_angles": {
                "status": "nothing_to_generate",
                "requested_clusters": 0,
                "generated_clusters": 0,
                "generated_angles": 0,
            }
        },
        collection_run_id="collection-123",
        db_path="test.duckdb",
        recorder=fake_recorder,
    )

    assert recorded is True
    assert captured["status"] == "skipped"
    assert captured["source"] == "scheduled_topic_angles"
    assert captured["action"] == "예약 수집 후 주제 방향 자동 생성"
    assert captured["correlation_id"] == "collection-123"
    assert captured["db_path"] == "test.duckdb"


def test_refresh_body_wrapper_records_topic_angle_outcome() -> None:
    calls: list[tuple[str, object]] = []
    result = {
        "topic_angles": {
            "status": "deferred_for_clustering_backlog",
            "remaining_items": 8,
        }
    }

    def fake_runner(collection_run_id: str | None):
        calls.append(("runner", collection_run_id))
        return 0, result

    def fake_record(refresh_result, *, collection_run_id, db_path):
        calls.append(("record", (refresh_result, collection_run_id, db_path)))
        return True

    assert run_refresh_body_with_topic_angle_log(
        fake_runner,
        "collection-456",
        db_path="test.duckdb",
        outcome_recorder=fake_record,
    ) == (0, result)
    assert calls[0] == ("runner", "collection-456")
    assert calls[1][0] == "record"
    assert calls[1][1][0] is result
    assert calls[1][1][1] == "collection-456"
    assert calls[1][1][2] == "test.duckdb"
