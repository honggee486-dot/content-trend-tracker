"""Streamlit 제작 화면 이동 전 충돌하는 임시 상태만 안전하게 정리합니다."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any


PAGE_KEY = "page"
PREFILL_TOPIC_KEY = "prefill_topic_id"
PREFILL_CONTENT_PACK_KEY = "prefill_content_pack_id"
PREFILL_DRAFT_KEY = "prefill_draft_id"
PREFILL_ANGLE_KEY = "prefill_angle"
CONTENT_PACK_REUSE_PAYLOAD_KEY = "content_pack_reuse_payload"

_PREFILL_KEYS = (
    PREFILL_TOPIC_KEY,
    PREFILL_CONTENT_PACK_KEY,
    PREFILL_DRAFT_KEY,
    PREFILL_ANGLE_KEY,
)

_AI_IMPORT_DERIVED_KEYS = (
    "parse_result",
    "parse_raw",
    "parse_pack_id",
    "parse_provider",
    "parse_fingerprint",
    "last_saved_fingerprint",
    "last_saved_draft_id",
)

_PAGE_ALLOWED_PREFILL_KEYS = {
    "AI 요청서": frozenset({PREFILL_TOPIC_KEY, PREFILL_ANGLE_KEY}),
    "AI 결과 가져오기": frozenset({PREFILL_CONTENT_PACK_KEY}),
    "글 편집": frozenset({PREFILL_DRAFT_KEY}),
    "발행 보조": frozenset({PREFILL_DRAFT_KEY}),
}

_MISSING = object()


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _pop(state: MutableMapping[str, Any], key: str, removed: list[str]) -> None:
    if key in state:
        state.pop(key, None)
        removed.append(key)


def _reuse_payload_topic_id(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return ""
    return _clean_text(payload.get("topic_id"))


def _reuse_topic_id(state: Mapping[str, Any]) -> str:
    return _reuse_payload_topic_id(state.get(CONTENT_PACK_REUSE_PAYLOAD_KEY))


def prepare_workflow_navigation_state(
    state: MutableMapping[str, Any],
    page: str,
    updates: Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    """대상 화면과 충돌하는 파생 상태를 제거하고 이동 상태를 준비합니다.

    사용자 원문(`ai_import_raw_<pack_id>`)이나 주제별 체크박스처럼 다시 활용할 수
    있는 입력값은 보존합니다. 파싱 결과·검증 지문과 서로 다른 주제·자료팩·초안의
    prefill 포인터 및 자료팩 재사용 payload만 대상 화면 계약에 맞게 정리합니다.
    """
    target_page = _clean_text(page)
    if not target_page:
        raise ValueError("이동할 화면을 지정하세요.")

    incoming = dict(updates or {})
    incoming_reuse_payload = incoming.pop(CONTENT_PACK_REUSE_PAYLOAD_KEY, _MISSING)
    reuse_update_provided = incoming_reuse_payload is not _MISSING
    removed: list[str] = []
    allowed_prefill = _PAGE_ALLOWED_PREFILL_KEYS.get(target_page, frozenset())
    for key in _PREFILL_KEYS:
        if key not in allowed_prefill:
            incoming.pop(key, None)

    previous_pack_id = _clean_text(state.get(PREFILL_CONTENT_PACK_KEY))
    pack_update_provided = PREFILL_CONTENT_PACK_KEY in incoming
    incoming_pack_id = _clean_text(incoming.get(PREFILL_CONTENT_PACK_KEY))
    target_pack_id = (
        incoming_pack_id
        if pack_update_provided
        else previous_pack_id
        if PREFILL_CONTENT_PACK_KEY in allowed_prefill
        else ""
    )

    previous_topic_id = _clean_text(state.get(PREFILL_TOPIC_KEY))
    topic_update_provided = PREFILL_TOPIC_KEY in incoming
    incoming_topic_id = _clean_text(incoming.get(PREFILL_TOPIC_KEY))
    target_topic_id = (
        incoming_topic_id
        if topic_update_provided
        else previous_topic_id
        if PREFILL_TOPIC_KEY in allowed_prefill
        else ""
    )

    active_parse_pack_id = _clean_text(state.get("parse_pack_id"))
    has_ai_import_derived_state = any(
        key in state for key in _AI_IMPORT_DERIVED_KEYS
    )

    for key in _PREFILL_KEYS:
        if key not in allowed_prefill:
            _pop(state, key, removed)

    switching_topic_without_angle = bool(
        target_page == "AI 요청서"
        and topic_update_provided
        and PREFILL_ANGLE_KEY not in incoming
        and previous_topic_id != target_topic_id
    )
    if switching_topic_without_angle:
        _pop(state, PREFILL_ANGLE_KEY, removed)

    leaving_ai_import = target_page != "AI 결과 가져오기"
    changing_ai_import_pack = False
    if target_page == "AI 결과 가져오기":
        known_pack_ids = {
            value
            for value in (previous_pack_id, active_parse_pack_id)
            if value
        }
        if not target_pack_id:
            changing_ai_import_pack = has_ai_import_derived_state
        elif any(value != target_pack_id for value in known_pack_ids):
            changing_ai_import_pack = True
        elif has_ai_import_derived_state and not known_pack_ids:
            changing_ai_import_pack = True

    if leaving_ai_import or changing_ai_import_pack:
        for key in _AI_IMPORT_DERIVED_KEYS:
            _pop(state, key, removed)

    current_reuse_topic_id = _reuse_topic_id(state)
    incoming_reuse_topic_id = (
        _reuse_payload_topic_id(incoming_reuse_payload)
        if reuse_update_provided
        else ""
    )
    target_reuse_topic_id = (
        incoming_reuse_topic_id
        if reuse_update_provided
        else current_reuse_topic_id
    )
    reuse_matches_target = bool(
        target_page == "AI 요청서"
        and target_reuse_topic_id
        and target_topic_id
        and target_reuse_topic_id == target_topic_id
    )
    if not reuse_matches_target:
        _pop(state, CONTENT_PACK_REUSE_PAYLOAD_KEY, removed)
    elif reuse_update_provided and isinstance(incoming_reuse_payload, Mapping):
        state[CONTENT_PACK_REUSE_PAYLOAD_KEY] = dict(incoming_reuse_payload)

    for key, value in incoming.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            _pop(state, str(key), removed)
        else:
            state[str(key)] = value

    state[PAGE_KEY] = target_page
    return tuple(dict.fromkeys(removed))
