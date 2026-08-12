from __future__ import annotations

from pathlib import Path

from src.services.workflow_navigation_service import (
    CONTENT_PACK_REUSE_PAYLOAD_KEY,
    prepare_workflow_navigation_state,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_full_content_workflow_navigation_sequence_isolated_by_target() -> None:
    state = {
        "page": "오늘의 트렌드",
        "ai_import_raw_pack_old": "사용자가 붙여넣은 이전 원문",
        "unrelated_state": "keep",
    }
    reuse_payload = {
        "topic_id": "topic_a",
        "topic_title": "주제 A",
        "version": 2,
    }

    prepare_workflow_navigation_state(
        state,
        "AI 요청서",
        {
            "prefill_topic_id": "topic_a",
            "prefill_angle": "주제 A 방향",
            CONTENT_PACK_REUSE_PAYLOAD_KEY: reuse_payload,
        },
    )

    assert state["page"] == "AI 요청서"
    assert state["prefill_topic_id"] == "topic_a"
    assert state["prefill_angle"] == "주제 A 방향"
    assert state[CONTENT_PACK_REUSE_PAYLOAD_KEY] == reuse_payload

    refresh_snapshot = dict(state)
    removed_on_refresh = prepare_workflow_navigation_state(state, "AI 요청서")

    assert removed_on_refresh == ()
    assert state == refresh_snapshot

    prepare_workflow_navigation_state(
        state,
        "AI 요청서",
        {"prefill_topic_id": "topic_b"},
    )

    assert state["prefill_topic_id"] == "topic_b"
    assert "prefill_angle" not in state
    assert CONTENT_PACK_REUSE_PAYLOAD_KEY not in state

    state.update(
        {
            "parse_result": {"title": "이전 자료팩 결과"},
            "parse_raw": "이전 파싱 원문",
            "parse_pack_id": "pack_old",
            "parse_provider": "ChatGPT",
            "parse_fingerprint": "fingerprint-old",
        }
    )
    prepare_workflow_navigation_state(
        state,
        "AI 결과 가져오기",
        {"prefill_content_pack_id": "pack_b"},
    )

    assert state["page"] == "AI 결과 가져오기"
    assert state["prefill_content_pack_id"] == "pack_b"
    assert "prefill_topic_id" not in state
    assert "parse_result" not in state
    assert "parse_raw" not in state
    assert "parse_pack_id" not in state
    assert "parse_provider" not in state
    assert "parse_fingerprint" not in state
    assert state["ai_import_raw_pack_old"] == "사용자가 붙여넣은 이전 원문"

    state.update(
        {
            "parse_result": {"title": "현재 자료팩 결과"},
            "parse_raw": "현재 파싱 원문",
            "parse_pack_id": "pack_b",
            "parse_provider": "Gemini",
            "parse_fingerprint": "fingerprint-b",
        }
    )
    removed_on_same_pack = prepare_workflow_navigation_state(
        state,
        "AI 결과 가져오기",
        {"prefill_content_pack_id": "pack_b"},
    )

    assert removed_on_same_pack == ()
    assert state["parse_result"] == {"title": "현재 자료팩 결과"}
    assert state["parse_pack_id"] == "pack_b"
    assert state["parse_fingerprint"] == "fingerprint-b"

    prepare_workflow_navigation_state(
        state,
        "글 편집",
        {"prefill_draft_id": "draft_b"},
    )

    assert state["page"] == "글 편집"
    assert state["prefill_draft_id"] == "draft_b"
    assert "prefill_content_pack_id" not in state
    assert "parse_result" not in state
    assert "parse_pack_id" not in state
    assert state["ai_import_raw_pack_old"] == "사용자가 붙여넣은 이전 원문"

    prepare_workflow_navigation_state(
        state,
        "발행 보조",
        {"prefill_draft_id": "draft_b"},
    )

    assert state["page"] == "발행 보조"
    assert state["prefill_draft_id"] == "draft_b"

    prepare_workflow_navigation_state(
        state,
        "AI 요청서",
        {"prefill_topic_id": "topic_c"},
    )

    assert state["page"] == "AI 요청서"
    assert state["prefill_topic_id"] == "topic_c"
    assert "prefill_draft_id" not in state
    assert "prefill_content_pack_id" not in state
    assert CONTENT_PACK_REUSE_PAYLOAD_KEY not in state
    assert state["unrelated_state"] == "keep"


def test_streamlit_workflow_wrappers_have_duplicate_installation_guards() -> None:
    source = (PROJECT_ROOT / "src" / "ui.py").read_text(encoding="utf-8")

    wrapper_contracts = (
        ("_content_work_queue_wrapper", "render_trend_dashboard"),
        ("_content_pack_history_wrapper", "render_content_pack"),
        ("_draft_revision_wrapper", "render_editor"),
        ("_publish_history_wrapper", "render_publish"),
    )
    for marker, target_name in wrapper_contracts:
        assert f'getattr(target, "{marker}", False)' in source
        assert f"wrapped.{marker} = True" in source
        assert source.count(f'caller_globals["{target_name}"] = wrapped') == 1


def test_current_gemini_capacity_copy_uses_runtime_limit_and_15_item_default() -> None:
    app_source = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")

    assert "BACKGROUND_TOPIC_ANGLE_ITEMS_PER_REQUEST" in app_source
    assert "현재 자동 분석: 요청당 최대" in app_source
    assert "topic_angle_batch_limit" in app_source
    assert "기본 구성은 요청당 15개·동시 요청 1개입니다." in app_source
    assert "기본 구성은 100개를 1회 요청으로 처리합니다." not in app_source
