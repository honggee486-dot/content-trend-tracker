from __future__ import annotations

import json
import urllib.error

import pytest

from src.config import GeminiConfig, get_gemini_config
from src.services.gemini_model_service import (
    AUTO_MODEL_SETTING,
    DATA_REVIEW_MODEL_SETTING,
    MANUAL_MODEL_SETTING,
    MODEL_CATALOG_REFRESHED_AT_SETTING,
    MODEL_PURPOSE_AUTO,
    MODEL_PURPOSE_DATA_REVIEW,
    MODEL_PURPOSE_MANUAL,
    GeminiModelCatalogError,
    GeminiModelInfo,
    build_gemini_config_for_purpose,
    fetch_gemini_model_catalog,
    get_available_gemini_models,
    get_selected_gemini_model,
    load_gemini_model_catalog,
    save_gemini_model_catalog,
    set_selected_gemini_model,
)




class _FakeConnection:
    def __init__(self):
        self.settings: dict[str, str] = {}
        self._row = None

    def execute(self, sql: str, params=None):
        values = list(params or [])
        normalized = " ".join(sql.split()).casefold()
        if normalized.startswith("select setting_value from app_settings"):
            key = str(values[0])
            self._row = (self.settings[key],) if key in self.settings else None
        elif normalized.startswith("insert into app_settings"):
            self.settings[str(values[0])] = str(values[1])
            self._row = None
        else:
            raise AssertionError(f"지원하지 않는 테스트 SQL: {sql}")
        return self

    def fetchone(self):
        return self._row


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _config(model: str = "gemini-env-default") -> GeminiConfig:
    return GeminiConfig(
        api_key="test-key",
        model=model,
        app_id="content-trend-tracker",
        quota_scope_id="test-scope",
        timeout_seconds=60,
        retry_wait_seconds=2.0,
        retry_max_wait_seconds=30.0,
        topic_angle_timeout_seconds=360,
        topic_angle_batch_limit=100,
        topic_angle_max_parallel_requests=1,
        topic_angle_request_stagger_seconds=5.0,
        topic_angle_min_opportunity_score=50.0,
        daily_request_reference_limit=1500,
        draft_thinking_level="high",
        topic_angle_thinking_level="medium",
    )


def test_fetch_model_catalog_paginates_and_filters_generation_models() -> None:
    requests = []
    pages = [
        {
            "models": [
                {
                    "name": "models/gemini-3.6-flash",
                    "baseModelId": "gemini-3.6-flash",
                    "displayName": "Gemini 3.6 Flash",
                    "inputTokenLimit": 1_048_576,
                    "outputTokenLimit": 65_536,
                    "supportedGenerationMethods": ["generateContent"],
                    "thinking": True,
                },
                {
                    "name": "models/text-embedding-004",
                    "baseModelId": "text-embedding-004",
                    "supportedGenerationMethods": ["embedContent"],
                },
                {
                    "name": "models/gemini-3.1-flash-image",
                    "baseModelId": "gemini-3.1-flash-image",
                    "supportedGenerationMethods": ["generateContent"],
                },
            ],
            "nextPageToken": "page-2",
        },
        {
            "models": [
                {
                    "name": "models/gemini-3.5-flash-lite",
                    "baseModelId": "gemini-3.5-flash-lite",
                    "displayName": "Gemini 3.5 Flash-Lite",
                    "inputTokenLimit": 1_048_576,
                    "outputTokenLimit": 65_536,
                    "supportedGenerationMethods": ["generateContent"],
                    "thinking": True,
                }
            ]
        },
    ]

    def opener(request, timeout):
        requests.append((request, timeout))
        return _FakeResponse(pages[len(requests) - 1])

    models = fetch_gemini_model_catalog(
        "secret-key",
        timeout_seconds=17,
        opener=opener,
    )

    assert [model.model_id for model in models] == [
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
    ]
    assert len(requests) == 2
    assert "pageToken=page-2" in requests[1][0].full_url
    assert "secret-key" not in requests[0][0].full_url
    assert requests[0][0].get_header("X-goog-api-key") == "secret-key"
    assert requests[0][1] == 17


def test_model_selection_and_catalog_persist_in_app_settings() -> None:
    con = _FakeConnection()
    catalog = [
        GeminiModelInfo(
            model_id="gemini-3.6-flash",
            display_name="Gemini 3.6 Flash",
            input_token_limit=1_048_576,
            output_token_limit=65_536,
            supported_generation_methods=("generateContent",),
            thinking=True,
        )
    ]

    save_gemini_model_catalog(con, catalog)
    set_selected_gemini_model(
        con,
        MODEL_PURPOSE_AUTO,
        "models/gemini-3.5-flash-lite",
    )
    set_selected_gemini_model(
        con,
        MODEL_PURPOSE_DATA_REVIEW,
        "gemini-3.6-flash",
    )

    assert con.settings[AUTO_MODEL_SETTING] == "gemini-3.5-flash-lite"
    assert con.settings[DATA_REVIEW_MODEL_SETTING] == "gemini-3.6-flash"
    assert con.settings[MODEL_CATALOG_REFRESHED_AT_SETTING]
    assert load_gemini_model_catalog(con) == catalog
    assert get_selected_gemini_model(
        con,
        MODEL_PURPOSE_AUTO,
        base_config=_config(),
    ) == "gemini-3.5-flash-lite"
    assert get_selected_gemini_model(
        con,
        MODEL_PURPOSE_DATA_REVIEW,
        base_config=_config(),
    ) == "gemini-3.6-flash"
    auto_config = build_gemini_config_for_purpose(
        con,
        MODEL_PURPOSE_AUTO,
        base_config=_config(),
    )
    manual_config = build_gemini_config_for_purpose(
        con,
        MODEL_PURPOSE_DATA_REVIEW,
        base_config=_config(),
    )

    assert auto_config.model == "gemini-3.5-flash-lite"
    assert manual_config.model == "gemini-3.6-flash"
    assert auto_config.api_key == "test-key"
    assert auto_config.topic_angle_batch_limit == 100


def test_available_models_keep_env_and_selected_values_when_catalog_is_empty() -> None:
    con = _FakeConnection()
    set_selected_gemini_model(con, MODEL_PURPOSE_AUTO, "gemini-custom-preview")
    models = get_available_gemini_models(con, base_config=_config())

    model_ids = {model.model_id for model in models}
    assert "gemini-3.6-flash" in model_ids
    assert "gemini-3.5-flash-lite" in model_ids
    assert "gemini-custom-preview" in model_ids
    assert "gemini-env-default" in model_ids


def test_default_gemini_config_uses_fifteen_item_single_request(monkeypatch) -> None:
    for name in (
        "GEMINI_TOPIC_ANGLE_ITEMS_PER_REQUEST",
        "GEMINI_TOPIC_ANGLE_BATCH_LIMIT",
        "GEMINI_TOPIC_ANGLE_MAX_PARALLEL_REQUESTS",
    ):
        monkeypatch.delenv(name, raising=False)

    config = get_gemini_config(model="gemini-3.6-flash")

    assert config.model == "gemini-3.6-flash"
    assert config.topic_angle_batch_limit == 15
    assert config.topic_angle_max_parallel_requests == 1


def test_failed_refresh_does_not_mutate_existing_cached_catalog() -> None:
    con = _FakeConnection()
    catalog = [
        GeminiModelInfo(
            model_id="gemini-3.6-flash",
            display_name="Gemini 3.6 Flash",
            supported_generation_methods=("generateContent",),
        )
    ]
    save_gemini_model_catalog(con, catalog)
    before = dict(con.settings)

    def failing_opener(request, timeout):
        raise urllib.error.URLError("offline")

    with pytest.raises(GeminiModelCatalogError):
        fetch_gemini_model_catalog("secret-key", opener=failing_opener)

    assert con.settings == before
    assert load_gemini_model_catalog(con) == catalog



def test_data_review_model_reads_legacy_manual_selection() -> None:
    con = _FakeConnection()
    con.settings[MANUAL_MODEL_SETTING] = "gemini-legacy-lite"

    selected = get_selected_gemini_model(
        con,
        MODEL_PURPOSE_DATA_REVIEW,
        base_config=_config(),
    )

    assert selected == "gemini-legacy-lite"
    assert DATA_REVIEW_MODEL_SETTING not in con.settings


def test_data_review_default_is_flash_lite() -> None:
    con = _FakeConnection()

    selected = get_selected_gemini_model(
        con,
        MODEL_PURPOSE_DATA_REVIEW,
        base_config=_config(),
    )

    assert selected == "gemini-3.5-flash-lite"
