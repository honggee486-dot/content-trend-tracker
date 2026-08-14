from types import SimpleNamespace

from src.services.content_pack_request_layout_runtime import (
    ContentPackRequestLayoutProxy,
    install_content_pack_request_layout_runtime,
)


class _FakeComponents:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def html(self, body: str, *, height: int) -> None:
        self.calls.append((body, height))


class _FakeColumn:
    def __init__(self) -> None:
        self.button_calls: list[tuple[object, dict[str, object]]] = []
        self.markdown_calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def button(self, label, *args, **kwargs):
        self.button_calls.append((label, dict(kwargs)))
        return True

    def markdown(self, body, *args, **kwargs):
        self.markdown_calls.append(str(body))


class _FakeStreamlit:
    def __init__(self) -> None:
        self.columns_calls: list[tuple[object, dict[str, object]]] = []
        self.text_area_calls: list[tuple[object, dict[str, object]]] = []
        self.radio_calls: list[tuple[object, list[object], dict[str, object]]] = []
        self.outer_columns = [_FakeColumn(), _FakeColumn()]

    def columns(self, spec, *args, **kwargs):
        self.columns_calls.append((spec, dict(kwargs)))
        return self.outer_columns

    def text_area(self, label, *args, **kwargs):
        self.text_area_calls.append((label, dict(kwargs)))
        return "prompt"

    def radio(self, label, options, *args, **kwargs):
        option_values = list(options)
        self.radio_calls.append((label, option_values, dict(kwargs)))
        index = int(kwargs.get("index", 0))
        return option_values[index]


def test_saved_topic_is_primary_when_available() -> None:
    fake = _FakeStreamlit()
    proxy = ContentPackRequestLayoutProxy(fake)

    selected = proxy.radio(
        "시작 방법",
        ["새 글감 바로 입력", "저장된 주제 사용"],
        index=1,
        horizontal=True,
        help="legacy help",
    )

    assert selected == "저장된 주제 사용"
    assert fake.radio_calls == [
        (
            "시작 방법",
            ["저장된 주제 사용", "새 글감 바로 입력"],
            {
                "index": 0,
                "horizontal": True,
                "help": (
                    "저장된 관심 주제가 있으면 이를 기본으로 사용합니다. "
                    "새 글감 바로 입력은 저장된 주제로 시작할 수 없을 때 사용하는 보조 흐름입니다."
                ),
            },
        )
    ]


def test_quick_topic_remains_available_when_no_saved_topic_exists() -> None:
    fake = _FakeStreamlit()
    proxy = ContentPackRequestLayoutProxy(fake)

    selected = proxy.radio(
        "시작 방법",
        ["새 글감 바로 입력"],
        index=0,
    )

    assert selected == "새 글감 바로 입력"
    assert fake.radio_calls[0][1] == ["새 글감 바로 입력"]


def test_request_layout_uses_wide_prompt_and_stacked_actions() -> None:
    fake = _FakeStreamlit()
    proxy = ContentPackRequestLayoutProxy(fake)

    assert (
        proxy.text_area(
            "ChatGPT 또는 Gemini에 그대로 붙여넣기",
            value="request",
            height=520,
        )
        == "prompt"
    )
    assert fake.columns_calls == [
        (
            [2.75, 1.0],
            {"gap": "medium", "vertical_alignment": "top"},
        )
    ]

    actions = proxy.columns([1, 1])
    assert actions[0] is fake.outer_columns[1]

    result_block = actions[1]
    assert result_block.button(
        "ChatGPT 결과 붙여넣기로 이동",
        type="primary",
        width="stretch",
    )
    assert fake.outer_columns[1].button_calls == [
        (
            "ChatGPT 결과 붙여넣기로 이동",
            {
                "type": "primary",
                "width": "stretch",
                "key": "content_pack_result_handoff",
            },
        )
    ]
    css = "\n".join(fake.outer_columns[1].markdown_calls)
    assert "height: 54px !important" in css
    assert "font-size: 14px !important" in css
    assert "font-weight: 600 !important" in css
    assert "button p" in css
    assert "margin-top: 0.35rem" in css


def test_chatgpt_button_matches_result_button_scale_and_textarea_top() -> None:
    components = _FakeComponents()
    ui_module = SimpleNamespace(
        _component_token=lambda key: "token",
        components=components,
        _ContentPackRequestLayoutProxy=object,
        render_chatgpt_request_button=lambda *args, **kwargs: None,
    )

    install_content_pack_request_layout_runtime(ui_module)

    assert ui_module._ContentPackRequestLayoutProxy is ContentPackRequestLayoutProxy
    ui_module.render_chatgpt_request_button("request", key="request-key")

    assert len(components.calls) == 1
    body, height = components.calls[0]
    assert height == 108
    assert "padding-top:24px" in body
    assert "height:54px" in body
    assert "font-size:14px" in body
    assert "font-weight:600" in body
    assert "#chatgpt-button-token:hover" in body
    assert "background:#6ea8fe" in body
    assert 'href="https://chatgpt.com/"' in body
    assert "navigator.clipboard.writeText(text)" in body
