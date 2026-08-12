from __future__ import annotations

from src.web_update_confirmation_ui import install_web_update_confirmation_ui


class _FakeStreamlit:
    def __init__(self) -> None:
        self.calls = []

    def checkbox(self, label, *args, **kwargs):
        self.calls.append((label, args, kwargs))
        return False


def test_update_confirmation_checkbox_is_suppressed_when_ready() -> None:
    fake = _FakeStreamlit()
    install_web_update_confirmation_ui(fake)

    confirmed = fake.checkbox(
        "work/0.10.105 · abcdef123456 적용과 앱 재시작을 확인했습니다.",
        disabled=False,
    )

    assert confirmed is True
    assert fake.calls == []


def test_update_confirmation_remains_false_when_blocked() -> None:
    fake = _FakeStreamlit()
    install_web_update_confirmation_ui(fake)

    confirmed = fake.checkbox(
        "work/0.10.105 · abcdef123456 적용과 앱 재시작을 확인했습니다.",
        disabled=True,
    )

    assert confirmed is False
    assert fake.calls == []


def test_unrelated_checkbox_is_unchanged() -> None:
    fake = _FakeStreamlit()
    install_web_update_confirmation_ui(fake)

    result = fake.checkbox("설정 활성화", value=True)

    assert result is False
    assert len(fake.calls) == 1
