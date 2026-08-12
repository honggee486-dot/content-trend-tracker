from __future__ import annotations

from functools import wraps
from typing import Any

_CONFIRM_MARKER = "적용과 앱 재시작을 확인했습니다."


def install_web_update_confirmation_ui(st_module: Any) -> None:
    """업데이트의 중복 확인 체크박스를 숨기고 적용 버튼만 사용합니다."""
    original = getattr(st_module, "checkbox", None)
    if not callable(original) or getattr(
        st_module,
        "_web_update_confirmation_ui_installed",
        False,
    ):
        return
    st_module._web_update_confirmation_ui_installed = True

    @wraps(original)
    def wrapped(label: Any, *args: Any, **kwargs: Any):
        if _CONFIRM_MARKER in str(label or ""):
            return not bool(kwargs.get("disabled", False))
        return original(label, *args, **kwargs)

    st_module.checkbox = wrapped
