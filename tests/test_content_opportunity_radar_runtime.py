from types import SimpleNamespace

import src.services.content_opportunity_radar_runtime as radar_runtime


def test_runtime_records_radar_after_successful_ranking(monkeypatch) -> None:
    calls: list[object] = []

    class FakeDiscoveryModule:
        @staticmethod
        def finalize_prepared_trend_rankings(con, calculation):
            calls.append((con, calculation))
            return {"items": 3, "clusters": 1, "reused": False}

    monkeypatch.setattr(
        radar_runtime,
        "refresh_opportunity_radar",
        lambda con, observed_at=None: {
            "status": "recorded",
            "observed": 1,
            "observed_at": str(observed_at),
        },
    )
    radar_runtime.install_content_opportunity_radar_contract(FakeDiscoveryModule)
    calculation = SimpleNamespace(
        cluster_rows=({"calculated_at": "2026-08-22T12:00:00"},)
    )

    result = FakeDiscoveryModule.finalize_prepared_trend_rankings(
        "connection",
        calculation,
    )

    assert len(calls) == 1
    assert result["clusters"] == 1
    assert result["opportunity_radar"]["status"] == "recorded"
    assert result["opportunity_radar"]["observed"] == 1


def test_runtime_does_not_cancel_ranking_when_radar_fails(monkeypatch) -> None:
    class FakeDiscoveryModule:
        @staticmethod
        def finalize_prepared_trend_rankings(con, calculation):
            return {"items": 5, "clusters": 2, "reused": False}

    def fail_radar(con, observed_at=None):
        raise RuntimeError("radar unavailable")

    monkeypatch.setattr(radar_runtime, "refresh_opportunity_radar", fail_radar)
    radar_runtime.install_content_opportunity_radar_contract(FakeDiscoveryModule)

    result = FakeDiscoveryModule.finalize_prepared_trend_rankings(
        "connection",
        SimpleNamespace(cluster_rows=()),
    )

    assert result["items"] == 5
    assert result["clusters"] == 2
    assert result["opportunity_radar"]["status"] == "failed"
    assert "radar unavailable" in result["opportunity_radar"]["error"]


def test_runtime_reused_ranking_only_reads_existing_summary(monkeypatch) -> None:
    class FakeDiscoveryModule:
        @staticmethod
        def finalize_prepared_trend_rankings(con, calculation):
            return {"items": 2, "clusters": 1, "reused": True}

    monkeypatch.setattr(
        radar_runtime,
        "get_opportunity_summary",
        lambda con: {"hot": 1, "early": 2, "opportunity": 3, "saturated": 4},
    )

    def unexpected_refresh(con, observed_at=None):
        raise AssertionError("reused ranking must not create another radar snapshot")

    monkeypatch.setattr(radar_runtime, "refresh_opportunity_radar", unexpected_refresh)
    radar_runtime.install_content_opportunity_radar_contract(FakeDiscoveryModule)

    result = FakeDiscoveryModule.finalize_prepared_trend_rankings(
        "connection",
        SimpleNamespace(cluster_rows=()),
    )

    assert result["opportunity_radar"] == {
        "status": "reused",
        "counts": {"hot": 1, "early": 2, "opportunity": 3, "saturated": 4},
    }
