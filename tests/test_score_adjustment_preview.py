from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.database import connect_database, init_database
from src.services.score_adjustment_preview_service import (
    MAX_ABSOLUTE_ADJUSTMENT,
    MIN_FEEDBACK_TYPE_COUNT,
    MIN_TOTAL_FEEDBACK,
    build_score_adjustment_preview,
    get_score_adjustment_preview,
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


def _preview_row(
    *,
    feedback_type: str,
    opportunity_score: float,
    item_count: int,
    unique_count: int,
    source_type_count: int,
    publisher_count: int,
) -> dict[str, object]:
    return {
        "cluster_id": f"cluster_{feedback_type}",
        "canonical_title": f"{feedback_type} 글감",
        "feedback_type": feedback_type,
        "opportunity_score": opportunity_score,
        "trend_score": 77.0,
        "item_count": item_count,
        "unique_evidence_count": unique_count,
        "source_type_count": source_type_count,
        "publisher_count": publisher_count,
        "updated_at": datetime(2026, 7, 31, 6, 0, 0),
    }


def test_score_preview_caps_adjustment_and_preserves_feedback_direction() -> None:
    rows = [
        _preview_row(
            feedback_type="good",
            opportunity_score=96.0,
            item_count=5,
            unique_count=5,
            source_type_count=4,
            publisher_count=4,
        ),
        _preview_row(
            feedback_type="false_merge",
            opportunity_score=50.0,
            item_count=5,
            unique_count=1,
            source_type_count=1,
            publisher_count=1,
        ),
    ]

    preview = build_score_adjustment_preview(
        rows,
        total_feedback_count=25,
        feedback_type_counts={"good": 3, "false_merge": 4},
    )
    preview_map = {row["feedback_type"]: row for row in preview}

    good = preview_map["good"]
    false_merge = preview_map["false_merge"]
    assert good["is_eligible"] is True
    assert good["suggested_adjustment"] == MAX_ABSOLUTE_ADJUSTMENT
    assert good["preview_opportunity_score"] == 100.0
    assert good["trend_score"] == 77.0
    assert false_merge["is_eligible"] is True
    assert false_merge["suggested_adjustment"] == -MAX_ABSOLUTE_ADJUSTMENT
    assert false_merge["preview_opportunity_score"] == 42.0
    assert good["direction"] == "가점"
    assert false_merge["direction"] == "감점"


def test_score_preview_hides_numeric_result_until_samples_are_sufficient() -> None:
    preview = build_score_adjustment_preview(
        [
            _preview_row(
                feedback_type="good",
                opportunity_score=60.0,
                item_count=4,
                unique_count=3,
                source_type_count=2,
                publisher_count=2,
            )
        ],
        total_feedback_count=5,
        feedback_type_counts={"good": 1},
    )[0]

    assert preview["is_eligible"] is False
    assert preview["preview_opportunity_score"] is None
    assert preview["sample_status"] == "표본 부족"
    assert f"전체 평가 {MIN_TOTAL_FEEDBACK}건 필요" in preview["sample_reason"]
    assert f"같은 평가 {MIN_FEEDBACK_TYPE_COUNT}건 필요" in preview["sample_reason"]
    assert preview["suggested_adjustment"] > 0


def _insert_cluster(con, cluster_id: str, title: str, opportunity_score: float) -> None:
    now = datetime(2026, 7, 31, 6, 0, 0)
    con.execute(
        """
        INSERT INTO trend_clusters(
            cluster_id, canonical_title, trend_score, opportunity_score,
            fact_risk_score, quality_score, rediscovery_score,
            recommendation_status, item_count, source_type_count, publisher_count,
            source_types_json, score_reasons_json, quality_reasons_json,
            first_seen_at, last_seen_at, calculated_at
        ) VALUES (?, ?, 70, ?, 5, 80, 2, 'review', 4, 2, 2,
                  '["naver_news", "daum_web"]', '[]', '[]', ?, ?, ?)
        """,
        [cluster_id, title, opportunity_score, now, now, now],
    )


def test_score_preview_reads_current_clusters_without_writing_scores(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "score-preview.duckdb"
    init_database(db_path)

    with connect_database(db_path) as con:
        for index in range(3):
            cluster_id = f"cluster_good_{index}"
            title = f"좋은 글감 {index}"
            _insert_cluster(con, cluster_id, title, 60.0 + index)
            save_trend_feedback(
                con,
                cluster_id=cluster_id,
                canonical_title=title,
                feedback_type="good",
                note="좋은 평가",
                diagnostics={
                    "raw_item_count": 4,
                    "unique_evidence_count": 3,
                    "source_type_count": 2,
                    "publisher_count": 2,
                },
            )
        for index in range(17):
            save_trend_feedback(
                con,
                cluster_id=f"old_ambiguous_{index}",
                canonical_title=f"이전 애매 글감 {index}",
                feedback_type="ambiguous",
                diagnostics={
                    "raw_item_count": 2,
                    "unique_evidence_count": 1,
                    "source_type_count": 1,
                    "publisher_count": 1,
                },
            )

        before = con.execute(
            "SELECT cluster_id, opportunity_score, trend_score, recommendation_status "
            "FROM trend_clusters ORDER BY cluster_id"
        ).fetchall()
        preview = get_score_adjustment_preview(con)
        fake_st = _FakeStreamlit()
        render_trend_feedback_diagnostics(con, st_module=fake_st)
        after = con.execute(
            "SELECT cluster_id, opportunity_score, trend_score, recommendation_status "
            "FROM trend_clusters ORDER BY cluster_id"
        ).fetchall()

    assert preview["total_feedback_count"] == 20
    assert preview["current_feedback_count"] == 3
    assert preview["orphaned_feedback_count"] == 17
    assert preview["eligible_count"] == 3
    assert all(row["preview_opportunity_score"] is not None for row in preview["rows"])
    assert before == after

    preview_frame = fake_st.dataframes[-1]
    assert {
        "평가",
        "글감 기회 원점수",
        "조정 방향",
        "예상 조정점수",
        "표본 상태",
        "조정 근거",
    }.issubset(set(preview_frame.columns))
    assert set(preview_frame["표본 상태"].tolist()) == {"미리보기 가능"}
    assert any("글감 기회 점수뿐" in value for value in fake_st.captions)
    assert any("자동 변경하지 않습니다" in value for value in fake_st.captions)
