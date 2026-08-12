from datetime import datetime, timedelta
from pathlib import Path

from src import collection_history_ui
from src.database import connect_database, init_database
from src.services.collection_history_service import finish_collection_run, start_collection_run
from src.services.topic_angle_history_service import get_topic_angle_history_summary


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None


class _FakeStreamlit:
    def __init__(self) -> None:
        self.metrics: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.captions: list[str] = []
        self.subheaders: list[str] = []

    def subheader(self, value: str) -> None:
        self.subheaders.append(value)

    def caption(self, value: str) -> None:
        self.captions.append(value)

    def container(self, **_kwargs) -> _Context:
        return _Context()

    def metric(self, *args, **kwargs) -> None:
        self.metrics.append((args, kwargs))


def _finish_topic_angle_run(
    con,
    *,
    started_at: datetime,
    requested: int,
    generated: int,
    directions: int,
    duration_seconds: float,
) -> None:
    run_id = start_collection_run(
        con,
        "topic_angle_generation",
        started_at=started_at,
    )
    finish_collection_run(
        con,
        run_id,
        result={
            "status": "success" if generated == requested else "partial_success",
            "requested_clusters": requested,
            "generated_clusters": generated,
            "generated_angles": directions,
            "attempts": 1,
            "requested_batches": 1,
            "duration_seconds": duration_seconds,
            "error_message": "" if generated == requested else "일부 글감 미처리",
        },
        finished_at=started_at + timedelta(seconds=duration_seconds),
    )


def test_topic_angle_history_summary_is_empty_without_runs(tmp_path: Path) -> None:
    db_path = tmp_path / "empty-history.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        summary = get_topic_angle_history_summary(con)

    assert summary["history_count"] == 0
    assert summary["save_rate_percent"] is None
    assert summary["last_requested_clusters"] == 0


def test_topic_angle_history_summary_aggregates_saved_and_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "topic-angle-history.duckdb"
    init_database(db_path)
    base = datetime(2026, 7, 30, 12, 0, 0)

    with connect_database(db_path) as con:
        _finish_topic_angle_run(
            con,
            started_at=base,
            requested=40,
            generated=30,
            directions=90,
            duration_seconds=1.0,
        )
        _finish_topic_angle_run(
            con,
            started_at=base + timedelta(minutes=10),
            requested=40,
            generated=40,
            directions=120,
            duration_seconds=2.0,
        )
        summary = get_topic_angle_history_summary(con, limit=10)

    assert summary["history_count"] == 2
    assert summary["last_requested_clusters"] == 40
    assert summary["last_saved_clusters"] == 40
    assert summary["last_missing_clusters"] == 0
    assert summary["last_generated_angles"] == 120
    assert summary["total_requested_clusters"] == 80
    assert summary["total_saved_clusters"] == 70
    assert summary["total_missing_clusters"] == 10
    assert summary["save_rate_percent"] == 87.5
    assert summary["problem_run_count"] == 1
    assert summary["average_duration_ms"] == 1500


def test_topic_angle_save_diagnostics_renders_recent_result(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "topic-angle-ui.duckdb"
    init_database(db_path)
    started_at = datetime(2026, 7, 30, 13, 0, 0)

    with connect_database(db_path) as con:
        _finish_topic_angle_run(
            con,
            started_at=started_at,
            requested=40,
            generated=36,
            directions=108,
            duration_seconds=3.5,
        )
        fake_st = _FakeStreamlit()
        monkeypatch.setattr(collection_history_ui, "st", fake_st)
        collection_history_ui._render_topic_angle_save_diagnostics(con)

    assert fake_st.subheaders == ["Gemini 글감 저장 진단"]
    assert [args[0] for args, _kwargs in fake_st.metrics] == [
        "최근 요청 대비 저장",
        "최근 미처리",
        "최근 10회 저장률",
        "최근 10회 부분·실패",
    ]
    assert fake_st.metrics[0][0][1] == "36/40개"
    assert fake_st.metrics[1][0][1] == "4개"
    assert fake_st.metrics[2][0][1] == "90.0%"
    assert fake_st.metrics[3][0][1] == "1회"
    assert all(kwargs.get("border") is True for _args, kwargs in fake_st.metrics)
    assert any("방향 108개" in caption for caption in fake_st.captions)
