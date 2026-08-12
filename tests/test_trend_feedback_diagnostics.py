from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.database import connect_database, init_database
from src.services.trend_feedback_diagnostics_service import (
    get_trend_feedback_diagnostics,
)
from src.services.trend_feedback_service import save_trend_feedback
from src.trend_feedback_diagnostics_ui import render_trend_feedback_diagnostics


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
        self.dataframes: list[object] = []

    def subheader(self, value: str) -> None:
        self.subheaders.append(value)

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


def _insert_source_item(
    con,
    *,
    source_item_id: str,
    source_type: str,
    external_id: str,
    source_name: str,
    now: datetime,
) -> None:
    con.execute(
        """
        INSERT INTO source_items(
            source_item_id, source_type, external_id, raw_title, normalized_title,
            source_url, normalized_url, source_name, observed_at, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            source_item_id,
            source_type,
            external_id,
            f"원문 {source_item_id}",
            f"원문 {source_item_id}",
            f"https://example.com/{source_item_id}",
            f"https://example.com/{source_item_id}",
            source_name,
            now,
            now,
        ],
    )


def _insert_query_discovery(
    con,
    *,
    run_id: str,
    source_name: str,
    source_type: str,
    query: str,
    source_item_id: str,
    now: datetime,
) -> None:
    con.execute(
        """
        INSERT INTO collection_query_discoveries(
            run_id, source_name, source_type, discovery_query, source_item_id,
            external_id, source_url, is_new, result_rank, discovered_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, TRUE, 1, ?)
        """,
        [
            run_id,
            source_name,
            source_type,
            query,
            source_item_id,
            f"external-{run_id}",
            f"https://example.com/{source_item_id}",
            now,
        ],
    )


def _seed_feedback_diagnostics(con) -> None:
    now = datetime(2026, 7, 31, 5, 0, 0)
    feedback_rows = [
        (
            "cluster_good",
            "좋은 AI 글감",
            "good",
            {"raw_item_count": 4, "unique_evidence_count": 3, "source_type_count": 2, "publisher_count": 2},
        ),
        (
            "cluster_useless",
            "쓸모없는 할인 글감",
            "useless",
            {"raw_item_count": 3, "unique_evidence_count": 1, "source_type_count": 1, "publisher_count": 1},
        ),
        (
            "cluster_false",
            "잘못 묶인 글감",
            "false_merge",
            {"raw_item_count": 3, "unique_evidence_count": 2, "source_type_count": 2, "publisher_count": 1},
        ),
    ]
    for cluster_id, title, feedback_type, diagnostics in feedback_rows:
        save_trend_feedback(
            con,
            cluster_id=cluster_id,
            canonical_title=title,
            feedback_type=feedback_type,
            note=f"{title} 메모",
            diagnostics=diagnostics,
        )

    source_rows = [
        ("item_good_news", "naver_news", "ext1", "뉴스 발행처", "cluster_good"),
        ("item_good_web", "daum_web", "ext2", "웹 발행처", "cluster_good"),
        ("item_useless_blog", "naver_blog", "ext3", "블로그 발행처", "cluster_useless"),
        ("item_false_news", "naver_news", "ext4", "다른 뉴스", "cluster_false"),
    ]
    for source_item_id, source_type, external_id, source_name, cluster_id in source_rows:
        _insert_source_item(
            con,
            source_item_id=source_item_id,
            source_type=source_type,
            external_id=external_id,
            source_name=source_name,
            now=now,
        )
        con.execute(
            "INSERT INTO trend_cluster_items(cluster_id, source_item_id, linked_at) VALUES (?, ?, ?)",
            [cluster_id, source_item_id, now],
        )

    _insert_query_discovery(
        con,
        run_id="run-good-news",
        source_name="naver",
        source_type="naver_news",
        query="AI 검색",
        source_item_id="item_good_news",
        now=now,
    )
    _insert_query_discovery(
        con,
        run_id="run-good-web",
        source_name="daum",
        source_type="daum_web",
        query="AI 검색",
        source_item_id="item_good_web",
        now=now,
    )
    _insert_query_discovery(
        con,
        run_id="run-useless-blog",
        source_name="naver",
        source_type="naver_blog",
        query="할인 검색",
        source_item_id="item_useless_blog",
        now=now,
    )


def test_feedback_diagnostics_aggregate_quality_sources_and_queries(tmp_path: Path) -> None:
    db_path = tmp_path / "feedback-diagnostics.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        _seed_feedback_diagnostics(con)
        diagnostics = get_trend_feedback_diagnostics(con)

    assert diagnostics["total_count"] == 3
    assert diagnostics["good_count"] == 1
    assert diagnostics["rejected_count"] == 2
    assert diagnostics["good_rate_percent"] == 33.3
    assert diagnostics["rejected_rate_percent"] == 66.7
    assert diagnostics["average_unique_evidence_count"] == 2.0
    assert diagnostics["low_evidence_count"] == 1
    assert diagnostics["single_publisher_count"] == 2

    type_map = {row["feedback_type"]: row for row in diagnostics["type_rows"]}
    assert type_map["good"]["average_unique_evidence_count"] == 3.0
    assert type_map["useless"]["low_evidence_rate_percent"] == 100.0

    source_map = {row["source_type"]: row for row in diagnostics["source_type_rows"]}
    assert source_map["naver_news"]["evaluated_count"] == 2
    assert source_map["naver_news"]["good_rate_percent"] == 50.0
    assert source_map["naver_blog"]["rejected_rate_percent"] == 100.0

    query_map = {
        (row["source_type"], row["discovery_query"]): row
        for row in diagnostics["query_rows"]
    }
    assert query_map[("naver_news", "AI 검색")]["good_rate_percent"] == 100.0
    assert query_map[("naver_blog", "할인 검색")]["rejected_rate_percent"] == 100.0
    assert len(diagnostics["recent_rows"]) == 3


def test_feedback_diagnostics_ui_renders_metrics_and_non_automation_notes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "feedback-diagnostics-ui.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        _seed_feedback_diagnostics(con)
        fake_st = _FakeStreamlit()
        render_trend_feedback_diagnostics(con, st_module=fake_st)

    assert fake_st.subheaders == ["사용자 글감 평가 진단"]
    assert [args[0] for args, _kwargs in fake_st.metrics] == [
        "누적 평가",
        "좋은 글감",
        "애매한 글감",
        "제외 판단",
        "독립 근거 평균",
        "발행처 평균",
    ]
    assert all(kwargs.get("border") is True for _args, kwargs in fake_st.metrics)
    assert len(fake_st.dataframes) == 4
    assert set(fake_st.dataframes[0]["평가"].tolist()) == {
        "좋은 글감",
        "쓸모없는 글감",
        "잘못 묶인 주제",
    }
    assert "NAVER 뉴스" in fake_st.dataframes[1]["출처 유형"].tolist()
    assert "AI 검색" in fake_st.dataframes[2]["검색어"].tolist()
    assert any("자동 변경하지 않습니다" in value for value in fake_st.captions)


def test_query_diagnostics_ui_includes_feedback_diagnostics() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "query_discovery_diagnostics_ui.py"
    ).read_text(encoding="utf-8")
    assert "render_trend_feedback_diagnostics(con, st_module=st_module)" in source
