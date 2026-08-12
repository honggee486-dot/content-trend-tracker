from __future__ import annotations

from src.trend_auto_model_ui import install_trend_auto_model_ui


class _FakeStreamlit:
    def __init__(self) -> None:
        self.selectbox_calls = []
        self.caption_calls = []
        self.markdown_calls = []

    def selectbox(self, label, options, *args, **kwargs):
        self.selectbox_calls.append((label, list(options), args, kwargs))
        return list(options)[kwargs.get("index", 0)]

    def caption(self, value, *args, **kwargs):
        self.caption_calls.append((value, args, kwargs))
        return value

    def markdown(self, value, *args, **kwargs):
        self.markdown_calls.append((value, args, kwargs))
        return value


def test_dashboard_model_selector_and_its_caption_are_suppressed() -> None:
    fake = _FakeStreamlit()
    install_trend_auto_model_ui(fake)

    selected = fake.selectbox(
        "Gemini 자동 분석 모델",
        ["gemini-a", "gemini-b"],
        index=1,
    )
    caption_result = fake.caption(
        "자동·예약 분석 모델: gemini-b · 참고 RPM 5"
    )

    assert selected == "gemini-b"
    assert caption_result is None
    assert fake.selectbox_calls == []
    assert fake.caption_calls == []


def test_unrelated_selectbox_and_caption_are_unchanged() -> None:
    fake = _FakeStreamlit()
    install_trend_auto_model_ui(fake)

    selected = fake.selectbox("정렬", ["최신", "점수"], index=0)
    caption_result = fake.caption("일반 설명")

    assert selected == "최신"
    assert caption_result == "일반 설명"
    assert len(fake.selectbox_calls) == 1
    assert len(fake.caption_calls) == 1


def test_dashboard_intro_refers_to_settings_model() -> None:
    fake = _FakeStreamlit()
    install_trend_auto_model_ui(fake)

    fake.markdown(
        "<p>새 고득점 글감을 아래에서 선택한 Gemini 모델로 한 번 더 분석합니다.</p>",
        unsafe_allow_html=True,
    )

    rendered = fake.markdown_calls[0][0]
    assert "아래에서 선택한" not in rendered
    assert "설정에서 지정한 Gemini 모델" in rendered


def test_settings_caption_no_longer_mentions_dashboard_model_change() -> None:
    fake = _FakeStreamlit()
    install_trend_auto_model_ui(fake)

    fake.caption(
        "모델은 위 Gemini 모델 설정과 오늘의 트렌드 화면에서 변경할 수 있습니다."
    )

    rendered = fake.caption_calls[0][0]
    assert rendered == "모델은 위 Gemini 모델 설정에서 변경할 수 있습니다."
