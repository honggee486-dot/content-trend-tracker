from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.database import connect_database, init_database
from src.query_discovery_diagnostics_ui import render_query_discovery_diagnostics
from src.services.query_discovery_diagnostics_service import (
    get_query_discovery_diagnostics,
)


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class _FilterColumn:
    def selectbox(self, _label: str, *, options, **_kwargs):
        return options[0]


class _FakeStreamlit:
    def __init__(self) -> None:
        self.metrics: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.captions: list[str] = []
        self.dataframes: list[object] = []

    def subheader(self, _value: str) -> None:
        return None

    def columns(self, count: int) -> list[_FilterColumn]:
        return [_FilterColumn() for _ in range(count)]

    def container(self, **_kwargs) -> _Context:
        return _Context()

    def expander(self, *_args, **_kwargs) -> _Context:
        return _Context()

    def metric(self, *args, **kwargs) -> None:
        self.metrics.append((args, kwargs))

    def caption(self, value: str) -> None:
        self.captions.append(value)

    def dataframe(self, value, **_kwargs) -> None:
        self.dataframes.append(value)


def _insert_discovery(
    con,
    *,
    run_id: str,
    source_name: str,
    source_type: str,
    query: str,
    source_item_id: str,
    is_new: bool,
    rank: int | None,
    discovered_at: datetime,
) -> None:
    con.execute(
        """
        INSERT INTO collection_query_discoveries(
            run_id, source_name, source_type, discovery_query, source_item_id,
            external_id, source_url, is_new, result_rank, discovered_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id,
            source_name,
            source_type,
            query,
            source_item_id,
            f"external-{run_id}",
            f"https://example.com/{run_id}",
            is_new,
            rank,
            discovered_at,
        ],
    )


def test_query_discovery_diagnostics_aggregate_period_and_source(tmp_path: Path) -> None:
    db_path = tmp_path / "query-diagnostics.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 31, 12, 0, 0)

    with connect_database(db_path) as con:
        rows = [
            ("run1", "naver", "naver_news", "AI 검색", "item1", True, 1, now - timedelta(days=2)),
            ("run2", "naver", "naver_news", "AI 검색", "item1", False, 3, now - timedelta(days=1)),
            ("run3", "naver", "naver_news", "AI 검색", "item2", True, 5, now - timedelta(hours=20)),
            ("run4", "naver", "naver_blog", "AI 검색", "item3", True, 2, now - timedelta(hours=10)),
            ("run5", "daum", "daum_web", "AI 검색", "item4", True, 10, now - timedelta(hours=5)),
            ("run6", "daum", "daum_cafe", "오래된 검색", "item5", True, 4, now - timedelta(days=20)),
        ]
        for row in rows:
            _insert_discovery(
                con,
                run_id=row[0],
                source_name=row[1],
                source_type=row[2],
                query=row[3],
                source_item_id=row[4],
                is_new=row[5],
                rank=row[6],
                discovered_at=row[7],
            )

        recent = get_query_discovery_diagnostics(con, days=7, now=now)
        naver = get_query_discovery_diagnostics(
            con, days=7, source_name="naver", now=now
        )
        month = get_query_discovery_diagnostics(con, days=30, now=now)

    assert recent["query_count"] == 1
    assert recent["discovery_count"] == 5
    assert recent["unique_item_count"] == 4
    assert recent["new_count"] == 4
    assert recent["repeat_count"] == 1
    assert recent["duplicate_discovery_count"] == 1
    assert recent["new_rate_percent"] == 80.0
    assert recent["duplicate_rate_percent"] == 20.0
    assert recent["average_rank"] == 4.2
    assert recent["best_rank"] == 1
    assert len(recent["rows"]) == 3

    assert naver["discovery_count"] == 4
    assert naver["unique_item_count"] == 3
    assert naver["new_count"] == 3
    assert {row["source_name"] for row in naver["rows"]} == {"naver"}

    assert month["query_count"] == 2
    assert month["discovery_count"] == 6


def test_query_discovery_diagnostics_ui_renders_metrics_and_limit_note(tmp_path: Path) -> None:
    db_path = tmp_path / "query-diagnostics-ui.duckdb"
    init_database(db_path)
    now = datetime.now()
    with connect_database(db_path) as con:
        _insert_discovery(
            con,
            run_id="run-ui",
            source_name="naver",
            source_type="naver_news",
            query="AI 검색",
            source_item_id="item-ui",
            is_new=True,
            rank=2,
            discovered_at=now,
        )
        fake_st = _FakeStreamlit()
        render_query_discovery_diagnostics(con, st_module=fake_st)

    assert [args[0] for args, _kwargs in fake_st.metrics] == [
        "저장된 수집 주기",
        "스케줄러 상태",
        "마지막 백그라운드 실행",
        "주의 출처",
        "이력 없는 출처",
        "사용된 검색어",
        "발견 기록",
        "신규 원문",
        "고유 원문",
        "검색 결과 순위",
    ]
    assert all(kwargs.get("border") is True for _args, kwargs in fake_st.metrics)
    assert len(fake_st.dataframes) == 2
    assert {"출처", "상태", "마지막 정상", "연속 문제"}.issubset(
        set(fake_st.dataframes[0].columns)
    )
    row = fake_st.dataframes[1].iloc[0]
    assert row["포털"] == "NAVER"
    assert row["세부 출처"] == "NAVER 뉴스"
    assert row["검색어"] == "AI 검색"
    assert row["신규율"] == "100.0%"
    assert any("결과 0건이었던 검색 요청" in value for value in fake_st.captions)


def test_collection_history_ui_calls_query_diagnostics() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "collection_history_ui.py").read_text(
        encoding="utf-8"
    )
    assert "render_query_discovery_diagnostics(con, st_module=st)" in source
