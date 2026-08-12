from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.database import connect_database, init_database
from src.services.source_freshness_service import (
    build_source_freshness_diagnostics,
    get_source_freshness_diagnostics,
)
from src.source_freshness_ui import render_source_freshness_diagnostics


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class _FakeStreamlit:
    def __init__(self) -> None:
        self.subheaders: list[str] = []
        self.metrics: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.captions: list[str] = []
        self.warnings: list[str] = []
        self.dataframes: list[object] = []

    def subheader(self, value: str) -> None:
        self.subheaders.append(value)

    def container(self, **_kwargs) -> _Context:
        return _Context()

    def metric(self, *args, **kwargs) -> None:
        self.metrics.append((args, kwargs))

    def caption(self, value: str) -> None:
        self.captions.append(value)

    def warning(self, value: str) -> None:
        self.warnings.append(value)

    def dataframe(self, value, **_kwargs) -> None:
        self.dataframes.append(value)


def test_source_freshness_classifies_scheduler_and_source_states() -> None:
    now = datetime(2026, 7, 31, 6, 0, 0)
    source_rows = [
        {
            "source_name": "naver",
            "status": "success",
            "newly_saved_count": 3,
            "updated_count": 1,
            "error_message": "",
            "started_at": now - timedelta(hours=1),
        },
        {
            "source_name": "daum",
            "status": "failure",
            "newly_saved_count": 0,
            "updated_count": 0,
            "error_message": "HTTP 429",
            "started_at": now - timedelta(minutes=30),
        },
        {
            "source_name": "daum",
            "status": "success",
            "newly_saved_count": 1,
            "updated_count": 0,
            "error_message": "",
            "started_at": now - timedelta(hours=5),
        },
        {
            "source_name": "google_trends",
            "status": "success",
            "newly_saved_count": 0,
            "updated_count": 2,
            "error_message": "",
            "started_at": now - timedelta(hours=5),
        },
        {
            "source_name": "youtube",
            "status": "success",
            "newly_saved_count": 2,
            "updated_count": 0,
            "error_message": "",
            "started_at": now - timedelta(hours=10),
        },
    ]
    background_rows = [
        {
            "status": "success",
            "started_at": now - timedelta(hours=9),
            "error_message": "",
        }
    ]

    diagnostics = build_source_freshness_diagnostics(
        source_rows,
        background_rows,
        interval_minutes=240,
        now=now,
    )
    source_map = {row["source_name"]: row for row in diagnostics["source_rows"]}

    assert diagnostics["scheduler_state"] == "overdue"
    assert diagnostics["stale_minutes"] == 480
    assert source_map["naver"]["state"] == "healthy"
    assert source_map["daum"]["state"] == "failure"
    assert source_map["daum"]["consecutive_problem_count"] == 1
    assert source_map["google_trends"]["state"] == "warning"
    assert source_map["youtube"]["state"] == "stale"
    assert diagnostics["attention_source_count"] >= 3


def test_source_freshness_reads_empty_database_and_saved_interval(tmp_path: Path) -> None:
    db_path = tmp_path / "source-freshness.duckdb"
    init_database(db_path)
    with connect_database(db_path) as con:
        con.execute(
            """
            INSERT INTO app_settings(setting_key, setting_value, updated_at)
            VALUES ('trend_refresh_interval_minutes', '180', CURRENT_TIMESTAMP)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = EXCLUDED.setting_value,
                updated_at = EXCLUDED.updated_at
            """
        )
        diagnostics = get_source_freshness_diagnostics(
            con,
            now=datetime(2026, 7, 31, 6, 0, 0),
        )

    assert diagnostics["interval_minutes"] == 180
    assert diagnostics["stale_minutes"] == 360
    assert diagnostics["scheduler_state"] == "no_history"
    assert diagnostics["source_rows"]
    assert all(row["state"] == "no_history" for row in diagnostics["source_rows"])


def test_source_freshness_ui_shows_overdue_warning_and_source_table(monkeypatch) -> None:
    now = datetime(2026, 7, 31, 6, 0, 0)
    diagnostics = {
        "interval_minutes": 240,
        "stale_minutes": 480,
        "scheduler_state": "overdue",
        "latest_background_status": "success",
        "last_background_at": now - timedelta(hours=9),
        "last_background_success_at": now - timedelta(hours=9),
        "background_elapsed_minutes": 540,
        "next_expected_at": now - timedelta(hours=5),
        "stale_after_at": now - timedelta(hours=1),
        "background_error_message": "",
        "attention_source_count": 1,
        "stale_source_count": 0,
        "failed_source_count": 1,
        "no_history_source_count": 0,
        "source_rows": [
            {
                "source_name": "daum",
                "state": "failure",
                "latest_status": "failure",
                "latest_at": now - timedelta(minutes=30),
                "last_healthy_at": now - timedelta(hours=5),
                "last_new_at": now - timedelta(hours=5),
                "latest_elapsed_minutes": 30,
                "healthy_elapsed_minutes": 300,
                "consecutive_problem_count": 1,
                "newly_saved_count": 0,
                "updated_count": 0,
                "error_message": "HTTP 429",
            }
        ],
    }
    monkeypatch.setattr(
        "src.source_freshness_ui.get_source_freshness_diagnostics",
        lambda _con: diagnostics,
    )
    fake_st = _FakeStreamlit()

    render_source_freshness_diagnostics(object(), st_module=fake_st)

    assert fake_st.subheaders == ["출처 신선도·스케줄러 상태"]
    assert [args[0] for args, _kwargs in fake_st.metrics] == [
        "저장된 수집 주기",
        "스케줄러 상태",
        "마지막 백그라운드 실행",
        "주의 출처",
        "이력 없는 출처",
    ]
    assert all(kwargs.get("border") is True for _args, kwargs in fake_st.metrics)
    assert any("두 배가 지나도록" in value for value in fake_st.warnings)
    assert len(fake_st.dataframes) == 1
    assert {"출처", "상태", "마지막 정상", "연속 문제", "오류 요약"}.issubset(
        set(fake_st.dataframes[0].columns)
    )
