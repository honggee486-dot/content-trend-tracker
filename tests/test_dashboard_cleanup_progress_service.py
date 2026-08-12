from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.services.dashboard_cleanup_progress_service import (
    cleanup_preflight_progress_message,
    post_collection_cleanup_progress_message,
)


def test_cleanup_preflight_distinguishes_none_from_deferred_cleanup() -> None:
    assert cleanup_preflight_progress_message(None) == (
        "자동 정리 대상 없음 · 출처 수집 시작"
    )
    assert cleanup_preflight_progress_message(SimpleNamespace(executed=False)) == (
        "자동 정리 예약 완료 · 출처 저장 후 순위 준비 직전에 실행"
    )


def test_post_collection_message_reports_executed_cleanup_count() -> None:
    cleanup = SimpleNamespace(executed=True, total_rows_deleted=1234)

    assert post_collection_cleanup_progress_message(
        cleanup,
        {"ranking": {"status": "success"}},
    ) == "출처 저장 후 자동 정리 1,234건 · 군집·순위 처리 완료"


def test_post_collection_message_explains_all_source_failure_preservation() -> None:
    cleanup = SimpleNamespace(executed=False, total_rows_deleted=0)

    assert post_collection_cleanup_progress_message(
        cleanup,
        {"ranking": {"status": "skipped_source_failure"}},
    ) == "전체 출처 실패 · 예약된 자동 정리 보류 및 기존 순위·보존 자료 유지"


def test_post_collection_message_reports_cleanup_not_due() -> None:
    assert post_collection_cleanup_progress_message(
        None,
        {"ranking": {"status": "success"}},
    ) == "출처 저장 및 군집·순위 처리 완료 · 자동 정리 대상 없음"


def test_post_collection_message_keeps_overlap_status_distinct() -> None:
    cleanup = SimpleNamespace(executed=True, total_rows_deleted=42)

    assert post_collection_cleanup_progress_message(
        cleanup,
        {"ranking": {"status": "skipped_overlap"}},
    ) == "출처 저장 후 자동 정리 42건 · 기존 군집 작업으로 순위 재계산 보류"


def test_dashboard_refresh_script_uses_deferred_cleanup_progress_messages() -> None:
    project_root = Path(__file__).resolve().parents[1]
    text = (project_root / "scripts" / "refresh_trends_dashboard.py").read_text(
        encoding="utf-8"
    )

    assert "cleanup_preflight_progress_message(result)" in text
    assert "post_collection_cleanup_progress_message(" in text
    assert 'cleanup_state: dict[str, Any] = {"result": None}' in text
    assert 'cleanup_state["result"] = result' in text
    assert '"수집 설정과 저장 자료 정리 확인 완료"' not in text
