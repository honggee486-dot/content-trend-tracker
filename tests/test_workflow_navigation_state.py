from __future__ import annotations

from pathlib import Path

import pytest

from src.services.workflow_navigation_service import (
    CONTENT_PACK_REUSE_PAYLOAD_KEY,
    prepare_workflow_navigation_state,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_request_page_clears_conflicting_prefills_and_preserves_matching_reuse() -> None:
    state = {
        "page": "AI 결과 가져오기",
        "prefill_topic_id": "topic_old",
        "prefill_content_pack_id": "pack_old",
        "prefill_draft_id": "draft_old",
        "parse_result": {"schema_version": "2.0"},
        "parse_pack_id": "pack_old",
        "parse_fingerprint": "fingerprint-old",
        "ai_import_raw_pack_old": "사용자가 붙여넣은 원문",
        CONTENT_PACK_REUSE_PAYLOAD_KEY: {"topic_id": "topic_new", "version": 2},
        "unrelated_state": "keep",
    }

    removed = prepare_workflow_navigation_state(
        state,
        "AI 요청서",
        {
            "prefill_topic_id": "topic_new",
            "prefill_angle": "검색 의도 중심",
        },
    )

    assert state["page"] == "AI 요청서"
    assert state["prefill_topic_id"] == "topic_new"
    assert state["prefill_angle"] == "검색 의도 중심"
    assert "prefill_content_pack_id" not in state
    assert "prefill_draft_id" not in state
    assert "parse_result" not in state
    assert "parse_pack_id" not in state
    assert state["ai_import_raw_pack_old"] == "사용자가 붙여넣은 원문"
    assert state[CONTENT_PACK_REUSE_PAYLOAD_KEY]["topic_id"] == "topic_new"
    assert state["unrelated_state"] == "keep"
    assert "prefill_content_pack_id" in removed
    assert "parse_result" in removed


def test_matching_incoming_reuse_payload_is_applied_atomically() -> None:
    state = {
        "page": "AI 요청서",
        "prefill_topic_id": "topic_old",
        "prefill_angle": "이전 주제 방향",
        CONTENT_PACK_REUSE_PAYLOAD_KEY: {"topic_id": "topic_old", "version": 1},
    }
    payload = {
        "topic_id": "topic_new",
        "topic_title": "새 주제",
        "version": 3,
        "audience": "새 독자",
    }

    removed = prepare_workflow_navigation_state(
        state,
        "AI 요청서",
        {
            "prefill_topic_id": "topic_new",
            CONTENT_PACK_REUSE_PAYLOAD_KEY: payload,
        },
    )

    assert state["page"] == "AI 요청서"
    assert state["prefill_topic_id"] == "topic_new"
    assert "prefill_angle" not in state
    assert state[CONTENT_PACK_REUSE_PAYLOAD_KEY] == payload
    assert state[CONTENT_PACK_REUSE_PAYLOAD_KEY] is not payload
    assert "prefill_angle" in removed


def test_mismatched_incoming_reuse_payload_cannot_reenter_target_topic() -> None:
    state = {
        "page": "AI 요청서",
        "prefill_topic_id": "topic_old",
        CONTENT_PACK_REUSE_PAYLOAD_KEY: {"topic_id": "topic_old", "version": 1},
    }

    removed = prepare_workflow_navigation_state(
        state,
        "AI 요청서",
        {
            "prefill_topic_id": "topic_new",
            CONTENT_PACK_REUSE_PAYLOAD_KEY: {
                "topic_id": "topic_wrong",
                "version": 9,
            },
        },
    )

    assert state["page"] == "AI 요청서"
    assert state["prefill_topic_id"] == "topic_new"
    assert CONTENT_PACK_REUSE_PAYLOAD_KEY not in state
    assert CONTENT_PACK_REUSE_PAYLOAD_KEY in removed


def test_switching_topics_without_new_angle_clears_old_angle() -> None:
    state = {
        "page": "AI 요청서",
        "prefill_topic_id": "topic_old",
        "prefill_angle": "이전 주제 방향",
    }

    removed = prepare_workflow_navigation_state(
        state,
        "AI 요청서",
        {"prefill_topic_id": "topic_new"},
    )

    assert state["prefill_topic_id"] == "topic_new"
    assert "prefill_angle" not in state
    assert "prefill_angle" in removed


def test_disallowed_incoming_prefills_cannot_reenter_target_page() -> None:
    state = {
        "page": "글 편집",
        "prefill_draft_id": "draft_old",
    }

    removed = prepare_workflow_navigation_state(
        state,
        "AI 요청서",
        {
            "prefill_topic_id": "topic_new",
            "prefill_angle": "새 주제 방향",
            "prefill_content_pack_id": "pack_wrong",
            "prefill_draft_id": "draft_wrong",
        },
    )

    assert state["page"] == "AI 요청서"
    assert state["prefill_topic_id"] == "topic_new"
    assert state["prefill_angle"] == "새 주제 방향"
    assert "prefill_content_pack_id" not in state
    assert "prefill_draft_id" not in state
    assert "prefill_draft_id" in removed


def test_new_content_pack_clears_old_parse_state_but_keeps_raw_inputs() -> None:
    state = {
        "page": "AI 결과 가져오기",
        "prefill_topic_id": "topic_old",
        "prefill_content_pack_id": "pack_old",
        "prefill_draft_id": "draft_old",
        "parse_result": {"title": "이전 결과"},
        "parse_raw": "이전 파싱 원문",
        "parse_pack_id": "pack_old",
        "parse_provider": "ChatGPT",
        "last_saved_draft_id": "draft_old",
        "ai_import_raw_pack_old": "이전 자료팩 원문",
        CONTENT_PACK_REUSE_PAYLOAD_KEY: {"topic_id": "topic_old"},
    }

    prepare_workflow_navigation_state(
        state,
        "AI 결과 가져오기",
        {"prefill_content_pack_id": "pack_new"},
    )

    assert state["page"] == "AI 결과 가져오기"
    assert state["prefill_content_pack_id"] == "pack_new"
    assert "prefill_topic_id" not in state
    assert "prefill_draft_id" not in state
    assert "parse_result" not in state
    assert "parse_raw" not in state
    assert "parse_pack_id" not in state
    assert "parse_provider" not in state
    assert "last_saved_draft_id" not in state
    assert CONTENT_PACK_REUSE_PAYLOAD_KEY not in state
    assert state["ai_import_raw_pack_old"] == "이전 자료팩 원문"


def test_same_content_pack_preserves_current_parse_state() -> None:
    state = {
        "page": "AI 결과 가져오기",
        "prefill_content_pack_id": "pack_same",
        "parse_result": {"title": "현재 결과"},
        "parse_pack_id": "pack_same",
        "parse_fingerprint": "same-fingerprint",
    }

    removed = prepare_workflow_navigation_state(
        state,
        "AI 결과 가져오기",
        {"prefill_content_pack_id": "pack_same"},
    )

    assert removed == ()
    assert state["parse_result"] == {"title": "현재 결과"}
    assert state["parse_pack_id"] == "pack_same"
    assert state["parse_fingerprint"] == "same-fingerprint"


def test_explicit_empty_pack_clears_parse_state_but_preserves_raw_input() -> None:
    state = {
        "page": "AI 결과 가져오기",
        "prefill_content_pack_id": "pack_old",
        "parse_result": {"title": "이전 결과"},
        "parse_raw": "이전 파싱 원문",
        "parse_pack_id": "pack_old",
        "parse_fingerprint": "fingerprint-old",
        "ai_import_raw_pack_old": "사용자가 붙여넣은 원문",
    }

    removed = prepare_workflow_navigation_state(
        state,
        "AI 결과 가져오기",
        {"prefill_content_pack_id": "   "},
    )

    assert state["page"] == "AI 결과 가져오기"
    assert "prefill_content_pack_id" not in state
    assert "parse_result" not in state
    assert "parse_raw" not in state
    assert "parse_pack_id" not in state
    assert "parse_fingerprint" not in state
    assert state["ai_import_raw_pack_old"] == "사용자가 붙여넣은 원문"
    assert "prefill_content_pack_id" in removed
    assert "parse_result" in removed


def test_explicit_empty_topic_clears_reuse_payload_and_old_angle() -> None:
    state = {
        "page": "AI 요청서",
        "prefill_topic_id": "topic_old",
        "prefill_angle": "이전 주제 방향",
        CONTENT_PACK_REUSE_PAYLOAD_KEY: {"topic_id": "topic_old", "version": 3},
    }

    removed = prepare_workflow_navigation_state(
        state,
        "AI 요청서",
        {"prefill_topic_id": None},
    )

    assert state["page"] == "AI 요청서"
    assert "prefill_topic_id" not in state
    assert "prefill_angle" not in state
    assert CONTENT_PACK_REUSE_PAYLOAD_KEY not in state
    assert "prefill_topic_id" in removed
    assert "prefill_angle" in removed
    assert CONTENT_PACK_REUSE_PAYLOAD_KEY in removed


def test_unidentified_parse_state_is_cleared_when_opening_pack() -> None:
    state = {
        "page": "AI 요청서",
        "parse_result": {"title": "기준 불명"},
        "parse_fingerprint": "unknown",
        "ai_import_raw_pack_new": "새 자료팩 원문",
    }

    prepare_workflow_navigation_state(
        state,
        "AI 결과 가져오기",
        {"prefill_content_pack_id": "pack_new"},
    )

    assert "parse_result" not in state
    assert "parse_fingerprint" not in state
    assert state["ai_import_raw_pack_new"] == "새 자료팩 원문"


def test_draft_navigation_clears_request_and_import_pointers() -> None:
    state = {
        "page": "AI 결과 가져오기",
        "prefill_topic_id": "topic_a",
        "prefill_content_pack_id": "pack_a",
        "prefill_angle": "이전 방향",
        "parse_result": {"title": "초안 후보"},
        "parse_pack_id": "pack_a",
        CONTENT_PACK_REUSE_PAYLOAD_KEY: {"topic_id": "topic_a"},
        "ai_import_raw_pack_a": "보존할 원문",
    }

    prepare_workflow_navigation_state(
        state,
        "글 편집",
        {"prefill_draft_id": "draft_a"},
    )

    assert state["page"] == "글 편집"
    assert state["prefill_draft_id"] == "draft_a"
    assert "prefill_topic_id" not in state
    assert "prefill_content_pack_id" not in state
    assert "prefill_angle" not in state
    assert "parse_result" not in state
    assert "parse_pack_id" not in state
    assert CONTENT_PACK_REUSE_PAYLOAD_KEY not in state
    assert state["ai_import_raw_pack_a"] == "보존할 원문"


def test_empty_page_is_rejected_without_mutating_state() -> None:
    state = {"page": "AI 요청서", "prefill_topic_id": "topic_a"}
    before = dict(state)

    with pytest.raises(ValueError, match="이동할 화면"):
        prepare_workflow_navigation_state(state, "   ")

    assert state == before


def test_high_risk_ui_actions_use_shared_navigation_cleanup() -> None:
    queue_source = (PROJECT_ROOT / "src" / "content_work_queue_ui.py").read_text(
        encoding="utf-8"
    )
    generation_source = (PROJECT_ROOT / "src" / "ai_generation_history_ui.py").read_text(
        encoding="utf-8"
    )
    pack_source = (PROJECT_ROOT / "src" / "content_pack_history_ui.py").read_text(
        encoding="utf-8"
    )

    assert "prepare_workflow_navigation_state" not in queue_source
    assert "navigate(target_page, **action_state)" in queue_source
    assert "prepare_workflow_navigation_state" in generation_source
    assert "prepare_workflow_navigation_state" in pack_source
    assert 'session_state["page"] = "AI 결과 가져오기"' not in generation_source
    assert 'session_state["page"] = "글 편집"' not in generation_source
    assert 'st_module.session_state[REUSE_PAYLOAD_KEY] = payload' not in pack_source
    assert '"prefill_topic_id": str(payload["topic_id"])' in pack_source
    assert "REUSE_PAYLOAD_KEY: payload" in pack_source


def test_next_work_marks_p1a_complete_and_p1b_validation_status() -> None:
    next_work = (PROJECT_ROOT / "docs" / "NEXT_WORK.md").read_text(
        encoding="utf-8"
    )

    assert "P1-A: `app.py`의 제작 화면 이동을 공통 이동 서비스로 통합한다. (완료)" in next_work
    assert "P1-B: 격리 Chromium에서 제작 흐름 핵심 회귀검증을 완료하고 실제 로컬 데이터 표본을 확인한다. (자동 검증 완료·수동 표본 남음)" in next_work
    assert "### P1-A. 앱 내부 이동 경로 통합 (완료)" in next_work
    assert "### P1-B. 브라우저 회귀검증 (자동 검증 완료·수동 표본 남음)" in next_work
    assert "공통 `navigate_to_page()` 콜백만 호출" in next_work
