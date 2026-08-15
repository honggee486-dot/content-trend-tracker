from __future__ import annotations

from src.config import get_gemini_config


def test_gemini_timeout_test_baseline_isolated_from_local_dotenv() -> None:
    assert get_gemini_config().timeout_seconds == 60


def test_gemini_timeout_accepts_240_seconds(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "240")

    assert get_gemini_config().timeout_seconds == 240
