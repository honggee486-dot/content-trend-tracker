from __future__ import annotations

from src.ui import _StreamlitCaptionProxy, _rewrite_gemini_capacity_caption


class _FakeStreamlit:
    def __init__(self) -> None:
        self.number_inputs: list[tuple[object, tuple, dict]] = []
        self.captions: list[str] = []

    def number_input(self, label, *args, **kwargs):
        self.number_inputs.append((label, args, dict(kwargs)))
        return kwargs.get("value")

    def caption(self, value, *args, **kwargs):
        self.captions.append(str(value))
        return value


def test_clustering_runtime_inputs_are_fixed_to_current_contract() -> None:
    target = _FakeStreamlit()
    proxy = _StreamlitCaptionProxy(target)

    batch_size = proxy.number_input(
        "Gemini 요청 1회당 1차 군집",
        min_value=20,
        max_value=200,
        value=200,
        step=20,
        help="과거 도움말",
    )
    max_batches = proxy.number_input(
        "백그라운드 작업 1회당 최대 Gemini 요청",
        min_value=1,
        max_value=20,
        value=5,
        step=1,
        help="과거 도움말",
    )

    assert batch_size == 300
    assert max_batches == 20

    batch_kwargs = target.number_inputs[0][2]
    assert batch_kwargs["value"] == 300
    assert batch_kwargs["max_value"] == 300
    assert batch_kwargs["disabled"] is True
    assert "실제 요청 수는 더 작을 수" in batch_kwargs["help"]

    max_batch_kwargs = target.number_inputs[1][2]
    assert max_batch_kwargs["value"] == 20
    assert max_batch_kwargs["max_value"] == 20
    assert max_batch_kwargs["disabled"] is True
    assert "미처리 자료가 없으면" in max_batch_kwargs["help"]


def test_unrelated_number_input_is_not_changed() -> None:
    target = _FakeStreamlit()
    proxy = _StreamlitCaptionProxy(target)

    value = proxy.number_input(
        "다른 설정",
        min_value=1,
        max_value=30,
        value=15,
        step=1,
    )

    assert value == 15
    assert target.number_inputs[0][2] == {
        "min_value": 1,
        "max_value": 30,
        "value": 15,
        "step": 1,
    }


def test_clustering_caption_uses_three_hundred_and_twenty_batches() -> None:
    text = _rewrite_gemini_capacity_caption(
        "Flash-Lite는 1차 군집 최대 200개를 비교합니다. "
        "수동 실행은 별도 프로세스에서 최대 5배치를 처리하고 종료합니다."
    )
    previously_rewritten = _rewrite_gemini_capacity_caption(
        "수동 실행은 별도 프로세스에서 최대 10배치를 처리하고 종료합니다."
    )

    assert "최대 300개" in text
    assert "최대 20배치" in text
    assert "최대 20배치" in previously_rewritten
    assert "최대 200개" not in text
    assert "최대 5배치" not in text
    assert "최대 10배치" not in previously_rewritten
