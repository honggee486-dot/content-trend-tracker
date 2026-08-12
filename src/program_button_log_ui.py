from __future__ import annotations

import threading
from functools import wraps
from typing import Any, Callable

from src.services.program_log_service import record_program_event

_GUARD = threading.local()
_IGNORED_LABEL_PARTS = (
    "상태 새로고침",
    "목록 새로고침",
    "브랜치 새로고침",
)
_IGNORED_LABELS = {
    "이전",
    "다음",
    "닫기",
    "취소",
    "돌아가기",
}
_IGNORED_KEYS = {
    "refresh_clustering_job_status",
    "web_update_refresh_branches",
}


def _guard_active() -> bool:
    return bool(getattr(_GUARD, "active", False))


def _is_operational_button(label: Any, kwargs: dict[str, Any]) -> bool:
    clean_label = str(label or "").strip()
    if not clean_label or clean_label in _IGNORED_LABELS:
        return False
    if any(part in clean_label for part in _IGNORED_LABEL_PARTS):
        return False
    key = str(kwargs.get("key") or "").strip()
    return key not in _IGNORED_KEYS


def _record_button(label: Any, kwargs: dict[str, Any], *, source: str) -> None:
    if not _is_operational_button(label, kwargs):
        return
    clean_label = str(label or "").strip()
    key = str(kwargs.get("key") or "").strip()
    detail = f"버튼 키 {key}" if key else ""
    record_program_event(
        event_type="button",
        status="clicked",
        source=source,
        action=clean_label,
        detail=detail,
    )


def _wrap_module_button(
    st_module: Any,
    attribute_name: str,
) -> None:
    original = getattr(st_module, attribute_name, None)
    if not callable(original) or getattr(original, "_program_button_log", False):
        return

    @wraps(original)
    def wrapped(label: Any, *args: Any, **kwargs: Any):
        previous = _guard_active()
        _GUARD.active = True
        try:
            clicked = original(label, *args, **kwargs)
        finally:
            _GUARD.active = previous
        if bool(clicked):
            _record_button(label, kwargs, source=f"streamlit.{attribute_name}")
        return clicked

    wrapped._program_button_log = True  # type: ignore[attr-defined]
    setattr(st_module, attribute_name, wrapped)


def _wrap_delta_generator_method(method_name: str) -> None:
    try:
        from streamlit.delta_generator import DeltaGenerator
    except Exception:
        return
    original: Callable[..., Any] | None = getattr(DeltaGenerator, method_name, None)
    if not callable(original) or getattr(original, "_program_button_log", False):
        return

    @wraps(original)
    def wrapped(self, label: Any, *args: Any, **kwargs: Any):
        clicked = original(self, label, *args, **kwargs)
        if bool(clicked) and not _guard_active():
            _record_button(
                label,
                kwargs,
                source=f"streamlit.column.{method_name}",
            )
        return clicked

    wrapped._program_button_log = True  # type: ignore[attr-defined]
    setattr(DeltaGenerator, method_name, wrapped)


def install_program_button_logging(st_module: Any) -> None:
    """실제 작업을 시작하는 일반·컬럼·폼 버튼 클릭만 기록합니다."""
    if getattr(st_module, "_program_button_logging_installed", False):
        return
    st_module._program_button_logging_installed = True
    _wrap_delta_generator_method("button")
    _wrap_delta_generator_method("form_submit_button")
    _wrap_module_button(st_module, "button")
    _wrap_module_button(st_module, "form_submit_button")
