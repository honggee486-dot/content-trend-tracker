from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable


_STEP_VALIDATE_LABEL = "1. 형식·출처 검사"
_STEP_SAVE_LABEL = "2. 검사 결과를 새 초안으로 저장"
_STEP_EDIT_LABEL = "3. 저장한 초안 편집으로 이동"
_STEP_KEYS = (
    "ai_import_step_validate",
    "ai_import_step_save",
    "ai_import_step_edit",
)
_ACTION_ROW_CSS = """
<style>
.st-key-ai_import_step_validate button,
.st-key-ai_import_step_save button,
.st-key-ai_import_step_edit button {
    width: 100%;
    min-height: 54px;
    font-size: 14px;
    font-weight: 600;
}
.st-key-ai_import_step_validate button p,
.st-key-ai_import_step_save button p,
.st-key-ai_import_step_edit button p {
    font-size: 14px;
    font-weight: 600;
}
</style>
"""
_LIGHT_PREVIEW_STYLE = """
<style>
:root { color-scheme: light; }
html, body {
    background: #ffffff !important;
    color: #111827 !important;
}
body {
    margin: 0;
    padding: 1rem 1.15rem 1.5rem;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 16px;
    line-height: 1.7;
}
body * {
    color: #111827 !important;
}
a, a * {
    color: #0b57d0 !important;
}
blockquote {
    margin-left: 0;
    padding: 0.65rem 0.9rem;
    border-left: 4px solid #cbd5e1;
    background: #f8fafc !important;
}
pre, code {
    background: #f3f4f6 !important;
    color: #111827 !important;
}
pre {
    padding: 0.8rem;
    overflow-x: auto;
}
table {
    border-collapse: collapse;
    width: 100%;
}
th, td {
    border: 1px solid #d1d5db;
    padding: 0.5rem 0.65rem;
}
hr {
    border: 0;
    border-top: 1px solid #d1d5db;
}
</style>
"""


@dataclass(frozen=True)
class AiImportActionState:
    validation_is_current: bool
    can_save: bool
    already_saved: bool
    can_edit: bool


def build_ai_import_action_state(
    session_state: Any,
    *,
    content_pack_id: str,
    fingerprint: str,
) -> AiImportActionState:
    result = session_state.get("parse_result")
    checked_pack_id = str(session_state.get("parse_pack_id") or "")
    checked_fingerprint = str(session_state.get("parse_fingerprint") or "")
    validation_is_current = bool(
        result is not None
        and checked_pack_id == str(content_pack_id)
        and checked_fingerprint == str(fingerprint)
    )
    saved_draft_id = str(session_state.get("last_saved_draft_id") or "")
    already_saved = bool(
        validation_is_current
        and saved_draft_id
        and str(session_state.get("last_saved_fingerprint") or "")
        == str(fingerprint)
    )
    can_save = bool(
        validation_is_current
        and bool(getattr(result, "is_valid", False))
        and not already_saved
    )
    return AiImportActionState(
        validation_is_current=validation_is_current,
        can_save=can_save,
        already_saved=already_saved,
        can_edit=already_saved,
    )


def build_light_html_preview(body_html: object) -> str:
    body = str(body_html or "")
    lowered = body.lower()
    head_start = lowered.find("<head")
    if head_start >= 0:
        head_end = lowered.find(">", head_start)
        if head_end >= 0:
            return body[: head_end + 1] + _LIGHT_PREVIEW_STYLE + body[head_end + 1 :]
    return _LIGHT_PREVIEW_STYLE + body


def enforce_revision_update(
    update_func: Callable[..., int],
    *args,
    **kwargs,
) -> int:
    render_kwargs = dict(kwargs)
    render_kwargs["create_revision"] = True
    if not str(render_kwargs.get("change_note") or "").strip():
        render_kwargs["change_note"] = "직접 편집"
    return update_func(*args, **render_kwargs)


class _AiImportStreamlitProxy:
    def __init__(self, target) -> None:
        self._target = target
        self._content_pack_id = ""
        self._fingerprint = ""
        self.validate_clicked = False
        self._save_clicked = False
        self._edit_clicked = False
        self._row_rendered = False

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def capture_fingerprint(self, *, content_pack_id: object, fingerprint: object) -> None:
        self._content_pack_id = str(content_pack_id or "")
        self._fingerprint = str(fingerprint or "")

    def _render_action_row(self) -> bool:
        if self._row_rendered:
            return self.validate_clicked
        self._row_rendered = True
        state = build_ai_import_action_state(
            self._target.session_state,
            content_pack_id=self._content_pack_id,
            fingerprint=self._fingerprint,
        )
        self._target.markdown(_ACTION_ROW_CSS, unsafe_allow_html=True)
        columns = self._target.columns(
            3,
            gap="small",
            vertical_alignment="center",
        )
        self.validate_clicked = columns[0].button(
            _STEP_VALIDATE_LABEL,
            key=_STEP_KEYS[0],
            type="primary",
            width="stretch",
        )
        self._save_clicked = columns[1].button(
            _STEP_SAVE_LABEL,
            key=_STEP_KEYS[1],
            type="primary",
            width="stretch",
            disabled=not state.can_save,
        )
        self._edit_clicked = columns[2].button(
            _STEP_EDIT_LABEL,
            key=_STEP_KEYS[2],
            type="primary",
            width="stretch",
            disabled=not state.can_edit,
        )
        return self.validate_clicked

    def button(self, label: object, *args, **kwargs):
        text = str(label or "")
        if text == "형식·출처 검사":
            return self._render_action_row()
        if text == "검사 결과를 새 초안으로 저장":
            return self._save_clicked
        if text == "저장한 초안 편집으로 이동":
            return self._edit_clicked
        return self._target.button(label, *args, **kwargs)


class _EditorStreamlitProxy:
    def __init__(self, target) -> None:
        self._target = target

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def checkbox(self, label: object, *args, **kwargs):
        if str(label or "") == "새 수정 버전으로 저장":
            return True
        return self._target.checkbox(label, *args, **kwargs)

    def text_input(self, label: object, *args, **kwargs):
        if str(label or "") != "수정 메모":
            return self._target.text_input(label, *args, **kwargs)
        render_kwargs = dict(kwargs)
        render_kwargs["value"] = "직접 편집"
        render_kwargs.pop("disabled", None)
        return self._target.text_input("변경 내용 메모", *args, **render_kwargs)

    def form_submit_button(self, label: object, *args, **kwargs):
        if str(label or "") == "글 저장":
            return self._target.form_submit_button(
                "수정 내용 저장",
                *args,
                **kwargs,
            )
        return self._target.form_submit_button(label, *args, **kwargs)


class _PreviewComponentsProxy:
    def __init__(self, target) -> None:
        self._target = target

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def html(self, body: object, *args, **kwargs):
        return self._target.html(
            build_light_html_preview(body),
            *args,
            **kwargs,
        )


def _install_ai_import_ui(caller_globals: dict[str, object]) -> None:
    target = caller_globals.get("render_ai_import")
    original_fingerprint = caller_globals.get("build_ai_result_validation_fingerprint")
    original_streamlit = caller_globals.get("st")
    if (
        not callable(target)
        or not callable(original_fingerprint)
        or original_streamlit is None
        or getattr(target, "_content_workflow_ai_import_wrapper", False)
    ):
        return

    @wraps(target)
    def wrapped(*args, **kwargs):
        streamlit_module = caller_globals.get("st")
        fingerprint_func = caller_globals.get("build_ai_result_validation_fingerprint")
        proxy = _AiImportStreamlitProxy(streamlit_module)

        def capture_fingerprint(*fingerprint_args, **fingerprint_kwargs):
            fingerprint = fingerprint_func(*fingerprint_args, **fingerprint_kwargs)
            content_pack_id = fingerprint_kwargs.get("content_pack_id")
            if content_pack_id is None and fingerprint_args:
                content_pack_id = fingerprint_args[0]
            proxy.capture_fingerprint(
                content_pack_id=content_pack_id,
                fingerprint=fingerprint,
            )
            return fingerprint

        caller_globals["st"] = proxy
        caller_globals["build_ai_result_validation_fingerprint"] = capture_fingerprint
        try:
            result = target(*args, **kwargs)
        finally:
            caller_globals["st"] = streamlit_module
            caller_globals["build_ai_result_validation_fingerprint"] = fingerprint_func
        if proxy.validate_clicked:
            streamlit_module.rerun()
        return result

    wrapped._content_workflow_ai_import_wrapper = True  # type: ignore[attr-defined]
    caller_globals["render_ai_import"] = wrapped


def _install_editor_ui(caller_globals: dict[str, object]) -> None:
    target = caller_globals.get("render_editor")
    original_streamlit = caller_globals.get("st")
    original_components = caller_globals.get("components")
    original_update_draft = caller_globals.get("update_draft")
    if (
        not callable(target)
        or original_streamlit is None
        or original_components is None
        or not callable(original_update_draft)
        or getattr(target, "_content_workflow_editor_wrapper", False)
    ):
        return

    @wraps(target)
    def wrapped(*args, **kwargs):
        streamlit_module = caller_globals.get("st")
        components_module = caller_globals.get("components")
        update_func = caller_globals.get("update_draft")
        caller_globals["st"] = _EditorStreamlitProxy(streamlit_module)
        caller_globals["components"] = _PreviewComponentsProxy(components_module)

        def revision_update(*update_args, **update_kwargs):
            return enforce_revision_update(
                update_func,
                *update_args,
                **update_kwargs,
            )

        caller_globals["update_draft"] = revision_update
        try:
            return target(*args, **kwargs)
        finally:
            caller_globals["st"] = streamlit_module
            caller_globals["components"] = components_module
            caller_globals["update_draft"] = update_func

    wrapped._content_workflow_editor_wrapper = True  # type: ignore[attr-defined]
    caller_globals["render_editor"] = wrapped


def install_content_workflow_ui_runtime(ui_module) -> None:
    target = getattr(ui_module, "_install_inline_version_caption_ui", None)
    if not callable(target) or getattr(
        target,
        "_content_workflow_ui_runtime_wrapper",
        False,
    ):
        return

    @wraps(target)
    def wrapped(caller_globals: dict[str, object]) -> None:
        _install_ai_import_ui(caller_globals)
        _install_editor_ui(caller_globals)
        target(caller_globals)

    wrapped._content_workflow_ui_runtime_wrapper = True  # type: ignore[attr-defined]
    ui_module._install_inline_version_caption_ui = wrapped
