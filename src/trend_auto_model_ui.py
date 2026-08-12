from __future__ import annotations

from functools import wraps
from typing import Any

_MODEL_LABEL = "Gemini 자동 분석 모델"
_MODEL_CAPTION_PREFIX = "자동·예약 분석 모델:"
_OLD_INTRO_TEXT = "아래에서 선택한 Gemini 모델로 한 번 더 분석합니다."
_NEW_INTRO_TEXT = "설정에서 지정한 Gemini 모델로 한 번 더 분석합니다."
_OLD_SETTINGS_CAPTION = "모델은 위 Gemini 모델 설정과 오늘의 트렌드 화면에서 변경할 수 있습니다."
_NEW_SETTINGS_CAPTION = "모델은 위 Gemini 모델 설정에서 변경할 수 있습니다."
_SUPPRESS_CAPTION_MARKER = "_suppress_dashboard_auto_model_caption_once"


def _selected_option(options: Any, index: Any) -> Any:
    if index is None:
        return None
    try:
        values = list(options)
        return values[int(index)] if values else None
    except (TypeError, ValueError, IndexError):
        return None


def install_trend_auto_model_ui(st_module: Any) -> None:
    """오늘의 트렌드에서 설정과 중복되는 모델 선택 UI만 숨깁니다."""
    original_selectbox = getattr(st_module, "selectbox", None)
    original_caption = getattr(st_module, "caption", None)
    original_markdown = getattr(st_module, "markdown", None)
    if (
        not callable(original_selectbox)
        or not callable(original_caption)
        or not callable(original_markdown)
        or getattr(st_module, "_trend_auto_model_ui_installed", False)
    ):
        return

    st_module._trend_auto_model_ui_installed = True

    @wraps(original_selectbox)
    def wrapped_selectbox(label: Any, options: Any, *args: Any, **kwargs: Any):
        if str(label or "").strip() != _MODEL_LABEL:
            return original_selectbox(label, options, *args, **kwargs)
        index = kwargs.get("index", args[0] if args else 0)
        selected = _selected_option(options, index)
        if selected is None:
            return original_selectbox(label, options, *args, **kwargs)
        setattr(st_module, _SUPPRESS_CAPTION_MARKER, True)
        return selected

    @wraps(original_caption)
    def wrapped_caption(value: Any, *args: Any, **kwargs: Any):
        text = str(value or "")
        if bool(getattr(st_module, _SUPPRESS_CAPTION_MARKER, False)):
            setattr(st_module, _SUPPRESS_CAPTION_MARKER, False)
            if text.strip().startswith(_MODEL_CAPTION_PREFIX):
                return None
        rendered = text.replace(_OLD_SETTINGS_CAPTION, _NEW_SETTINGS_CAPTION)
        return original_caption(rendered, *args, **kwargs)

    @wraps(original_markdown)
    def wrapped_markdown(value: Any, *args: Any, **kwargs: Any):
        rendered = value
        if isinstance(value, str) and _OLD_INTRO_TEXT in value:
            rendered = value.replace(_OLD_INTRO_TEXT, _NEW_INTRO_TEXT)
        return original_markdown(rendered, *args, **kwargs)

    st_module.selectbox = wrapped_selectbox
    st_module.caption = wrapped_caption
    st_module.markdown = wrapped_markdown
