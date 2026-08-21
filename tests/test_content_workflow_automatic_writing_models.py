from __future__ import annotations

import json
from datetime import datetime, timedelta

import duckdb

import src.services.content_pack_automatic_writing_model_service as service


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, *, method, url, headers, body, timeout_seconds):
        self.calls.append((method, url, body))
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        expected, status, payload, content_type = self.responses.pop(0)
        assert expected in url
        if isinstance(payload, (dict, list)):
            raw = json.dumps(payload).encode("utf-8")
        else:
            raw = str(payload).encode("utf-8")
        return service.HttpResponse(status, raw, {"content-type": content_type})


def make_con():
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE app_settings (
            setting_key VARCHAR PRIMARY KEY,
            setting_value VARCHAR,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    return con


def env():
    return {
        "OPENROUTER_API_KEY": "or-test",
        "GROQ_API_KEY": "groq-test",
        "GROQ_PLAN": "free",
        "OPENCODE_API_KEY": "oc-test",
    }


def catalog_transport():
    openrouter = {
        "data": [
            {
                "id": "z-ai/glm-5.2:free",
                "name": "GLM 5.2 Free",
                "context_length": 131072,
                "architecture": {"output_modalities": ["text"]},
                "pricing": {
                    "prompt": "0",
                    "completion": "0",
                    "request": "0",
                    "internal_reasoning": "0",
                },
            },
            {
                "id": "paid/model",
                "name": "Paid",
                "architecture": {"output_modalities": ["text"]},
                "pricing": {"prompt": "0.000001", "completion": "0"},
            },
            {
                "id": "event/model",
                "name": "Zero Event",
                "architecture": {"output_modalities": ["text"]},
                "pricing": {"prompt": "0", "completion": "0", "request": "0"},
            },
        ]
    }
    groq = {
        "data": [
            {"id": "openai/gpt-oss-120b", "active": True},
            {"id": "retired/model", "active": False},
        ]
    }
    opencode_models = {
        "object": "list",
        "data": [
            {"id": "big-pickle"},
            {"id": "mimo-v2.5-free"},
            {"id": "paid-model"},
        ],
    }
    opencode_pricing = """
    <table>
      <tr><th>Model</th><th>Input</th><th>Output</th></tr>
      <tr><td>Big Pickle</td><td>Free</td><td>Free</td></tr>
      <tr><td>MiMo-V2.5 Free</td><td>Free</td><td>Free</td></tr>
      <tr><td>Paid Model</td><td>$1.00</td><td>$2.00</td></tr>
    </table>
    """
    return FakeTransport(
        [
            ("openrouter.ai/api/v1/models", 200, openrouter, "application/json"),
            ("api.groq.com/openai/v1/models", 200, groq, "application/json"),
            ("opencode.ai/zen/v1/models", 200, opencode_models, "application/json"),
            ("opencode.ai/docs/zen", 200, opencode_pricing, "text/html"),
        ]
    )


def test_catalog_refresh_accepts_zero_price_events_and_uses_one_hour_ttl():
    con = make_con()
    now = datetime(2026, 8, 22, 8, 0, 0)
    transport = catalog_transport()

    snapshot = service.refresh_model_catalog(
        con,
        force=False,
        now=now,
        transport=transport,
        environ=env(),
    )

    keys = {item.key for item in snapshot.models}
    assert "openrouter:z-ai/glm-5.2:free" in keys
    assert "openrouter:event/model" in keys
    assert "openrouter:paid/model" not in keys
    assert "groq:openai/gpt-oss-120b" in keys
    assert "opencode:big-pickle" in keys
    assert "opencode:mimo-v2.5-free" in keys
    assert len(transport.calls) == 4

    cached = service.refresh_model_catalog(
        con,
        now=now + timedelta(minutes=59),
        transport=FakeTransport([]),
        environ=env(),
    )
    assert cached.checked_at == snapshot.checked_at

    assert service.model_catalog_due(con, now=now + timedelta(hours=1))


def test_openrouter_zero_cost_requires_zero_prompt_and_completion():
    assert service._openrouter_zero_cost(
        {"pricing": {"prompt": "0", "completion": "0", "request": "0"}}
    )
    assert not service._openrouter_zero_cost(
        {"pricing": {"prompt": "0", "completion": "0.000001", "request": "0"}}
    )
    assert not service._openrouter_zero_cost(
        {"pricing": {"prompt": "0", "completion": "0", "internal_reasoning": "0.000001"}}
    )
    assert not service._openrouter_zero_cost({"pricing": {"prompt": "0"}})


def test_priority_order_persists_and_rejects_duplicates():
    con = make_con()
    saved = service.save_priority(
        con,
        [
            "openrouter:z-ai/glm-5.2:free",
            "groq:openai/gpt-oss-120b",
            "opencode:big-pickle",
        ],
    )
    assert service.load_priority(con) == saved

    try:
        service.save_priority(
            con,
            [
                "openrouter:z-ai/glm-5.2:free",
                "openrouter:z-ai/glm-5.2:free",
            ],
        )
    except ValueError as exc:
        assert "중복" in str(exc)
    else:
        raise AssertionError("duplicate priority must fail")


def test_ranked_rows_use_writing_then_reasoning_and_keep_unreviewed_models():
    catalog = service.ModelCatalogSnapshot(
        checked_at="2026-08-22T08:00:00",
        models=(
            service.AutomaticWritingModel("openrouter", "a", "A", True, "0"),
            service.AutomaticWritingModel("groq", "b", "B", True, "free"),
            service.AutomaticWritingModel("opencode", "c", "C", True, "free"),
        ),
        providers=(),
    )
    perf = service.PerformanceSnapshot(
        refreshed_at="2026-08-22T08:00:00",
        scores=(
            service.ModelPerformance(
                "openrouter", "a", 90, 80, 80, 85, "high", "", "2026-08-22T08:00:00"
            ),
            service.ModelPerformance(
                "groq", "b", 90, 85, 70, 85, "medium", "", "2026-08-22T08:00:00"
            ),
        ),
    )
    rows = service.ranked_model_rows(catalog, perf)
    assert [row["model_id"] for row in rows] == ["b", "a", "c"]
    assert rows[-1]["writing"] is None


def test_zero_cost_fallback_stops_after_first_success(monkeypatch):
    con = make_con()
    catalog = service.ModelCatalogSnapshot(
        checked_at="2026-08-22T08:00:00",
        models=(
            service.AutomaticWritingModel("openrouter", "first", "First", True, "0"),
            service.AutomaticWritingModel("groq", "second", "Second", True, "free"),
            service.AutomaticWritingModel("opencode", "third", "Third", True, "free"),
        ),
        providers=(),
    )
    service.set_setting(con, service.CATALOG_SETTING, service._serialize_catalog(catalog))
    service.set_setting(con, service.CATALOG_CHECKED_AT_SETTING, catalog.checked_at)
    service.save_priority(con, ["openrouter:first", "groq:second", "opencode:third"])

    preflight_calls = []
    generation_calls = []

    def fake_preflight(provider, model_id, **kwargs):
        preflight_calls.append((provider, model_id))
        return True, "0원"

    def fake_generation(provider, model_id, prompt, **kwargs):
        generation_calls.append((provider, model_id))
        if model_id == "first":
            return False, "", "429"
        if model_id == "second":
            return True, '{"ok":true}', ""
        raise AssertionError("third model must not run after success")

    monkeypatch.setattr(service, "verify_zero_cost", fake_preflight)
    monkeypatch.setattr(service, "_call_generation", fake_generation)

    result = service.run_zero_cost_priority_fallback(
        con,
        "hello",
        now=datetime(2026, 8, 22, 8, 30),
        transport=FakeTransport([]),
        environ=env(),
    )
    assert result.status == "success"
    assert result.model_id == "second"
    assert generation_calls == [("openrouter", "first"), ("groq", "second")]
    assert preflight_calls == [("openrouter", "first"), ("groq", "second")]


def test_performance_refresh_is_lazy_24h_and_keeps_old_data_on_failure(monkeypatch):
    con = make_con()
    now = datetime(2026, 8, 22, 8, 0, 0)
    catalog = service.ModelCatalogSnapshot(
        checked_at=now.isoformat(timespec="seconds"),
        models=(
            service.AutomaticWritingModel(
                "openrouter", "z-ai/glm-5.2:free", "GLM", True, "0"
            ),
        ),
        providers=(),
    )
    service.set_setting(con, service.CATALOG_SETTING, service._serialize_catalog(catalog))
    service.set_setting(con, service.CATALOG_CHECKED_AT_SETTING, catalog.checked_at)
    service.save_priority(con, ["openrouter:z-ai/glm-5.2:free"])

    existing = service.PerformanceSnapshot(
        refreshed_at=(now - timedelta(hours=25)).isoformat(timespec="seconds"),
        scores=(
            service.ModelPerformance(
                "openrouter",
                "z-ai/glm-5.2:free",
                89,
                92,
                90,
                90.2,
                "medium",
                "old",
                (now - timedelta(hours=25)).isoformat(timespec="seconds"),
            ),
        ),
    )
    service.set_setting(con, service.PERFORMANCE_SETTING, service._serialize_performance(existing))
    service.set_setting(
        con,
        service.PERFORMANCE_REFRESHED_AT_SETTING,
        existing.refreshed_at,
    )

    monkeypatch.setattr(
        service,
        "run_zero_cost_priority_fallback",
        lambda *args, **kwargs: service.FallbackResult(
            status="all_failed",
            attempts=(
                service.FallbackAttempt("openrouter", "z-ai/glm-5.2:free", "failed", "429"),
            ),
        ),
    )
    result = service.refresh_performance_if_due(
        con,
        now=now,
        transport=FakeTransport([]),
        environ=env(),
    )
    assert result.status == "failed"
    assert result.snapshot.refreshed_at == existing.refreshed_at
    assert service.get_setting(con, service.PERFORMANCE_REFRESHED_AT_SETTING) == existing.refreshed_at

    service.set_setting(
        con,
        service.PERFORMANCE_REFRESHED_AT_SETTING,
        (now - timedelta(hours=23)).isoformat(timespec="seconds"),
    )
    fresh = service.refresh_performance_if_due(
        con,
        now=now,
        transport=FakeTransport([]),
        environ=env(),
    )
    assert fresh.status == "fresh"
