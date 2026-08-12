from __future__ import annotations

from pathlib import Path

import pytest


_MANAGED_APP_ENVIRONMENT_KEYS = (
    "CONTENT_TREND_TRACKER_SUPERVISOR_PID",
    "CONTENT_TREND_TRACKER_SUPERVISOR_START_TICKS",
    "CONTENT_TREND_TRACKER_APP_PORT",
    "CONTENT_TREND_TRACKER_RUNTIME_STATE",
    "CONTENT_TREND_TRACKER_UPDATE_REQUEST",
)


@pytest.fixture(autouse=True)
def isolate_external_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent tests from reading or overwriting the live supervisor state."""
    for key in _MANAGED_APP_ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))


@pytest.fixture(autouse=True)
def isolate_trend_discovery_tests_from_local_gemini_key(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep deterministic ranking tests independent from the developer's .env."""
    test_path = Path(str(getattr(request.node, "path", "")))
    if test_path.name == "test_trend_discovery_service.py":
        monkeypatch.setenv("GEMINI_API_KEY", "")
