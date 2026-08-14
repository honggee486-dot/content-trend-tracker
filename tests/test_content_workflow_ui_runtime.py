from __future__ import annotations

import json
from types import SimpleNamespace

from src.database import connect_database, init_database
from src.services.ai_result_parser import parse_ai_result
from src.services.content_pack_service import save_content_pack
from src.services.content_workflow_ui_runtime import (
    _AiImportStreamlitProxy,
    _EditorStreamlitProxy,
    build_ai_import_action_state,
    build_light_html_preview,
    enforce_revision_update,
)
from src.services.draft_service import (
    get_draft,
    save_generation_and_draft,
    update_draft,
)
from src.services.topic_service import add_manual_topic


class _FakeColumn:
    def __init__(self, owner) -> None:
        self.owner = owner

    def button(self, label, *args, **kwargs):
        record = (str(label), dict(kwargs))
        self.owner.button_calls.append(record)
        return str(kwargs.get("key") or "") in self.owner.clicked_keys


class _FakeStreamlit:
    def __init__(self) -> None:
        self.session_state: dict[str, object] = {}
        self.button_calls: list[tuple[str, dict[str, object]]] = []
        self.clicked_keys: set[str] = set()
        self.markdowns: list[str] = []
        self.columns_calls: list[tuple[object, dict[str, object]]] = []
        self.checkbox_calls: list[tuple[object, dict[str, object]]] = []
        self.text_input_calls: list[tuple[object, dict[str, object]]] = []
        self.form_submit_calls: list[tuple[object, dict[str, object]]] = []

    def columns(self, spec, *args, **kwargs):
        self.columns_calls.append((spec, dict(kwargs)))
        return [_FakeColumn(self) for _ in range(3)]

    def markdown(self, body, *args, **kwargs):
        self.markdowns.append(str(body))

    def button(self, label, *args, **kwargs):
        self.button_calls.append((str(label), dict(kwargs)))
        return False

    def checkbox(self, label, *args, **kwargs):
        self.checkbox_calls.append((label, dict(kwargs)))
        return False

    def text_input(self, label, *args, **kwargs):
        self.text_input_calls.append((label, dict(kwargs)))
        return str(kwargs.get("value") or "")

    def form_submit_button(self, label, *args, **kwargs):
        self.form_submit_calls.append((label, dict(kwargs)))
        return False


def _row_disabled_values(fake: _FakeStreamlit) -> list[bool]:
    return [
        bool(kwargs.get("disabled", False))
        for _, kwargs in fake.button_calls[-3:]
    ]


def test_ai_import_actions_unlock_in_order_and_stale_results_lock_again() -> None:
    fake = _FakeStreamlit()
    proxy = _AiImportStreamlitProxy(fake)
    proxy.capture_fingerprint(content_pack_id="pack-1", fingerprint="fp-1")

    proxy.button("형식·출처 검사")
    assert [label for label, _ in fake.button_calls[-3:]] == [
        "1. 형식·출처 검사",
        "2. 검사 결과를 새 초안으로 저장",
        "3. 저장한 초안 편집으로 이동",
    ]
    assert _row_disabled_values(fake) == [False, True, True]

    fake.session_state.update(
        {
            "parse_result": SimpleNamespace(is_valid=True),
            "parse_pack_id": "pack-1",
            "parse_fingerprint": "fp-1",
        }
    )
    validated = build_ai_import_action_state(
        fake.session_state,
        content_pack_id="pack-1",
        fingerprint="fp-1",
    )
    assert validated.validation_is_current is True
    assert validated.can_save is True
    assert validated.can_edit is False

    stale = build_ai_import_action_state(
        fake.session_state,
        content_pack_id="pack-1",
        fingerprint="fp-changed",
    )
    assert stale.validation_is_current is False
    assert stale.can_save is False
    assert stale.can_edit is False

    fake.session_state.update(
        {
            "last_saved_fingerprint": "fp-1",
            "last_saved_draft_id": "draft-1",
        }
    )
    saved = build_ai_import_action_state(
        fake.session_state,
        content_pack_id="pack-1",
        fingerprint="fp-1",
    )
    assert saved.already_saved is True
    assert saved.can_save is False
    assert saved.can_edit is True


def test_ai_import_action_buttons_share_size_contract() -> None:
    fake = _FakeStreamlit()
    proxy = _AiImportStreamlitProxy(fake)
    proxy.capture_fingerprint(content_pack_id="pack", fingerprint="fp")
    proxy.button("형식·출처 검사")

    assert fake.columns_calls == [
        (3, {"gap": "small", "vertical_alignment": "center"})
    ]
    css = "\n".join(fake.markdowns)
    assert "min-height: 54px" in css
    assert "font-size: 14px" in css
    assert "font-weight: 600" in css
    assert all(kwargs["width"] == "stretch" for _, kwargs in fake.button_calls[-3:])


def test_editor_proxy_removes_revision_toggle_and_renames_save_fields() -> None:
    fake = _FakeStreamlit()
    proxy = _EditorStreamlitProxy(fake)

    assert proxy.checkbox("새 수정 버전으로 저장", value=True) is True
    assert fake.checkbox_calls == []

    assert proxy.text_input("수정 메모", value="사용자 편집") == "직접 편집"
    assert fake.text_input_calls == [
        ("변경 내용 메모", {"value": "직접 편집"})
    ]

    proxy.form_submit_button("글 저장", type="primary")
    assert fake.form_submit_calls == [
        ("수정 내용 저장", {"type": "primary"})
    ]


def test_revision_enforcing_update_creates_new_revision_even_if_false_requested(
    tmp_path,
) -> None:
    db_path = tmp_path / "revision-always.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        topic_id, _ = add_manual_topic(con, title="항상 리비전")
        pack = save_content_pack(
            con,
            topic_id=topic_id,
            audience="일반 독자",
            purpose="정보 제공",
            angle="핵심 정리",
            category="정보",
            target_length=1200,
            title_rules="과장 금지",
            outline="도입\n핵심\n정리",
            forbidden_expressions="무조건",
            fact_check_items="수치 확인",
        )
        payload = {
            "schema_version": "1.0",
            "title": "첫 제목",
            "summary": "요약",
            "category": "정보",
            "tags": ["리비전"],
            "body_markdown": "# 첫 제목\n\n" + ("본문 내용입니다. " * 40),
            "fact_checks": [],
            "sources": [],
            "image_prompts": [],
        }
        raw = json.dumps(payload, ensure_ascii=False)
        parsed = parse_ai_result(raw)
        _, draft_id = save_generation_and_draft(
            con,
            content_pack_id=pack["content_pack_id"],
            ai_provider="ChatGPT",
            raw_response=raw,
            result=parsed,
        )
        before_count = con.execute(
            "SELECT COUNT(*) FROM draft_revisions WHERE draft_id = ?",
            [draft_id],
        ).fetchone()[0]

        revision = enforce_revision_update(
            update_draft,
            con,
            draft_id=draft_id,
            title="수정 제목",
            summary="수정 요약",
            category="정보",
            tags=["리비전", "편집"],
            body_markdown="수정한 본문입니다.",
            create_revision=False,
            change_note="",
        )
        draft = get_draft(con, draft_id)
        after_count = con.execute(
            "SELECT COUNT(*) FROM draft_revisions WHERE draft_id = ?",
            [draft_id],
        ).fetchone()[0]
        latest_note = con.execute(
            """
            SELECT change_note
            FROM draft_revisions
            WHERE draft_id = ?
            ORDER BY revision_number DESC
            LIMIT 1
            """,
            [draft_id],
        ).fetchone()[0]

    assert revision == 2
    assert draft is not None
    assert draft["current_revision"] == 2
    assert draft["title"] == "수정 제목"
    assert after_count == before_count + 1
    assert latest_note == "직접 편집"


def test_html_preview_forces_light_readable_css_without_changing_stored_body() -> None:
    stored = '<h2>제목</h2><p style="color:white">본문</p><a href="#">링크</a>'
    rendered = build_light_html_preview(stored)

    assert stored in rendered
    assert "background: #ffffff !important" in rendered
    assert "color: #111827 !important" in rendered
    assert "color: #0b57d0 !important" in rendered
    assert "color-scheme: light" in rendered


def test_runtime_is_installed_from_streamlit_package_bootstrap() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "src" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "install_content_workflow_ui_runtime" in source
    assert "install_content_workflow_ui_runtime(_ui_module)" in source
