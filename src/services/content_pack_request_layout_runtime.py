from __future__ import annotations

import base64
import html
from collections.abc import Callable


_CONTENT_PACK_PROMPT_LABEL = "ChatGPT 또는 Gemini에 그대로 붙여넣기"
_CONTENT_PACK_START_LABEL = "시작 방법"
_SAVED_TOPIC_MODE = "저장된 주제 사용"
_QUICK_TOPIC_MODE = "새 글감 바로 입력"
_RESULT_BUTTON_LABEL = "ChatGPT 결과 붙여넣기로 이동"
_RESULT_BUTTON_KEY = "content_pack_result_handoff"
_REQUEST_ACTION_TOP_OFFSET_PX = 24
_RESULT_BUTTON_CSS = f"""
<style>
.st-key-{_RESULT_BUTTON_KEY} {{
    margin-top: 0.35rem;
}}
.st-key-{_RESULT_BUTTON_KEY} button {{
    width: 100% !important;
    height: 54px !important;
    min-height: 54px !important;
    box-sizing: border-box !important;
    padding: 12px 16px !important;
    border: 1px solid #6ea8fe !important;
    border-radius: 8px !important;
    background: #6ea8fe !important;
    color: white !important;
}}
.st-key-{_RESULT_BUTTON_KEY} button p {{
    margin: 0 !important;
    color: white !important;
    font-size: 14px !important;
    font-weight: 600 !important;
    line-height: 1.2 !important;
}}
.st-key-{_RESULT_BUTTON_KEY} button:hover {{
    background: #5b9bfd !important;
    border-color: #5b9bfd !important;
}}
.st-key-{_RESULT_BUTTON_KEY} button:active {{
    background: #4f8ff0 !important;
    border-color: #4f8ff0 !important;
}}
</style>
"""


class _ResultActionBlock:
    def __init__(self, target) -> None:
        self._target = target

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def button(self, label: object, *args, **kwargs):
        if str(label or "") != _RESULT_BUTTON_LABEL:
            return self._target.button(label, *args, **kwargs)

        render_kwargs = dict(kwargs)
        render_kwargs.setdefault("key", _RESULT_BUTTON_KEY)
        with self._target:
            self._target.markdown(_RESULT_BUTTON_CSS, unsafe_allow_html=True)
            return self._target.button(label, *args, **render_kwargs)


class ContentPackRequestLayoutProxy:
    """Keep the request wide, prioritize saved topics, and stack handoff actions."""

    def __init__(self, target) -> None:
        self._target = target
        self._request_action_parent = None

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def radio(self, label: object, options, *args, **kwargs):
        if str(label or "") != _CONTENT_PACK_START_LABEL:
            return self._target.radio(label, options, *args, **kwargs)

        option_values = list(options)
        if _SAVED_TOPIC_MODE not in option_values:
            return self._target.radio(label, option_values, *args, **kwargs)

        reordered = [
            _SAVED_TOPIC_MODE,
            _QUICK_TOPIC_MODE,
            *[
                value
                for value in option_values
                if value not in {_SAVED_TOPIC_MODE, _QUICK_TOPIC_MODE}
            ],
        ]
        render_kwargs = dict(kwargs)
        render_kwargs["index"] = 0
        render_kwargs["help"] = (
            "저장된 관심 주제가 있으면 이를 기본으로 사용합니다. "
            "새 글감 바로 입력은 저장된 주제로 시작할 수 없을 때 사용하는 보조 흐름입니다."
        )
        return self._target.radio(label, reordered, *args, **render_kwargs)

    def text_area(self, label: object, *args, **kwargs):
        if str(label or "") != _CONTENT_PACK_PROMPT_LABEL:
            return self._target.text_area(label, *args, **kwargs)

        layout_cols = self._target.columns(
            [2.75, 1.0],
            gap="medium",
            vertical_alignment="top",
        )
        with layout_cols[0]:
            result = self._target.text_area(label, *args, **kwargs)
        self._request_action_parent = layout_cols[1]
        return result

    def columns(self, spec, *args, **kwargs):
        is_request_action_row = (
            self._request_action_parent is not None
            and isinstance(spec, (list, tuple))
            and tuple(spec) == (1, 1)
        )
        if not is_request_action_row:
            return self._target.columns(spec, *args, **kwargs)

        action_parent = self._request_action_parent
        self._request_action_parent = None
        return [action_parent, _ResultActionBlock(action_parent)]


def _build_chatgpt_request_renderer(ui_module) -> Callable[..., None]:
    def render_chatgpt_request_button(
        text: str,
        *,
        key: str,
        label: str = "ChatGPT에서 요청하기",
        height: int = 84,
    ) -> None:
        payload = base64.b64encode(str(text or "").encode("utf-8")).decode("ascii")
        safe_label = html.escape(label)
        token = ui_module._component_token(key)
        ui_module.components.html(
            f"""
            <style>
              html, body {{
                margin:0;
                padding:0;
              }}
              #chatgpt-request-{token} {{
                width:100%;
                box-sizing:border-box;
                padding-top:{_REQUEST_ACTION_TOP_OFFSET_PX}px;
              }}
              #chatgpt-button-{token} {{
                display:flex;
                align-items:center;
                justify-content:center;
                width:100%;
                height:54px;
                min-height:54px;
                box-sizing:border-box;
                padding:12px 16px;
                border:1px solid #6ea8fe;
                border-radius:8px;
                background:#6ea8fe;
                color:white;
                text-decoration:none;
                cursor:pointer;
                font-size:14px;
                font-weight:600;
                line-height:1.2;
                transition:background-color .15s ease,border-color .15s ease,
                           box-shadow .15s ease,transform .05s ease;
              }}
              #chatgpt-button-{token}:hover {{
                background:#5b9bfd;
                border-color:#5b9bfd;
                box-shadow:0 0 0 1px rgba(110,168,254,.20);
              }}
              #chatgpt-button-{token}:active {{
                background:#4f8ff0;
                border-color:#4f8ff0;
                transform:translateY(1px);
              }}
              #chatgpt-button-{token}:focus-visible {{
                outline:2px solid rgba(110,168,254,.65);
                outline-offset:2px;
              }}
            </style>
            <div id="chatgpt-request-{token}">
              <a id="chatgpt-button-{token}"
                 href="https://chatgpt.com/"
                 target="_blank"
                 rel="noopener noreferrer">
                {safe_label}
              </a>
              <div id="chatgpt-msg-{token}"
                   style="font-size:12px;line-height:1.35;margin-top:5px;color:#9ca3af;"></div>
            </div>
            <script>
            document.getElementById("chatgpt-button-{token}").addEventListener("click", () => {{
              const raw = atob("{payload}");
              const bytes = Uint8Array.from(raw, c => c.charCodeAt(0));
              const text = new TextDecoder().decode(bytes);
              const msg = document.getElementById("chatgpt-msg-{token}");
              msg.textContent = "요청서를 복사하는 중입니다...";
              try {{
                if (!navigator.clipboard || !navigator.clipboard.writeText) {{
                  throw new Error("clipboard unavailable");
                }}
                navigator.clipboard.writeText(text).then(() => {{
                  msg.textContent = "요청서를 복사했습니다. 열린 ChatGPT에서 Ctrl+V 후 전송하세요.";
                }}).catch(() => {{
                  msg.textContent = "ChatGPT는 열었지만 복사하지 못했습니다. 위 요청서를 직접 복사하세요.";
                }});
              }} catch (err) {{
                msg.textContent = "ChatGPT는 열었지만 복사하지 못했습니다. 위 요청서를 직접 복사하세요.";
              }}
            }});
            </script>
            """,
            height=height + _REQUEST_ACTION_TOP_OFFSET_PX,
        )

    return render_chatgpt_request_button


def install_content_pack_request_layout_runtime(ui_module) -> None:
    """Refine the existing content-pack handoff without changing its storage flow."""
    ui_module._ContentPackRequestLayoutProxy = ContentPackRequestLayoutProxy
    ui_module.render_chatgpt_request_button = _build_chatgpt_request_renderer(ui_module)
